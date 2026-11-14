"""
social/tiktok_publisher.py — TikTok Content Posting API client.

Sandbox mode: set TIKTOK_SANDBOX=true in .env
Production:   set TIKTOK_SANDBOX=false after App Review approval.

Publishing flow (2026-11-13: USER token, not client_credentials - TikTok answers the app-level token with
401 access_token_invalid on /post/publish/*):
  1. The owner's stored OAuth user token (get_publish_token), granted with video.publish
  2. video_init — initialize upload session
  3. video_upload — upload video bytes
  4. video_publish — publish with caption + hashtags

Analytics flow (added 2026-08-16 — deep capability verification pass):
  TikTok's analytics/read endpoints (video.list, video.query) categorically
  reject an app-level client_credentials token — confirmed LIVE against the
  real API: POST /v2/video/list/ with a fresh client_credentials token
  returns HTTP 401 {"code":"access_token_invalid",...}. They require a
  USER-scoped token from the OAuth2 authorization_code flow (the owner
  clicking "Allow" on TikTok's own consent screen — this app IS already
  registered with TikTok for that flow: routes/webhooks.py's
  /api/v1/webhooks/tiktok-oauth callback exists and correctly exchanges a
  real ?code= for a token, but the result was never persisted (bare "# TODO"
  in the original code) — so even a completed consent was thrown away.
  Fixed here: get_authorize_url() builds the real consent URL (same
  redirect_uri the callback already expects), and
  store_user_token()/get_valid_user_access_token() persist/retrieve/refresh
  it via the EXISTING SystemSetting table (no new DB table/file). Until the
  owner completes that one consent click, get_valid_user_access_token()
  correctly returns None — this is not a workaround, it's the real state.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional
from urllib.parse import urlencode

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
_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
# Must exactly match the redirect_uri routes/webhooks.py's tiktok_oauth_callback
# already exchanges against — TikTok requires an exact string match.
REDIRECT_URI = "https://www.autosparefinder.co.il/api/v1/webhooks/tiktok-oauth"
# The app (developers.tiktok.com) is live with the Content Posting API in UPLOAD-AS-DRAFT mode: scopes
# user.info.basic + video.upload. Asking for video.publish / video.list before TikTok enables them gives
# error=invalid_scope. A Direct Post revision (video.publish) was submitted 2026-11-13; once TikTok approves it, set
# TIKTOK_DIRECT_POST=1, restart, and have the owner re-consent (a token only carries the scopes granted when it was
# issued). Until then a post is delivered to the creator's TikTok inbox as a draft and the creator taps Post.
_DIRECT_POST_PRIVACY = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")


def direct_post_enabled() -> bool:
    return os.getenv("TIKTOK_DIRECT_POST", "0").strip().lower() in ("1", "true", "yes", "on")


def publish_scope() -> str:
    return "video.publish" if direct_post_enabled() else "video.upload"


def consent_scopes() -> str:
    return f"user.info.basic,{publish_scope()}"


def direct_post_privacy() -> str:
    """Privacy of a Direct Post. An unaudited app may only post privately, so the default is SELF_ONLY; set
    TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE after TikTok audits the app. Anything unknown falls back to SELF_ONLY."""
    level = os.getenv("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY").strip().upper()
    return level if level in _DIRECT_POST_PRIVACY else "SELF_ONLY"


def needs_reconnect() -> str:
    return (f"no access token - TikTok needs the owner's consent ({publish_scope()}). "
            "Send 'טוקן טיקטוק' in the console and approve once.")


_TOKEN_SETTING_KEY = "tiktok_oauth_user_token"


def get_authorize_url(state: str = "") -> str:
    """The real TikTok consent URL the OWNER must open and click 'Allow' on —
    this is the one step that structurally cannot be automated (matches the
    platform's own standing rule: an OAuth consent click must be the owner's,
    never simulated). Everything up to this point is already correctly wired."""
    params = {
        "client_key": TIKTOK_CLIENT_KEY,
        "scope": consent_scopes(),
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
    }
    if state:
        params["state"] = state
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


async def store_user_token(
    db: Any, *, access_token: str, refresh_token: str, open_id: str,
    expires_in: int = 0, scope: str = "",
) -> None:
    """Persist the OAuth user token via the EXISTING generic SystemSetting
    table (system_settings, already live in the catalog DB) — no new table,
    no new file. Closes the 'TODO: persist' gap in
    routes/webhooks.py::tiktok_oauth_callback."""
    import json as _json
    import sqlalchemy as sa
    from datetime import datetime

    value = _json.dumps({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "open_id": open_id,
        "scope": scope,
        "obtained_at": time.time(),
        "expires_at": time.time() + expires_in if expires_in else 0,
    })
    existing = await db.execute(sa.text(
        "SELECT id FROM system_settings WHERE key = :k"
    ), {"k": _TOKEN_SETTING_KEY})
    row = existing.fetchone()
    if row:
        await db.execute(sa.text(
            "UPDATE system_settings SET value = :v, updated_at = :now WHERE key = :k"
        ), {"v": value, "now": datetime.utcnow(), "k": _TOKEN_SETTING_KEY})
    else:
        await db.execute(sa.text("""
            INSERT INTO system_settings (id, key, value, value_type, description, is_public, updated_at)
            VALUES (gen_random_uuid(), :k, :v, 'json', 'TikTok OAuth user token (analytics) — internal, not public', false, :now)
        """), {"k": _TOKEN_SETTING_KEY, "v": value, "now": datetime.utcnow()})
    await db.commit()
    logger.info("tiktok_publisher: user token stored open_id=%s scope=%s", open_id, scope)


async def get_stored_user_token(db: Any) -> Optional[dict]:
    """Returns the stored token dict, or None if the owner has never
    completed the consent flow (the real, current state today)."""
    import json as _json
    import sqlalchemy as sa

    row = (await db.execute(sa.text(
        "SELECT value FROM system_settings WHERE key = :k"
    ), {"k": _TOKEN_SETTING_KEY})).fetchone()
    if not row or not row[0]:
        return None
    try:
        return _json.loads(row[0])
    except Exception:
        return None


async def get_valid_user_access_token(db: Any) -> Optional[str]:
    """Returns a usable user-scoped access_token for analytics calls,
    refreshing via the stored refresh_token if expired. Returns None if the
    owner has never completed the OAuth consent flow — this is the real
    gate every analytics collector must check first."""
    tok = await get_stored_user_token(db)
    if not tok:
        return None
    if tok.get("expires_at") and time.time() < tok["expires_at"] - 60:
        return tok.get("access_token")
    refresh_token = tok.get("refresh_token")
    if not refresh_token:
        return tok.get("access_token")  # no refresh available; try what we have
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                _TOKEN_URL,
                data={
                    "client_key": TIKTOK_CLIENT_KEY,
                    "client_secret": TIKTOK_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        data = r.json()
        new_access = data.get("access_token")
        if not new_access:
            logger.warning("tiktok_publisher: token refresh failed: %s", data.get("error", data))
            return None
        await store_user_token(
            db,
            access_token=new_access,
            refresh_token=data.get("refresh_token", refresh_token),
            open_id=tok.get("open_id", ""),
            expires_in=data.get("expires_in", 0),
            scope=data.get("scope", tok.get("scope", "")),
        )
        return new_access
    except Exception as exc:
        logger.error("tiktok_publisher: token refresh error: %s", exc)
        return None


async def fetch_publish_status(publish_id: str, access_token: str) -> Optional[dict]:
    """POST /v2/post/publish/status/fetch/ — resolves our publish_id (captured
    at publish time) to TikTok's real video status, including the video_id
    once processing completes. Read-only."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{_BASE}/post/publish/status/fetch/",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"publish_id": publish_id},
            )
        data = r.json()
        if r.status_code != 200:
            logger.warning("tiktok_publisher: publish status fetch failed for %s: %s", publish_id, data.get("error", data))
            return None
        return data.get("data")
    except Exception as exc:
        logger.error("tiktok_publisher: publish status fetch error: %s", exc)
        return None


