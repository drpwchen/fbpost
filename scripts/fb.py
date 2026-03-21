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
from datetime import datetime

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
)

def _last_comments_file(profile: str = "default") -> str:
    """Return per-profile path for .last_comments.json."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile_dir = os.path.join(base, "profiles", profile)
    if os.path.isdir(profile_dir):
        return os.path.join(profile_dir, ".last_comments.json")
    return os.path.join(base, ".last_comments.json")


# ── Post ─────────────────────────────────────────────────────────────────────

def cmd_post(args):
    """Post text to Facebook."""
    cookies = load_cookies(args.profile)
    actor_id = cookies["actor_id"]
    session = create_session(cookies)

    print("Fetching tokens from Facebook...")
    tokens = fetch_tokens(session)
    print(f"  fb_dtsg: {tokens['fb_dtsg'][:20]}...")

    session_id = str(uuid.uuid4())
    variables = {
        "input": {
            "composer_entry_point": "inline_composer",
            "composer_source_surface": "timeline",
            "idempotence_token": f"{session_id}_FEED",
            "source": "WWW",
            "attachments": [],
            "audience": {
                "privacy": {
                    "allow": [],
                    "base_state": args.privacy,
                    "deny": [],
                    "tag_expansion_state": "UNSPECIFIED",
                }
            },
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

    print(f"\nPosting to Facebook (privacy: {args.privacy})...")
    data = graphql_request(
        session, tokens, actor_id, POST_DOC_ID,
        "ComposerStoryCreateMutation", variables,
    )

    if "errors" in data:
        print("Error from Facebook:", file=sys.stderr)
        print(json.dumps(data["errors"], indent=2), file=sys.stderr)
        sys.exit(1)

    story = data.get("data", {}).get("story_create", {}).get("story")
    if story:
        print(f"\nSuccess! Story created.")
        print(f"  Story ID: {story.get('id', 'unknown')}")
        if story.get("url"):
            print(f"  URL: {story['url']}")
    else:
        print("\nRequest sent. Response:")
        print(json.dumps(data, indent=2)[:1000])


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
                print(f"📝 Post: (no text)")
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
        print(f"\nReply posted successfully!")
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
    from scripts.fb_messenger import send_message
    headless = not args.no_headless
    asyncio.run(send_message(args.profile, args.thread_id, args.text, headless))


def cmd_inbox(args):
    """List Messenger inbox."""
    from scripts.fb_messenger import list_inbox
    headless = not args.no_headless
    asyncio.run(list_inbox(args.profile, args.count, headless))


def cmd_read(args):
    """Read messages from a Messenger thread."""
    from scripts.fb_messenger import read_thread
    headless = not args.no_headless
    asyncio.run(read_thread(args.profile, args.thread_id, args.count, headless))


def cmd_search(args):
    """Search for a user in Messenger and read their messages."""
    from scripts.fb_messenger import search_and_read
    headless = not args.no_headless
    asyncio.run(search_and_read(args.profile, args.query, args.count, headless))


def cmd_history(args):
    """Extract chat history from a Messenger thread by scrolling."""
    from scripts.fb_messenger import scroll_and_extract_thread
    headless = not args.no_headless
    asyncio.run(scroll_and_extract_thread(
        args.profile, args.thread_id, args.days, headless, args.output,
    ))


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
        "--privacy", choices=["EVERYONE", "FRIENDS", "SELF"],
        default="SELF", help="Privacy setting (default: SELF)",
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

    # reply
    reply_parser = subparsers.add_parser("reply", help="Reply to a comment")
    reply_parser.add_argument("id", help="Comment index (from 'comments' list) or base64 comment ID")
    reply_parser.add_argument("text", help="Reply text")

    # login
    subparsers.add_parser("login", help="Open browser for manual Facebook login")

    # send
    send_parser = subparsers.add_parser("send", help="Send a Messenger message")
    send_parser.add_argument("thread_id", help="Messenger thread ID")
    send_parser.add_argument("text", help="Message text")
    send_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode (visible browser)")

    # inbox
    inbox_parser = subparsers.add_parser("inbox", help="List Messenger inbox conversations")
    inbox_parser.add_argument("--count", type=int, default=20, help="Number of conversations (default: 20)")
    inbox_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode")

    # read
    read_parser = subparsers.add_parser("read", help="Read messages from a Messenger thread")
    read_parser.add_argument("thread_id", help="Messenger thread ID")
    read_parser.add_argument("--count", type=int, default=20, help="Number of messages (default: 20)")
    read_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode")

    # search (find user and read messages)
    search_parser = subparsers.add_parser("search", help="Search Messenger for a user and read messages")
    search_parser.add_argument("query", help="Name to search for")
    search_parser.add_argument("--count", type=int, default=20, help="Number of messages (default: 20)")
    search_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode")

    # history (scroll and extract full chat history)
    history_parser = subparsers.add_parser("history", help="Extract chat history by scrolling through a thread")
    history_parser.add_argument("thread_id", help="Messenger thread ID")
    history_parser.add_argument("--days", type=int, default=30, help="Number of days of history (default: 30)")
    history_parser.add_argument("--output", help="Output JSON file path (default: profiles/<profile>/chat_history_<thread_id>.json)")
    history_parser.add_argument("--no-headless", action="store_true", help="Run in headed mode (visible browser)")

    args = parser.parse_args()

    if args.command == "post":
        cmd_post(args)
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
    elif args.command == "history":
        cmd_history(args)


if __name__ == "__main__":
    main()
