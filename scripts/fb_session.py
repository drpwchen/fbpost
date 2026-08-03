"""Shared Facebook session, cookies, tokens, and GraphQL helpers."""

import base64
import json
import os
import re
import sys
import uuid

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


def _graphql_form(tokens, actor_id, doc_id, friendly_name, variables) -> dict:
    """Build the form body every /api/graphql call shares."""
    return {
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


def _graphql_post(session, tokens, actor_id, doc_id, friendly_name, variables):
    """Send the request and return its raw body, minus the anti-JSON prefix."""
    form_data = _graphql_form(tokens, actor_id, doc_id, friendly_name, variables)

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

    resp = session.post(GRAPHQL_URL, data=form_data, timeout=60)

    if resp.status_code != 200:
        print(f"Error: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)

    body = resp.text
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):]
    return body


def graphql_chunks(
    session: requests.Session,
    tokens: dict[str, str],
    actor_id: str,
    doc_id: str,
    friendly_name: str,
    variables: dict,
) -> list[dict]:
    """Return EVERY JSON chunk of a GraphQL response, in arrival order.

    Queries that defer part of their tree (the Content Library table is one)
    answer with several newline-separated JSON objects: a first payload marked
    `is_final: false`, then `label`/`path` chunks carrying the deferred data.
    graphql_request() returns only the first chunk with a `data` key, which for
    those queries is the shell — the rows look missing when they are simply in
    a later chunk.
    """
    body = _graphql_post(session, tokens, actor_id, doc_id, friendly_name, variables)
    chunks = []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not chunks:
        print("Error: Could not parse response JSON", file=sys.stderr)
        print(body[:500], file=sys.stderr)
        sys.exit(1)
    errors = [e for c in chunks for e in (c.get("errors") or [])]
    if errors and not any(c.get("data") for c in chunks):
        print("Error: GraphQL request failed:", file=sys.stderr)
        for err in errors:
            print(f"  {err.get('message', err)}", file=sys.stderr)
        sys.exit(1)
    return chunks


def graphql_request(
    session: requests.Session,
    tokens: dict[str, str],
    actor_id: str,
    doc_id: str,
    friendly_name: str,
    variables: dict,
) -> dict:
    """POST to /api/graphql/ with token injection and response parsing."""
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

    body = _graphql_post(session, tokens, actor_id, doc_id, friendly_name, variables)

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


# ── Content Library (published / scheduled posts) ────────────────────────────

CONTENT_LIBRARY_PV_FLAGS = {
    "__relay_internal__pv__ProdashWebContentLibraryDeferTableGKrelayprovider": True,
    "__relay_internal__pv__ContentLibraryStatusColumnGKrelayprovider": False,
    "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": True,
    "__relay_internal__pv__enableProdashWebCrossPostInsightsrelayprovider": True,
    "__relay_internal__pv__ContentLibraryUnifiedContentStatusGKrelayprovider": True,
}

# The tabs of the Content Library, as its `routeFilter` / `filteringOption`.
CONTENT_FILTERS = ("PUBLISHED", "SCHEDULED", "DRAFT")
CONTENT_DATE_RANGES = ("LAST_7D", "LAST_28D", "LAST_90D")


def _find_key(node, key):
    """Depth-first search for the first dict value stored under `key`."""
    if isinstance(node, dict):
        found = node.get(key)
        if isinstance(found, dict):
            return found
        for value in node.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def post_id_from_story_id(story_id: str) -> str:
    """Pull the numeric post id out of a base64 story id.

    Story ids decode to `S:_I<actor>:<post_id>` (timeline), the same with the
    post id repeated, or `S:_I<actor>:VK:<post_id>` for a post in a group.
    """
    try:
        raw = base64.b64decode(story_id + "==").decode("utf-8", "replace")
    except Exception:
        return ""
    digits = [part for part in raw.split(":") if part.isdigit()]
    return digits[-1] if digits else ""


