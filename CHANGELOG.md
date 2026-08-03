# Changelog

All notable changes are documented here.
Originally forked from [htlin222/fbpost](https://github.com/htlin222/fbpost);
maintained standalone since 2026-07-30 (upstream inactive since March 2026,
PRs #1/#2 closed unmerged). MIT license and original copyright retained.

## [0.6.0] - 2026-08-03

### Added

- `fb post --image` now goes through the API instead of the browser composer.
  Photos are uploaded to the composer's own endpoint
  (`upload.facebook.com/ajax/react_composer/attachments/photo/upload`, form
  fields `source=8` / `profile_id` / `waterfallxapp=comet` / `farr` /
  `upload_id`) and the returned `photoID` is passed to
  `ComposerStoryCreateMutation` as `attachments: [{"photo": {"id": ...}}]`.
  A long photo post used to take many minutes of per-character typing in a
  real browser and could time out with no way to tell whether it had been
  published; it is now a couple of seconds and returns the post id.
  Captured by hooking `XMLHttpRequest.send` in the page — Playwright cannot
  read a multipart request body, and every parameter but the form fields
  lives in the query string.
- `fb post --image --composer` keeps the old browser path as an opt-in
  fallback in case Facebook changes the upload endpoint.

### Fixed

- `--comment` now works with `--image`. It previously required `--schedule`
  because the composer path returned no post id; the API path returns one, so
  the URL-in-a-comment habit works on immediate photo posts too.
- `post-delete-scheduled` could not delete anything: the row menu's 刪除貼文
  item is visible and enabled, but an invisible full-width wrapper (and the
  neighbouring post's text block) sits on top of it, so Playwright's
  actionability click retried until it timed out and the command died with a
  traceback. Clicks on the Content Library row controls now fall back to a
  DOM click, which ignores pointer-event hit-testing. Applied to the row
  actions button, the menu item, the confirmation dialog and the
  post-preview button that `--ids` / `comment-scheduled` rely on.
- `fb comments` printed its follow-up hint as `uv run fb.py reply`; the entry
  point is `fb`, and `fb.py` fails with a ModuleNotFoundError.

### Notes

- `graphql_request()` leaves `Content-Type: application/x-www-form-urlencoded`
  on the shared session. Uploading after any GraphQL call therefore sent a
  multipart body labelled urlencoded and Facebook answered 400 — which hit
  exactly the `--privacy SUBSCRIBERS` path, since resolving the subscriber
  list id is itself a GraphQL call. `upload_photo()` now removes that header
  (and `X-FB-Friendly-Name`) for its own request and restores them after.

## [0.5.0] - 2026-07-31

### Added

- `fb post --privacy SUBSCRIBERS` — post to the profile's subscriber-only
  audience, on both the GraphQL path and the browser composer path
  (`--image`). Facebook does not expose this as a privacy state: it is
  `base_state: SELF` plus an account-specific subscriber list id in `allow`.
  The id is resolved at post time from the account's own audience list
  (`CometPrivacySelectorPickerContainerQuery`), keyed on the stable
  `supporter_exclusive` icon rather than a localized label, so nothing is
  hardcoded and another account's id can never leak in. A profile without
  subscriptions gets a clear refusal instead of a post to a wider audience.
- The audience of a post created through the GraphQL path is now read back
  from Facebook and reported (`Audience confirmed by Facebook: 僅限訂閱者`)
  whenever a list-backed audience was requested — "the mutation returned a
  post id" does not prove the allow list survived, and a dropped list id
  would silently reduce a subscriber post to only-me.

### Fixed

- The composer path could post to the wrong audience. Selecting the audience
  clicked the option and pressed 完成 after a fixed 500 ms, without ever
  checking that the selection took — with a photo attached it frequently did
  not, leaving the previous audience (usually 所有人) in place. The option's
  radio must now read `aria-checked=true`, and after the dialog closes the
  composer's own audience label is compared against what was asked for;
  either check failing aborts before the post is submitted.
- The audience dialog was located as "the last open dialog", which picked up
  whatever else Facebook had opened on top (e.g. its "photo could not be
  read" error) and then failed with a confusing message. It is now found by
  its heading.
- An unrecognized `--privacy` value fell back to 所有人 (public) in the
  composer path — the worst possible default. It now fails.

## [0.4.0] - 2026-07-30

### Added

- `fb comment-scheduled <index> "text"` — comment on a post that hasn't
  published yet, picked by its `post-list-scheduled` index (with the same
  `--match` safety check as delete). A scheduled row exposes no post id, so
  the command opens that row's post preview (the Content Library dialog you
  would comment in by hand) and reads the post_id out of the response that
  renders it, then comments over the existing GraphQL path.
- `fb post --image --schedule ... --comment "text"` now works: the composer
  returns no post_id, so the id is resolved from the Content Library row
  matching the post's own text. Photo + "link in comments" is one command
  again. (An immediate photo post still needs a separate `fb comment`.)
- `fb post-list-scheduled --ids` — also print each post's numeric post_id.

### Fixed

- **Scheduled `--image` posts could publish on a date and time nobody asked
  for.** The composer's schedule popup accepted values that never reached
  Facebook: a 24-hour time like `23:30` was dropped for FB's own default
  (now + 2h), and the date field is a combobox whose value can be written
  without changing the picker's state — so the post kept the default date
  while every read-back looked correct. The time is now filled, read back and
  retried in 12-hour spellings; the date is chosen from the calendar; both are
  re-checked against the Content Library afterwards, which warns if Facebook
  stored something else. A date outside Facebook's ~30-day scheduling window
  now fails before posting instead of silently sliding to another day.
- Scheduled-post rows: skeleton rows Facebook mounts while re-rendering could
  take an index (a listing showed 5 posts for 2 real ones), which
  delete-by-index could then act on. Rows without content are dropped and the
  row count must hold still before it is trusted; deletion re-counts through
  the same parser instead of a raw button count, which had produced false
  `FAILED` reports on deletions that did go through.
- A scheduled post whose text contains 上午/下午 no longer shows an empty
  preview (the row parser mistook the body for the schedule line).

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
