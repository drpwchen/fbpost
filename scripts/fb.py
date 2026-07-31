#!/usr/bin/env python3
"""Facebook CLI tool — post, list comments, reply to comments, messenger."""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta

from scripts.fb_config import COMMENTS_DOC_ID, POST_DOC_ID, REPLY_DOC_ID
from scripts.fb_session import (
    COMMENTS_RELAY_PV_FLAGS,
    POST_RELAY_PV_FLAGS,
    REPLY_RELAY_PV_FLAGS,
    create_session,
    fetch_tokens,
    graphql_request,
    load_cookies,
    fetch_page_id,
    fetch_story_text,
    fetch_story_privacy,
    resolve_privacy_row,
    PLAIN_PRIVACY_STATES,
)

def _last_comments_file(profile: str = "default") -> str:
    """Return per-profile path for .last_comments.json."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile_dir = os.path.join(base, "profiles", profile)
    if os.path.isdir(profile_dir):
        return os.path.join(profile_dir, ".last_comments.json")
    return os.path.join(base, ".last_comments.json")


# ── Post ─────────────────────────────────────────────────────────────────────

def _parse_schedule(schedule_at: str) -> int:
    """Parse 'YYYY-MM-DD HH:MM' into epoch seconds, enforcing FB's ~10-minute
    minimum lead time. The epoch is absolute, so unlike the old composer path
    (which typed the time as text) there is no account-timezone ambiguity."""
    try:
        dt = datetime.strptime(schedule_at, "%Y-%m-%d %H:%M")
    except ValueError:
        print(
            f"Error: --schedule must be 'YYYY-MM-DD HH:MM' (24hr), got {schedule_at!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    if dt <= datetime.now() + timedelta(minutes=10):
        print(
            "Error: --schedule time must be at least 10 minutes in the future "
            "(Facebook's minimum).",
            file=sys.stderr,
        )
        sys.exit(1)
    return int(dt.timestamp())


def _resolve_post_id(raw: str) -> str:
    """Accept a numeric post_id or a base64 story_id (UzpfS… = 'S:_I<user>:<post>')
    and return the numeric post_id."""
    if raw.isdigit():
        return raw
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        # "S:_I<user_id>:<post_id>" — take the trailing numeric segment
        tail = decoded.rsplit(":", 1)[-1]
        if tail.isdigit():
            return tail
    except Exception:
        pass
    print(
        f"Error: cannot resolve post id from {raw!r} — pass the numeric post_id "
        "printed by 'fb post'.",
        file=sys.stderr,
    )
    sys.exit(1)


def _create_comment(session, tokens, actor_id, post_id: str, text: str):
    """Create a TOP-LEVEL comment on one of our own posts via GraphQL.

    Works on scheduled (unpublished) posts too — feedback:<post_id> resolves
    before publish, which is what lets us pre-write the URL comment the user
    parks under each post. Exits non-zero unless FB returns the created
    comment edge (no blind success)."""
    feedback_id = base64.b64encode(f"feedback:{post_id}".encode()).decode()
    session_id = str(uuid.uuid4())
    variables = {
        "feedLocation": "DEDICATED_COMMENTING_SURFACE",
        "feedbackSource": 110,
        "groupID": None,
        "input": {
            "actor_id": actor_id,
            "client_mutation_id": "1",
            "attachments": None,
            "feedback_id": feedback_id,
            "formatting_style": None,
            "message": {"ranges": [], "text": text},
            "attribution_id_v2": f"CometFocusedStoryViewRoot.react,comet.focused_story_view,unexpected,{int(datetime.now().timestamp() * 1000)},536197,,,;",
            "vod_video_timestamp": None,
            "is_tracking_encrypted": True,
            "tracking": [],
            "feedback_source": "DEDICATED_COMMENTING_SURFACE",
            "idempotence_token": f"client:{uuid.uuid4()}",
            "session_id": session_id,
        },
        "inviteShortLinkKey": None,
        "renderLocation": None,
        "scale": 2,
        "useDefaultActor": False,
        "focusCommentID": None,
        **REPLY_RELAY_PV_FLAGS,
    }

    data = graphql_request(
        session, tokens, actor_id, REPLY_DOC_ID,
        "useCometUFICreateCommentMutation", variables,
    )
    if "errors" in data:
        print("Error from Facebook while commenting:", file=sys.stderr)
        print(json.dumps(data["errors"], indent=2, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    cc = (data.get("data") or {}).get("comment_create") or {}
    node = (cc.get("feedback_comment_edge") or {}).get("node") or {}
    body = (node.get("preferred_body") or node.get("body") or {}).get("text", "")
    if node.get("id") and text in body:
        print(f"Comment created on post {post_id}: {text}")
    else:
        print(
            "FAILED: Facebook did not return the created comment — "
            "comment may NOT have been posted.",
            file=sys.stderr,
        )
        print(json.dumps(data, ensure_ascii=False)[:800], file=sys.stderr)
        sys.exit(1)


def _warn_if_schedule_drifted(when: str, requested: str):
    """Compare the Content Library's own schedule line with what we asked for.

    The composer can only see the fields it typed into; this is the first look
    at what Facebook actually stored."""
    from scripts.fb_browser import parse_when
    actual = parse_when(when)
    if not actual:
        return
    try:
        wanted = datetime.strptime(requested, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return
    if abs((actual - wanted).total_seconds()) > 60:
        print(
            f"WARNING: scheduled for {actual:%Y-%m-%d %H:%M}, but you asked for "
            f"{wanted:%Y-%m-%d %H:%M}. Facebook did not keep the requested time — "
            "delete it with 'fb post-delete-scheduled' and reschedule.",
            file=sys.stderr,
        )


def cmd_post(args):
    """Post text to Facebook."""
    if args.image:
        # Photo upload isn't supported by the raw GraphQL mutation below —
        # drive the real composer UI instead. (Scheduling IS GraphQL now:
        # unpublished_content_data on ComposerStoryCreateMutation.)
        if args.comment and not args.schedule:
            # Published immediately, so there is no Content Library row to read
            # the id off — the only route left is scraping /me for the newest
            # post, which can't tell a SELF dry run from the real thing.
            print(
                "Error: --comment with --image needs --schedule (the scheduled "
                "post's id is resolvable from the Content Library). For an "
                "immediate photo post, publish first, then "
                "'fb comment <post_id> \"text\"'.",
                file=sys.stderr,
            )
            sys.exit(1)
        from scripts.fb_browser import post_via_composer
        asyncio.run(post_via_composer(
            args.profile, args.text,
            image_path=args.image,
            schedule_at=args.schedule,
            privacy=args.privacy,
            headless=args.headless,
        ))
        if args.comment:
            # The composer prints no post_id; the Content Library preview knows
            # it. Pick our row by its own text (must match exactly one row).
            print("\nResolving the scheduled post's id to attach the comment...")
            from scripts.fb_browser import resolve_scheduled_post_id
            info = asyncio.run(resolve_scheduled_post_id(
                args.profile, contains=args.text[:60], headless=True,
            ))
            if not info:
                print(
                    "The post IS scheduled, but its id could not be resolved — "
                    "no comment was added. Run 'fb post-list-scheduled' then "
                    "'fb comment-scheduled <#> \"text\"'.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"  post_id: {info['post_id']} (row [{info['index']}] {info['when']})")
            _warn_if_schedule_drifted(info["when"], args.schedule)
            cookies = load_cookies(args.profile)
            actor_id = cookies["actor_id"]
            session = create_session(cookies)
            tokens = fetch_tokens(session)
            _create_comment(session, tokens, actor_id, info["post_id"], args.comment)
        return

    schedule_epoch = _parse_schedule(args.schedule) if args.schedule else None

    cookies = load_cookies(args.profile)
    actor_id = cookies["actor_id"]
    session = create_session(cookies)

    print("Fetching tokens from Facebook...")
    tokens = fetch_tokens(session)
    print(f"  fb_dtsg: {tokens['fb_dtsg'][:20]}...")

    # SUBSCRIBERS (and any other list-backed audience) is not a base_state —
    # it needs this account's own list id, looked up before we post.
    privacy_row = resolve_privacy_row(session, tokens, actor_id, args.privacy)
    if args.privacy not in PLAIN_PRIVACY_STATES:
        print(f"  audience: {args.privacy} -> {json.dumps(privacy_row)}")

    session_id = str(uuid.uuid4())
    variables = {
        "input": {
            "composer_entry_point": "inline_composer",
            "composer_source_surface": "timeline",
            "idempotence_token": f"{session_id}_FEED",
            "source": "WWW",
            "attachments": [],
            "audience": {"privacy": privacy_row},
            "message": {"ranges": [], "text": args.text},
            "with_tags_ids": None,
            "inline_activities": [],
            "text_format_preset_id": "0",
            "publishing_flow": {
                "supported_flows": ["ASYNC_SILENT", "ASYNC_NOTIF", "FALLBACK"]
            },
            "logging": {"composer_session_id": session_id},
            "navigation_data": {
                "attribution_id_v2": "ProfileCometTimelineListViewRoot.react,comet.profile.timeline.list,unexpected,1773335298129,370206,190055527696468,,;"
            },
            "tracking": [None],
            "event_share_metadata": {"surface": "newsfeed"},
            "actor_id": actor_id,
            "client_mutation_id": "1",
        },
        "feedLocation": "TIMELINE",
        "feedbackSource": 0,
        "focusCommentID": None,
        "gridMediaWidth": 230,
        "groupID": None,
        "scale": 2,
        "privacySelectorRenderLocation": "COMET_STREAM",
        "checkPhotosToReelsUpsellEligibility": True,
        "referringStoryRenderLocation": None,
        "renderLocation": "timeline",
        "useDefaultActor": False,
        "inviteShortLinkKey": None,
        "isFeed": False,
        "isFundraiser": False,
        "isFunFactPost": False,
        "isGroup": False,
        "isEvent": False,
        "isTimeline": True,
        "isSocialLearning": False,
        "isPageNewsFeed": False,
        "isProfileReviews": False,
        "isWorkSharedDraft": False,
        "hashtag": None,
        "canUserManageOffers": False,
        **POST_RELAY_PV_FLAGS,
    }

    if schedule_epoch:
        variables["input"]["unpublished_content_data"] = {
            "scheduled_publish_time": schedule_epoch,
            "unpublished_content_type": "SCHEDULED",
        }
        print(f"\nScheduling post for {args.schedule} (privacy: {args.privacy})...")
    else:
        print(f"\nPosting to Facebook (privacy: {args.privacy})...")

    data = graphql_request(
        session, tokens, actor_id, POST_DOC_ID,
        "ComposerStoryCreateMutation", variables,
    )

    if "errors" in data:
        print("Error from Facebook:", file=sys.stderr)
        print(json.dumps(data["errors"], indent=2), file=sys.stderr)
        sys.exit(1)

    story_create = data.get("data", {}).get("story_create") or {}
    post_id = story_create.get("post_id")
    story = story_create.get("story")
    if post_id:
        if schedule_epoch:
            print(f"\nSuccess! Post scheduled for {args.schedule}.")
        else:
            print("\nSuccess! Story created.")
        print(f"  Post ID: {post_id}")
        if story_create.get("story_id"):
            print(f"  Story ID: {story_create['story_id']}")
        if story and story.get("url"):
            print(f"  URL: {story['url']}")
    elif story:
        print("\nSuccess! Story created.")
        print(f"  Story ID: {story.get('id', 'unknown')}")
        if story.get("url"):
            print(f"  URL: {story['url']}")
    else:
        print("\nRequest sent. Response:")
        print(json.dumps(data, indent=2)[:1000])
        if args.comment:
            print(
                "Skipping --comment: no post_id returned to attach it to.",
                file=sys.stderr,
            )
            sys.exit(1)

    # A list-backed audience is the one case where "the mutation succeeded"
    # is not enough: if the allow list were dropped, base_state SELF would
    # quietly make a subscriber post visible to nobody but the author.
    if args.privacy not in PLAIN_PRIVACY_STATES:
        story_id = story_create.get("story_id") or (story or {}).get("id")
        stored = fetch_story_privacy(session, tokens, actor_id, story_id) if story_id else None
        if stored is None:
            print(
                "WARNING: could not read the post's audience back from Facebook "
                f"— verify by hand that it is {args.privacy}.",
                file=sys.stderr,
            )
        elif stored["allow"] != privacy_row["allow"]:
            print(
                f"WARNING: Facebook stored audience {stored['label']!r} "
                f"(allow={stored['allow']}), not the requested {args.privacy} "
                f"(allow={privacy_row['allow']}). Check the post before relying on it.",
                file=sys.stderr,
            )
        else:
            print(f"  Audience confirmed by Facebook: {stored['label']}")

    if args.comment and post_id:
        print()
        _create_comment(session, tokens, actor_id, str(post_id), args.comment)


# ── Comments ─────────────────────────────────────────────────────────────────

def _fetch_comments(session, tokens, actor_id, page_id, args, cursor):
    """Fetch comments from the API. Returns (edges, page_info) or None on failure."""
    variables = {
        "count": args.count,
        "cursor": cursor,
        "feedLocation": "PROFESSIONAL_DASHBOARD",
        "pageID": page_id,
        "scale": 2,
        "selectedFilter": "ALL",
        "selectedRespondedFilterType": args.filter,
        "selectedTabType": "YOUR_POSTS",
        "useDefaultActor": False,
        **COMMENTS_RELAY_PV_FLAGS,
    }

    print(f"Fetching {args.filter.lower()} comments...")
    data = graphql_request(
        session, tokens, actor_id, COMMENTS_DOC_ID,
        "ProdashCometCommentsManagerCommentsListPaginationQuery", variables,
    )

    if "errors" in data:
        for err in data["errors"]:
            print(f"  Warning: {err.get('message', 'Unknown error')}", file=sys.stderr)
        if "data" not in data or data["data"] is None:
            return None

    prodash_comments = (
        data.get("data", {})
        .get("node", {})
        .get("prodash_comments_manager", {})
        .get("prodash_comments")
    ) or {}

    edges = prodash_comments.get("edges", [])
    page_info = prodash_comments.get("page_info") or {}

    return (edges, page_info) if edges else None


def cmd_list_scheduled(args):
    """List scheduled posts from the Content Library (Scheduled tab)."""
    from scripts.fb_browser import list_scheduled_posts
    asyncio.run(list_scheduled_posts(
        args.profile, headless=not args.no_headless, with_ids=args.ids,
    ))


def cmd_comment_scheduled(args):
    """Comment on a not-yet-published scheduled post, picked by list index.

    Bridges the one gap left by 'fb comment': a post scheduled with --image
    goes through the browser composer, which prints no post_id, so there was
    nothing to attach the URL comment to. The Content Library's post preview
    knows the id — resolve it there, then comment over the normal GraphQL path
    (feedback:<post_id> resolves before publish)."""
    from scripts.fb_browser import resolve_scheduled_post_id
    info = asyncio.run(resolve_scheduled_post_id(
        args.profile,
        index=args.index,
        match=args.match,
        headless=not args.no_headless,
    ))
    if not info:
        print(
            "Could not resolve a scheduled post to comment on — nothing was posted.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Target [{info['index']}] {info['when']} — {info['text']}")
    print(f"  post_id: {info['post_id']}")
    cookies = load_cookies(args.profile)
    actor_id = cookies["actor_id"]
    session = create_session(cookies)
    print("Fetching tokens from Facebook...")
    tokens = fetch_tokens(session)
    _create_comment(session, tokens, actor_id, info["post_id"], args.text)


def cmd_delete_scheduled(args):
    """Delete a scheduled post by its 1-based list index."""
    from scripts.fb_browser import delete_scheduled_post
    asyncio.run(delete_scheduled_post(
        args.profile, args.index, headless=args.headless, match=args.match,
    ))


def cmd_report(args):
    """Create + download a content data report CSV from the Professional Dashboard."""
    from scripts.fb_browser import export_content_report
    out = args.out or f"fb_report_{datetime.now().strftime('%Y-%m-%d')}.csv"
    asyncio.run(export_content_report(
        args.profile, out, headless=not args.no_headless, timeout_s=args.timeout,
        include_revenue=args.include_revenue,
    ))


def cmd_comments(args):
    """List unreplied comments from Professional Dashboard."""
    cookies = load_cookies(args.profile)
    actor_id = cookies["actor_id"]
    session = create_session(cookies)
    page_id = args.page or fetch_page_id(session)

    print("Fetching tokens from Facebook...")
    tokens = fetch_tokens(session)

    # Load cursor for --next
    cursor = None
    if args.next:
        try:
            with open(_last_comments_file(args.profile)) as f:
                last = json.load(f)
            cursor = last.get("end_cursor")
            if not cursor:
                print("No next page available.", file=sys.stderr)
                sys.exit(1)
        except (FileNotFoundError, json.JSONDecodeError):
            print("No previous comments fetch found. Run without --next first.", file=sys.stderr)
            sys.exit(1)

    data = _fetch_comments(session, tokens, actor_id, page_id, args, cursor)

    # If cursor-based fetch fails entirely, retry from the beginning
    if data is None and cursor:
        print("Cursor may be stale, retrying from the beginning...")
        data = _fetch_comments(session, tokens, actor_id, page_id, args, cursor=None)

    if data is None:
        print("No comments found.")
        return

    edges, page_info = data

    if not edges:
        print("No comments found.")
        return

    # Parse comments
    comments = []
    for edge in edges:
        node = edge.get("node", {})
        item = node.get("item_renderer", {}).get("item", {})
        comment = item.get("comment", {})
        parent = item.get("comment_parent_post_story", {})

        author = comment.get("author", {}).get("name", "Unknown")
        text = (comment.get("preferred_body") or comment.get("body_renderer") or {}).get("text", "")
        comment_id = comment.get("id", "")
        feedback_id = comment.get("feedback", {}).get("id", "")
        created_time = comment.get("created_time", 0)
        comment_url = comment.get("feedback", {}).get("url", "")

        # Extract original post text and story ID from parent story
        post_story = parent.get("story", {})
        post_text = (post_story.get("message") or {}).get("text", "")
        post_title = parent.get("title", "")
        story_id = post_story.get("id", "")

        # Extract short post URL from comment URL or parent story
        post_url = ""
        if comment_url:
            m = re.search(r'(pfbid\w+)', comment_url)
            if m:
                post_url = m.group(1)

        time_str = ""
        if created_time:
            time_str = datetime.fromtimestamp(created_time).strftime("%Y-%m-%d %H:%M")

        comments.append({
            "author": author,
            "text": text,
            "comment_id": comment_id,
            "feedback_id": feedback_id,
            "post_url": post_url,
            "post_text": post_text or post_title,
            "story_id": story_id,
            "time": time_str,
            "created_time": created_time,
        })

    # Filter out self-comments and link-only comments
    from scripts.fb_browser import load_profile_config
    config = load_profile_config(args.profile)
    self_name = config.get("self_name", "")

    filtered = []
    skipped_self = 0
    skipped_link = 0
    for c in comments:
        # Skip comments from self
        if self_name and c["author"] == self_name:
            skipped_self += 1
            continue
        # Skip link-only comments (text is just a URL with optional whitespace)
        text_stripped = c["text"].strip()
        if re.match(r'^https?://\S+$', text_stripped):
            skipped_link += 1
            continue
        filtered.append(c)

    if skipped_self or skipped_link:
        parts = []
        if skipped_self:
            parts.append(f"{skipped_self} self")
        if skipped_link:
            parts.append(f"{skipped_link} link-only")
        print(f"  Filtered out: {', '.join(parts)} comment(s)")

    comments = filtered

    # Save for reply-by-index
    save_data = {
        "comments": comments,
        "end_cursor": page_info.get("end_cursor"),
        "has_next_page": page_info.get("has_next_page", False),
    }
    with open(_last_comments_file(args.profile), "w") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    # Fetch full story text for unique posts
    unique_stories = {}
    for c in comments:
        sid = c["story_id"]
        if sid and sid not in unique_stories:
            unique_stories[sid] = None  # placeholder
    if unique_stories:
        print(f"Fetching full post content for {len(unique_stories)} unique post(s)...")
        for sid in unique_stories:
            texts = fetch_story_text(session, tokens, actor_id, sid)
            unique_stories[sid] = texts

    # Update comments with full post text
    for c in comments:
        sid = c["story_id"]
        if sid and unique_stories.get(sid):
            c["post_text_full"] = unique_stories[sid]
        else:
            c["post_text_full"] = [c["post_text"]] if c["post_text"] else []

    # Print grouped by post
    print()
    seen_posts = {}
    for i, c in enumerate(comments):
        post_key = c["post_url"] or f"unknown_{i}"
        if post_key not in seen_posts:
            seen_posts[post_key] = True
            if i > 0:
                print()
            # Show all unique texts (own post + shared post)
            full_texts = c["post_text_full"]
            if full_texts:
                print(f"📝 Post: {full_texts[0][:80]}{'…' if len(full_texts[0]) > 80 else ''}")
                for extra in full_texts[1:]:
                    print(f"   ↳ Shared: {extra[:80]}{'…' if len(extra) > 80 else ''}")
            else:
                print("📝 Post: (no text)")
            print(f"   ({c['post_url'][:60]})")
            print(f"   {'#':<3} {'Author':<20} {'Comment':<50} {'Time'}")
            print(f"   {'─' * 90}")
        text_short = c["text"].replace("\n", " ")
        text_short = text_short[:48] + ("…" if len(text_short) > 48 else "")
        print(f"   {i:<3} {c['author']:<20} {text_short:<50} {c['time']}")

    print()
    print(f"  {len(comments)} comments shown. ", end="")
    if page_info.get("has_next_page"):
        print("Use --next for more.")
    else:
        print("No more pages.")
    profile_flag = f" --profile {args.profile}" if args.profile != "default" else ""
    print(f"  Reply with: uv run fb.py{profile_flag} reply <#> \"your reply\"")


# ── Comment (top-level, incl. scheduled posts) ──────────────────────────────

def cmd_comment(args):
    """Create a top-level comment on one of our own posts (works on scheduled
    posts before they publish — the URL-in-comments habit)."""
    post_id = _resolve_post_id(args.post_id)
    cookies = load_cookies(args.profile)
    actor_id = cookies["actor_id"]
    session = create_session(cookies)
    print("Fetching tokens from Facebook...")
    tokens = fetch_tokens(session)
    _create_comment(session, tokens, actor_id, post_id, args.text)


# ── Reply ────────────────────────────────────────────────────────────────────

def cmd_reply(args):
    """Reply to a comment."""
    cookies = load_cookies(args.profile)
    actor_id = cookies["actor_id"]
    session = create_session(cookies)

    # Resolve comment ID — by index or direct
    comment_id = args.id
    feedback_id = None

    try:
        idx = int(comment_id)
        # Look up from last comments
        with open(_last_comments_file(args.profile)) as f:
            last = json.load(f)
        comments = last.get("comments", [])
        if idx < 0 or idx >= len(comments):
            print(f"Error: Index {idx} out of range (0-{len(comments)-1}).", file=sys.stderr)
            sys.exit(1)
        entry = comments[idx]
        comment_id = entry["comment_id"]
        feedback_id = entry["feedback_id"]
        print(f"Replying to {entry['author']}: \"{entry['text'][:50]}...\"")
    except ValueError:
        # Direct comment_id provided — need feedback_id too
        # If it starts with "Y29t" it's likely a base64 comment ID
        if not feedback_id:
            print(f"Using comment ID: {comment_id[:30]}...")
            try:
                decoded = base64.b64decode(comment_id).decode("utf-8")
                # comment:POST_ID_COMMENT_ID -> feedback:POST_ID_COMMENT_ID
                if decoded.startswith("comment:"):
                    feedback_decoded = "feedback:" + decoded[len("comment:"):]
                    feedback_id = base64.b64encode(feedback_decoded.encode()).decode()
                else:
                    print("Error: Cannot derive feedback_id. Provide index from 'comments' list.", file=sys.stderr)
                    sys.exit(1)
            except Exception:
                print("Error: Cannot decode comment ID. Use index from 'comments' list.", file=sys.stderr)
                sys.exit(1)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Error: No previous comments listing. Run 'comments' first.", file=sys.stderr)
        sys.exit(1)

    print("Fetching tokens from Facebook...")
    tokens = fetch_tokens(session)

    session_id = str(uuid.uuid4())
    variables = {
        "feedLocation": "DEDICATED_COMMENTING_SURFACE",
        "feedbackSource": 110,
        "groupID": None,
        "input": {
            "actor_id": actor_id,
            "client_mutation_id": "1",
            "attachments": None,
            "feedback_id": feedback_id,
            "formatting_style": None,
            "message": {"ranges": [], "text": args.text},
            "reply_comment_parent_fbid": comment_id,
            "reply_target_clicked": True,
            "attribution_id_v2": f"ProdashCometV2CommentsManager.react,comet.profile.professional_dashboard.comments_manager,unexpected,{int(datetime.now().timestamp() * 1000)},536197,,,;",
            "vod_video_timestamp": None,
            "is_tracking_encrypted": True,
            "tracking": [
                json.dumps({
                    "assistant_caller": "comet_above_composer",
                    "conversation_guide_session_id": str(uuid.uuid4()),
                    "conversation_guide_shown": None,
                })
            ],
            "feedback_source": "DEDICATED_COMMENTING_SURFACE",
            "idempotence_token": f"client:{uuid.uuid4()}",
            "session_id": session_id,
        },
        "inviteShortLinkKey": None,
        "renderLocation": None,
        "scale": 2,
        "useDefaultActor": False,
        "focusCommentID": comment_id,
        **REPLY_RELAY_PV_FLAGS,
    }

    print(f"Sending reply: \"{args.text}\"")
    data = graphql_request(
        session, tokens, actor_id, REPLY_DOC_ID,
        "useCometUFICreateCommentMutation", variables,
    )

    if "errors" in data:
        print("Error from Facebook:", file=sys.stderr)
        print(json.dumps(data["errors"], indent=2), file=sys.stderr)
        sys.exit(1)

    # Check for success
    comment_create = data.get("data", {}).get("comment_create", {})
    comment = comment_create.get("comment")
    if comment:
        print("\nReply posted successfully!")
        print(f"  Comment ID: {comment.get('id', 'unknown')}")
    else:
        print("\nRequest sent. Response:")
        print(json.dumps(data, indent=2)[:1000])


# ── Messenger (Playwright) ──────────────────────────────────────────────────

def cmd_login(args):
    """Open browser for manual Facebook login."""
    from scripts.fb_browser import capture_login
    asyncio.run(capture_login(args.profile))


def cmd_send(args):
    """Send a Messenger message."""
    from scripts.fb_messenger import send_message, resolve_thread_id
    thread_id, _ = resolve_thread_id(args.profile, args.thread_id)
    headless = args.headless
    asyncio.run(send_message(args.profile, thread_id, args.text, headless))


def cmd_inbox(args):
    """List Messenger inbox."""
    from scripts.fb_messenger import list_inbox
    headless = not args.no_headless
    asyncio.run(list_inbox(args.profile, args.count, headless))


def cmd_read(args):
    """Read messages from a Messenger thread."""
    from scripts.fb_messenger import read_thread, resolve_thread_id
    thread_id, _ = resolve_thread_id(args.profile, args.thread_id)
    headless = not args.no_headless
    asyncio.run(read_thread(args.profile, thread_id, args.count, headless))


def cmd_search(args):
    """Search for a user in Messenger and read their messages."""
    from scripts.fb_messenger import search_and_read
    headless = not args.no_headless
    asyncio.run(search_and_read(args.profile, args.query, args.count, headless))


def cmd_history(args):
    """Extract chat history from a Messenger thread by scrolling."""
    from scripts.fb_messenger import scroll_and_extract_thread, resolve_thread_id
    thread_id, _ = resolve_thread_id(args.profile, args.thread_id)
    headless = not args.no_headless
    asyncio.run(scroll_and_extract_thread(
        args.profile, thread_id, args.days, headless, args.output,
    ))


def cmd_contacts(args):
    """List cached Messenger contacts."""
    from scripts.fb_messenger import list_contacts
    list_contacts(args.profile)


def cmd_discover_e2ee(args):
    """Discover and cache top E2E contacts from Messenger."""
    from scripts.fb_messenger import discover_e2ee_contacts
    headless = not args.no_headless
    asyncio.run(discover_e2ee_contacts(args.profile, args.count, headless))


def cmd_verify_contacts(args):
    """Verify cached E2E contacts (1-on-1 vs group)."""
    from scripts.fb_messenger import verify_contacts
    headless = not args.no_headless
    asyncio.run(verify_contacts(args.profile, args.count, headless))


def cmd_daemon(args):
    """Start persistent browser daemon for fast Messenger access."""
    from scripts.fb_browser import start_daemon
    asyncio.run(start_daemon(args.profile))


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Facebook CLI tool")
    parser.add_argument(
        "--profile", default="default",
        help="Cookie profile name (default: default). Maps to profiles/<name>/cookies.json",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # post
    post_parser = subparsers.add_parser("post", help="Post text to Facebook")
    post_parser.add_argument("text", help="The text to post")
    post_parser.add_argument(
        "--privacy", choices=["EVERYONE", "FRIENDS", "SELF", "SUBSCRIBERS"],
        default="SELF",
        help="Audience (default: SELF). SUBSCRIBERS = subscriber-only sharing "
        "on this profile; it cannot be dry-run as SELF, so verify a "
        "SUBSCRIBERS post from a logged-out browser instead.",
    )
    post_parser.add_argument(
        "--image", help="Path to an image to attach (routes through the browser composer, not the GraphQL fast path)",
    )
    post_parser.add_argument(
        "--schedule",
        help="Schedule for later, 'YYYY-MM-DD HH:MM' 24hr local time, at least "
        "10 minutes out. Sent via GraphQL (absolute epoch — no timezone "
        "ambiguity) unless --image forces the browser composer.",
    )
    post_parser.add_argument(
        "--comment",
        help="After posting/scheduling, pre-write this as a top-level comment "
        "on the new post (e.g. the article URL). GraphQL path only (not with --image).",
    )
    post_parser.add_argument(
        "--headless", action="store_true",
        help="Run headless when --image is used (default: headed)",
    )

    # comment (top-level comment on own post, incl. scheduled posts)
    comment_parser = subparsers.add_parser(
        "comment",
        help="Add a top-level comment to one of your own posts (works on scheduled posts)",
    )
    comment_parser.add_argument(
        "post_id",
        help="Numeric post_id (printed by 'fb post') or base64 story_id (UzpfS…)",
    )
    comment_parser.add_argument("text", help="Comment text")

    # post-list-scheduled (read-only -> headless by default, like inbox/read)
    pls_parser = subparsers.add_parser(
        "post-list-scheduled",
        help="List scheduled posts (Content Library > Scheduled tab)",
    )
    pls_parser.add_argument(
        "--no-headless", action="store_true",
        help="Run in headed mode (default: headless)",
    )
    pls_parser.add_argument(
        "--ids", action="store_true",
        help="Also resolve each post's numeric post_id (~10s per post)",
    )

    # comment-scheduled (adds a comment to an unpublished post -> write op)
    pcs_parser = subparsers.add_parser(
        "comment-scheduled",
        help="Comment on a scheduled (not yet published) post by its 1-based index",
    )
    pcs_parser.add_argument("index", type=int, help="1-based index shown by post-list-scheduled")
    pcs_parser.add_argument("text", help="Comment text (e.g. the URL you park in comments)")
    pcs_parser.add_argument(
        "--match",
        help="Safety check: abort unless the target row's preview text contains this string",
    )
    pcs_parser.add_argument(
        "--no-headless", action="store_true",
        help="Run in headed mode (default: headless)",
    )

    # post-delete-scheduled (destructive -> headed by default, like send/post)
    pds_parser = subparsers.add_parser(
        "post-delete-scheduled",
        help="Delete a scheduled post by its 1-based index (from post-list-scheduled)",
    )
    pds_parser.add_argument("index", type=int, help="1-based index shown by post-list-scheduled")
    pds_parser.add_argument(
        "--match",
        help="Safety check: abort unless the target row's preview text contains this string",
    )
    pds_parser.add_argument(
        "--headless", action="store_true",
        help="Run headless (default: headed)",
    )

    # comments
    comments_parser = subparsers.add_parser("comments", help="List unreplied comments")
    comments_parser.add_argument("--page", help="Page ID (default: from .env)")
    comments_parser.add_argument("--count", type=int, default=10, help="Number of comments (default: 10)")
    comments_parser.add_argument(
        "--filter", choices=["UNRESPONDED", "ALL"],
        default="UNRESPONDED", help="Filter type (default: UNRESPONDED)",
    )
    comments_parser.add_argument("--next", action="store_true", help="Fetch next page")

    # report
    report_parser = subparsers.add_parser(
        "report",
        help="Create + download a content data report CSV (revenue metrics excluded)",
    )
    report_parser.add_argument("--out", help="Output CSV path (default: fb_report_YYYY-MM-DD.csv)")
    report_parser.add_argument("--timeout", type=int, default=240,
                               help="Seconds to wait for report generation (default: 240)")
    report_parser.add_argument(
        "--include-revenue", action="store_true",
        help="Keep 收益 metrics (WARNING: FB's export silently drops every column after them)",
    )
    report_parser.add_argument("--no-headless", action="store_true", help="Show the browser")

    # reply
    reply_parser = subparsers.add_parser("reply", help="Reply to a comment")
    reply_parser.add_argument("id", help="Comment index (from 'comments' list) or base64 comment ID")
    reply_parser.add_argument("text", help="Reply text")

    # login
    subparsers.add_parser("login", help="Open browser for manual Facebook login")

    # send
    send_parser = subparsers.add_parser("send", help="Send a Messenger message")
    send_parser.add_argument("thread_id", help="Thread ID or contact name (from cache)")
    send_parser.add_argument("text", help="Message text")
    send_parser.add_argument("--headless", action="store_true", help="Run in headless mode (hidden browser)")

    # inbox
    inbox_parser = subparsers.add_parser("inbox", help="List Messenger inbox conversations")
    inbox_parser.add_argument("--count", type=int, default=20, help="Number of conversations (default: 20)")
    inbox_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode")

    # read
    read_parser = subparsers.add_parser("read", help="Read messages from a Messenger thread")
    read_parser.add_argument("thread_id", help="Thread ID or contact name (from cache)")
    read_parser.add_argument("--count", type=int, default=20, help="Number of messages (default: 20)")
    read_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode")

    # search (find user and read messages)
    search_parser = subparsers.add_parser("search", help="Search Messenger for a user and read messages")
    search_parser.add_argument("query", help="Name to search for")
    search_parser.add_argument("--count", type=int, default=20, help="Number of messages (default: 20)")
    search_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode")

    # contacts
    subparsers.add_parser("contacts", help="List cached Messenger contacts")

    # discover-e2ee
    e2ee_parser = subparsers.add_parser("discover-e2ee", help="Discover and cache top E2E contacts")
    e2ee_parser.add_argument("--count", type=int, default=20, help="Number of E2E contacts to find (default: 20)")
    e2ee_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode")

    # verify-contacts
    verify_parser = subparsers.add_parser("verify-contacts", help="Verify E2E contacts (1-on-1 vs group)")
    verify_parser.add_argument("--count", type=int, default=20, help="Number of contacts to verify (default: 20)")
    verify_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode")

    # daemon
    subparsers.add_parser("daemon", help="Start persistent browser for fast Messenger access")

    # history (scroll and extract full chat history)
    history_parser = subparsers.add_parser("history", help="Extract chat history by scrolling through a thread")
    history_parser.add_argument("thread_id", help="Thread ID or contact name (from cache)")
    history_parser.add_argument("--days", type=int, default=30, help="Number of days of history (default: 30)")
    history_parser.add_argument("--output", help="Output JSON file path (default: profiles/<profile>/chat_history_<thread_id>.json)")
    history_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode (visible browser)")

    args = parser.parse_args()

    if args.command == "post":
        cmd_post(args)
    elif args.command == "post-list-scheduled":
        cmd_list_scheduled(args)
    elif args.command == "post-delete-scheduled":
        cmd_delete_scheduled(args)
    elif args.command == "comment-scheduled":
        cmd_comment_scheduled(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "comment":
        cmd_comment(args)
    elif args.command == "comments":
        cmd_comments(args)
    elif args.command == "reply":
        cmd_reply(args)
    elif args.command == "login":
        cmd_login(args)
    elif args.command == "send":
        cmd_send(args)
    elif args.command == "inbox":
        cmd_inbox(args)
    elif args.command == "read":
        cmd_read(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "contacts":
        cmd_contacts(args)
    elif args.command == "discover-e2ee":
        cmd_discover_e2ee(args)
    elif args.command == "verify-contacts":
        cmd_verify_contacts(args)
    elif args.command == "daemon":
        cmd_daemon(args)
    elif args.command == "history":
        cmd_history(args)


if __name__ == "__main__":
    main()
