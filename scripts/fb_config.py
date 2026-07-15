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

# Browser identity used for BOTH Playwright contexts and raw HTTP requests —
# keep the Chrome major version in sync with the Sec-Ch-Ua headers in
# fb_session.py when bumping it.
FB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
