"""Playwright browser management — cookie injection, login capture, stealth."""

import asyncio
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from scripts.fb_config import FB_USER_AGENT

stealth = Stealth(
    navigator_platform_override="MacIntel",
    navigator_vendor_override="Google Inc.",
)


def _base_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _profile_dir(profile: str) -> str:
    return os.path.join(_base_dir(), "profiles", profile)


def _storage_state_path(profile: str) -> str:
    return os.path.join(_profile_dir(profile), "storage_state.json")


def _cookies_path(profile: str) -> str:
    return os.path.join(_profile_dir(profile), "cookies.json")


def _config_path(profile: str) -> str:
    return os.path.join(_profile_dir(profile), "config.json")


def load_profile_config(profile: str) -> dict:
    """Load profile config (PIN code, etc.) from profiles/<profile>/config.json."""
    config_path = _config_path(profile)
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


def convert_cookies(profile: str) -> list[dict]:
    """Convert cookies.json (browser export format) to Playwright format."""
    cookies_file = _cookies_path(profile)
    if not os.path.exists(cookies_file):
        print(f"Error: {cookies_file} not found.", file=sys.stderr)
        sys.exit(1)

    with open(cookies_file) as f:
        raw_cookies = json.load(f)

    pw_cookies = []
    for c in raw_cookies:
        same_site_map = {
            "no_restriction": "None",
            "unspecified": "Lax",
            "lax": "Lax",
            "strict": "Strict",
        }
        same_site = same_site_map.get(
            (c.get("sameSite") or "unspecified").lower(), "Lax"
        )

        pw_cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".facebook.com"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", True),
            "httpOnly": c.get("httpOnly", False),
            "sameSite": same_site,
        }
        if c.get("expirationDate"):
            pw_cookie["expires"] = c["expirationDate"]

        pw_cookies.append(pw_cookie)

    return pw_cookies


def _user_data_dir(profile: str) -> str:
    return os.path.join(_profile_dir(profile), "chromium_data")


CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
_DAEMON_PID_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".daemon.pid")


async def connect_to_daemon(p):
    """Try to connect to the fbpost daemon browser via CDP.

    Returns (browser, context) or (None, None).
    """
    try:
        browser = await p.chromium.connect_over_cdp(CDP_URL, timeout=2000)
        # Reuse existing context (has cookies already)
        context = browser.contexts[0] if browser.contexts else None
        return browser, context
    except Exception:
        return None, None


