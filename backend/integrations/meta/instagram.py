"""
integrations/meta/instagram.py — Instagram Business Graph API adapter.

Uses the Facebook Graph API (instagram_business_account path) once the
IG token lands in env. Until then, every call returns {ok: False, not_configured: True}.

Official API: https://developers.facebook.com/docs/instagram-api

Required env vars (once Meta IG review clears):
  INSTAGRAM_USER_ID       — numeric IG user id (from /me?fields=instagram_business_account)
  INSTAGRAM_ACCESS_TOKEN  — user access token with instagram_basic + instagram_content_publish

Media container → publish flow (for image/video posts):
  1. POST /{user_id}/media   → container_id
  2. POST /{user_id}/media_publish {creation_id: container_id} → media_id
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("integrations.meta.instagram")


def _ig_configured() -> bool:
    return bool(
        os.getenv("INSTAGRAM_USER_ID", "").strip()
        and os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    )


def _ig_creds() -> tuple[str, str]:
    return (
        os.getenv("INSTAGRAM_USER_ID", ""),
        os.getenv("INSTAGRAM_ACCESS_TOKEN", ""),
    )


async def publish_image_post(
    caption: str,
    image_url: str,
    *,
    campaign_id: str | None = None,
) -> dict:
    """Publish an image post to the IG Business account.

    Returns: {ok, media_id, permalink, not_configured, error}
    """
    if not _ig_configured():
        return {"ok": False, "not_configured": True, "error": "INSTAGRAM_USER_ID or INSTAGRAM_ACCESS_TOKEN not set"}

    uid, token = _ig_creds()
    from social.meta_client import graph_post

    # Step 1: create media container
    container = await graph_post(
        f"{uid}/media",
        body={"image_url": image_url, "caption": caption},
        token=token,
    )
    cid = container.get("id")
    if not cid:
        return {"ok": False, "error": f"container creation failed: {container.get('error')}"}

    # Step 2: publish the container
    pub = await graph_post(
        f"{uid}/media_publish",
        body={"creation_id": cid},
        token=token,
    )
    media_id = pub.get("id")
    if not media_id:
        return {"ok": False, "error": f"publish failed: {pub.get('error')}"}

    log.info("instagram.publish_image_post: media_id=%s campaign=%s", media_id, campaign_id)
    return {"ok": True, "media_id": media_id, "not_configured": False}


async def get_media_insights(media_id: str) -> dict:
    """Retrieve engagement metrics for a published IG media object.

    Returns: {ok, impressions, reach, likes, comments, shares, saves, not_configured, error}
    """
    if not _ig_configured():
        return {"ok": False, "not_configured": True}

    uid, token = _ig_creds()
    from social.meta_client import graph_get

    data = await graph_get(
        f"{media_id}/insights",
        params={"metric": "impressions,reach,likes,comments,shares,saved"},
        token=token,
    )
    if "error" in data:
        return {"ok": False, "error": str(data["error"])}

    metrics: dict = {"ok": True, "not_configured": False}
    for item in data.get("data", []):
        metrics[item["name"]] = item.get("value", 0)
    return metrics


async def get_recent_media(limit: int = 10) -> dict:
    """List the most recent media objects on the IG Business account.

    Returns: {ok, media: [{id, caption, media_type, timestamp, permalink}], not_configured, error}
    """
    if not _ig_configured():
        return {"ok": False, "not_configured": True, "media": []}

    uid, token = _ig_creds()
    from social.meta_client import graph_get

    data = await graph_get(
        f"{uid}/media",
        params={"fields": "id,caption,media_type,timestamp,permalink", "limit": limit},
        token=token,
    )
    if "error" in data:
        return {"ok": False, "error": str(data["error"]), "media": []}

    return {"ok": True, "not_configured": False, "media": data.get("data", [])}