def _content_entry(node: dict) -> dict:
    """Flatten one Content Library row into the fields the CLI prints."""
    story = node.get("story") or {}
    insights = ((node.get("tofu_entity") or {}).get("entity_insights")) or {}
    story_id = story.get("id") or ""
    group = (story.get("target_group") or {}).get("name") or ""
    return {
        "story_id": story_id,
        "post_id": post_id_from_story_id(story_id),
        "text": (node.get("title") or "").strip(),
        "when_text": node.get("timestamp_text") or "",
        "created": story.get("creation_time") or 0,
        "scheduled": story.get("scheduled_publish_time") or 0,
        "status": story.get("unpublished_content_type") or "",
        "url": story.get("url") or "",
        "group": group,
        "views": ((insights.get("views") or {}).get("value")),
        "engagement": ((insights.get("engagement") or {}).get("value")),
        "kind": node.get("business_content_type") or "",
    }


def fetch_content_library(
    session, tokens, actor_id, filtering: str = "SCHEDULED",
    limit: int = 20, date_range: str = "LAST_28D",
) -> list[dict]:
    """List the account's own posts from the Content Library, newest first.

    `filtering` picks the tab (PUBLISHED / SCHEDULED / DRAFT). This is the same
    data the dashboard's table shows, so it is also the only reliable way to
    confirm a post really was published — a profile page scrape does not load
    the post wall for this account.
    """
    from scripts.fb_config import CONTENT_LIBRARY_DOC_ID, CONTENT_LIBRARY_PAGE_DOC_ID

    variables = {
        "isExportEnabled": True, "isMonetizationEnabled": True,
        "pageID": actor_id, "ref": None,
        "routeDateRange": date_range, "routeEndDate": None,
        "routeFilter": filtering, "routeKeyword": None,
        "routePlacementType": "ALL", "routePostType": "ALL_CONTENT",
        "routePostTypes": None, "routeSortBy": "DATE",
        "routeSortingMethod": "METRICS_DESCENDING", "routeStartDate": None,
        "routeTranslationTypeFilter": None, "shouldFetchOptimalTime": False,
        **CONTENT_LIBRARY_PV_FLAGS,
    }
    chunks = graphql_chunks(
        session, tokens, actor_id, CONTENT_LIBRARY_DOC_ID,
        "ProdashCometV2ContentLibraryQuery", variables,
    )
    conn, page_id = None, None
    for chunk in chunks:
        conn = _find_key(chunk, "prodash_content_library")
        if conn:
            # The table chunk carries the library's own page id next to it;
            # the pagination query needs that id, not the c_user id.
            data = chunk.get("data") or {}
            page_id = data.get("id") or _find_key(chunk, "node") or actor_id
            if isinstance(page_id, dict):
                page_id = page_id.get("id", actor_id)
            break
    if not conn:
        return []

    entries = [_content_entry(e["node"]) for e in conn.get("edges", []) if e.get("node")]
    info = conn.get("page_info") or {}

    pv_page = {k: v for k, v in CONTENT_LIBRARY_PV_FLAGS.items() if "DeferTable" not in k}
    while len(entries) < limit and info.get("has_next_page") and info.get("end_cursor"):
        page_vars = {
            "after": info["end_cursor"], "customEndDate": None, "customStartDate": None,
            "dateRange": date_range, "filteringOption": filtering,
            "first": min(25, limit - len(entries)), "isMonetizationEnabled": True,
            "keyword": None, "metricAddOnType": "DATE", "placementType": "ALL",
            "postType": "ALL_CONTENT", "postTypes": None,
            "sortingMethod": "METRICS_DESCENDING", "translationTypeFilter": None,
            "id": page_id, **pv_page,
        }
        page_chunks = graphql_chunks(
            session, tokens, actor_id, CONTENT_LIBRARY_PAGE_DOC_ID,
            "ProdashCometV2ContentLibraryPaginationQuery", page_vars,
        )
        conn = None
        for chunk in page_chunks:
            conn = _find_key(chunk, "prodash_content_library")
            if conn:
                break
        if not conn or not conn.get("edges"):
            break
        entries.extend(
            _content_entry(e["node"]) for e in conn["edges"] if e.get("node")
        )
        info = conn.get("page_info") or {}

    return entries[:limit]


