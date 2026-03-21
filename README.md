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

Install a cookie export extension (e.g., [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm) or [EditThisCookie](https://chromewebstore.google.com/detail/editthiscookie/fngmhnnpilhplaeedifhccceomclgfbg)):

1. Log in to [facebook.com](https://www.facebook.com) in Chrome
2. Click the cookie extension icon
3. Export cookies as **JSON** (the extension's "Export" button)
4. Save as `profiles/default/cookies.json`

For fan page profiles, switch to the Page first ("Switch to Page" in Facebook), then export.

### 4. Set up profile config (optional)

For E2E encrypted Messenger threads, create `profiles/default/config.json`:

```json
{
  "pincode": "123456",
  "self_name": "Your Name"
}
```

- `pincode`: Your Messenger E2E encryption PIN (auto-entered during browser automation)
- `self_name`: Your Facebook display name (used to filter out your own comments from the unreplied list)

## Cookie Profiles

```
profiles/
├── default/
│   ├── cookies.json        # personal profile (from cookie extension export)
│   ├── config.json         # optional: pincode, self_name
│   └── storage_state.json  # auto-saved by Playwright after login
└── fanpage/
    └── cookies.json        # exported while in "Switch to Page" mode
```

Fan page cookies contain `i_user` (page actor ID) which is automatically used as the acting identity.

## Usage

### Post

```bash
uv run fb post "Hello world" --privacy FRIENDS
uv run fb --profile fanpage post "Hello from page" --privacy EVERYONE
```

Options: `--privacy SELF|FRIENDS|EVERYONE` (default: `SELF`)

### List unreplied comments

```bash
uv run fb comments                         # unreplied comments
uv run fb comments --filter ALL            # include already-replied
uv run fb comments --count 20             # fetch more per page
uv run fb comments --next                  # next page
uv run fb comments --page PAGE_ID         # override auto-detected page ID
uv run fb --profile fanpage comments      # list fan page comments
```

Comments from yourself (`self_name` in config) and link-only comments are automatically filtered out.

### Reply to a comment

```bash
uv run fb reply 0 "Thanks!"                           # by index from last listing
uv run fb reply Y29tbWV... "Thanks!"                   # by base64 comment ID
uv run fb --profile fanpage reply 0 "Thanks!"          # reply as fan page
```

### Browser login (captures cookies via Playwright)

```bash
uv run fb login
uv run fb --profile fanpage login
```

Opens a headed browser for manual login. Cookies and storage state are saved automatically.

### Messenger — Send a message

```bash
uv run fb send <thread_id> "Hello!"
uv run fb send <thread_id> "Hello!" --no-headless      # visible browser
```

### Messenger — List inbox

```bash
uv run fb inbox
uv run fb inbox --count 30 --no-headless
```

### Messenger — Read thread

```bash
uv run fb read <thread_id>
uv run fb read <thread_id> --count 50 --no-headless
```

### Messenger — Search and read

```bash
uv run fb search "Name"
uv run fb search "Name" --count 30 --no-headless
```

### Messenger — Extract chat history

Scrolls through a thread to load and extract messages (handles Messenger's DOM virtualization):

```bash
uv run fb history <thread_id> --days 30 --no-headless
uv run fb history <thread_id> --days 14 --output chat.json
```

Output is saved to `profiles/<profile>/chat_history_<thread_id>.json`.

## Files

| File | Purpose |
|------|---------|
| `scripts/fb.py` | Main CLI — all subcommands |
| `scripts/fb_session.py` | Session management, cookies, tokens, GraphQL helpers |
| `scripts/fb_config.py` | GraphQL doc IDs (update when Facebook changes them) |
| `scripts/fb_browser.py` | Playwright browser context, cookie injection, stealth, PIN handling |
| `scripts/fb_messenger.py` | Messenger operations — send, read, search, history extraction |
| `scripts/post.py` | Standalone post script (legacy) |

## How it works

### GraphQL API (post, comments, reply)

- Loads cookies from `profiles/<name>/cookies.json`
- Uses `i_user` cookie as actor ID for fan page profiles, `c_user` for personal
- Fetches CSRF tokens (`fb_dtsg`, `lsd`, etc.) from Facebook homepage
- Auto-detects `pageID` from the Professional Dashboard page
- Uses Facebook's internal GraphQL API (`/api/graphql/`) with Relay doc IDs
- Comment listing saves results per-profile for reply-by-index

### Playwright browser automation (login, messenger)

- Injects cookies into a Chromium browser context
- Applies stealth settings to avoid detection
- Auto-enters E2E encryption PIN from `config.json`
- Handles Messenger's virtualized DOM by scrolling and collecting incrementally
- Saves browser state (`storage_state.json`) for faster subsequent runs

## Notes

- GraphQL doc IDs in `fb_config.py` are global (same for all users) but may change when Facebook deploys new frontend code. Update values there when requests start returning errors.
- Messenger history extraction (`fb history`) works with E2E encrypted threads — the URL path `/messages/e2ee/t/<id>/` is used automatically for numeric thread IDs.
- The `--no-headless` flag is recommended for debugging and for E2E threads that require PIN entry.
