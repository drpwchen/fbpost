![Last commit](https://img.shields.io/github/last-commit/drpwchen/fbpost?style=flat-square)
![License](https://img.shields.io/github/license/drpwchen/fbpost?style=flat-square)

# fbpost

Facebook CLI tool — post, comments, replies, and Messenger automation via GraphQL API + Playwright.

> **Originally forked from [htlin222/fbpost](https://github.com/htlin222/fbpost)** — all credit for the original design and the GraphQL groundwork goes to [@htlin222](https://github.com/htlin222), and this project stays MIT-licensed with their copyright intact. It is now maintained here as a standalone project, because upstream has been inactive since March 2026. On top of the original it adds:
>
> - `fb post --image` (photo attachments) and `--schedule` (scheduled posts), both via pure GraphQL
> - `fb post --comment` / `fb comment` — pre-write a top-level comment on a post, including scheduled posts before they publish ("link in comments")
> - `fb post-list-scheduled` / `fb post-list-published` / `fb comment-scheduled` / `fb post-delete-scheduled` / `fb post-delete` — list, comment on, and delete your own posts from the CLI, scheduled or already published, all through the Content Library GraphQL API
> - `fb report` — create and download a Professional Dashboard content data report (CSV) in one command, plus `scripts/report_to_md.py` to turn it into a markdown tracking table
> - A reliability sweep: daemon-safe teardown everywhere, cookie-based login verification, honest post-send verification (several code paths used to report success without checking), wrong-contact guards in `fb search`, Windows support fixes, and event-driven waits that cut 3-12s of fixed sleeps per command
>
> The reliability fixes and composer features were offered upstream as
> [PR #1](https://github.com/htlin222/fbpost/pull/1) and
> [PR #2](https://github.com/htlin222/fbpost/pull/2); they were closed unmerged
> in July 2026. Anyone is welcome to take them from here.

## What this version is like to use

**API first, browser only when there is no API.** Posting, photos, scheduling,
audiences, listing your own posts, deleting them, and commenting all go through
Facebook's own GraphQL endpoints — seconds per command, no window opening.
Playwright is still there for what genuinely needs a real browser (login,
Messenger), and every converted command keeps its old browser implementation
behind `--browser` for the day Facebook changes a query.

**It refuses to claim success it did not verify.** A photo post reads back the
id, an audience is re-read from the story Facebook stored, a schedule is
compared against the epoch it kept, a deletion is confirmed by the post being
gone from the library. The recurring bug in this space is a command that
reports success while nothing happened; several of those are fixed here, and
new features are built so the same failure cannot come back quietly.

**Destructive things ask you to prove you mean them.** Deletes resolve the
index in the same run, print what they are about to remove, and abort unless
`--match` text is really in that row — deleting a published post by index
without `--match` is refused outright. During testing that guard caught a
genuinely wrong row.

**Audiences are never guessed.** Subscriber-only sharing is not a privacy
state: it is an account-specific list id, looked up at post time from your own
audience list and keyed on a stable icon rather than a translated label. An
unknown `--privacy` fails instead of falling back to a wider audience.

**It says how it knows.** Every non-obvious payload in the code carries a note
on how it was captured and what breaks if Facebook changes it, and the
CHANGELOG records the real bug behind each fix rather than a version bump.

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

Options: `--privacy SELF|FRIENDS|EVERYONE|SUBSCRIBERS` (default: `SELF`)

`SUBSCRIBERS` is the profile's subscriber-only audience (「訂閱者 / 僅限此個人
檔案的訂閱者」), available on profiles with Facebook subscriptions turned on.
Facebook does not model it as a privacy state: it is `base_state: SELF` plus a
per-account subscriber list id, so the id is looked up from your own audience
list at post time (matched on the stable `supporter_exclusive` icon, not on a
localized label) and never hardcoded. If that option is missing from the
account, the post is refused rather than sent to a wider audience. On the
GraphQL path the audience is also read back off the created post and printed
(`Audience confirmed by Facebook: 僅限訂閱者`); on the opt-in composer path
(`--image --composer`) the picker's radio must actually flip and the composer
must show the audience before anything is submitted.

Note that a subscriber post can't be rehearsed with `--privacy SELF` — the
audiences are different objects. Verify a real one from a logged-out browser
(it should not render) or from a non-subscriber account.

Schedule for later (pure GraphQL — no browser, no zh-TW selector dependency):

```bash
uv run fb post "Later post" --privacy EVERYONE --schedule "2026-07-18 11:00"
```

The schedule time must be at least 10 minutes out (Facebook's minimum) and is
your machine's local time, sent as an absolute epoch — no account-timezone
ambiguity. Photo posts take the same path: `--image` uploads to the composer's
own upload endpoint and passes the returned photo id to the post mutation, so
no browser is involved and a long post no longer spends minutes being typed
character by character.

If Facebook ever changes that endpoint, `--image --composer` forces the old
browser path, where the schedule goes into the zh-TW UI: the time is typed and
read back, the date is chosen from the calendar popup (writing into that field
changes only what you see, not what Facebook stores), and both are re-checked
against the Content Library afterwards. Facebook's own picker only allows
roughly 30 days ahead — asking for a later date fails loudly instead of
quietly publishing on the wrong day.

Pre-write a comment on the new post in the same command — handy for the
"link in comments" habit; it works on scheduled posts before they publish:

```bash
uv run fb post "Post text" --privacy EVERYONE \
  --schedule "2026-07-18 11:00" --comment "https://example.com/article"
```

This works with `--image` as well, scheduled or immediate, because the photo
path returns a post id directly. (With the `--composer` fallback the post must
be scheduled, since the browser path returns no id — it is then read back from
the Content Library preview before the comment is sent.)

Or comment on any of your own posts later (numeric post_id printed by
`fb post`, or the base64 story ID):

```bash
uv run fb comment 1234567890123456 "https://example.com/article"
```

### Your posts — List / Comment / Delete

Your own posts live in the Professional Dashboard's Content Library (**已發佈**
and **已排定發佈** tabs); for a personal profile with no Page, Meta Business
Suite is unavailable, so that library is the source of truth. These commands
query it over GraphQL — a couple of seconds each, no browser.

```bash
uv run fb post-list-scheduled              # scheduled posts: index, time, post_id, preview
uv run fb post-list-published              # published posts: same, plus views / engagement
uv run fb post-list-published --count 25 --days LAST_90D

uv run fb comment-scheduled 2 "https://example.com/article"     # comment on a post that hasn't published yet
uv run fb comment-scheduled 2 "text" --match "draft about cats" # abort if row 2 doesn't contain this text

uv run fb post-delete-scheduled 2 --match "draft about cats"   # delete a scheduled post
uv run fb post-delete --post-id 1234567890                     # delete a PUBLISHED post by id
uv run fb post-delete 3 --match "draft about cats"             # ...or by index from post-list-published
```

`post-list-published` is the only reliable way to confirm a post really went
out: scraping the profile page does not load the post wall for this kind of
account, so a post made through the API had nothing to check against.

Every destructive command resolves the index freshly, prints exactly what it is
about to delete, aborts when the `--match` text is not in that row, and
re-reads the library afterwards to confirm the post is gone before reporting
success. `post-delete` additionally *requires* `--match` when you pass an
index, because a published post cannot be restored — pass `--post-id` when you
want to skip indexing.

`comment-scheduled` fills the gap `fb comment` leaves: it looks the scheduled
post's id up in the library and comments over the normal GraphQL path, so the
comment is waiting under the post the moment it publishes.

The listings and deletes take `--browser` to fall back to driving the dashboard
UI, in case Facebook changes the Content Library query. That path is the old
implementation, kept intact: `--no-headless` to watch a listing, `--headless`
to hide the (headed by default) delete.

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

### GraphQL API (post, photos, listing, deleting, comments, reply)

- Loads cookies from `profiles/<name>/cookies.json`
- Uses `i_user` cookie as actor ID for fan page profiles, `c_user` for personal
- Fetches CSRF tokens (`fb_dtsg`, `lsd`, etc.) from Facebook homepage
- Auto-detects `pageID` from the Professional Dashboard page
- Uses Facebook's internal GraphQL API (`/api/graphql/`) with Relay doc IDs
- Reads deferred responses in full: the Content Library streams its table as
  later chunks of one response, so those queries collect every chunk instead of
  the first one

### Playwright Browser Automation (login, messenger)

- Injects cookies into a Chromium browser context
- Applies stealth settings to avoid detection
- Auto-enters E2E encryption PIN from `config.json`
- Pre-send verification: confirms text is in input box before pressing Enter
- Handles Messenger's virtualized DOM by scrolling and collecting incrementally
- Saves browser state (`storage_state.json`) for faster subsequent runs

## Notes

- **The browser-driven flows (`--image` posts, scheduled-post list/comment/delete, `fb report`) target Facebook's Traditional Chinese (zh-TW) UI** — their selectors are zh-TW labels. If your Facebook display language isn't 繁體中文, those flows will fail at the first zh-TW-labeled step. `fb post` (including `--schedule` and `--comment`), `fb comment`, `fb comments`, and `fb reply` are pure GraphQL and language-independent.
- GraphQL doc IDs in `fb_config.py` may change when Facebook deploys new frontend code. Update them when requests start returning errors.
- E2E threads use the URL path `/messages/e2ee/t/<id>/` automatically for numeric thread IDs > 15 digits.
- Send defaults to headed mode. Use `--headless` only after verifying your E2E PIN session is active.
- Contact names support fuzzy matching — partial names work if they match exactly one contact.

---

## 🌱 Start here if you're new to AI agents ／ AI agent 新手起點

This tool is one piece of my personal AI workflow. If you want to learn how to use AI agents like Claude Code from zero (no programming background needed), I wrote a beginner series (in Traditional Chinese):

這個工具是我個人 AI 工作流的一部分。想從零開始學怎麼用 Claude Code 這類 AI agent（不需要程式背景），可以從我的入門系列開始：

1. [從零開始：安裝、看懂 GitHub、跑起你的第一個工具](https://drpwchen.com/posts/getting-started/)
2. [怎麼跟 AI agent 講話：心法、元技能與規則檔](https://drpwchen.com/posts/talking-to-agents/)
3. [自動化流程不是設計出來的，是長出來的](https://drpwchen.com/posts/growing-your-workflow/)

Full map of my tools and posts ／ 所有工具與文章的全貌 → [drpwchen.com/map](https://drpwchen.com/map/)
