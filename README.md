![GitHub stars](https://img.shields.io/github/stars/htlin222/fbpost?style=flat-square)
![Last commit](https://img.shields.io/github/last-commit/htlin222/fbpost?style=flat-square)
![License](https://img.shields.io/github/license/htlin222/fbpost?style=flat-square)

# fbpost

Facebook CLI tool — post, comments, replies, and Messenger automation via GraphQL API + Playwright.

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
uv run fb send 黃豆泥 "你好"

# Send a Messenger message (by thread ID)
uv run fb send 9318393524851955 "你好"
```

## Commands

### Post

```bash
uv run fb post "Hello world" --privacy FRIENDS
uv run fb --profile fanpage post "Hello from page" --privacy EVERYONE
```

Options: `--privacy SELF|FRIENDS|EVERYONE` (default: `SELF`)

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
uv run fb send 黃豆泥 "Hello!"              # by contact name (from cache)
uv run fb send 9318393524851955 "Hello!"   # by thread ID
uv run fb send 黃豆泥 "Hello!" --headless   # hidden browser (less reliable for E2E)
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
uv run fb read 黃豆泥                       # by name
uv run fb read 9318393524851955            # by thread ID
uv run fb read 黃豆泥 --count 50
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
uv run fb history 黃豆泥 --days 30
uv run fb history 黃豆泥 --days 14 --output chat.json
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
uv run fb send 黃豆泥 "Hello!" --headless
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

- GraphQL doc IDs in `fb_config.py` may change when Facebook deploys new frontend code. Update them when requests start returning errors.
- E2E threads use the URL path `/messages/e2ee/t/<id>/` automatically for numeric thread IDs > 15 digits.
- Send defaults to headed mode. Use `--headless` only after verifying your E2E PIN session is active.
- Contact names support fuzzy matching — partial names work if they match exactly one contact.