async def start_daemon(profile: str):
    """Start a persistent headless Chromium that stays running for fast access.

    Other commands connect to it via CDP, skipping cold start entirely.
    """
    import signal

    p = await async_playwright().start()

    # Check if already running
    browser, _ = await connect_to_daemon(p)
    if browser:
        print(f"Daemon already running on port {CDP_PORT}.")
        await p.stop()
        return
    if os.path.exists(_DAEMON_PID_FILE):
        # Leftover from a hard kill/crash — the port probe above is the real
        # liveness signal, so reconcile the stale file.
        os.remove(_DAEMON_PID_FILE)

    user_data = _user_data_dir(profile)
    os.makedirs(user_data, exist_ok=True)
    storage_path = _storage_state_path(profile)

    print(f"Starting fbpost daemon on port {CDP_PORT}...")

    # Launch with remote debugging enabled
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            f"--remote-debugging-port={CDP_PORT}",
        ],
    )

    ua = FB_USER_AGENT

    if os.path.exists(storage_path):
        context = await browser.new_context(
            storage_state=storage_path,
            viewport={"width": 1920, "height": 1080},
            user_agent=ua,
        )
    else:
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=ua,
        )
        pw_cookies = convert_cookies(profile)
        await context.add_cookies(pw_cookies)

    await stealth.apply_stealth_async(context)

    # Pre-warm: navigate to Messenger so first send is instant
    page = await context.new_page()
    print("Pre-warming Messenger...")
    await page.goto("https://www.facebook.com/messages/", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Handle any PIN dialog
    await handle_pin_dialog(page, profile)

    # Save PID
    with open(_DAEMON_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    print(f"Daemon ready (PID {os.getpid()}). Commands will auto-connect.")
    print("Press Ctrl+C to stop.")

    # Keep alive until interrupted
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    except NotImplementedError:
        pass  # Windows event loops don't support signal handlers; Ctrl+C
        # surfaces as KeyboardInterrupt instead.

    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Clean up even on an unexpected exit so no orphaned Chromium keeps
        # the CDP port and no stale PID file is left behind.
        print("\nStopping daemon...")
        if os.path.exists(_DAEMON_PID_FILE):
            os.remove(_DAEMON_PID_FILE)
        try:
            await save_storage_state(context, profile)
        except Exception:
            pass
        await browser.close()
        await p.stop()
    print("Daemon stopped.")


async def create_browser_context(profile: str, headless: bool = True, persistent: bool = False):
    """Create Playwright browser context with cookies or storage state.

    If persistent=True, uses a persistent browser context (reuses Chromium
    profile data across runs — much faster startup, no cookie re-injection).

    Returns (playwright, browser, context) tuple. Caller must close.
    For persistent contexts, browser is None (context IS the browser).
    """
    p = await async_playwright().start()

    launch_args = [
        "--disable-blink-features=AutomationControlled",
    ]

    ua = FB_USER_AGENT

    # Try to connect to daemon first (fastest path — no cold start)
    browser, context = await connect_to_daemon(p)
    if browser and context:
        print("Connected to daemon.")
        # Mark browser so callers know not to close it
        browser._fbpost_daemon = True
        return p, browser, context

    if persistent:
        user_data = _user_data_dir(profile)
        os.makedirs(user_data, exist_ok=True)

        context = await p.chromium.launch_persistent_context(
            user_data,
            headless=headless,
            args=launch_args,
            viewport={"width": 1920, "height": 1080},
            user_agent=ua,
        )

        # Always ensure cookies are present — persistent context may
        # lose them on first run or after cleanup
        try:
            existing = await context.cookies("https://www.facebook.com")
            has_auth = any(c["name"] == "c_user" for c in existing)
        except Exception:
            has_auth = False

        if not has_auth:
            # Try storage_state cookies first, then raw cookies
            storage_path = _storage_state_path(profile)
            if os.path.exists(storage_path):
                with open(storage_path) as f:
                    state = json.load(f)
                if state.get("cookies"):
                    await context.add_cookies(state["cookies"])
                    has_auth = True

            if not has_auth:
                pw_cookies = convert_cookies(profile)
                await context.add_cookies(pw_cookies)

        await stealth.apply_stealth_async(context)
        return p, None, context

    # Non-persistent (original behavior)
    browser = await p.chromium.launch(
        headless=headless,
        args=launch_args,
    )

    storage_path = _storage_state_path(profile)

    # Prefer storage_state if available (has cookies + localStorage)
    if os.path.exists(storage_path):
        context = await browser.new_context(
            storage_state=storage_path,
            viewport={"width": 1920, "height": 1080},
            user_agent=ua,
        )
    else:
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=ua,
        )
        # Inject cookies from cookies.json
        pw_cookies = convert_cookies(profile)
        await context.add_cookies(pw_cookies)

    # Apply stealth to the context
    await stealth.apply_stealth_async(context)

    return p, browser, context


async def handle_pin_dialog(page, profile: str) -> bool:
    """Auto-enter PIN code if the E2E encryption dialog appears.

    Reads PIN from profiles/<profile>/config.json {"pincode": "123456"}.
    Returns True if PIN was entered, False if no dialog or no PIN configured.
    """
    # Check if PIN input is visible
    pin_input = page.locator('input[aria-label*="PIN"], input[aria-label*="pin"]').first
    try:
        is_visible = await pin_input.is_visible()
    except Exception:
        return False

    if not is_visible:
        return False

    config = load_profile_config(profile)
    pincode = config.get("pincode", "")
    if not pincode:
        print("PIN dialog detected but no pincode in config.json. Please add it.", file=sys.stderr)
        print(f"  Create {_config_path(profile)} with: {{\"pincode\": \"your_pin\"}}", file=sys.stderr)
        return False

    print("Entering PIN code...")
    # PIN inputs are typically individual digit fields
    pin_inputs = page.locator('input[aria-label*="PIN"], input[aria-label*="pin"]')
    pin_count = await pin_inputs.count()

    if pin_count > 1:
        # Multiple single-digit inputs
        for i, digit in enumerate(pincode):
            if i < pin_count:
                inp = pin_inputs.nth(i)
                await inp.fill(digit)
                await page.wait_for_timeout(100)
    else:
        # Single input field
        await pin_input.fill(pincode)

    # Press Enter or click confirm button
    await page.wait_for_timeout(500)
    confirm_btn = page.locator(
        'div[role="dialog"] div[role="button"]:has-text("確認"), '
        'div[role="dialog"] div[role="button"]:has-text("Confirm"), '
        'div[role="dialog"] div[role="button"]:has-text("Submit")'
    ).first
    try:
        if await confirm_btn.is_visible():
            await confirm_btn.click()
        else:
            await pin_input.press("Enter")
    except Exception:
        await pin_input.press("Enter")

    await page.wait_for_timeout(3000)
    print("PIN entered.")
    return True


async def verify_login(page) -> bool:
    """Check if we're logged in.

    URL checks alone are not enough: the logged-out homepage lives at the
    bare facebook.com URL (no "/login" substring) — the same blind spot that
    once broke capture_login. So also require the c_user auth cookie and the
    absence of a visible password field.
    """
    url = page.url
    if "/login" in url or "checkpoint" in url:
        return False
    try:
        cookies = await page.context.cookies("https://www.facebook.com")
        if not any(c["name"] == "c_user" for c in cookies):
            return False
    except Exception:
        return False
    try:
        if await page.locator('input[name="pass"]').first.is_visible():
            return False
    except Exception:
        pass
    return True


async def save_storage_state(context, profile: str):
    """Save browser context state (cookies + localStorage) for reuse."""
    storage_path = _storage_state_path(profile)
    await context.storage_state(path=storage_path)
    print(f"Storage state saved to {storage_path}")


async def capture_login(profile: str):
    """Open headed browser for manual login, then capture cookies."""
    p = await async_playwright().start()

    browser = await p.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )

    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=FB_USER_AGENT,
    )

    await stealth.apply_stealth_async(context)
    page = await context.new_page()

    print("Opening Facebook login page...")
    print("Please log in manually. The browser will close automatically once logged in.")
    await page.goto("https://www.facebook.com/")

    # Wait for successful login — poll for the c_user auth cookie, since the
    # logged-out homepage is also at the bare facebook.com URL (no "/login"
    # substring), which made URL-only checks fire immediately on page load.
    try:
        while True:
            await page.wait_for_timeout(2000)
            cookies = await context.cookies()
            if any(
                c["name"] == "c_user" and "facebook.com" in c.get("domain", "")
                for c in cookies
            ):
                break
    except Exception:
        pass

    print("Login detected! Saving cookies...")

    # Save storage state
    profile_dir = _profile_dir(profile)
    os.makedirs(profile_dir, exist_ok=True)
    await save_storage_state(context, profile)

    # Also save cookies in the original format for compatibility
    cookies = await context.cookies()
    fb_cookies = [c for c in cookies if "facebook.com" in c.get("domain", "")]

    # Convert back to the export format
    export_cookies = []
    for c in fb_cookies:
        same_site_map = {"None": "no_restriction", "Lax": "lax", "Strict": "strict"}
        export = {
            "domain": c["domain"],
            "hostOnly": not c["domain"].startswith("."),
            "httpOnly": c.get("httpOnly", False),
            "name": c["name"],
            "path": c.get("path", "/"),
            "sameSite": same_site_map.get(c.get("sameSite", "Lax"), "unspecified"),
            "secure": c.get("secure", True),
            "session": c.get("expires", -1) == -1,
            "storeId": "0",
            "value": c["value"],
        }
        if c.get("expires") and c["expires"] > 0:
            export["expirationDate"] = c["expires"]
        export_cookies.append(export)

    cookies_path = _cookies_path(profile)
    with open(cookies_path, "w") as f:
        json.dump(export_cookies, f, indent=2)
    print(f"Cookies saved to {cookies_path}")

    await browser.close()
    await p.stop()
    print("Done! You can now use other commands.")


