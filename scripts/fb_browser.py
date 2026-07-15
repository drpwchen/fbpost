"""Playwright browser management — cookie injection, login capture, stealth."""

import asyncio
import json
import os
import sys
from datetime import datetime

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

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

    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"

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
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()

    print("\nStopping daemon...")
    if os.path.exists(_DAEMON_PID_FILE):
        os.remove(_DAEMON_PID_FILE)
    await save_storage_state(context, profile)
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

    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"

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
    """Check if we're logged in by verifying URL doesn't redirect to login."""
    url = page.url
    if "/login" in url or "checkpoint" in url:
        return False
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
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
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


_PRIVACY_LABELS = {
    "EVERYONE": "所有人",
    "FRIENDS": "朋友",
    "SELF": "只限本人",
}


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

    try:
        page = await context.new_page()
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)

        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)

        # Open the composer (label varies EN/zh-TW)
        opened = False
        for label in ["What's on your mind", "在想些什麼"]:
            try:
                await page.get_by_text(label, exact=False).first.click(timeout=5000)
                opened = True
                break
            except Exception:
                continue
        if not opened:
            print("Error: Could not open the composer (post box not found).", file=sys.stderr)
            sys.exit(1)
        await page.wait_for_timeout(3000)

        dialog = page.get_by_role("dialog").first

        # PRE-SEND style verification: type text and confirm it landed before
        # doing anything else (same discipline as send_message in fb_messenger.py).
        textbox = dialog.get_by_role("textbox").first
        await textbox.click(timeout=5000)
        await textbox.type(text, delay=20, timeout=15000)
        await page.wait_for_timeout(500)
        try:
            input_text = await textbox.inner_text()
        except Exception:
            input_text = ""
        if text not in input_text:
            print("FAILED: post text not confirmed in composer box. Aborting.", file=sys.stderr)
            sys.exit(1)

        if image_path:
            if not os.path.isfile(image_path):
                print(f"Error: image file not found: {image_path}", file=sys.stderr)
                sys.exit(1)
            file_input = dialog.locator('input[type="file"]').first
            await file_input.set_input_files(os.path.abspath(image_path), timeout=15000)
            try:
                await dialog.get_by_text("已上傳的影音內容", exact=False).first.wait_for(
                    state="visible", timeout=20000
                )
                print("Photo attached.")
            except Exception:
                print("FAILED: photo did not appear to attach (upload indicator never showed).", file=sys.stderr)
                sys.exit(1)

        # Audience/privacy — always set explicitly rather than trusting
        # whatever FB last remembered as the default.
        target_label = _PRIVACY_LABELS.get(privacy, "所有人")
        audience_row = dialog.get_by_text("貼文分享對象", exact=False).first
        await audience_row.click(timeout=8000)
        await page.wait_for_timeout(1500)
        privacy_dialogs = page.get_by_role("dialog")
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
            if dt <= datetime.now():
                print("Error: --schedule time must be in the future.", file=sys.stderr)
                sys.exit(1)
            date_str = f"{dt.year}年{dt.month}月{dt.day}日"
            time_str = dt.strftime("%H:%M")

            await dialog.get_by_text("排程", exact=True).first.click(timeout=8000)
            await page.wait_for_timeout(2000)

            heading = page.get_by_text("排程選項", exact=True).first
            await heading.wait_for(state="visible", timeout=8000)
            schedule_container = heading.locator(
                "xpath=ancestor::*[.//text()[contains(.,'日期')] and .//text()[contains(.,'時間')]][1]"
            )
            schedule_inputs = schedule_container.locator("input")
            await schedule_inputs.nth(0).fill(date_str, timeout=5000)
            await page.wait_for_timeout(500)
            await schedule_inputs.nth(1).fill(time_str, timeout=5000)
            await page.wait_for_timeout(500)

            confirm_btn = schedule_container.locator('div[role="button"][aria-label="排定稍後通話"]').first
            try:
                await confirm_btn.wait_for(state="visible", timeout=5000)
            except Exception:
                # Upstream label may change; fall back to any enabled button
                # in the schedule container that isn't the close/date-picker button.
                confirm_btn = schedule_container.locator(
                    'div[role="button"]:not([aria-label*="關閉"]):not([aria-label*="日期"])'
                ).last
            await confirm_btn.click(timeout=8000)
            await page.wait_for_timeout(2000)

            # This confirm button only closes the date/time popup and stores
            # the chosen time on the composer — it does NOT submit anything.
            # Confirmed by inspection: after it closes, the composer's main
            # action button relabels from "發佈" to "設定貼文發佈時間"
            # ("Set post publish time"), and THAT is the real submit button.
            # A prior version of this function stopped after the popup
            # confirm and reported "scheduled" even though nothing was ever
            # sent — the draft just sat open until the browser closed.
            finalize_btn = dialog.get_by_text("設定貼文發佈時間", exact=True).first
            await finalize_btn.wait_for(state="visible", timeout=5000)
            await finalize_btn.click(timeout=8000)
            await page.wait_for_timeout(3000)

            # POST-SEND verification: the composer dialog should be gone once
            # the scheduled post is actually accepted. If it's still open,
            # nothing was submitted — report failure instead of guessing.
            still_open = await dialog.count() and await dialog.is_visible()
            if still_open:
                print(
                    "FAILED: composer still open after clicking 設定貼文發佈時間 — "
                    "post was NOT scheduled.", file=sys.stderr,
                )
                sys.exit(1)
            print(f"Post scheduled for {date_str} {time_str}.")
        else:
            publish_btn = dialog.get_by_text("發佈", exact=True).first
            await publish_btn.click(timeout=8000)
            await page.wait_for_timeout(3000)

            still_open = await dialog.count() and await dialog.is_visible()
            if still_open:
                print(
                    "FAILED: composer still open after clicking 發佈 — post was NOT published.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print("Post published.")

        await save_storage_state(context, profile)

    finally:
        await browser.close()
        await p.stop()
