"""Shared Facebook session, cookies, tokens, and GraphQL helpers."""

import json
import os
import re
import sys

import requests
from dotenv import dotenv_values

from scripts.fb_config import (
    FB_USER_AGENT,
    PRIVACY_SELECTOR_DOC_ID,
    PRIVACY_WRITE_ID,
    STORY_VIEW_DOC_ID,
)


GRAPHQL_URL = "https://www.facebook.com/api/graphql/"

COOKIE_KEYS = [
    "c_user", "i_user", "datr", "fr", "xs", "sb", "presence", "ps_l", "ps_n", "wd", "dpr",
]

# Relay PV flags for post creation
POST_RELAY_PV_FLAGS = {
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

# Relay PV flags for comments listing
COMMENTS_RELAY_PV_FLAGS = {
    "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": False,
    "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
    "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
    "__relay_internal__pv__IsWorkUserrelayprovider": False,
}

# Relay PV flags for reply mutation
REPLY_RELAY_PV_FLAGS = {
    "__relay_internal__pv__groups_comet_use_glvrelayprovider": False,
    "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
    "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
    "__relay_internal__pv__IsWorkUserrelayprovider": False,
    "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "ORIGINAL",
}


# Relay PV flags for story view query
STORY_VIEW_RELAY_PV_FLAGS = {
    "__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider": True,
    "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": True,
    "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
    "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
    "__relay_internal__pv__IsWorkUserrelayprovider": False,
    "__relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider": False,
    "__relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider": True,
    "__relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider": True,
    "__relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider": False,
    "__relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider": False,
    "__relay_internal__pv__IsMergQAPollsrelayprovider": False,
    "__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider": True,
    "__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider": False,
    "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "ORIGINAL",
    "__relay_internal__pv__CometUFIShareActionMigrationrelayprovider": True,
    "__relay_internal__pv__CometUFISingleLineUFIrelayprovider": False,
    "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": False,
    "__relay_internal__pv__StoriesArmadilloReplyEnabledrelayprovider": True,
    "__relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider": True,
    "__relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider": 206,
    "__relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider": False,
    "__relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider": True,
}


def _base_dir() -> str:
    """Return project root directory (parent of scripts/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cookies(profile: str = "default") -> dict[str, str]:
    """Load Facebook cookies from profiles/<profile>/cookies.json, with fallback."""
    base = _base_dir()
    profile_cookies = os.path.join(base, "profiles", profile, "cookies.json")
    legacy_cookies = os.path.join(base, "cookies.json")
    env_file = os.path.join(base, ".env")

    cookies = {}
    if os.path.exists(profile_cookies):
        with open(profile_cookies) as f:
            data = json.load(f)
        for item in data:
            name = item["name"]
            if name in COOKIE_KEYS:
                cookies[name] = item["value"]
    elif profile == "default" and os.path.exists(legacy_cookies):
        with open(legacy_cookies) as f:
            data = json.load(f)
        for item in data:
            name = item["name"]
            if name in COOKIE_KEYS:
                cookies[name] = item["value"]
    elif profile == "default" and os.path.exists(env_file):
        env = dotenv_values(env_file)
        for key in COOKIE_KEYS:
            val = env.get(key)
            if val:
                cookies[key] = val
    if "c_user" not in cookies or "xs" not in cookies:
        print(f"Error: Need at least c_user and xs cookies for profile '{profile}'.", file=sys.stderr)
        print(f"Place cookies.json in profiles/{profile}/cookies.json", file=sys.stderr)
        sys.exit(1)
    # i_user = page actor ID when in "Switch to Page" mode
    # Use it as the acting identity, keep c_user for session auth
    if "i_user" in cookies:
        cookies["actor_id"] = cookies["i_user"]
    else:
        cookies["actor_id"] = cookies["c_user"]
    return cookies


def fetch_page_id(session: requests.Session) -> str:
    """Fetch page_id from the Professional Dashboard page HTML."""
    resp = session.get(
        "https://www.facebook.com/professional_dashboard/", timeout=15,
    )
    resp.raise_for_status()
    html = resp.text

    # The dashboard HTML embeds pageID in preloaded query variables
    m = re.search(r'"pageID":"(\d+)"', html)
    if m:
        return m.group(1)

    # Fallback: URL-encoded form
    m = re.search(r'pageID%22%3A%22(\d+)%22', html)
    if m:
        return m.group(1)

    print("Error: Could not extract page_id from Professional Dashboard.", file=sys.stderr)
    print("Pass --page PAGE_ID explicitly.", file=sys.stderr)
    sys.exit(1)


def create_session(cookies: dict[str, str]) -> requests.Session:
    """Build a requests.Session with browser headers and cookies."""
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        "User-Agent": FB_USER_AGENT,
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
    })
    return session


def fetch_tokens(session: requests.Session) -> dict[str, str]:
    """Fetch fb_dtsg, lsd, jazoest, __rev, __hsi from Facebook homepage."""
    resp = session.get("https://www.facebook.com/", timeout=15)
    resp.raise_for_status()
    html = resp.text

    tokens = {}

    m = re.search(r'"DTSGInitData",\[\],\{"token":"([^"]+)"', html)
    if not m:
        m = re.search(r'name="fb_dtsg" value="([^"]+)"', html)
    if m:
        tokens["fb_dtsg"] = m.group(1)

    m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
    if not m:
        m = re.search(r'name="lsd" value="([^"]+)"', html)
    if m:
        tokens["lsd"] = m.group(1)

    m = re.search(r'jazoest[=:](\d+)', html)
    if m:
        tokens["jazoest"] = m.group(1)

    m = re.search(r'"client_revision":(\d+)', html)
    if not m:
        m = re.search(r'"__spin_r":(\d+)', html)
    if m:
        tokens["__rev"] = m.group(1)

    m = re.search(r'"hsi":"(\d+)"', html)
    if m:
        tokens["__hsi"] = m.group(1)

    m = re.search(r'"__spin_t":(\d+)', html)
    if m:
        tokens["__spin_t"] = m.group(1)

    m = re.search(r'"__spin_b":"([^"]+)"', html)
    if m:
        tokens["__spin_b"] = m.group(1)

    if "fb_dtsg" not in tokens:
        print("Error: Could not extract fb_dtsg from Facebook page.", file=sys.stderr)
        print("Your cookies may be expired. Update .env with fresh values.", file=sys.stderr)
        sys.exit(1)

    return tokens


def graphql_request(
    session: requests.Session,
    tokens: dict[str, str],
    actor_id: str,
    doc_id: str,
    friendly_name: str,
    variables: dict,
) -> dict:
    """POST to /api/graphql/ with token injection and response parsing."""
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
        "fb_api_req_friendly_name": friendly_name,
        "server_timestamps": "true",
        "variables": json.dumps(variables),
        "doc_id": doc_id,
    }

    session.headers.update({
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.facebook.com",
        "Referer": "https://www.facebook.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-FB-Friendly-Name": friendly_name,
    })

    resp = session.post(GRAPHQL_URL, data=form_data, timeout=30)

    if resp.status_code != 200:
        print(f"Error: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)

    def _check_graphql_errors(data):
        # An HTTP 200 can still carry a GraphQL-level failure (expired
        # token, rate limit, permission). Returning it as success makes
        # callers read empty `data` and report nothing wrong.
        if isinstance(data, dict) and data.get("errors") and not data.get("data"):
            print("Error: GraphQL request failed:", file=sys.stderr)
            for err in data["errors"]:
                print(f"  {err.get('message', err)}", file=sys.stderr)
            sys.exit(1)
        return data

    body = resp.text
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):]

    try:
        return _check_graphql_errors(json.loads(body))
    except json.JSONDecodeError:
        for line in body.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "data" in data:
                    return data
            except json.JSONDecodeError:
                continue
        print("Error: Could not parse response JSON", file=sys.stderr)
        print(body[:500], file=sys.stderr)
        sys.exit(1)


def fetch_story_text(session, tokens, actor_id, story_id):
    """Fetch full post text including shared/attached story text."""
    variables = {
        "feedLocation": "DEDICATED_COMMENTING_SURFACE",
        "feedbackSource": 110,
        "focusCommentID": None,
        "privacySelectorRenderLocation": "COMET_STREAM",
        "referringStoryRenderLocation": "professional_dashboard",
        "renderLocation": "professional_dashboard",
        "scale": 2,
        "useDefaultActor": False,
        "storyID": story_id,
        **STORY_VIEW_RELAY_PV_FLAGS,
    }

    data = graphql_request(
        session, tokens, actor_id, STORY_VIEW_DOC_ID,
        "CometFocusedStoryViewStoryQuery", variables,
    )

    node = data.get("data", {}).get("node", {})
    texts = []

    # Extract main post message
    content = (node.get("comet_sections") or {}).get("content") or {}
    content = (content.get("story") or {}).get("comet_sections") or {}
    main_msg_obj = ((content.get("message") or {}).get("story") or {}).get("message") or {}
    main_msg = main_msg_obj.get("text", "") if isinstance(main_msg_obj, dict) else ""
    if main_msg:
        texts.append(main_msg)

    # Extract attached/shared story message (two possible paths)
    def _get_attached_text(obj, depth=0):
        if depth > 3 or not isinstance(obj, dict):
            return
        # Direct attached_story path
        attached = obj.get("attached_story")
        if isinstance(attached, dict):
            # Try comet_sections.message path
            msg = (attached.get("comet_sections", {})
                   .get("message", {}).get("story", {})
                   .get("message", {}).get("text", ""))
            if msg and msg not in texts:
                texts.append(msg)
            # Try nested attached_story_layout
            layout = (attached.get("comet_sections", {})
                      .get("attached_story_layout", {}).get("story", {})
                      .get("comet_sections", {}))
            layout_msg = (layout.get("message", {}).get("story", {})
                          .get("message", {}).get("text", ""))
            if layout_msg and layout_msg not in texts:
                texts.append(layout_msg)
            _get_attached_text(attached, depth + 1)

    # Search from content.story and from node directly
    _get_attached_text(content)
    attached_story = content.get("attached_story")
    if isinstance(attached_story, dict):
        story_inner = attached_story.get("story")
        if isinstance(story_inner, dict):
            _get_attached_text(story_inner)
    _get_attached_text(node)

    return texts


# ── Audience / privacy ───────────────────────────────────────────────────────

# Audiences that are NOT a plain base_state. Facebook models them as
# base_state SELF plus an account-specific list id in `allow` — the id differs
# per account (and would silently post to the wrong people if hardcoded), so it
# is resolved at runtime. The icon name is the stable key; labels are localized.
PRIVACY_ICON_BY_NAME = {
    "SUBSCRIBERS": "supporter_exclusive",
}

PLAIN_PRIVACY_STATES = ("EVERYONE", "FRIENDS", "SELF")


def _iter_privacy_options(node):
    """Yield every privacy option object in a privacy-selector response."""
    if isinstance(node, dict):
        row = node.get("privacy_row_input")
        icon = node.get("icon_image")
        if isinstance(row, dict) and isinstance(icon, dict) and icon.get("name"):
            yield {"icon": icon["name"], "label": node.get("label", ""), "row": row}
        for value in node.values():
            yield from _iter_privacy_options(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_privacy_options(value)


def fetch_privacy_options(session, tokens, actor_id) -> list[dict]:
    """Fetch the composer's audience list (what the 貼文分享對象 dialog shows)."""
    variables = {
        "localPrivacyRow": {
            "allow": [],
            "base_state": "EVERYONE",
            "deny": [],
            "tag_expansion_state": "UNSPECIFIED",
        },
        "privacyWriteID": PRIVACY_WRITE_ID,
        "renderLocation": "COMET_FULLSCREEN_COMPOSER",
        "scale": 2,
    }
    data = graphql_request(
        session, tokens, actor_id, PRIVACY_SELECTOR_DOC_ID,
        "CometPrivacySelectorPickerContainerQuery", variables,
    )
    options, seen = [], set()
    for opt in _iter_privacy_options(data):
        key = (opt["icon"], json.dumps(opt["row"], sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        options.append(opt)
    return options


def resolve_privacy_row(session, tokens, actor_id, privacy: str) -> dict:
    """Return the `audience.privacy` payload for a --privacy value.

    Plain states are built locally; list-backed audiences (SUBSCRIBERS) are
    looked up in the account's own privacy selector. Exits with a clear error
    rather than falling back to a wider audience — silently posting
    subscriber-only content to everyone is the failure mode to avoid.
    """
    if privacy in PLAIN_PRIVACY_STATES:
        return {
            "allow": [],
            "base_state": privacy,
            "deny": [],
            "tag_expansion_state": "UNSPECIFIED",
        }

    icon = PRIVACY_ICON_BY_NAME.get(privacy)
    if not icon:
        print(f"Error: unsupported --privacy value {privacy!r}", file=sys.stderr)
        sys.exit(1)

    options = fetch_privacy_options(session, tokens, actor_id)
    match = next((o for o in options if o["icon"] == icon), None)
    if not match:
        available = ", ".join(f"{o['icon']}({o['label']})" for o in options) or "none"
        print(
            f"Error: this profile's audience list has no '{icon}' option, so "
            f"--privacy {privacy} cannot be used. Subscriber-only sharing needs "
            "Facebook subscriptions enabled on the profile. "
            f"Available: {available}",
            file=sys.stderr,
        )
        sys.exit(1)

    row = match["row"]
    if not row.get("allow"):
        print(
            f"Error: the '{icon}' audience came back without a list id "
            f"({json.dumps(row, ensure_ascii=False)}) — refusing to post, since "
            "base_state SELF alone would make the post visible only to you.",
            file=sys.stderr,
        )
        sys.exit(1)

    return {
        "allow": list(row["allow"]),
        "base_state": row.get("base_state", "SELF"),
        "deny": list(row.get("deny") or []),
        "tag_expansion_state": row.get("tag_expansion_state", "UNSPECIFIED"),
    }


def fetch_story_privacy(session, tokens, actor_id, story_id: str) -> dict | None:
    """Read back the audience Facebook actually stored on a story.

    Returns {"icon", "label", "allow"} or None if the story's privacy scope
    could not be read. Used to confirm a list-backed audience landed, since
    the mutation reports success regardless of what it stored.
    """
    variables = {
        "feedLocation": "DEDICATED_COMMENTING_SURFACE",
        "feedbackSource": 110,
        "focusCommentID": None,
        "privacySelectorRenderLocation": "COMET_STREAM",
        "referringStoryRenderLocation": "professional_dashboard",
        "renderLocation": "professional_dashboard",
        "scale": 2,
        "useDefaultActor": False,
        "storyID": story_id,
        **STORY_VIEW_RELAY_PV_FLAGS,
    }
    try:
        data = graphql_request(
            session, tokens, actor_id, STORY_VIEW_DOC_ID,
            "CometFocusedStoryViewStoryQuery", variables,
        )
    except SystemExit:
        return None

    def _find(node, pred):
        if isinstance(node, dict):
            if pred(node):
                return node
            for value in node.values():
                found = _find(value, pred)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = _find(value, pred)
                if found is not None:
                    return found
        return None

    # The stored audience lives on the story's privacy_scope_renderer; its
    # human label sits further down under entry_point_renderer, so the two
    # are collected separately rather than off one node.
    renderer = _find(
        data,
        lambda n: isinstance(n.get("privacy_scope_renderer"), dict)
        and isinstance(n["privacy_scope_renderer"].get("privacy_row_input"), dict),
    )
    if renderer is None:
        return None
    renderer = renderer["privacy_scope_renderer"]
    row = renderer["privacy_row_input"]
    labelled = _find(
        renderer,
        lambda n: isinstance(n.get("icon_image"), dict)
        and n["icon_image"].get("name")
        and n.get("label"),
    ) or {}
    return {
        "icon": (labelled.get("icon_image") or {}).get("name", ""),
        "label": labelled.get("label", ""),
        "allow": list(row.get("allow") or []),
    }
