![Last commit](https://img.shields.io/github/last-commit/drpwchen/fbpost?style=flat-square)
![License](https://img.shields.io/github/license/drpwchen/fbpost?style=flat-square)

# fbpost

Facebook CLI tool — post, comments, replies, and Messenger automation via GraphQL API + Playwright.

> **This is a maintained fork of [htlin222/fbpost](https://github.com/htlin222/fbpost)** (all credit for the original design to [@htlin222](https://github.com/htlin222)). This fork adds:
>
> - `fb post --image` (photo attachments) via the composer UI and `--schedule` (scheduled posts) via pure GraphQL
> - `fb post --comment` / `fb comment` — pre-write a top-level comment on a post, including scheduled posts before they publish ("link in comments")
> - `fb post-list-scheduled` / `fb post-delete-scheduled` — manage scheduled posts from the CLI
> - `fb report` — create and download a Professional Dashboard content data report (CSV) in one command, plus `scripts/report_to_md.py` to turn it into a markdown tracking table
> - A reliability sweep: daemon-safe teardown everywhere, cookie-based login verification, honest post-send verification (several code paths used to report success without checking), wrong-contact guards in `fb search`, Windows support fixes, and event-driven waits that cut 3-12s of fixed sleeps per command
>
> The reliability fixes and composer features are also submitted upstream as
> [PR #1](https://github.com/htlin222/fbpost/pull/1) and [PR #2](https://github.com/htlin222/fbpost/pull/2).

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Install Playwright browser

```bash
uv run playwright install chromium
```

### 3. Export cookies from Chrome

Install a cookie export extension (e.g., [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)):

1. Log in to [facebook.com](https://www.facebook.com) in Chrome
2. Click the cookie extension icon
3. Export cookies as **JSON**
4. Save as `profiles/default/cookies.json`

For fan page profiles, switch to the Page first ("Switch to Page" in Facebook), then export.

### 4. Set up profile config

Create `profiles/default/config.json`:

```json
{
  "pincode": "123456",
  "self_name": "Your Name"
}
```

- `pincode`: Your Messenger E2E encryption PIN (auto-entered during browser automation)
- `self_name`: Your Facebook display name (filters out your own comments)

## Quick Start

```bash
# Post to Facebook
uv run fb post "Hello world"

# List unreplied comments
uv run fb comments

# Reply to a comment
uv run fb reply 0 "Thanks!"

# Send a Messenger message (by name)
uv run fb send Alice "你好"

# Send a Messenger message (by thread ID)
uv run fb send 1234567890123456 "你好"
```

## Commands

### Post

```bash
uv run fb post "Hello world" --privacy FRIENDS
uv run fb --profile fanpage post "Hello from page" --privacy EVERYONE
```

Options: `--privacy SELF|FRIENDS|EVERYONE` (default: `SELF`)

Schedule for later (pure GraphQL — no browser, no zh-TW selector dependency):

```bash
uv run fb post "Later post" --privacy EVERYONE --schedule "2026-07-18 11:00"
```

The schedule time must be at least 10 minutes out (Facebook's minimum) and is
your machine's local time, sent as an absolute epoch — no account-timezone
ambiguity. Only `--image` still routes through the browser composer (photo
upload has no GraphQL fast path), where the time is typed into the zh-TW UI.

Pre-write a comment on the new post in the same command — handy for the
"link in comments" habit; it works on scheduled posts before they publish:

```bash
uv run fb post "Post text" --privacy EVERYONE \
  --schedule "2026-07-18 11:00" --comment "https://example.com/article"
```

Or comment on any of your own posts later (numeric post_id printed by
`fb post`, or the base64 story ID):

```bash
uv run fb comment 1234567890123456 "https://example.com/article"
```

### Scheduled posts — List / Delete

Scheduled posts live in the Professional Dashboard's Content Library
(**Content → Scheduled** tab); for a personal profile with no Page, Meta
Business Suite is unavailable, so these commands drive that tab.

```bash
uv run fb post-list-scheduled              # list scheduled posts with a 1-based index
uv run fb post-delete-scheduled 2          # delete the post shown at index 2
uv run fb post-delete-scheduled 2 --match "draft about cats"   # abort if row 2 doesn't contain this text
```

Both commands scroll until the lazy-loaded table stops growing, so the index
covers ALL scheduled posts. `post-delete-scheduled` resolves the index freshly
in its own session, prints exactly what it is about to delete, aborts if
`--match` text isn't in that row, and re-verifies the scheduled count actually
dropped before reporting success.

Like the other read-only commands, `post-list-scheduled` is headless by
default (`--no-headless` to watch). Deletion is destructive, so
`post-delete-scheduled` is headed by default (`--headless` to hide).

### Content data report (post analytics export)

Automates the Professional Dashboard export flow (匯出資料 → 建立資料報告 →
poll 報告紀錄 → download) and saves the CSV — one row per post with views,
reach, engagement, reactions, comments, saves, and shares:

```bash
uv run fb report                           # saves fb_report_YYYY-MM-DD.csv
uv run fb report --out my_report.csv       # custom output path
uv run fb report --timeout 360             # wait longer for report generation
```

**Revenue metrics are excluded by default, on purpose**: Facebook's export
has a bug where any checked 收益 (revenue) metric silently drops every column
after it from the CSV. If your profile is monetized and you accept a
truncated export, opt back in with `--include-revenue`.

Two gotchas the command already handles, worth knowing about:

- The date range is the dashboard default (**past 28 days**) — keep your own
  history if you need older data.
- **Timestamps in the CSV are US Pacific time**, not your local timezone.
  `scripts/report_to_md.py <report.csv>` converts them to Taipei (+15h) and
  emits a markdown table; adapt the offset for your timezone.

### Comments

```bash
uv run fb comments                         # unreplied comments
uv run fb comments --filter ALL            # include already-replied
uv run fb comments --count 20             # fetch more per page
uv run fb comments --next                  # next page
uv run fb --profile fanpage comments      # fan page comments
```

Comments from yourself and link-only comments are automatically filtered out.

### Reply

```bash
uv run fb reply 0 "Thanks!"                           # by index from last listing
uv run fb reply Y29tbWV... "Thanks!"                   # by base64 comment ID
uv run fb --profile fanpage reply 0 "Thanks!"          # reply as fan page
```

### Login (browser-based)

```bash
uv run fb login
```

Opens a headed browser for manual login. Cookies and storage state are saved automatically.

### Messenger — Send

```bash
uv run fb send Alice "Hello!"              # by contact name (from cache)
uv run fb send 1234567890123456 "Hello!"   # by thread ID
uv run fb send Alice "Hello!" --headless   # hidden browser (less reliable for E2E)
```

Send defaults to **headed mode** (visible browser) for reliable E2E encryption handling. The flow:
1. Navigate to E2E thread
2. Auto-enter PIN if needed
3. Verify text is in input box
4. Press Enter to send

### Messenger — Inbox

```bash
uv run fb inbox
uv run fb inbox --count 30 --no-headless
```

### Messenger — Read

```bash
uv run fb read Alice                       # by name
uv run fb read 1234567890123456            # by thread ID
uv run fb read Alice --count 50
```

### Messenger — Search and Read

```bash
uv run fb search "Name"
uv run fb search "Name" --count 30
```

Searches Messenger, opens the matching thread, reads messages, and caches the contact for future use.

### Messenger — Chat History

Scrolls through a thread to extract messages (handles Messenger's DOM virtualization):

```bash
uv run fb history Alice --days 30
uv run fb history Alice --days 14 --output chat.json
```

Output: `profiles/<profile>/chat_history_<thread_id>.json`

### Contacts

```bash
uv run fb contacts                         # list cached contacts
uv run fb discover-e2ee --count 100        # scan sidebar for E2E contacts
uv run fb verify-contacts --count 20       # classify as DM vs group
```

#### Contact Discovery

`discover-e2ee` scrolls the Messenger sidebar to find E2E encrypted conversations and caches them. After discovery, you can send messages by name instead of thread ID.

#### Contact Verification

`verify-contacts` opens each cached E2E thread to check if it's a 1-on-1 (DM) or group conversation. Results are saved to the contacts cache with a `type` field.

### Daemon Mode

Keep a browser running for instant Messenger access (~6s per send):

```bash
# Terminal 1: start daemon
uv run fb daemon

# Terminal 2: send messages (auto-connects to daemon)
uv run fb send Alice "Hello!" --headless
```

## Cookie Profiles

```
profiles/
├── default/
│   ├── cookies.json        # personal profile (cookie extension export)
│   ├── config.json         # pincode, self_name
│   ├── contacts.json       # cached Messenger contacts (auto-generated)
│   └── storage_state.json  # auto-saved by Playwright
└── fanpage/
    └── cookies.json        # exported while in "Switch to Page" mode
```

## Files

| File | Purpose |
|------|---------|
| `scripts/fb.py` | Main CLI — all subcommands |
| `scripts/fb_session.py` | Session management, cookies, tokens, GraphQL helpers |
| `scripts/fb_config.py` | GraphQL doc IDs (update when Facebook changes them) |
| `scripts/fb_browser.py` | Playwright browser context, cookie injection, stealth, PIN handling |
| `scripts/fb_messenger.py` | Messenger operations — send, read, search, contacts, history |
| `scripts/post.py` | Standalone post script (legacy) |

## How It Works

### GraphQL API (post, comments, reply)

- Loads cookies from `profiles/<name>/cookies.json`
- Uses `i_user` cookie as actor ID for fan page profiles, `c_user` for personal
- Fetches CSRF tokens (`fb_dtsg`, `lsd`, etc.) from Facebook homepage
- Auto-detects `pageID` from the Professional Dashboard page
- Uses Facebook's internal GraphQL API (`/api/graphql/`) with Relay doc IDs

### Playwright Browser Automation (login, messenger)

- Injects cookies into a Chromium browser context
- Applies stealth settings to avoid detection
- Auto-enters E2E encryption PIN from `config.json`
- Pre-send verification: confirms text is in input box before pressing Enter
- Handles Messenger's virtualized DOM by scrolling and collecting incrementally
- Saves browser state (`storage_state.json`) for faster subsequent runs

## Notes

- **The browser-driven flows (`--image` posts, scheduled-post list/delete, `fb report`) target Facebook's Traditional Chinese (zh-TW) UI** — their selectors are zh-TW labels. If your Facebook display language isn't 繁體中文, those flows will fail at the first zh-TW-labeled step. `fb post` (including `--schedule` and `--comment`), `fb comment`, `fb comments`, and `fb reply` are pure GraphQL and language-independent.
- GraphQL doc IDs in `fb_config.py` may change when Facebook deploys new frontend code. Update them when requests start returning errors.
- E2E threads use the URL path `/messages/e2ee/t/<id>/` automatically for numeric thread IDs > 15 digits.
- Send defaults to headed mode. Use `--headless` only after verifying your E2E PIN session is active.
- Contact names support fuzzy matching — partial names work if they match exactly one contact.
