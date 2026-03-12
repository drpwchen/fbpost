#!/usr/bin/env python3
"""Post text to Facebook using the GraphQL API."""

import argparse
import json
import os
import re
import sys
import uuid

import requests
from dotenv import dotenv_values


GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
DOC_ID = "26937332182536553"

COOKIE_KEYS = ["c_user", "datr", "fr", "xs", "sb", "presence", "ps_l", "ps_n", "wd", "dpr"]

# Relay internal PV flags captured from HAR
RELAY_PV_FLAGS = {
    "__relay_internal__pv__CometUFIShareActionMigrationrelayprovider": True,
    "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": True,
    "__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider": True,
    "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": False,
    "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "ORIGINAL",
    "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
    "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
    "__relay_internal__pv__IsWorkUserrelayprovider": False,
    "__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider": False,
    "__relay_internal__pv__CometUFISingleLineUFIrelayprovider": False,
    "__relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider": False,
    "__relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider": True,
    "__relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider": True,
    "__relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider": False,
    "__relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider": False,
    "__relay_internal__pv__IsMergQAPollsrelayprovider": False,
    "__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider": True,
    "__relay_internal__pv__StoriesArmadilloReplyEnabledrelayprovider": True,
    "__relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider": True,
    "__relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider": 206,
    "__relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider": False,
    "__relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider": True,
    "__relay_internal__pv__groups_comet_use_glvrelayprovider": False,
    "__relay_internal__pv__GHLShouldChangeSponsoredAuctionDistanceFieldNamerelayprovider": True,
    "__relay_internal__pv__GHLShouldUseSponsoredAuctionLabelFieldNameV1relayprovider": True,
    "__relay_internal__pv__GHLShouldUseSponsoredAuctionLabelFieldNameV2relayprovider": False,
}


def load_cookies() -> dict[str, str]:
    """Load Facebook cookies from cookies.json (preferred) or .env file."""
    cookies_json = os.path.join(os.path.dirname(__file__) or ".", "cookies.json")
    env_file = os.path.join(os.path.dirname(__file__) or ".", ".env")

    cookies = {}
    if os.path.exists(cookies_json):
        with open(cookies_json) as f:
            data = json.load(f)
        for item in data:
            name = item["name"]
            if name in COOKIE_KEYS:
                cookies[name] = item["value"]
    elif os.path.exists(env_file):
        env = dotenv_values(env_file)
        for key in COOKIE_KEYS:
            val = env.get(key)
            if val:
                cookies[key] = val
    if "c_user" not in cookies or "xs" not in cookies:
        print("Error: Need at least c_user and xs cookies.", file=sys.stderr)
        print("Provide cookies.json (browser export) or .env file.", file=sys.stderr)
        sys.exit(1)
    return cookies


def fetch_tokens(session: requests.Session) -> dict[str, str]:
    """Fetch fb_dtsg, lsd, jazoest, __rev, __hsi from Facebook homepage."""
    resp = session.get("https://www.facebook.com/", timeout=15)
    resp.raise_for_status()
    html = resp.text

    tokens = {}

    # fb_dtsg
    m = re.search(r'"DTSGInitData",\[\],\{"token":"([^"]+)"', html)
    if not m:
        m = re.search(r'name="fb_dtsg" value="([^"]+)"', html)
    if m:
        tokens["fb_dtsg"] = m.group(1)

    # lsd
    m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
    if not m:
        m = re.search(r'name="lsd" value="([^"]+)"', html)
    if m:
        tokens["lsd"] = m.group(1)

    # jazoest
    m = re.search(r'jazoest[=:](\d+)', html)
    if m:
        tokens["jazoest"] = m.group(1)

    # __rev (client revision)
    m = re.search(r'"client_revision":(\d+)', html)
    if not m:
        m = re.search(r'"__spin_r":(\d+)', html)
    if m:
        tokens["__rev"] = m.group(1)

    # __hsi
    m = re.search(r'"hsi":"(\d+)"', html)
    if m:
        tokens["__hsi"] = m.group(1)

    # __spin_t
    m = re.search(r'"__spin_t":(\d+)', html)
    if m:
        tokens["__spin_t"] = m.group(1)

    # __spin_b
    m = re.search(r'"__spin_b":"([^"]+)"', html)
    if m:
        tokens["__spin_b"] = m.group(1)

    if "fb_dtsg" not in tokens:
        print("Error: Could not extract fb_dtsg from Facebook page.", file=sys.stderr)
        print("Your cookies may be expired. Update .env with fresh values.", file=sys.stderr)
        sys.exit(1)

    return tokens


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
    actor_id = cookies["c_user"]

    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Sec-Ch-Ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    print("Fetching tokens from Facebook...")
    tokens = fetch_tokens(session)
    print(f"  fb_dtsg: {tokens['fb_dtsg'][:20]}...")
    print(f"  __rev: {tokens.get('__rev', 'N/A')}")

    variables = build_variables(text, actor_id, privacy)

    form_data = {
        "av": actor_id,
        "__user": actor_id,
        "__a": "1",
        "__req": "1",
        "__ccg": "EXCELLENT",
        "dpr": "2",
        "__comet_req": "15",
        "fb_dtsg": tokens["fb_dtsg"],
        "jazoest": tokens.get("jazoest", ""),
        "lsd": tokens.get("lsd", ""),
        "__rev": tokens.get("__rev", ""),
        "__hsi": tokens.get("__hsi", ""),
        "__spin_r": tokens.get("__rev", ""),
        "__spin_b": tokens.get("__spin_b", "trunk"),
        "__spin_t": tokens.get("__spin_t", ""),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": "ComposerStoryCreateMutation",
        "server_timestamps": "true",
        "variables": json.dumps(variables),
        "doc_id": DOC_ID,
    }

    print(f"\nPosting to Facebook (privacy: {privacy})...")
    session.headers.update(
        {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.facebook.com",
            "Referer": "https://www.facebook.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-FB-Friendly-Name": "ComposerStoryCreateMutation",
        }
    )
    resp = session.post(GRAPHQL_URL, data=form_data, timeout=30)

    if resp.status_code != 200:
        print(f"Error: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)

    # Facebook returns multiple JSON objects separated by newlines
    # The first line is often "for (;;);" anti-hijacking prefix
    body = resp.text
    if body.startswith("for (;;);"):
        body = body[len("for (;;);") :]

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Try parsing line by line
        for line in body.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "data" in data:
                    break
            except json.JSONDecodeError:
                continue
        else:
            print("Error: Could not parse response JSON", file=sys.stderr)
            print(body[:500], file=sys.stderr)
            sys.exit(1)

    # Check for errors
    if "errors" in data:
        print("Error from Facebook:", file=sys.stderr)
        print(json.dumps(data["errors"], indent=2), file=sys.stderr)
        sys.exit(1)

    # Look for story creation confirmation
    story_create = (
        data.get("data", {}).get("story_create", {})
    )
    story = story_create.get("story")

    if story:
        story_id = story.get("id", "unknown")
        url = story.get("url", "")
        print(f"\nSuccess! Story created.")
        print(f"  Story ID: {story_id}")
        if url:
            print(f"  URL: {url}")
    else:
        # Even without story_create in response, post may have succeeded async
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