async def _teardown(p, browser, context, page=None):
    """Close what this command owns — never kill a shared daemon browser.

    create_browser_context may hand back the long-lived daemon browser
    (marked _fbpost_daemon); closing it would silently tear the daemon down
    and every later command would pay a cold start again. In that case only
    close the page we opened on it.
    """
    try:
        if browser is not None and getattr(browser, "_fbpost_daemon", False):
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
        elif browser is not None:
            await browser.close()
        elif context is not None:
            await context.close()
    finally:
        await p.stop()


async def _wait_composer_closed(page, dialog, seconds=15) -> bool:
    """Poll for the composer dialog to disappear; True means it closed.

    Uses the dialog's element handle captured up front so a *different*
    dialog appearing after submit can't be mistaken for the composer still
    being open. The close can lag several seconds (slower on a public
    audience / slow network) — a fixed wait plus a single check used to
    false-fail on EVERYONE posts even though the post WAS accepted.
    """
    try:
        handle = await dialog.element_handle(timeout=2000)
    except Exception:
        handle = None
    for _ in range(seconds * 2):
        await page.wait_for_timeout(500)
        try:
            if handle is not None:
                if not await handle.is_visible():
                    return True
            elif not (bool(await dialog.count()) and await dialog.is_visible()):
                return True
        except Exception:
            return True
    return False


_PRIVACY_LABELS = {
    "EVERYONE": "所有人",
    "FRIENDS": "朋友",
    "SELF": "只限本人",
}


def _time_candidates(dt) -> list[str]:
    """Spellings to try for the composer's time field, likeliest first."""
    h12 = dt.hour % 12 or 12
    ampm_zh = "上午" if dt.hour < 12 else "下午"
    ampm_en = "AM" if dt.hour < 12 else "PM"
    return [
        dt.strftime("%H:%M"),
        f"{ampm_zh}{h12}:{dt.minute:02d}",
        f"{h12}:{dt.minute:02d} {ampm_en}",
    ]


def _date_candidates(dt) -> list[str]:
    return [
        f"{dt.year}年{dt.month}月{dt.day}日",
        f"{dt.month}/{dt.day}/{dt.year}",
        dt.strftime("%Y-%m-%d"),
    ]


def _read_time(value: str):
    """Parse a time field back out ('23:30', '11:30 PM', '下午11:30') -> (h, m)."""
    m = re.search(r"(\d{1,2}):(\d{2})", value or "")
    if not m:
        return None
    h, minute = int(m.group(1)), int(m.group(2))
    up = (value or "").upper()
    if ("PM" in up or "下午" in value) and h < 12:
        h += 12
    if ("AM" in up or "上午" in value) and h == 12:
        h = 0
    return h, minute


def _read_date(value: str):
    """Parse a date field back out -> (y, m, d), whatever separator FB uses."""
    nums = [int(n) for n in re.findall(r"\d+", value or "")]
    if len(nums) < 3:
        return None
    if nums[0] > 31:  # y m d
        return nums[0], nums[1], nums[2]
    return nums[2], nums[0], nums[1]  # m d y


async def _fill_verified(page, field, candidates, reader, expected, label) -> bool:
    """Fill `field` until reading it back yields `expected`.

    Facebook's schedule fields accept a keystroke stream and silently keep
    their own default when the format doesn't parse — so a blind fill can
    schedule a post for a time nobody asked for. Nothing has been submitted
    at this point, so failing here is safe.
    """
    seen = []
    for candidate in candidates:
        try:
            await field.fill(candidate, timeout=5000)
        except Exception:
            continue
        await page.wait_for_timeout(600)
        try:
            got = (await field.input_value()).strip()
        except Exception:
            got = ""
        seen.append(f"{candidate!r}->{got!r}")
        if reader(got) == expected:
            return True
    print(
        f"FAILED setting the schedule {label}: Facebook did not accept it "
        f"(tried {'; '.join(seen) or 'nothing'}). Nothing was posted.",
        file=sys.stderr,
    )
    return False


async def _pick_schedule_date(page, date_field, dt) -> bool:
    """Choose the schedule date from the composer's calendar popup.

    The date field is a combobox, not a text box: writing into it updates the
    DOM value (so every read-back looks right) while the picker keeps its own
    state, and the post publishes on the DEFAULT date. Clicking the day cell is
    the only thing that commits. Cells are named by their full date
    ("2026年8月29日 星期六"), so matching is unambiguous even for the
    neighbouring-month days a month view spills over.
    """
    target = f"{dt.year}年{dt.month}月{dt.day}日"
    await date_field.click(timeout=5000)
    await page.wait_for_timeout(1200)
    grid = page.get_by_role("grid").first
    try:
        await grid.wait_for(state="visible", timeout=8000)
    except Exception:
        print("FAILED: the schedule date picker did not open.", file=sys.stderr)
        return False
    for _ in range(24):  # ~2 years of 下個月 clicks
        cell = grid.get_by_role("gridcell").filter(has_text=re.compile(re.escape(target)))
        if await cell.count():
            if await cell.first.get_attribute("aria-disabled") == "true":
                print(
                    f"FAILED: {target} is greyed out in the date picker — "
                    "Facebook only schedules about 30 days ahead (and at least "
                    "10 minutes from now). Nothing was posted.",
                    file=sys.stderr,
                )
                return False
            await cell.first.click(timeout=5000)
            await page.wait_for_timeout(1200)
            got = _read_date((await date_field.input_value()).strip())
            if got == (dt.year, dt.month, dt.day):
                return True
            print(
                f"FAILED: picked {target} but the date field reads {got}.",
                file=sys.stderr,
            )
            return False
        try:
            await page.get_by_role("button", name="下個月").first.click(timeout=5000)
        except Exception:
            break
        await page.wait_for_timeout(900)
    print(f"FAILED: could not reach {target} in the date picker.", file=sys.stderr)
    return False


