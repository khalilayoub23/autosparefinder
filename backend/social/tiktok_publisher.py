"""
social/tiktok_publisher.py — TikTok Content Posting API client.

Sandbox mode: set TIKTOK_SANDBOX=true in .env
Production:   set TIKTOK_SANDBOX=false after App Review approval.

Flow:
  1. OAuth2 — exchange client_key/secret for access_token
  2. video_init — initialize upload session
  3. video_upload — upload video bytes
  4. video_publish — publish with caption + hashtags
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("tiktok_publisher")

TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
TIKTOK_SANDBOX       = os.getenv("TIKTOK_SANDBOX", "true").lower() == "true"

_BASE = (
    "https://open.tiktokapis.com/v2"
    if not TIKTOK_SANDBOX
    else "https://open.tiktokapis.com/v2"  # sandbox uses same base, different token
)


async def get_access_token() -> Optional[str]:
    """Client Credentials flow — returns app-level access token."""
    if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
        logger.error("TikTok credentials not set in .env")
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                data={
                    "client_key":    TIKTOK_CLIENT_KEY,
                    "client_secret": TIKTOK_CLIENT_SECRET,
                    "grant_type":    "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            logger.error("TikTok token error: %s", data)
        return token
    except Exception as exc:
        logger.error("get_access_token failed: %s", exc)
        return None


async def publish_video(
    video_bytes: bytes,
    caption: str,
    hashtags: list[str],
    access_token: Optional[str] = None,
) -> dict:
    """
    Upload and publish a video to TikTok.

    Returns {"ok": bool, "publish_id": str|None, "error": str|None}
    """
    token = access_token or await get_access_token()
    if not token:
        return {"ok": False, "publish_id": None, "error": "no access token"}

    full_caption = caption + " " + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:

            # Step 1 — initialize upload
            init_resp = await client.post(
                f"{_BASE}/post/publish/video/init/",
                headers=headers,
                json={
                    "post_info": {
                        "title":        full_caption[:150],
                        "privacy_level": "PUBLIC_TO_EVERYONE",
                        "disable_duet":  False,
                        "disable_stitch": False,
                        "disable_comment": False,
                        "video_cover_timestamp_ms": 1000,
                    },
                    "source_info": {
                        "source":     "FILE_UPLOAD",
                        "video_size": len(video_bytes),
                        "chunk_size": len(video_bytes),
                        "total_chunk_count": 1,
                    },
                },
            )
            init_data = init_resp.json()
            if init_resp.status_code != 200:
                return {"ok": False, "publish_id": None, "error": str(init_data)}

            publish_id  = init_data["data"]["publish_id"]
            upload_url  = init_data["data"]["upload_url"]

            # Step 2 — upload video bytes
            upload_resp = await client.put(
                upload_url,
                content=video_bytes,
                headers={
                    "Content-Type":  "video/mp4",
                    "Content-Range": f"bytes 0-{len(video_bytes)-1}/{len(video_bytes)}",
                },
            )
            if upload_resp.status_code not in (200, 201):
                return {"ok": False, "publish_id": publish_id,
                        "error": f"upload failed: {upload_resp.status_code}"}

        logger.info("tiktok_publisher: published video publish_id=%s sandbox=%s",
                    publish_id, TIKTOK_SANDBOX)
        return {"ok": True, "publish_id": publish_id, "error": None}

    except Exception as exc:
        logger.error("publish_video failed: %s", exc)
        return {"ok": False, "publish_id": None, "error": str(exc)}


async def post_text_content(
    caption: str,
    hashtags: list[str],
    access_token: Optional[str] = None,
) -> dict:
    """
    Post text-only content (photo post) — useful for quick marketing posts
    without video production.
    """
    token = access_token or await get_access_token()
    if not token:
        return {"ok": False, "post_id": None, "error": "no access token"}

    full_caption = caption + " " + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{_BASE}/post/publish/content/init/",
                headers=headers,
                json={
                    "post_info": {
                        "title":         full_caption[:150],
                        "privacy_level": "PUBLIC_TO_EVERYONE",
                    },
                    "source_info": {"source": "PULL_FROM_URL"},
                },
            )
        data = resp.json()
        if resp.status_code != 200:
            return {"ok": False, "post_id": None, "error": str(data)}
        return {"ok": True, "post_id": data.get("data", {}).get("publish_id"), "error": None}
    except Exception as exc:
        logger.error("post_text_content failed: %s", exc)
        return {"ok": False, "post_id": None, "error": str(exc)}
