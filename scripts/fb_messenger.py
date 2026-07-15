"""Messenger operations via Playwright browser automation."""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta

from scripts.fb_browser import (
    create_browser_context,
    handle_pin_dialog,
    save_storage_state,
    verify_login,
)


def _contacts_path(profile: str) -> str:
    """Return path to the contacts cache file."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "profiles", profile, "contacts.json")


def _load_contacts(profile: str) -> dict:
    """Load cached contacts {name: {thread_id, e2ee, last_used}}."""
    path = _contacts_path(profile)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_contact(profile: str, name: str, thread_id: str, e2ee: bool = False):
    """Save or update a contact in the cache."""
    contacts = _load_contacts(profile)
    contacts[name] = {
        "thread_id": thread_id,
        "e2ee": e2ee,
        "last_used": datetime.now().isoformat(),
    }
    path = _contacts_path(profile)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(contacts, f, ensure_ascii=False, indent=2)


def resolve_thread_id(profile: str, name_or_id: str) -> tuple[str, bool]:
    """Resolve a name or thread ID to (thread_id, is_e2ee).

    If name_or_id looks like a thread ID (numeric), return it directly.
    Otherwise, look up the contacts cache by name (fuzzy match).
    Returns (thread_id, is_e2ee) or raises SystemExit if not found.
    """
    # Already a thread ID
    if name_or_id.isdigit():
        e2ee = len(name_or_id) > 15
        return name_or_id, e2ee

    # Look up in contacts cache
    contacts = _load_contacts(profile)

    # Exact match
    if name_or_id in contacts:
        c = contacts[name_or_id]
        return c["thread_id"], c.get("e2ee", False)

    # Fuzzy match — substring or partial
    matches = []
    for cname, cdata in contacts.items():
        if name_or_id in cname or cname in name_or_id:
            matches.append((cname, cdata))

    if len(matches) == 1:
        cname, cdata = matches[0]
        print(f"Matched contact: {cname} → {cdata['thread_id']}")
        return cdata["thread_id"], cdata.get("e2ee", False)
    elif len(matches) > 1:
        print("Multiple contacts match:", file=sys.stderr)
        for cname, cdata in matches:
            print(f"  {cname}: {cdata['thread_id']}", file=sys.stderr)
        print("Be more specific.", file=sys.stderr)
        sys.exit(1)

    # Not found
    print(f"Contact '{name_or_id}' not found in cache.", file=sys.stderr)
    print("Use a thread ID directly, or run 'fb contacts' to see cached contacts.", file=sys.stderr)
    print("Contacts are auto-cached when you use 'fb search' or 'fb read'.", file=sys.stderr)
    sys.exit(1)


def list_contacts(profile: str):
    """Print cached contacts."""
    contacts = _load_contacts(profile)
    if not contacts:
        print("No cached contacts. Use 'fb search <name>' to discover and cache threads.")
        return

    print(f"\n  {'Name':<25} {'Thread ID':<22} {'E2E':<5} {'Type':<6} {'Last Used'}")
    print(f"  {'─' * 85}")
    for name, data in sorted(contacts.items(), key=lambda x: x[1].get("last_used", ""), reverse=True):
        e2ee = "Yes" if data.get("e2ee") else "No"
        ctype = data.get("type", "")
        last = data.get("last_used", "")[:10]
        print(f"  {name:<25} {data['thread_id']:<22} {e2ee:<5} {ctype:<6} {last}")
    print(f"\n  {len(contacts)} contacts cached.")


async def verify_contacts(profile: str, count: int = 20, headless: bool = True):
    """Verify cached E2E contacts by reading thread headers.

    Opens each thread briefly to check if it's 1-on-1 or group,
    then updates the contacts cache with the type.
    """
    contacts = _load_contacts(profile)
    e2ee_contacts = [
        (name, data) for name, data in contacts.items()
        if data.get("e2ee") and not data.get("type")
    ]
    # Sort by last_used descending
    e2ee_contacts.sort(key=lambda x: x[1].get("last_used", ""), reverse=True)
    e2ee_contacts = e2ee_contacts[:count]

    if not e2ee_contacts:
        print("No unverified E2E contacts to check.")
        return

    p, browser, context = await create_browser_context(profile, headless)

    try:
        page = await context.new_page()

        # Go to Messenger first to handle PIN
        print("Loading Messenger...")
        await page.goto("https://www.facebook.com/messages/", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        for _ in range(3):
            if await _dismiss_dialogs(page, profile):
                await page.wait_for_timeout(2000)
            else:
                break

        if not await verify_login(page):
            print("Error: Not logged in.", file=sys.stderr)
            sys.exit(1)

        print(f"Verifying {len(e2ee_contacts)} contacts...\n")
        print(f"  {'#':<4} {'Name':<25} {'Type':<8} {'Members'}")
        print(f"  {'─' * 70}")

        for i, (name, data) in enumerate(e2ee_contacts):
            tid = data["thread_id"]
            url = f"https://www.facebook.com/messages/e2ee/t/{tid}/"

            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                await _dismiss_dialogs(page, profile)
                await page.wait_for_timeout(1000)

                # Check thread header for participant count
                # 1-on-1: shows single name; Group: shows member names/count
                header = page.locator('[role="main"] h2, [role="main"] h3').first
                try:
                    header_text = await header.inner_text(timeout=3000)
                except Exception:
                    header_text = ""

                # Check for group indicators in the page
                members_el = page.locator('text=/\\d+ 位成員|\\d+ members/i').first
                try:
                    members_text = await members_el.inner_text(timeout=1000)
                    ctype = "group"
                except Exception:
                    members_text = ""
                    # Read a few messages to check sender diversity
                    msg_rows = page.locator('[role="main"] [role="row"]')
                    row_count = await msg_rows.count()
                    senders = set()
                    for j in range(min(row_count, 10)):
                        try:
                            row_text = await msg_rows.nth(j).inner_text()
                            lines = [l.strip() for l in row_text.split('\n') if l.strip()]
                            if lines and len(lines[0]) <= 30:
                                senders.add(lines[0])
                        except Exception:
                            pass
                    # Filter out common non-sender lines
                    senders -= {'', '你傳送了', '進入', '載入中……'}
                    ctype = "dm" if len(senders) <= 2 else "group"
                    members_text = ", ".join(list(senders)[:3])

                # Update contact cache
                contacts[name]["type"] = ctype
                print(f"  {i:<4} {name:<25} {ctype:<8} {members_text[:35]}")

            except Exception as e:
                print(f"  {i:<4} {name:<25} {'error':<8} {str(e)[:35]}")

        # Save updated contacts
        path = _contacts_path(profile)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(contacts, f, ensure_ascii=False, indent=2)

        dm_count = sum(1 for n, d in e2ee_contacts if contacts[n].get("type") == "dm")
        group_count = sum(1 for n, d in e2ee_contacts if contacts[n].get("type") == "group")
        print(f"\n  Verified: {dm_count} DM, {group_count} group")

        await save_storage_state(context, profile)

    finally:
        await browser.close()
        await p.stop()


async def send_message(profile: str, thread_id: str, text: str, headless: bool = True):
    """Send a message to a Messenger thread."""
    is_daemon = False
    p, browser, context = await create_browser_context(profile, headless, persistent=False)

    try:
        is_daemon = browser and getattr(browser, '_fbpost_daemon', False)

        if thread_id.isdigit() and len(thread_id) > 15:
            url = f"https://www.facebook.com/messages/e2ee/t/{thread_id}/"
        else:
            url = f"https://www.facebook.com/messages/t/{thread_id}/"

        # For daemon: try to find an existing page already on this thread
        page = None
        if is_daemon:
            for pg in context.pages:
                if thread_id in pg.url:
                    page = pg
                    break
            if not page and context.pages:
                page = context.pages[0]

        if not page:
            page = await context.new_page()

        # Navigate — always go to URL (even for daemon, thread may differ)
        current_thread = ""
        for part in ["/messages/e2ee/t/", "/messages/t/"]:
            if part in page.url:
                current_thread = page.url.split(part)[-1].rstrip("/").split("?")[0]

        if current_thread == thread_id:
            print(f"Reusing existing page for {thread_id}.")
            await page.bring_to_front()
        else:
            # Navigate directly to the thread URL.
            # For E2E: if input doesn't appear, the retry loop below
            # will reload the page, which usually resolves encryption loading.
            print(f"Navigating to {url}...")
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            # Handle PIN or other blocking dialogs
            for _ in range(3):
                if await _dismiss_dialogs(page, profile):
                    await page.wait_for_timeout(2000)
                else:
                    break

        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)

        # Wait for the message input OR PIN dialog
        print("Waiting for message input...")

        input_box = None
        for attempt in range(10):
            # Try multiple selectors for the input box
            for selector_fn in [
                lambda: page.get_by_role("textbox", name="Message"),
                lambda: page.get_by_role("textbox", name="訊息"),
                lambda: page.locator('[contenteditable="true"][role="textbox"]').first,
            ]:
                ib = selector_fn()
                try:
                    await ib.wait_for(state="visible", timeout=2000)
                    input_box = ib
                    break
                except Exception:
                    pass
            if input_box:
                break

            # Maybe PIN dialog is blocking — keep trying
            if await _dismiss_dialogs(page, profile):
                await page.wait_for_timeout(2000)
                continue

            # Reload page to retry E2E decryption
            if attempt in (2, 5):
                print(f"  Input not found (attempt {attempt+1}), reloading...")
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                for _ in range(3):
                    if await _dismiss_dialogs(page, profile):
                        await page.wait_for_timeout(2000)
                    else:
                        break
                continue

            if attempt == 9:
                print("Error: Could not find message input.", file=sys.stderr)
                sys.exit(1)

        # Verify we're on the correct thread — abort if wrong
        if thread_id not in page.url:
            print(f"ABORT: wrong thread! Expected {thread_id}, got {page.url}", file=sys.stderr)
            sys.exit(1)

        # PRE-SEND: fill text and verify it's in the input before pressing Enter
        await input_box.click(force=True)
        await page.wait_for_timeout(500)

        await input_box.fill(text)
        await page.wait_for_timeout(300)

        try:
            input_text = await input_box.inner_text()
        except Exception:
            input_text = ""

        if text not in input_text:
            print("  fill() failed, falling back to type()...")
            await input_box.click(force=True)
            await page.keyboard.press("Meta+a")
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(200)
            await input_box.type(text, delay=30)
            await page.wait_for_timeout(300)

            try:
                input_text = await input_box.inner_text()
            except Exception:
                input_text = ""

        if text not in input_text:
            print(f"FAILED: text not in input box. Aborting send.", file=sys.stderr)
            sys.exit(1)

        # Text confirmed in input — send it
        await input_box.press("Enter")
        await page.wait_for_timeout(3000)

        # POST-SEND: pressing Enter doesn't guarantee delivery (e.g. E2E not
        # ready yet, box not focused). Confirm the input box actually cleared
        # before reporting success — otherwise the text is still sitting there.
        try:
            remaining = await input_box.inner_text()
        except Exception:
            remaining = ""

        if text in remaining:
            print(f"FAILED: input box still contains the text after Enter — message was NOT sent.", file=sys.stderr)
            sys.exit(1)

        print(f"Message sent: \"{text[:50]}{'...' if len(text) > 50 else ''}\"")



    finally:
        if is_daemon:
            pass  # keep browser + page alive for reuse
        elif browser:
            await browser.close()
        else:
            await context.close()
        await p.stop()


async def _extract_conversations(page, count: int) -> list[dict]:
    """Extract conversation list from Messenger sidebar."""
    # Use role="row" inside the navigation area — catches both regular and E2E chats
    rows = page.locator('div[role="navigation"] div[role="row"]')
    row_count = await rows.count()

    conversations = []

    for i in range(row_count):
        if len(conversations) >= count:
            break
        try:
            row = rows.nth(i)
            box = await row.bounding_box()
            # Only sidebar items (x < 400, reasonable y)
            if not box or box["x"] > 400:
                continue

            text_content = await row.inner_text()
            lines = [l.strip() for l in text_content.split("\n") if l.strip()]
            if not lines:
                continue

            # Try to get thread ID from an <a> link inside this row
            thread_id = ""
            e2ee = False
            # Check E2E link first
            e2ee_link = row.locator('a[href*="/messages/e2ee/t/"]').first
            try:
                href = await e2ee_link.get_attribute("href", timeout=500) or ""
                if "/messages/e2ee/t/" in href:
                    thread_id = href.split("/messages/e2ee/t/")[-1].rstrip("/").split("?")[0]
                    e2ee = True
            except Exception:
                pass

            # Fallback to regular link
            if not thread_id:
                link = row.locator('a[href*="/messages/t/"]').first
                try:
                    href = await link.get_attribute("href", timeout=500) or ""
                    if "/messages/e2ee/t/" in href:
                        thread_id = href.split("/messages/e2ee/t/")[-1].rstrip("/").split("?")[0]
                        e2ee = True
                    elif "/messages/t/" in href:
                        thread_id = href.split("/messages/t/")[-1].rstrip("/").split("?")[0]
                except Exception:
                    pass

            name = lines[0]
            preview = lines[1] if len(lines) > 1 else ""
            time_str = lines[-1] if len(lines) > 2 else ""

            conversations.append({
                "name": name,
                "preview": preview,
                "time": time_str,
                "thread_id": thread_id,
                "e2ee": e2ee,
            })
        except Exception:
            continue

    return conversations


async def list_inbox(profile: str, count: int = 20, headless: bool = True):
    """List Messenger inbox conversations."""
    p, browser, context = await create_browser_context(profile, headless)

    try:
        page = await context.new_page()

        print("Navigating to Messenger...")
        await page.goto(
            "https://www.facebook.com/messages/",
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(3000)

        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)

        print("Loading conversations...")
        await page.wait_for_timeout(2000)

        conversations = await _extract_conversations(page, count)

        # Print results
        print()
        print(f"  {'#':<4} {'Name':<25} {'E2E':<5} {'Thread ID':<20} {'Preview'}")
        print(f"  {'─' * 85}")
        for i, c in enumerate(conversations):
            preview = c["preview"][:35] + ("..." if len(c["preview"]) > 35 else "")
            tid = c["thread_id"][:18] if c["thread_id"] else "?"
            e2ee = "Yes" if c.get("e2ee") else ""
            print(f"  {i:<4} {c['name']:<25} {e2ee:<5} {tid:<20} {preview}")

        print(f"\n  {len(conversations)} conversations shown.")

        await save_storage_state(context, profile)

    finally:
        await browser.close()
        await p.stop()


async def _scroll_sidebar(page, target_count: int, max_scrolls: int = 80) -> list[dict]:
    """Scroll the Messenger sidebar to load more conversations.

    Returns deduplicated list of conversations (up to target_count).
    """
    seen_ids = set()
    all_convos = []
    no_new_streak = 0

    for scroll_i in range(max_scrolls):
        convos = await _extract_conversations(page, target_count * 5)

        new_count = 0
        for c in convos:
            key = c["thread_id"] or c["name"]
            if key not in seen_ids:
                seen_ids.add(key)
                all_convos.append(c)
                new_count += 1

        if new_count > 0:
            no_new_streak = 0
        else:
            no_new_streak += 1

        if len(all_convos) >= target_count:
            break

        if no_new_streak >= 5:
            break

        # Scroll the sidebar down — try multiple strategies
        scrolled = await page.evaluate('''() => {
            // Strategy: find the scrollable container in the navigation area
            const nav = document.querySelector('[role="navigation"]');
            if (!nav) return false;

            // Walk all descendants looking for the scrollable one
            const candidates = nav.querySelectorAll('div');
            for (const el of candidates) {
                if (el.scrollHeight > el.clientHeight + 50) {
                    const before = el.scrollTop;
                    el.scrollTop += 500;
                    if (el.scrollTop > before) return true;
                }
            }

            // Fallback: scroll last visible row into view
            const rows = nav.querySelectorAll('[role="row"]');
            if (rows.length > 0) {
                rows[rows.length - 1].scrollIntoView({ behavior: "instant", block: "end" });
                return true;
            }
            return false;
        }''')
        await page.wait_for_timeout(1500)

        if not scrolled:
            # Try keyboard scroll: focus sidebar and press End/PageDown
            try:
                last_row = page.locator('div[role="navigation"] div[role="row"]').last
                await last_row.scroll_into_view_if_needed()
                await page.wait_for_timeout(1000)
            except Exception:
                pass

        if scroll_i % 10 == 9:
            print(f"  Scrolled {scroll_i + 1} times, found {len(all_convos)} conversations...")

    return all_convos


async def discover_e2ee_contacts(
    profile: str, count: int = 20, headless: bool = True
):
    """Discover top E2E encrypted contacts from Messenger sidebar and cache them.

    Scrolls the sidebar to find E2E conversations, then saves them to the
    contacts cache for quick access via name.
    """
    p, browser, context = await create_browser_context(profile, headless)

    try:
        page = await context.new_page()

        print("Navigating to Messenger...")
        await page.goto(
            "https://www.facebook.com/messages/",
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(3000)

        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)

        # Handle any initial dialogs
        await _dismiss_dialogs(page, profile)
        await page.wait_for_timeout(2000)

        print(f"Scanning sidebar for E2E contacts (target: {count})...")

        # Scroll sidebar to collect enough conversations
        # We need more than `count` total since not all are E2E
        all_convos = await _scroll_sidebar(page, count * 5)

        # Filter E2E only
        e2ee_convos = [c for c in all_convos if c.get("e2ee") and c["thread_id"]]

        print(f"\nFound {len(e2ee_convos)} E2E contacts out of {len(all_convos)} total conversations.")

        # Cache them
        cached = 0
        for c in e2ee_convos[:count]:
            _save_contact(profile, c["name"], c["thread_id"], e2ee=True)
            cached += 1

        # Print results
        print()
        print(f"  {'#':<4} {'Name':<25} {'Thread ID':<22} {'Preview'}")
        print(f"  {'─' * 80}")
        for i, c in enumerate(e2ee_convos[:count]):
            preview = c.get("preview", "")[:35] + ("..." if len(c.get("preview", "")) > 35 else "")
            print(f"  {i:<4} {c['name']:<25} {c['thread_id']:<22} {preview}")

        print(f"\n  {len(e2ee_convos[:count])} E2E contacts found and cached.")
        print(f"  Use 'fb send <name> \"message\"' to send messages by name.")

        await save_storage_state(context, profile)

    finally:
        await browser.close()
        await p.stop()


async def _dismiss_dialogs(page, profile: str = "default"):
    """Dismiss any blocking dialogs (e.g. E2E encryption PIN prompt)."""
    # First try entering PIN if the dialog is for E2E encryption
    if await handle_pin_dialog(page, profile):
        return True

    # Then try common dismiss/close buttons
    for selector in [
        '[aria-label="Close"], [aria-label="關閉"]',
        'div[role="dialog"] [aria-label="Close"], div[role="dialog"] [aria-label="關閉"]',
        'a:has-text("稍後再說")',
        'a:has-text("Not now")',
        '[role="dialog"] a[role="button"]',
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    return False


async def search_and_read(
    profile: str, query: str, count: int = 20, headless: bool = True
):
    """Search for a user in Messenger, open their thread, and read messages."""
    p, browser, context = await create_browser_context(profile, headless)

    try:
        page = await context.new_page()

        print("Navigating to Messenger...")
        await page.goto(
            "https://www.facebook.com/messages/",
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(4000)

        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)

        # Dismiss any blocking dialogs
        await _dismiss_dialogs(page, profile)
        await page.wait_for_timeout(1000)

        # Find the Messenger search box (supports both EN and zh-TW labels)
        print(f"Searching for \"{query}\"...")
        search_box = page.locator(
            'input[aria-label*="Messenger"], input[aria-label*="messenger"]'
        ).first

        try:
            await search_box.wait_for(state="visible", timeout=5000)
        except Exception:
            # Maybe still behind a dialog — try again
            await _dismiss_dialogs(page, profile)
            await page.wait_for_timeout(1000)
            await search_box.wait_for(state="visible", timeout=5000)

        await search_box.click()
        await page.wait_for_timeout(500)
        await search_box.fill(query)
        await page.wait_for_timeout(3000)

        # Click the matching search result
        # Search results appear as listbox options or links
        result_links = page.locator('a[href*="/messages/t/"]')
        result_count = await result_links.count()

        # Count only the new results (not the existing sidebar chats)
        # The search dropdown results are typically in a listbox
        listbox_items = page.get_by_role("option")
        listbox_count = await listbox_items.count()

        clicked = False
        seen_candidates = []
        if listbox_count > 0:
            # Find the result matching our query — never guess: a search
            # result that doesn't literally contain the query text is not
            # confirmed to be the right person (this previously caused a
            # message to be sent to the wrong contact when the fallback
            # blindly clicked the first result).
            for i in range(min(listbox_count, 10)):
                item = listbox_items.nth(i)
                try:
                    text = await item.inner_text()
                    seen_candidates.append(text.split("\n")[0].strip())
                    if query in text:
                        await item.click()
                        clicked = True
                        break
                except Exception:
                    continue
        elif result_count > 0:
            for i in range(min(result_count, 10)):
                link = result_links.nth(i)
                try:
                    text = await link.inner_text()
                    seen_candidates.append(text.split("\n")[0].strip())
                    if query in text:
                        await link.click()
                        clicked = True
                        break
                except Exception:
                    continue

        if not clicked:
            print(f"No result whose text literally contains \"{query}\" — refusing to guess.", file=sys.stderr)
            if seen_candidates:
                print(f"Candidates seen: {seen_candidates}", file=sys.stderr)
                print("Re-run search with one of these exact names, or use a thread ID directly.", file=sys.stderr)
            await save_storage_state(context, profile)
            sys.exit(1)

        await page.wait_for_timeout(4000)

        # Dismiss any post-navigation dialogs (e.g. PIN prompt)
        await _dismiss_dialogs(page, profile)
        await page.wait_for_timeout(1000)

        # Get thread ID from URL
        current_url = page.url
        thread_id = ""
        e2ee = False
        if "/messages/e2ee/t/" in current_url:
            thread_id = current_url.split("/messages/e2ee/t/")[-1].rstrip("/").split("?")[0]
            e2ee = True
        elif "/messages/t/" in current_url:
            thread_id = current_url.split("/messages/t/")[-1].rstrip("/").split("?")[0]

        # Cache the discovered contact
        if thread_id:
            _save_contact(profile, query, thread_id, e2ee)
            print(f"Cached contact: {query} → {thread_id} (E2E: {e2ee})")

        print(f"Opened thread: {thread_id or '(unknown)'}")
        print("Reading messages...")
        await page.wait_for_timeout(2000)

        messages = await _extract_messages(page, count)

        # Print messages
        print()
        if thread_id:
            print(f"  Thread: {thread_id}")
        print(f"  {'─' * 60}")
        for msg in messages:
            for line in msg.split("\n"):
                line = line.strip()
                if line:
                    print(f"  {line}")
            print()

        print(f"  {len(messages)} messages shown.")

        await save_storage_state(context, profile)

    finally:
        await browser.close()
        await p.stop()


async def _extract_messages(page, count: int) -> list[str]:
    """Extract messages from the current Messenger thread view."""
    # Messages are in role="row" elements within the message list
    message_rows = page.get_by_role("row")
    msg_count = await message_rows.count()

    if msg_count == 0:
        # Fallback: look for message group containers
        message_rows = page.locator('[data-scope="messages_table"] div[role="row"]')
        msg_count = await message_rows.count()

    messages = []
    start = max(0, msg_count - count)

    for i in range(start, msg_count):
        try:
            row = message_rows.nth(i)
            text_content = await row.inner_text()
            text_content = text_content.strip()
            if text_content:
                messages.append(text_content)
        except Exception:
            continue

    return messages


async def read_thread(
    profile: str, thread_id: str, count: int = 20, headless: bool = True
):
    """Read messages from a specific Messenger thread."""
    p, browser, context = await create_browser_context(profile, headless)

    try:
        page = await context.new_page()

        # Use E2E URL path for numeric thread IDs (encrypted threads)
        if thread_id.isdigit() and len(thread_id) > 15:
            url = f"https://www.facebook.com/messages/e2ee/t/{thread_id}/"
        else:
            url = f"https://www.facebook.com/messages/t/{thread_id}/"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)

        # Handle PIN dialog for E2E threads
        await _dismiss_dialogs(page, profile)
        await page.wait_for_timeout(1000)

        print("Loading messages...")
        await page.wait_for_timeout(2000)

        messages = await _extract_messages(page, count)

        # Print messages
        print()
        print(f"  Thread: {thread_id}")
        print(f"  {'─' * 60}")
        for msg in messages:
            for line in msg.split("\n"):
                line = line.strip()
                if line:
                    print(f"  {line}")
            print()

        print(f"  {len(messages)} messages shown.")

        await save_storage_state(context, profile)

    finally:
        await browser.close()
        await p.stop()


def _parse_timestamp(text: str, reference_date: datetime | None = None) -> str | None:
    """Try to parse a Chinese/English timestamp string into ISO format.

    Handles patterns like:
    - 今天上午3:06 / 今天下午11:55
    - 昨天上午10:30
    - 週一 ~ 週日 (weekday names)
    - 3月5日 上午9:00
    - 2026年3月5日
    - Mar 5, 2026
    - 上午/下午 HH:MM (standalone)
    """
    if not reference_date:
        reference_date = datetime.now()

    text = text.strip()

    # Skip non-timestamp strings
    if not text or len(text) > 40:
        return None

    # "今天" (today)
    m = re.match(r'今天\s*(上午|下午)(\d{1,2}):(\d{2})', text)
    if m:
        period, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
        if period == '下午' and h != 12:
            h += 12
        elif period == '上午' and h == 12:
            h = 0
        return reference_date.replace(hour=h, minute=mi, second=0).isoformat(timespec='minutes')

    # "昨天" (yesterday)
    m = re.match(r'昨天\s*(上午|下午)(\d{1,2}):(\d{2})', text)
    if m:
        period, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
        if period == '下午' and h != 12:
            h += 12
        elif period == '上午' and h == 12:
            h = 0
        d = reference_date - timedelta(days=1)
        return d.replace(hour=h, minute=mi, second=0).isoformat(timespec='minutes')

    # Weekday names: 週一..週日
    weekday_map = {'週日': 6, '週一': 0, '週二': 1, '週三': 2, '週四': 3, '週五': 4, '週六': 5}
    for name, wd in weekday_map.items():
        if text.startswith(name):
            # Find the most recent occurrence of this weekday
            days_back = (reference_date.weekday() - wd) % 7
            if days_back == 0:
                days_back = 7  # if same weekday, it means last week
            d = reference_date - timedelta(days=days_back)
            # Try to extract time
            m = re.search(r'(上午|下午)(\d{1,2}):(\d{2})', text)
            if m:
                period, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
                if period == '下午' and h != 12:
                    h += 12
                elif period == '上午' and h == 12:
                    h = 0
                return d.replace(hour=h, minute=mi, second=0).isoformat(timespec='minutes')
            return d.strftime('%Y-%m-%d')

    # "3月5日" or "3月5日 上午9:00"
    m = re.match(r'(\d{1,2})月(\d{1,2})日\s*(上午|下午)?(\d{1,2})?:?(\d{2})?', text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = reference_date.year
        # If the month/day is in the future, it was last year
        try:
            d = reference_date.replace(month=month, day=day)
            if d > reference_date:
                d = d.replace(year=year - 1)
        except ValueError:
            return None
        if m.group(3) and m.group(4) and m.group(5):
            period = m.group(3)
            h, mi = int(m.group(4)), int(m.group(5))
            if period == '下午' and h != 12:
                h += 12
            elif period == '上午' and h == 12:
                h = 0
            return d.replace(hour=h, minute=mi, second=0).isoformat(timespec='minutes')
        return d.strftime('%Y-%m-%d')

    # "2026年3月5日"
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # Standalone "上午/下午 HH:MM"
    m = re.match(r'(上午|下午)(\d{1,2}):(\d{2})$', text)
    if m:
        period, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
        if period == '下午' and h != 12:
            h += 12
        elif period == '上午' and h == 12:
            h = 0
        return reference_date.replace(hour=h, minute=mi, second=0).isoformat(timespec='minutes')

    return None


def _parse_raw_messages(raw_texts: list[str], my_name: str = "你") -> list[dict]:
    """Parse raw row texts into structured messages.

    Each raw text block may contain:
    - Sender indicator ("你傳送了" = you sent, or contact name)
    - Timestamp lines
    - Message content
    - UI artifacts to filter out
    """
    messages = []
    # Patterns to skip (UI artifacts)
    skip_patterns = re.compile(
        r'^(進入|·|已收回|已讀|未讀|送出|傳送|轉傳|回覆|更多|表情|GIF|貼圖|相片|影片|語音|視訊|'
        r'查看翻譯|翻譯|See translation|Translated|You sent|Sent|Delivered|Read|'
        r'Unsent|More|Reply|React|Forward|GIF|Sticker|Photo|Video|Voice|Video call|'
        r'\d+ others|其他 \d+ 人)$',
        re.IGNORECASE
    )

    current_timestamp = None

    for raw in raw_texts:
        lines = [l.strip() for l in raw.split('\n') if l.strip()]
        if not lines:
            continue

        # Try to find timestamp in the lines
        for line in lines:
            ts = _parse_timestamp(line)
            if ts:
                current_timestamp = ts

        # Try to identify sender and content
        sender = None
        content_lines = []

        for line in lines:
            # Skip timestamps and UI artifacts
            if _parse_timestamp(line):
                continue
            if skip_patterns.match(line):
                continue
            if len(line) <= 1:
                continue

            # Sender detection: "你傳送了" prefix means "you sent"
            if '你傳送了' in line or line.startswith('你：'):
                sender = my_name
                # Extract content after the prefix if present
                after = re.sub(r'^你(傳送了|：)\s*', '', line).strip()
                if after:
                    content_lines.append(after)
                continue

            # If no sender identified yet, the first meaningful line might be sender name
            if sender is None:
                # Check if this line could be a sender name (short, no special chars)
                # In E2E threads, sender names appear as separate lines
                if len(line) <= 30 and not re.search(r'[.!?。！？，,]', line):
                    sender = line
                    continue

            content_lines.append(line)

        content = '\n'.join(content_lines).strip()
        if content:
            messages.append({
                'sender': sender or '(unknown)',
                'text': content,
                'timestamp': current_timestamp,
            })

    return messages


async def _scroll_and_collect(page, days: int, max_scrolls: int = 300) -> list[str]:
    """Scroll up in the message container, collecting raw row texts as we go.

    Messenger virtualizes the DOM (removes old rows as you scroll), so we
    must extract visible rows on each scroll iteration and deduplicate.

    Returns deduplicated list of raw row texts in chronological order.
    """
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')
    print(f"Will scroll until messages older than {cutoff_str} are loaded...")

    main_rows = page.locator('[role="main"] [role="row"]')
    seen_texts = set()
    all_texts = []  # ordered list of unique texts
    scroll_count = 0
    no_new_count = 0

    async def _collect_visible():
        """Collect currently visible row texts, return count of new ones."""
        nonlocal no_new_count
        count = await main_rows.count()
        new_found = 0
        for j in range(count):
            try:
                text = (await main_rows.nth(j).inner_text()).strip()
                if text and text not in seen_texts:
                    seen_texts.add(text)
                    all_texts.append(text)
                    new_found += 1
            except Exception:
                continue
        return new_found

    # Collect initial visible messages
    await _collect_visible()

    for i in range(max_scrolls):
        # Scroll up
        await page.evaluate('''() => {
            const main = document.querySelector('[role="main"]');
            if (!main) return;
            let el = main.querySelector('[role="row"]');
            while (el && el !== main) {
                el = el.parentElement;
                if (el && el.scrollHeight > el.clientHeight + 10) {
                    el.scrollTop = Math.max(0, el.scrollTop - 800);
                    return;
                }
            }
            main.scrollTop = 0;
        }''')
        await page.wait_for_timeout(1000)

        new_count = await _collect_visible()
        scroll_count += 1

        if new_count == 0:
            no_new_count += 1
        else:
            no_new_count = 0

        # Stop if no new messages after 8 consecutive scrolls
        if no_new_count >= 8:
            print(f"  No new messages after {no_new_count} scrolls. Stopping.")
            break

        # Check timestamps of newly collected texts for cutoff
        reached_cutoff = False
        for text in all_texts[-20:]:  # check recent additions
            for line in text.split('\n'):
                ts = _parse_timestamp(line.strip())
                if ts and ts < cutoff_str:
                    reached_cutoff = True
                    break
            if reached_cutoff:
                break

        if reached_cutoff:
            print(f"  Reached messages older than {cutoff_str}. Done scrolling.")
            break

        if scroll_count % 20 == 0:
            print(f"  Scrolled {scroll_count} times, {len(all_texts)} unique rows collected...")

    print(f"  Total: {scroll_count} scrolls, {len(all_texts)} unique message rows collected.")
    return all_texts


async def scroll_and_extract_thread(
    profile: str,
    thread_id: str,
    days: int = 30,
    headless: bool = True,
    output_file: str | None = None,
):
    """Scroll through a Messenger thread to load history, extract and save messages.

    Opens the E2E thread, handles PIN dialog, scrolls up to load `days` worth
    of messages, parses them into structured format, and saves to JSON.
    """
    p, browser, context = await create_browser_context(profile, headless)

    try:
        page = await context.new_page()

        # Use E2E URL path for encrypted threads
        if thread_id.isdigit():
            url = f"https://www.facebook.com/messages/e2ee/t/{thread_id}/"
        else:
            url = f"https://www.facebook.com/messages/t/{thread_id}/"

        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        if not await verify_login(page):
            print("Error: Not logged in. Run 'fb login' first.", file=sys.stderr)
            sys.exit(1)

        # Handle PIN dialog for E2E threads
        await page.wait_for_timeout(2000)
        await _dismiss_dialogs(page, profile)
        await page.wait_for_timeout(2000)

        # Scroll up and collect messages incrementally (DOM is virtualized)
        print(f"Loading {days} days of message history...")
        raw_texts = await _scroll_and_collect(page, days)

        # Also dump raw texts for debugging
        debug_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "profiles", profile, f"chat_raw_{thread_id}.json",
        )
        with open(debug_file, "w", encoding="utf-8") as f:
            json.dump(raw_texts, f, ensure_ascii=False, indent=2)
        print(f"  Raw texts saved to {debug_file}")

        # Parse into structured messages
        print("Parsing messages...")
        messages = _parse_raw_messages(raw_texts)
        print(f"  Parsed {len(messages)} messages.")

        # Filter by date range
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec='minutes')
        filtered = []
        for msg in messages:
            if msg['timestamp'] and msg['timestamp'] < cutoff:
                continue
            filtered.append(msg)

        if len(filtered) < len(messages):
            print(f"  After date filter: {len(filtered)} messages within {days} days.")
            messages = filtered

        # Determine output file
        if not output_file:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            profile_dir = os.path.join(base, "profiles", profile)
            os.makedirs(profile_dir, exist_ok=True)
            output_file = os.path.join(profile_dir, f"chat_history_{thread_id}.json")

        # Save to JSON
        output_data = {
            "thread_id": thread_id,
            "extracted_at": datetime.now().isoformat(),
            "days": days,
            "message_count": len(messages),
            "messages": messages,
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        print(f"\nSaved {len(messages)} messages to {output_file}")

        # Print summary
        senders = {}
        for msg in messages:
            s = msg['sender']
            senders[s] = senders.get(s, 0) + 1
        print("  Message counts by sender:")
        for sender, count in sorted(senders.items(), key=lambda x: -x[1]):
            print(f"    {sender}: {count}")

        await save_storage_state(context, profile)
        return output_file

    finally:
        await browser.close()
        await p.stop()
