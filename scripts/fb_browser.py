"""Playwright browser management — cookie injection, login capture, stealth."""

import json
import os
import sys

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


async def connect_to_running_chrome(p):
    """Try to connect to a running Chrome with remote debugging on port 9222.

    Start Chrome with: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222
    Returns browser or None.
    """
    try:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=2000)
        return browser
    except Exception:
        return None


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

    # Try to connect to running Chrome first (fastest path)
    if not headless:
        browser = await connect_to_running_chrome(p)
        if browser:
            context = browser.contexts[0] if browser.contexts else await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=ua,
            )
            print("Connected to running Chrome (CDP).")
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

    # Wait for successful login — poll URL until no /login
    try:
        while True:
            await page.wait_for_timeout(2000)
            url = page.url
            if "facebook.com" in url and "/login" not in url and "checkpoint" not in url:
                # Verify we can access the main page
                if page.url.rstrip("/").endswith("facebook.com") or "/home" in page.url or "/?sk=" in page.url:
                    break
                # Also break if we're on any non-login facebook page
                if "facebook.com" in page.url and "/login" not in page.url:
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
