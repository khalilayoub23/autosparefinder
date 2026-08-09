"""
integrations/meta/facebook_pages.py — Facebook Page operations.

Re-export of social/facebook_pages.py with explicit __all__ for the structured
namespace. Implementation (retry, auth, rate-limit) lives in social/facebook_pages.py.
"""

from social.facebook_pages import (
    publish_post,
    publish_video,
    get_comments,
    reply_to_comment,
    get_post_insights,
    get_page_insights,
    get_recent_posts,
)

__all__ = [
    "publish_post",
    "publish_video",
    "get_comments",
    "reply_to_comment",
    "get_post_insights",
    "get_page_insights",
    "get_recent_posts",
]
