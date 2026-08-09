"""
integrations/meta/graph_client.py — Raw Meta Graph API HTTP client.

Thin re-export of social/meta_client.graph_get and graph_post so the
integrations/ namespace is self-contained. The actual implementation
(retry, rate-limiting, exponential backoff) lives in social/meta_client.py.
"""

from social.meta_client import graph_get, graph_post

__all__ = ["graph_get", "graph_post"]