def delete_story(session, tokens, actor_id, story_id: str) -> bool:
    """Delete one of the account's own stories (published or scheduled).

    Same mutation the post's ⋯ menu fires. Returns True only when Facebook
    answers with the deleted story — callers still re-list to confirm, since a
    mutation reporting success is not proof the row is gone.
    """
    from scripts.fb_config import STORY_DELETE_DOC_ID

    variables = {
        "input": {
            "story_id": story_id,
            "story_location": "PERMALINK",
            "actor_id": actor_id,
            "client_mutation_id": "1",
        },
        "groupID": None,
        "inviteShortLinkKey": None,
        "renderLocation": None,
        "scale": 2,
        "__relay_internal__pv__groups_comet_use_glvrelayprovider": False,
    }
    data = graphql_request(
        session, tokens, actor_id, STORY_DELETE_DOC_ID,
        "useCometFeedStoryDeleteMutation", variables,
    )
    if data.get("errors"):
        for err in data["errors"]:
            print(f"  {err.get('message', err)}", file=sys.stderr)
        return False
    return bool((data.get("data") or {}).get("story_delete"))


# ── Photo upload ─────────────────────────────────────────────────────────────

# The composer's own upload endpoint. Captured 2026-08-03 by hooking
# XMLHttpRequest.send in the page (Playwright cannot read a multipart body,
# and the request's parameters live in the query string, not the form).
PHOTO_UPLOAD_URL = (
    "https://upload.facebook.com/ajax/react_composer/attachments/photo/upload"
)

_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}


def upload_photo(session, tokens, actor_id, image_path: str) -> str:
    """Upload one photo and return its photoID, for `attachments` on a post.

    This is the API equivalent of dropping a file on the composer, so a photo
    post no longer has to drive the browser. An unattached upload is invisible
    on the profile — nothing is published until the id is used in a mutation.
    """
    if not os.path.isfile(image_path):
        print(f"Error: image file not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(image_path)[1].lower()
    mime = _MIME_BY_EXT.get(ext)
    if not mime:
        print(
            f"Error: unsupported image type {ext!r} "
            f"(known: {', '.join(sorted(_MIME_BY_EXT))}).",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(image_path, "rb") as fh:
        blob = fh.read()

    params = {
        "av": actor_id, "__aaid": "0", "__user": actor_id, "__a": "1",
        "__comet_req": "15",
        "fb_dtsg": tokens["fb_dtsg"],
        "jazoest": tokens.get("jazoest", ""),
        "lsd": tokens.get("lsd", ""),
        "__spin_r": tokens.get("__rev", ""),
        "__spin_b": tokens.get("__spin_b", "trunk"),
        "__spin_t": tokens.get("__spin_t", ""),
        "__crn": "comet.fbweb.CometPostCreateRoute",
    }
    # Field names as the composer sends them; source=8 marks a composer photo.
    form = {
        "source": "8",
        "profile_id": actor_id,
        "waterfallxapp": "comet",
        "upload_id": "jsc_c_" + uuid.uuid4().hex[:8],
    }
    files = {"farr": (os.path.basename(image_path), blob, mime)}

    # graphql_request() leaves Content-Type: application/x-www-form-urlencoded
    # (and X-FB-Friendly-Name) on the shared session. requests only generates
    # the multipart Content-Type with its boundary when nothing else sets one,
    # so those leftovers make Facebook read this body as urlencoded and answer
    # 400 — but only when a GraphQL call ran first, which is exactly what the
    # SUBSCRIBERS path does. Drop them for this request, then restore.
    stashed = {
        key: session.headers.pop(key)
        for key in ("Content-Type", "X-FB-Friendly-Name")
        if key in session.headers
    }
    try:
        resp = session.post(
            PHOTO_UPLOAD_URL, params=params, data=form, files=files,
            headers={
                "Referer": "https://www.facebook.com/",
                "Origin": "https://www.facebook.com",
            },
            timeout=300,
        )
    finally:
        session.headers.update(stashed)
    body = resp.text
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):]
    try:
        data = json.loads(body)
    except ValueError:
        print(
            f"Error: photo upload returned an unreadable response "
            f"(status {resp.status_code}): {body[:200]}",
            file=sys.stderr,
        )
        sys.exit(1)

    photo_id = (data.get("payload") or {}).get("photoID")
    if not photo_id:
        reason = data.get("errorSummary") or data.get("error") or "no photoID in response"
        print(f"Error: photo upload failed — {reason}", file=sys.stderr)
        sys.exit(1)

    size_kb = len(blob) // 1024
    print(f"  photo uploaded: {os.path.basename(image_path)} ({size_kb} KB) -> {photo_id}")
    return photo_id