async def fetch_video_metrics(video_ids: list[str], access_token: str) -> dict[str, dict]:
    """POST /v2/video/query/ — real view/like/comment/share counts for
    specific video IDs. Read-only. Returns {video_id: metrics_dict}."""
    if not video_ids:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{_BASE}/video/query/",
                params={"fields": "id,view_count,like_count,comment_count,share_count"},
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"filters": {"video_ids": video_ids}},
            )
        data = r.json()
        if r.status_code != 200:
            logger.warning("tiktok_publisher: video metrics fetch failed: %s", data.get("error", data))
            return {}
        out = {}
        for v in (data.get("data", {}) or {}).get("videos", []):
            out[v.get("id")] = v
        return out
    except Exception as exc:
        logger.error("tiktok_publisher: video metrics fetch error: %s", exc)
        return {}


async def get_publish_token() -> tuple[Optional[str], str]:
    """(token, reason). The owner's stored user token, refreshed when expired, ONLY if it was granted
    video.publish. Never the app-level client_credentials token: that one cannot publish."""
    try:
        from BACKEND_DATABASE_MODELS import async_session_factory
        async with async_session_factory() as db:
            tok = await get_stored_user_token(db)
            if not tok:
                return None, needs_reconnect()
            if publish_scope() not in str(tok.get("scope") or ""):
                return None, needs_reconnect() + " (stored token lacks " + publish_scope() + ")"
            access = await get_valid_user_access_token(db)
            return (access, "") if access else (None, needs_reconnect() + " (refresh failed)")
    except Exception as exc:
        logger.error("get_publish_token failed: %s", exc)
        return None, f"no access token - token lookup failed: {str(exc)[:80]}"


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
    token, reason = (access_token, "") if access_token else await get_publish_token()
    if not token:
        return {"ok": False, "publish_id": None, "error": reason or needs_reconnect()}

    full_caption = caption + " " + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:

            # Step 1 — initialize upload. Direct Post (video.publish, TIKTOK_DIRECT_POST=1) publishes to the profile;
            # otherwise the draft-inbox endpoint is used and the creator taps Post in the TikTok app.
            direct = direct_post_enabled()
            body = {"source_info": {
                "source":     "FILE_UPLOAD",
                "video_size": len(video_bytes),
                "chunk_size": len(video_bytes),
                "total_chunk_count": 1,
            }}
            if direct:
                body["post_info"] = {
                    "title":          full_caption[:150],
                    "privacy_level":  direct_post_privacy(),
                    "disable_duet":   False,
                    "disable_stitch": False,
                    "disable_comment": False,
                    "video_cover_timestamp_ms": 1000,
                }
            init_resp = await client.post(
                f"{_BASE}/post/publish/{'video' if direct else 'inbox/video'}/init/",
                headers=headers,
                json=body,
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
        return {"ok": True, "publish_id": publish_id, "error": None, "draft": not direct}

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
    token, reason = (access_token, "") if access_token else await get_publish_token()
    if not token:
        return {"ok": False, "post_id": None, "error": reason or needs_reconnect()}

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
