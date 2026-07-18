# Changelog

All notable changes to this fork are documented here.
Upstream: [htlin222/fbpost](https://github.com/htlin222/fbpost).

## [0.3.0] - 2026-07-18

### Added

- `fb post --comment "text"` — after creating/scheduling a post, pre-write a
  top-level comment on it in the same command. Works on scheduled posts
  before they publish (the "link in comments" pattern): `feedback:<post_id>`
  resolves pre-publish, so the comment is already there when the post goes
  live. Verified against the returned comment edge — no blind success.
- `fb comment <post_id> "text"` — standalone top-level comment on any of your
  own posts; accepts the numeric post_id printed by `fb post` or the base64
  story ID.

### Changed

- `fb post --schedule` now goes through pure GraphQL
  (`unpublished_content_data` on `ComposerStoryCreateMutation`) instead of
  driving the browser composer. No more zh-TW selector dependency or
  composer-popup flakiness for scheduling; the time is sent as an absolute
  epoch (no account-timezone ambiguity), and the command prints the new
  post's post_id/story_id. `--image` still uses the composer (photo upload
  has no GraphQL fast path).

## [0.2.0] - 2026-07-17

### Added

- `fb report` — create and download a Professional Dashboard content data
  report (CSV) in one command: opens 匯出資料 → 建立資料報告, unchecks the
  收益 (revenue) metrics by default (a checked revenue metric makes Facebook's
  export silently drop every column after it), polls 報告紀錄 until the report
  is 已完成, then downloads via the signed CDN URL with a CSV sanity check.
  `--include-revenue` opts back into revenue metrics, `--timeout` adjusts the
  generation wait, `--no-headless` shows the browser.
- `scripts/report_to_md.py` — converts the report CSV into a markdown tracking
  table; converts the CSV's US-Pacific timestamps to Taipei (+15h) and can
  join topic tags from a previous analysis CSV (`--enriched`).

### Fixed

- Report polling tolerates the 匯出資料 control disappearing while a report is
  generating (reload + retry per round instead of failing on first miss).

## [0.1.0] - 2026-07-16

Fork baseline (drpwchen/fbpost), on top of upstream `main`:

### Added

- `fb post --image` (photo attachments) and `--schedule "YYYY-MM-DD HH:MM"`
  (scheduled posts) via the composer UI (submitted upstream as PR #2).
- `fb post-list-scheduled` / `fb post-delete-scheduled` — list and delete
  scheduled posts from the Professional Dashboard Content Library, with
  lazy-load-complete scrolling, `--match` safety guard, and post-delete
  count verification.
- Native E2EE thread reading in `fb read` — auto-restores encrypted messages
  (「訊息遺失。立即還原」), auto-enters the PIN from `config.json`, and parses
  e2ee bubbles from accessibility labels, fully headless.

### Fixed

- Reliability sweep (16 bugs): daemon-safe teardown in every command,
  cookie-based login verification, honest send/post verification (several
  paths reported success without checking), wrong-contact guards in
  `fb search`, image-attach false failures, scheduled-delete truncation,
  10-minute schedule minimum, Windows keyboard/signal-handler fixes,
  GraphQL error swallowing, and event-driven waits replacing 3–12s of fixed
  sleeps per command. (Reliability subset submitted upstream as PR #1.)
