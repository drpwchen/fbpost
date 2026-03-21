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
