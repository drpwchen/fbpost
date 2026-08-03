"""Facebook GraphQL doc_ids and configuration.

Doc IDs are Facebook's internal identifiers for GraphQL queries/mutations.
They are global (same for all users) but may change when Facebook deploys
new frontend code. Update values here when requests start returning errors.
"""

# ComposerStoryCreateMutation — create a new post on timeline
POST_DOC_ID = "26937332182536553"

# ProdashCometCommentsManagerCommentsListPaginationQuery — list comments from Professional Dashboard
COMMENTS_DOC_ID = "27195336053390016"

# useCometUFICreateCommentMutation — reply to a comment
REPLY_DOC_ID = "26136887842641090"

# CometFocusedStoryViewStoryQuery — fetch full post/story content
STORY_VIEW_DOC_ID = "25859627517041679"

# CometPrivacySelectorPickerContainerQuery — the composer's audience list.
# Used to resolve audiences that are not a plain base_state (subscribers,
# close friends): those are base_state SELF plus an account-specific list id
# in `allow`, which has to be read off this query rather than hardcoded.
PRIVACY_SELECTOR_DOC_ID = "27910248861900432"

# ProdashCometV2ContentLibraryQuery — the Content Library's own first page,
# i.e. what the 已發佈 / 已排定發佈 / 草稿 tabs render. Answers with a DEFERRED
# stream (several JSON chunks); the table arrives in a later chunk, so read it
# with graphql_chunks(), not graphql_request().
CONTENT_LIBRARY_DOC_ID = "27296449853323919"

# ProdashCometV2ContentLibraryPaginationQuery — pages after the first. Its `id`
# is the Content Library's own page id (returned alongside the table), NOT the
# c_user id: passing c_user resolves a User node with no content library on it.
CONTENT_LIBRARY_PAGE_DOC_ID = "27642268785365896"

# useCometFeedStoryDeleteMutation — delete one of your own stories, published or
# scheduled. Takes the base64 story id (UzpfS…), not the numeric post id.
STORY_DELETE_DOC_ID = "27542772888736782"

# base64('privacy_scope_renderer:{"id":8787670733}') — the timeline composer's
# privacy scope, taken verbatim from the web client's own request.
PRIVACY_WRITE_ID = "cHJpdmFjeV9zY29wZV9yZW5kZXJlcjp7ImlkIjo4Nzg3NjcwNzMzfQ"

# Browser identity used for BOTH Playwright contexts and raw HTTP requests —
# keep the Chrome major version in sync with the Sec-Ch-Ua headers in
# fb_session.py when bumping it.
FB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
