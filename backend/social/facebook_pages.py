"""
Script: social/facebook_pages.py
Purpose: Full Facebook Page operations via the Meta Graph API.
         Extends the basic facebook_publisher.py with insights collection,
         comment management, video publishing, and post retrieval.
         All HTTP calls go through social/meta_client.py (rate limiting + retries).

Process:
  publish_post()      — text or image post (wraps existing publisher for consistency)
  publish_video()     — upload a video via the resumable Graph video API
  get_post_insights() — reach, impressions, engagement for a published post
  get_page_insights() — aggregate page-level metrics for a date range
  get_comments()      — list comments on a specific post
  reply_to_comment()  — publish a reply to a comment
  get_recent_posts()  — list the page's latest posts

Data Imported/Modified: reads engagement from Meta; writes to engagement_events via
                        feedback_analyzer (not directly here).
Data Sources: https://graph.facebook.com/
Last Updated: 2026-08-06
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from social.meta_client import (
    MetaAPIError,
    facebook_configured,
    graph_get,
    graph_post,
    _cfg_facebook,
)

log = logging.getLogger("facebook_pages")

# Fields fetched per post insight; must be approved for the page token's scopes.
_POST_INSIGHT_METRICS = (
    "post_impressions,"
    "post_impressions_unique,"
    "post_engaged_users,"
    "post_clicks,"
    "post_reactions_like_total,"
    "post_video_views"            # 0 on non-video posts — safe to request
)

_PAGE_INSIGHT_METRICS = (
    "page_impressions,"
    "page_impressions_unique,"
    "page_engaged_users,"
    "page_fan_adds_unique"
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _ok(post_id: str) -> dict:
    return {"ok": True, "id": post_id, "error": None, "not_configured": False}


def _fail(err: str, not_configured: bool = False) -> dict:
    return {"ok": False, "id": None, "error": err[:220], "not_configured": not_configured}


def _not_cfg() -> dict:
    return _fail("FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN not set", not_configured=True)


# ── Publishing ────────────────────────────────────────────────────────────────

async def publish_post(
    content: str,
    *,
    media_url: str | None = None,
    link: str | None = None,
    hashtags: list | None = None,
) -> dict:
    """Publish a text/image post to the Facebook Page.

    Delegates to facebook_publisher.publish() so that the existing registry
    dispatch path continues to work unchanged. Returns uniform result dict.
    """
    from social import facebook_publisher
    return await facebook_publisher.publish(
        content, media_url=media_url, hashtags=hashtags, link=link
    )


async def publish_video(
    video_path: str,
    *,
    title: str = "",
    description: str = "",
    scheduled_publish_time: int | None = None,
) -> dict:
    """Upload a video to the Facebook Page via the non-resumable /videos endpoint.

    video_path must be a local file path accessible by the backend container.
    For large videos (>100 MB) the resumable endpoint is required — not implemented
    here; add if NOA's video_gen.py starts producing files that large.
    """
    pid, tok, ver = _cfg_facebook()
    if not (pid and tok):
        return _not_cfg()
    try:
        import httpx
        with open(video_path, "rb") as fh:
            video_bytes = fh.read()
        data: dict[str, Any] = {
            "access_token": tok,
            "description": description or "",
        }
        if title:
            data["title"] = title
        if scheduled_publish_time:
            data["published"] = "false"
            data["scheduled_publish_time"] = str(scheduled_publish_time)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"https://graph.facebook.com/{ver}/{pid}/videos",
                data=data,
                files={"source": (os.path.basename(video_path), video_bytes, "video/mp4")},
            )
        body = resp.json() if resp.content else {}
        if resp.status_code == 200 and body.get("id"):
            return _ok(body["id"])
        err_msg = str((body.get("error") or {}).get("message") or resp.text)[:200]
        return _fail(f"facebook video {resp.status_code}: {err_msg}")
    except FileNotFoundError:
        return _fail(f"video file not found: {video_path}")
    except Exception as exc:
        return _fail(str(exc)[:200])


# ── Insights ──────────────────────────────────────────────────────────────────

async def get_post_insights(post_id: str) -> dict:
    """Fetch engagement metrics for one published post.

    Returns:
        {
            "ok": bool,
            "post_id": str,
            "impressions": int,          # total times shown
            "reach": int,                # unique accounts
            "engaged_users": int,
            "clicks": int,
            "likes": int,
            "video_views": int,
            "error": str|None,
        }
    """
    _, tok, _ = _cfg_facebook()
    if not tok:
        return {"ok": False, "error": "not configured", "post_id": post_id}
    try:
        data = await graph_get(
            f"{post_id}/insights",
            token=tok,
            params={"metric": _POST_INSIGHT_METRICS, "period": "lifetime"},
            endpoint_key="insights",
        )
        values: dict[str, int] = {}
        for row in data.get("data", []):
            name = row.get("name", "")
            vals = row.get("values", [])
            if vals:
                values[name] = int(vals[-1].get("value") or 0)
        return {
            "ok": True,
            "post_id": post_id,
            "impressions": values.get("post_impressions", 0),
            "reach": values.get("post_impressions_unique", 0),
            "engaged_users": values.get("post_engaged_users", 0),
            "clicks": values.get("post_clicks", 0),
            "likes": values.get("post_reactions_like_total", 0),
            "video_views": values.get("post_video_views", 0),
            "error": None,
        }
    except MetaAPIError as exc:
        log.warning("get_post_insights %s → MetaAPIError: %s", post_id, exc)
        return {"ok": False, "post_id": post_id, "error": str(exc)[:200]}
    except Exception as exc:
        return {"ok": False, "post_id": post_id, "error": str(exc)[:200]}


async def get_page_insights(
    since: datetime,
    until: datetime,
    *,
    metrics: str = _PAGE_INSIGHT_METRICS,
) -> dict:
    """Aggregate page-level metrics for a date range.

    Returns {"ok", "data": [{name, values:[{value,end_time}]}], "error"}
    """
    pid, tok, _ = _cfg_facebook()
    if not (pid and tok):
        return {"ok": False, "data": [], "error": "not configured"}
    try:
        resp = await graph_get(
            f"{pid}/insights",
            token=tok,
            params={
                "metric": metrics,
                "period": "day",
                "since": int(since.timestamp()),
                "until": int(until.timestamp()),
            },
            endpoint_key="insights",
        )
        return {"ok": True, "data": resp.get("data", []), "error": None}
    except MetaAPIError as exc:
        return {"ok": False, "data": [], "error": str(exc)[:200]}
    except Exception as exc:
        return {"ok": False, "data": [], "error": str(exc)[:200]}


# ── Comments ──────────────────────────────────────────────────────────────────

async def get_comments(post_id: str, *, limit: int = 25) -> dict:
    """Fetch top-level comments on a post.

    Returns {"ok", "comments": [{id, from, message, created_time}], "error"}
    """
    _, tok, _ = _cfg_facebook()
    if not tok:
        return {"ok": False, "comments": [], "error": "not configured"}
    try:
        resp = await graph_get(
            f"{post_id}/comments",
            token=tok,
            params={
                "fields": "id,from{name},message,created_time",
                "limit": str(limit),
            },
            endpoint_key="comments",
        )
        return {"ok": True, "comments": resp.get("data", []), "error": None}
    except MetaAPIError as exc:
        return {"ok": False, "comments": [], "error": str(exc)[:200]}
    except Exception as exc:
        return {"ok": False, "comments": [], "error": str(exc)[:200]}


async def reply_to_comment(comment_id: str, message: str) -> dict:
    """Publish a reply to a specific comment.

    Returns {"ok", "id": reply_comment_id, "error"}
    """
    _, tok, ver = _cfg_facebook()
    if not tok:
        return _not_cfg()
    try:
        resp = await graph_post(
            f"{comment_id}/comments",
            token=tok,
            data={"message": message},
            endpoint_key="replies",
        )
        reply_id = resp.get("id")
        if reply_id:
            return _ok(reply_id)
        return _fail(f"unexpected response: {resp}")
    except MetaAPIError as exc:
        return _fail(str(exc)[:200])
    except Exception as exc:
        return _fail(str(exc)[:200])


# ── Post retrieval ────────────────────────────────────────────────────────────

async def get_recent_posts(*, limit: int = 10) -> dict:
    """Fetch the page's most recent posts with basic engagement counts.

    Returns {"ok", "posts": [{id, message, created_time, likes, comments, shares}], "error"}
    """
    pid, tok, _ = _cfg_facebook()
    if not (pid and tok):
        return {"ok": False, "posts": [], "error": "not configured"}
    try:
        resp = await graph_get(
            f"{pid}/posts",
            token=tok,
            params={
                "fields": "id,message,created_time,likes.summary(true),comments.summary(true),shares",
                "limit": str(limit),
            },
            endpoint_key="default",
        )
        posts = []
        for raw in resp.get("data", []):
            posts.append({
                "id": raw.get("id"),
                "message": (raw.get("message") or "")[:300],
                "created_time": raw.get("created_time"),
                "likes": (raw.get("likes") or {}).get("summary", {}).get("total_count", 0),
                "comments": (raw.get("comments") or {}).get("summary", {}).get("total_count", 0),
                "shares": (raw.get("shares") or {}).get("count", 0),
            })
        return {"ok": True, "posts": posts, "error": None}
    except MetaAPIError as exc:
        return {"ok": False, "posts": [], "error": str(exc)[:200]}
    except Exception as exc:
        return {"ok": False, "posts": [], "error": str(exc)[:200]}