async def post_via_composer(
    profile: str,
    text: str,
    image_path: str = None,
    schedule_at: str = None,
    privacy: str = "EVERYONE",
    headless: bool = True,
):
    """Post to Facebook via the browser composer UI.

    Used instead of the fast GraphQL path (cmd_post in fb.py) whenever a
    photo attachment or a scheduled publish time is requested — neither is
    supported by the raw ComposerStoryCreateMutation payload this tool
    otherwise sends, so we drive the real composer like a human would.
    """
    p, browser, context = await create_browser_context(profile, headless, persistent=False)

    page = None
    step = "opening facebook.com"
    try:
        page = await context.new_page()
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=30000)

        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)

        # Open the composer (label varies EN/zh-TW). No fixed settle wait —
        # the click timeout itself absorbs render latency.
        step = "opening the composer"
        opened = False
        for label in ["在想些什麼", "What's on your mind"]:
            try:
                await page.get_by_text(label, exact=False).first.click(timeout=8000)
                opened = True
                break
            except Exception:
                continue
        if not opened:
            print("Error: Could not open the composer (post box not found).", file=sys.stderr)
            sys.exit(1)

        dialog = page.get_by_role("dialog").first
        textbox = dialog.get_by_role("textbox").first
        await textbox.wait_for(state="visible", timeout=10000)

        # PRE-SEND style verification: type text and confirm it landed before
        # doing anything else (same discipline as send_message in fb_messenger.py).
        step = "typing the post text"
        await textbox.click(timeout=5000)
        # Typing is per-character, so the timeout must scale with the text —
        # a fixed 15s silently caps posts at ~750 chars.
        type_delay = 20
        type_timeout = max(30000, len(text) * type_delay * 3)
        await textbox.type(text, delay=type_delay, timeout=type_timeout)
        await page.wait_for_timeout(500)
        try:
            input_text = await textbox.inner_text()
        except Exception:
            input_text = ""
        # The composer's inner_text re-renders blank lines (a paragraph break
        # comes back as extra empty lines), so a raw substring check fails on
        # any multi-paragraph post even when every character landed. Compare
        # with blank-line runs collapsed — still a real content check.
        def _norm(s):
            return "\n".join(
                line.strip() for line in s.replace("\r", "").split("\n") if line.strip()
            )

        if _norm(text) not in _norm(input_text):
            if os.environ.get("FB_DEBUG_TYPING"):
                import difflib
                print(f"[debug] expected {len(text)} chars, box has {len(input_text)}", file=sys.stderr)
                for line in difflib.unified_diff(
                    text.splitlines(), input_text.splitlines(),
                    fromfile="expected", tofile="box", lineterm="", n=1
                ):
                    print("[debug] " + line, file=sys.stderr)
            print("FAILED: post text not confirmed in composer box. Aborting.", file=sys.stderr)
            sys.exit(1)

        if image_path:
            if not os.path.isfile(image_path):
                print(f"Error: image file not found: {image_path}", file=sys.stderr)
                sys.exit(1)
            step = "attaching the photo"
            file_input = dialog.locator('input[type="file"]').first
            await file_input.set_input_files(os.path.abspath(image_path), timeout=15000)
            # Accept either the media-gallery caption or a rendered preview
            # image — a single still image can render without the
            # "已上傳的影音內容" caption, which used to false-FAIL the attach.
            attached = False
            for _ in range(40):  # up to ~20s
                try:
                    if await dialog.get_by_text("已上傳的影音內容", exact=False).first.is_visible():
                        attached = True
                    elif await dialog.locator('img[src^="blob:"]').first.is_visible():
                        attached = True
                except Exception:
                    pass
                if attached:
                    break
                await page.wait_for_timeout(500)
            if attached:
                print("Photo attached.")
            else:
                print("FAILED: photo did not appear to attach (no upload indicator or preview image).", file=sys.stderr)
                sys.exit(1)

        # Audience/privacy — always set explicitly rather than trusting
        # whatever FB last remembered as the default.
        step = "setting the audience"
        target_label = _PRIVACY_LABELS.get(privacy, "所有人")
        audience_row = dialog.get_by_text("貼文分享對象", exact=False).first
        await audience_row.click(timeout=8000)
        # Wait for the audience modal to open (dialog count grows) instead of
        # a blind sleep.
        privacy_dialogs = page.get_by_role("dialog")
        for _ in range(10):
            if await privacy_dialogs.count() > 1:
                break
            await page.wait_for_timeout(300)
        pd_count = await privacy_dialogs.count()
        privacy_modal = privacy_dialogs.nth(pd_count - 1)
        await privacy_modal.get_by_text(target_label, exact=True).first.click(timeout=5000)
        await page.wait_for_timeout(500)
        await privacy_modal.get_by_text("完成", exact=True).first.click(timeout=5000)
        await page.wait_for_timeout(1000)

        if schedule_at:
            try:
                dt = datetime.strptime(schedule_at, "%Y-%m-%d %H:%M")
            except ValueError:
                print(
                    f"Error: --schedule must be 'YYYY-MM-DD HH:MM' (24hr), got {schedule_at!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Times are typed verbatim into the composer, so FB interprets
            # them in the ACCOUNT's timezone; this validation uses the local
            # clock and assumes the two match. FB also enforces a ~10-minute
            # minimum lead time — catch that here with a clear message
            # instead of a generic composer failure later.
            if dt <= datetime.now() + timedelta(minutes=10):
                print(
                    "Error: --schedule time must be at least 10 minutes in the future "
                    "(Facebook's minimum; time is interpreted in your FB account's timezone).",
                    file=sys.stderr,
                )
                sys.exit(1)
            date_str = f"{dt.year}年{dt.month}月{dt.day}日"
            time_str = dt.strftime("%H:%M")

            step = "opening the schedule options"
            await dialog.get_by_text("排程", exact=True).first.click(timeout=8000)

            heading = page.get_by_text("排程選項", exact=True).first
            await heading.wait_for(state="visible", timeout=8000)
            schedule_container = heading.locator(
                "xpath=ancestor::*[.//text()[contains(.,'日期')] and .//text()[contains(.,'時間')]][1]"
            )
            step = "filling the schedule date/time"
            schedule_inputs = schedule_container.locator("input")
            want_date = (dt.year, dt.month, dt.day)
            want_time = (dt.hour, dt.minute)
            # The time field is a plain text box — fill it, then read it back
            # (a 24-hour "23:30" can be rejected outright, leaving FB's default
            # of now+2h behind a success message). The date needs the calendar.
            settled = False
            for _ in range(3):
                if not await _fill_verified(
                    page, schedule_inputs.nth(1), _time_candidates(dt),
                    _read_time, want_time, "time",
                ):
                    sys.exit(1)
                if not await _pick_schedule_date(page, schedule_inputs.nth(0), dt):
                    sys.exit(1)
                got_date = _read_date((await schedule_inputs.nth(0).input_value()).strip())
                got_time = _read_time((await schedule_inputs.nth(1).input_value()).strip())
                if got_date == want_date and got_time == want_time:
                    settled = True
                    break
                print(
                    f"  schedule fields drifted after filling (date={got_date}, "
                    f"time={got_time}) — refilling",
                    file=sys.stderr,
                )
            if not settled:
                print(
                    "FAILED: could not get Facebook to hold both the schedule "
                    "date and time. Nothing was posted.",
                    file=sys.stderr,
                )
                sys.exit(1)

            # Popup confirm button. Try plausible confirm labels first, then
            # the historical "排定稍後通話" aria-label, then fall back to the
            # last non-close/non-date button (the path that has worked in
            # live testing). This button only stores the chosen time — the
            # real submit is the relabeled main composer button below.
            step = "confirming the schedule popup"
            confirm_btn = None
            for name in ["儲存", "完成", "確定", "Save", "Done"]:
                cand = schedule_container.get_by_role("button", name=name).first
                try:
                    await cand.wait_for(state="visible", timeout=1000)
                    confirm_btn = cand
                    break
                except Exception:
                    continue
            if confirm_btn is None:
                cand = schedule_container.locator('div[role="button"][aria-label="排定稍後通話"]').first
                try:
                    await cand.wait_for(state="visible", timeout=2000)
                    confirm_btn = cand
                except Exception:
                    confirm_btn = schedule_container.locator(
                        'div[role="button"]:not([aria-label*="關閉"]):not([aria-label*="日期"])'
                    ).last
            await confirm_btn.click(timeout=8000)
            await page.wait_for_timeout(1000)

            # This confirm button only closes the date/time popup and stores
            # the chosen time on the composer — it does NOT submit anything.
            # Confirmed by inspection: after it closes, the composer's main
            # action button relabels from "發佈" to "設定貼文發佈時間"
            # ("Set post publish time"), and THAT is the real submit button.
            # A prior version of this function stopped after the popup
            # confirm and reported "scheduled" even though nothing was ever
            # sent — the draft just sat open until the browser closed.
            step = "submitting the scheduled post"
            finalize_btn = dialog.get_by_text("設定貼文發佈時間", exact=True).first
            await finalize_btn.wait_for(state="visible", timeout=5000)
            await finalize_btn.click(timeout=8000)

            # POST-SEND verification: the composer closes only when FB
            # accepts the scheduled post.
            if not await _wait_composer_closed(page, dialog):
                print(
                    "FAILED: composer still open after clicking 設定貼文發佈時間 — "
                    "post was NOT scheduled.", file=sys.stderr,
                )
                sys.exit(1)
            print(f"Post scheduled for {date_str} {time_str}.")
        else:
            step = "publishing the post"
            publish_btn = dialog.get_by_text("發佈", exact=True).first
            await publish_btn.click(timeout=8000)

            if not await _wait_composer_closed(page, dialog):
                print(
                    "FAILED: composer still open after clicking 發佈 — post was NOT published.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print("Post published.")

        await save_storage_state(context, profile)

    except PlaywrightTimeoutError:
        print(
            f"FAILED while {step}: Facebook's UI did not respond as expected "
            "(a selector timed out). The layout may have changed or the page is slow.",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        await _teardown(p, browser, context, page)


# ---------------------------------------------------------------------------
# Scheduled-post management (list / delete)
#
# Personal profiles have no Facebook Page, so Meta Business Suite is
# unavailable. Scheduled posts live in the Professional Dashboard's Content
# Library under the "已排定發佈" (Scheduled) tab. We drive that table.
# ---------------------------------------------------------------------------

CONTENT_LIBRARY_URL = (
    "https://www.facebook.com/professional_dashboard/content/content_library/"
)
_SCHEDULED_TAB = "已排定發佈"
_ROW_ACTION_LABEL = "可對此貼文採取的動作"
_DELETE_MENUITEM = "刪除貼文"
_VIEW_POST_BUTTON = "查看貼文頁面的連結"


async def _open_scheduled_tab(page):
    """Navigate to the Content Library and switch to the Scheduled tab."""
    await page.goto(CONTENT_LIBRARY_URL, wait_until="domcontentloaded", timeout=30000)
    if not await verify_login(page):
        print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
        sys.exit(1)
    # The tab is a role=tab; get_by_text often resolves to a hidden measuring
    # span reported as "not visible", so target the accessible tab role.
    tab = page.get_by_role("tab", name=_SCHEDULED_TAB)
    try:
        await tab.first.wait_for(state="visible", timeout=20000)
    except Exception:
        pass  # fall through to the click loop's own error handling
    for i in range(await tab.count()):
        try:
            await tab.nth(i).scroll_into_view_if_needed(timeout=3000)
            await tab.nth(i).click(timeout=4000)
            # Wait for rows to render (an empty list is valid — cap at ~6s).
            for _ in range(12):
                if await page.get_by_role("button", name=_ROW_ACTION_LABEL).count():
                    break
                await page.wait_for_timeout(500)
            return
        except Exception:
            continue
    print(
        f"Error: could not open the '{_SCHEDULED_TAB}' (Scheduled) tab.",
        file=sys.stderr,
    )
    sys.exit(1)


async def _scheduled_rows(page):
    """Return (action_button_locator, [{index, text, when}, ...]) in list order.

    Each scheduled row owns exactly one "actions" button. To read a row's OWN
    text (not the whole table's), climb to the nearest ancestor that contains
    this button but NOT the neighbouring rows' action buttons — i.e. the
    tightest ancestor holding a single action button.
    """
    actions = page.get_by_role("button", name=_ROW_ACTION_LABEL)
    # The table lazy-loads on scroll — keep scrolling the last row into view
    # until the count stops growing, so indexes cover ALL scheduled posts
    # (a truncated list would make delete-by-index hit the wrong post).
    prev = -1
    n = await actions.count()
    while n and n != prev:
        prev = n
        try:
            await actions.nth(n - 1).scroll_into_view_if_needed(timeout=3000)
        except Exception:
            break
        await page.wait_for_timeout(1000)
        n = await actions.count()
    # While re-rendering, FB briefly mounts skeleton rows that already carry an
    # actions button but no content. Require the count to hold still before
    # trusting it, or indexes shift under us between listing and deleting.
    for _ in range(6):
        await page.wait_for_timeout(1000)
        again = await actions.count()
        if again == n:
            break
        n = again
    rows = []
    for i in range(n):
        text, when = "", ""
        try:
            # each scheduled entry is a role=row that owns exactly one action
            # button; the header row has none, so buttons map 1:1 to data rows.
            row = actions.nth(i).locator("xpath=ancestor::*[@role='row'][1]")
            raw = (await row.inner_text()).replace("\xa0", " ")
            lines = [l.strip() for l in raw.splitlines() if l.strip() and l.strip() != "預覽"]
            # Schedule info reads "已排定 • 明天上午11:00". Anchor on 已排定
            # ONLY — an earlier version also treated any line containing
            # 上午/下午 as schedule info, which swallowed post bodies that
            # happen to say e.g. "上午和下午" and left `text` empty.
            # (the label and the time are two separate lines: "已排定 •" then
            # "明天上午7:00" — a time line is short and matches 上午/下午HH:MM)
            sched = [
                l for l in lines
                if "已排定" in l
                or (len(l) <= 30 and re.search(r"(上午|下午)\s*\d{1,2}:\d{2}", l))
            ]
            when = " ".join(sched)
            # FB renders the preview text twice; take the first non-schedule line
            text_lines = [l for l in lines if l not in sched]
            text = text_lines[0] if text_lines else ""
        except Exception:
            pass
        if not text and not when:
            continue  # skeleton/placeholder row — never let it take an index
        # "n" is the 1-based number users see and pass back on the command
        # line; "index" is where the row's button sits in the locator list.
        rows.append({"n": len(rows) + 1, "index": i, "text": text[:100], "when": when})
    return actions, rows


async def _row_post_id(page, actions, index: int, wait_ms: int = 20000):
    """Return the numeric post_id of the scheduled row at 0-based `index`.

    The row markup itself carries only CDN image ids, so we open the row's
    post-preview dialog — the 查看貼文頁面的連結 button, i.e. the same dialog
    where you can comment on an unpublished post by hand — and read the id out
    of the GraphQL response that renders it. Returns (post_id | None, ids_seen);
    a set of more than one distinct id means "don't guess" and yields None.
    """
    found: list[str] = []
    pending: list = []

    async def _scan(resp):
        if "/api/graphql" not in resp.url:
            return
        try:
            body = await resp.text()
        except Exception:
            return
        for m in re.finditer(r'"post_id":"(\d+)"', body):
            found.append(m.group(1))
        # top-level feedback ids: base64("feedback:<post_id>"). The comment-level
        # ones look like "feedback:<post_id>_<comment_id>" and are skipped.
        for m in re.finditer(r'"id":"(ZmVlZGJhY2s[A-Za-z0-9=_-]+)"', body):
            try:
                dec = base64.b64decode(m.group(1) + "==").decode("utf-8")
            except Exception:
                continue
            if dec.startswith("feedback:") and dec[len("feedback:"):].isdigit():
                found.append(dec[len("feedback:"):])

    def _handler(resp):
        pending.append(asyncio.create_task(_scan(resp)))

    page.on("response", _handler)
    try:
        row = actions.nth(index).locator("xpath=ancestor::*[@role='row'][1]")
        btn = row.get_by_role("button", name=_VIEW_POST_BUTTON)
        if not await btn.count():
            print(
                f"Could not find the '{_VIEW_POST_BUTTON}' button on row "
                f"[{index + 1}] — cannot resolve its post_id.",
                file=sys.stderr,
            )
            return None, []
        await btn.first.click(timeout=8000)
        for _ in range(max(1, wait_ms // 500)):
            if found:
                break
            await page.wait_for_timeout(500)
        await page.wait_for_timeout(1500)  # let sibling responses land too
    finally:
        page.remove_listener("response", _handler)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:  # close the preview so the caller can keep driving the list
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)
        except Exception:
            pass

    ids = sorted(set(found))
    if len(ids) == 1:
        return ids[0], ids
    if not ids:
        print(
            f"No post_id appeared in the preview of row [{index + 1}].",
            file=sys.stderr,
        )
    else:
        print(
            f"Ambiguous: row [{index + 1}]'s preview exposed several post ids "
            f"({', '.join(ids)}) — refusing to guess.",
            file=sys.stderr,
        )
    return None, ids


async def list_scheduled_posts(profile: str, headless: bool = True, with_ids: bool = False):
    """Print all scheduled posts (1-based index, time, preview[, post_id])."""
    p, browser, context = await create_browser_context(profile, headless, persistent=False)
    page = None
    try:
        page = await context.new_page()
        await _open_scheduled_tab(page)
        actions, rows = await _scheduled_rows(page)
        if not rows:
            print("No scheduled posts.")
            return
        print(f"Scheduled posts ({len(rows)}):")
        print("-" * 72)
        if with_ids:
            print("(resolving post ids — ~10s per post)")
        for r in rows:
            print(f"[{r['n']}] {r['when']}")
            print(f"    {r['text']}")
            if with_ids:
                pid, _ = await _row_post_id(page, actions, r["index"])
                print(f"    post_id: {pid or 'UNRESOLVED'}")
    finally:
        await _teardown(p, browser, context, page)


def parse_when(when: str, now: datetime = None):
    """Turn a row's "已排定 • 明天下午9:45" into a datetime, or None.

    Used to check after the fact that a post really landed on the time that
    was asked for — the composer's schedule fields have silently reverted
    before, and the composer itself cannot see the result.
    """
    if not when:
        return None
    now = now or datetime.now()
    t = _read_time(when)
    if not t:
        return None
    md = re.search(r"(\d{1,2})月(\d{1,2})日", when)
    if md:
        month, day = int(md.group(1)), int(md.group(2))
        year = now.year + 1 if (month, day) < (now.month, now.day) else now.year
        date = datetime(year, month, day)
    elif "明天" in when:
        date = now + timedelta(days=1)
    elif "今天" in when:
        date = now
    else:
        return None
    return date.replace(hour=t[0], minute=t[1], second=0, microsecond=0)


def _norm_preview(s: str) -> str:
    """Collapse whitespace so post text and row preview text compare sanely."""
    return re.sub(r"\s+", "", s or "")


async def resolve_scheduled_post_id(
    profile: str,
    index: int = None,
    match: str = None,
    contains: str = None,
    headless: bool = True,
):
    """Resolve one scheduled post to its numeric post_id.

    Pick the row either by 1-based `index` (as shown by post-list-scheduled) or
    by `contains` — a text fragment that must match EXACTLY ONE row. `match` is
    the same safety check delete uses: the chosen row's preview must contain it.
    Returns {"index", "when", "text", "post_id"} or None (reason on stderr).
    """
    p, browser, context = await create_browser_context(profile, headless, persistent=False)
    page = None
    try:
        page = await context.new_page()
        await _open_scheduled_tab(page)
        actions, rows = await _scheduled_rows(page)
        if not rows:
            print("No scheduled posts.", file=sys.stderr)
            return None

        if index is None:
            needle = _norm_preview(contains)
            hits = [r for r in rows if needle and needle in _norm_preview(r["text"])]
            if len(hits) != 1:
                print(
                    f"Cannot pick a row by text: {len(hits)} of {len(rows)} scheduled "
                    f"posts match {(contains or '')[:40]!r}. Run post-list-scheduled "
                    "and use the index instead.",
                    file=sys.stderr,
                )
                return None
            target = hits[0]
        else:
            if index < 1 or index > len(rows):
                print(
                    f"Error: index {index} out of range (1..{len(rows)}).",
                    file=sys.stderr,
                )
                return None
            target = rows[index - 1]

        if match and match not in target["text"] and match not in target["when"]:
            print(
                f"ABORTED: row [{target['n']}] does not match --match {match!r}.\n"
                f"  Found instead: {target['when']} — {target['text']}\n"
                "  The list may have changed; re-run post-list-scheduled.",
                file=sys.stderr,
            )
            return None

        post_id, _ = await _row_post_id(page, actions, target["index"])
        if not post_id:
            return None
        return {
            "index": target["n"],
            "when": target["when"],
            "text": target["text"],
            "post_id": post_id,
        }
    finally:
        await _teardown(p, browser, context, page)


async def delete_scheduled_post(profile: str, index: int, headless: bool = False, match: str = None):
    """Delete the scheduled post at 1-based `index` (as shown by list).

    The index is resolved freshly in THIS browser session; if `match` is
    given, the target row's preview text must contain it or nothing is
    deleted — protects against the list having changed since you looked.
    """
    p, browser, context = await create_browser_context(profile, headless, persistent=False)
    page = None
    try:
        page = await context.new_page()
        await _open_scheduled_tab(page)
        actions, rows = await _scheduled_rows(page)
        before = len(rows)
        if index < 1 or index > before:
            print(f"Error: index {index} out of range (1..{before}).", file=sys.stderr)
            sys.exit(1)
        target = rows[index - 1]
        if match and match not in target["text"] and match not in target["when"]:
            print(
                f"ABORTED: row [{index}] does not match --match {match!r}.\n"
                f"  Found instead: {target['when']} — {target['text']}\n"
                "  The list may have changed; re-run post-list-scheduled.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Deleting [{index}] {target['when']} — {target['text']}")

        await actions.nth(target["index"]).click(timeout=6000)
        await page.wait_for_timeout(1500)
        await page.get_by_role("menuitem", name=_DELETE_MENUITEM).first.click(timeout=6000)
        await page.wait_for_timeout(1500)

        # Confirmation dialog: click its primary delete/confirm button.
        dialogs = page.get_by_role("dialog")
        dc = await dialogs.count()
        if dc:
            dlg = dialogs.nth(dc - 1)
            for name in ["刪除貼文", "刪除", "確認"]:
                b = dlg.get_by_role("button", name=name)
                if await b.count():
                    await b.first.click(timeout=5000)
                    break
        await page.wait_for_timeout(4000)

        # POST verification: the row count must drop, or we report failure
        # rather than falsely claiming success. Re-scan through the same row
        # parser as `before` — a raw button count also sees skeleton rows and
        # has reported a false FAILED on a deletion that did go through.
        _, after_rows = await _scheduled_rows(page)
        after = len(after_rows)
        if after < before:
            print(f"Deleted. Scheduled posts: {before} -> {after}")
        else:
            print(
                f"FAILED: scheduled count did not drop ({before} -> {after}); "
                "deletion may not have gone through.",
                file=sys.stderr,
            )
            sys.exit(1)
    finally:
        await _teardown(p, browser, context, page)


# ---------------------------------------------------------------------------
# Content data report (匯出資料 → 建立資料報告 / 報告紀錄)
# ---------------------------------------------------------------------------

_EXPORT_BUTTON = "匯出資料"
_CREATE_REPORT_MENUITEM = "建立資料報告"
_REPORT_HISTORY_MENUITEM = "報告紀錄"
_ADVANCED_SWITCH = "顯示進階設定"
_METRICS_BUTTON = "顯示的衡量指標"
_CREATE_CSV_BUTTON = "建立報告（.csv）"
_REVENUE_PREFIX = "收益"
_REPORT_DONE = "已完成"


async def _open_export_menu(page, item: str):
    """Open the 匯出資料 menu and pick one of its menu items."""
    await page.get_by_role("button", name=_EXPORT_BUTTON).first.click(timeout=10000)
    mi = page.get_by_role("menuitem", name=item)
    await mi.first.wait_for(state="visible", timeout=10000)
    await mi.first.click(timeout=5000)
    await page.wait_for_timeout(1500)


async def _close_any_dialog(page):
    """Close whatever dialog is open (關閉 button, Escape as fallback)."""
    dialogs = page.get_by_role("dialog")
    if await dialogs.count():
        btn = dialogs.last.get_by_role("button", name="關閉")
        try:
            if await btn.count():
                await btn.first.click(timeout=3000)
            else:
                await page.keyboard.press("Escape")
        except Exception:
            await page.keyboard.press("Escape")
    for _ in range(10):
        if not await page.get_by_role("dialog").count():
            return
        await page.wait_for_timeout(300)


async def _history_state(page):
    """With the 報告紀錄 dialog open, return (download_links, entry_count, first_entry_text)."""
    dlg = page.get_by_role("dialog").last
    links = dlg.get_by_role("link")
    count = await links.count()
    text = " ".join((await dlg.inner_text()).split())
    first_entry = text.split("下載")[0] if "下載" in text else text
    return links, count, first_entry


async def export_content_report(profile: str, out_path: str, headless: bool = True,
                                timeout_s: int = 240, include_revenue: bool = False):
    """Create a content data report and download the CSV.

    Flow: 匯出資料 → 建立資料報告 → 顯示進階設定 → 顯示的衡量指標 → uncheck all
    收益* metrics (checked revenue metrics silently DROP every column after them
    in the export — pass include_revenue=True to keep them at your own risk)
    → 建立報告（.csv） → poll 報告紀錄 until a new entry is
    已完成 → click its 下載 link and save the CSV to `out_path`.

    Date range is the dashboard default (past 28 days). Timestamps in the CSV
    are US Pacific — convert with scripts/report_to_md.py (+15h to Taipei).
    """
    p, browser, context = await create_browser_context(profile, headless, persistent=False)
    page = None
    try:
        page = await context.new_page()
        await page.goto(CONTENT_LIBRARY_URL, wait_until="domcontentloaded", timeout=30000)
        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)
        await page.wait_for_timeout(3000)

        # Baseline: number of reports already in history, so we can tell the
        # new one apart from an older same-day report.
        await _open_export_menu(page, _REPORT_HISTORY_MENUITEM)
        _, baseline, _ = await _history_state(page)
        await _close_any_dialog(page)

        # Configure and request the report.
        await _open_export_menu(page, _CREATE_REPORT_MENUITEM)
        dlg = page.get_by_role("dialog").last
        await dlg.get_by_role("switch", name=_ADVANCED_SWITCH).click(timeout=8000)
        await dlg.get_by_role("button", name=_METRICS_BUTTON).click(timeout=8000)
        await page.wait_for_timeout(1500)
        if include_revenue:
            print(
                "Warning: keeping 收益 metrics — Facebook's export silently drops "
                "every column after them; expect a truncated CSV.",
                file=sys.stderr,
            )
        else:
            boxes = dlg.get_by_role("checkbox")
            unchecked = []
            for i in range(await boxes.count()):
                el = boxes.nth(i)
                name = (await el.get_attribute("aria-label")) or ""
                if not name.strip():
                    try:
                        name = await el.inner_text()
                    except Exception:
                        name = ""
                name = " ".join(name.split())
                if name.startswith(_REVENUE_PREFIX) and (await el.get_attribute("aria-checked")) == "true":
                    await el.click(timeout=5000)
                    unchecked.append(name)
            if unchecked:
                print(f"Excluded metrics: {', '.join(unchecked)}")
            else:
                print(
                    "Warning: no checked 收益 metric found — if any stays checked, "
                    "columns after it are silently dropped from the CSV.",
                    file=sys.stderr,
                )
        await dlg.get_by_role("button", name=_CREATE_CSV_BUTTON).click(timeout=8000)
        for _ in range(30):
            if not await page.get_by_role("dialog").count():
                break
            await page.wait_for_timeout(500)
        print("Report requested; polling 報告紀錄 for completion...")

        # Poll history until the NEW report is 已完成, then download it.
        now = datetime.now()
        today_str = f"於{now.year}/{now.month}/{now.day}產生"
        requested_at = time.time()
        deadline = requested_at + timeout_s
        last_err = ""
        while True:
            try:
                # Reload fresh each poll — while a report is generating, the
                # header/匯出資料 control can be temporarily absent or replaced,
                # so any single failed attempt just means "retry next round".
                await page.goto(CONTENT_LIBRARY_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                await _open_export_menu(page, _REPORT_HISTORY_MENUITEM)
                links, count, first_entry = await _history_state(page)
            except Exception as e:
                last_err = str(e).splitlines()[0][:120]
                if time.time() > deadline:
                    print(
                        f"FAILED: report polling kept erroring until timeout "
                        f"({timeout_s}s). Last error: {last_err}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                await _close_any_dialog(page)
                await page.wait_for_timeout(6000)
                continue
            fresh = count > baseline
            done = today_str in first_entry and _REPORT_DONE in first_entry
            if done and not fresh and time.time() > requested_at + 45:
                # The history list caps how many entries it shows, so the
                # count can't grow past the cap. A completed same-day report
                # at the top is the one we just requested (or its identical
                # same-day twin) — take it rather than stalling.
                fresh = True
            if fresh and done:
                # The 下載 link opens an l.facebook.com redirect in a new tab,
                # which never surfaces as a page-level download event. The CDN
                # URL is signed (works without cookies) — unwrap and GET it.
                href = await links.first.get_attribute("href")
                if not href:
                    print("FAILED: download link has no href.", file=sys.stderr)
                    sys.exit(1)
                if "/l.php" in href:
                    q = parse_qs(urlparse(href).query)
                    href = q.get("u", [href])[0]
                resp = await page.request.get(href)
                if not resp.ok:
                    print(f"FAILED: download GET returned {resp.status}.", file=sys.stderr)
                    sys.exit(1)
                body = await resp.body()
                head = body[:200].decode("utf-8-sig", errors="replace")
                if "貼文編號" not in head and "," not in head:
                    print(
                        f"FAILED: downloaded content does not look like the report CSV "
                        f"(starts with: {head[:80]!r}).",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                with open(out_path, "wb") as f:
                    f.write(body)
                print(f"Saved: {out_path}")
                return
            if time.time() > deadline:
                print(
                    f"FAILED: report not completed within {timeout_s}s "
                    f"(history entries {baseline} -> {count}; newest: {first_entry[:120]}).",
                    file=sys.stderr,
                )
                sys.exit(1)
            await _close_any_dialog(page)
            await page.wait_for_timeout(6000)
    finally:
        await _teardown(p, browser, context, page)
