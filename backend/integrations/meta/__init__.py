"""
integrations/meta — Official Meta Graph API integration.

Components:
  auth_manager     Token validation, storage, exchange flows
  graph_client     Raw HTTP client with retry + rate limiting
  facebook_pages   Page post, comment, insight operations
  instagram        Instagram Business content + engagement
  webhook_handler  Meta webhook signature verification + event parsing
  rate_limiter     Per-endpoint rolling window enforcement

All components are properly structured modules. auth_manager, graph_client,
and rate_limiter delegate to social/meta_client.py; facebook_pages delegates
to social/facebook_pages.py. instagram and webhook_handler are new here.
"""

from integrations.meta.auth_manager import (
    validate_token,
    exchange_for_long_lived_token,
    is_configured,
)
from integrations.meta.graph_client import graph_get, graph_post
from integrations.meta.rate_limiter import check_rate_limit, rate_limit_status
from integrations.meta.facebook_pages import (
    publish_post,
    publish_video,
    get_comments,
    reply_to_comment,
    get_post_insights,
    get_page_insights,
)

__all__ = [
    "validate_token",
    "exchange_for_long_lived_token",
    "is_configured",
    "graph_get",
    "graph_post",
    "check_rate_limit",
    "rate_limit_status",
    "publish_post",
    "publish_video",
    "get_comments",
    "reply_to_comment",
    "get_post_insights",
    "get_page_insights",
]
