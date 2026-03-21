#!/usr/bin/env python3
"""Post text to Facebook using the GraphQL API."""

import argparse
import json
import sys
import uuid

from scripts.fb_config import POST_DOC_ID
from scripts.fb_session import (
    POST_RELAY_PV_FLAGS as RELAY_PV_FLAGS,
    create_session,
    fetch_tokens,
    graphql_request,
    load_cookies,
)


def build_variables(text: str, actor_id: str, privacy: str) -> dict:
    """Build the GraphQL variables payload."""
    session_id = str(uuid.uuid4())
    idempotence_token = f"{session_id}_FEED"

    variables = {
        "input": {
            "composer_entry_point": "inline_composer",
            "composer_source_surface": "timeline",
            "idempotence_token": idempotence_token,
            "source": "WWW",
            "attachments": [],
            "audience": {
                "privacy": {
                    "allow": [],
                    "base_state": privacy,
                    "deny": [],
                    "tag_expansion_state": "UNSPECIFIED",
                }
            },
            "message": {"ranges": [], "text": text},
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
        **RELAY_PV_FLAGS,
    }
    return variables


def post_to_facebook(text: str, privacy: str = "SELF") -> None:
    """Post text to Facebook."""
    cookies = load_cookies()
    actor_id = cookies["actor_id"]
    session = create_session(cookies)

    print("Fetching tokens from Facebook...")
    tokens = fetch_tokens(session)
    print(f"  fb_dtsg: {tokens['fb_dtsg'][:20]}...")
    print(f"  __rev: {tokens.get('__rev', 'N/A')}")

    variables = build_variables(text, actor_id, privacy)

    print(f"\nPosting to Facebook (privacy: {privacy})...")
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


def main():
    parser = argparse.ArgumentParser(description="Post text to Facebook")
    parser.add_argument("text", help="The text to post")
    parser.add_argument(
        "--privacy",
        choices=["EVERYONE", "FRIENDS", "SELF"],
        default="SELF",
        help="Privacy setting (default: SELF = only me)",
    )
    args = parser.parse_args()

    post_to_facebook(args.text, args.privacy)


if __name__ == "__main__":
    main()
