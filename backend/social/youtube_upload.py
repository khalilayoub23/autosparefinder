"""
social/youtube_upload.py — upload a Short/video to NOA's YouTube channel (Data API v3).

Uses the same OAuth refresh token as the engagement adapter (`youtube.force-ssl` scope,
which covers uploads). Resumable upload (2 steps: init → PUT bytes). Vertical ≤60s video
+ #Shorts in the title/description => YouTube treats it as a Short.

    upload_short(video_path, title, description, tags, privacy="private") -> {ok,id,url,error}

privacy: "private" (owner-only, good for tests) | "unlisted" | "public".
Last Updated: 2026-07-26
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"


def upload_short(video_path: str, title: str, description: str = "",
                 tags: Optional[List[str]] = None, privacy: str = "private") -> Dict[str, Any]:
    from social.engagement import _yt_token, youtube_configured
    if not youtube_configured():
        return {"ok": False, "error": "youtube not configured"}
    tok = _yt_token()
    if not tok:
        return {"ok": False, "error": "youtube auth failed"}
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"file not found: {video_path}"}
    size = os.path.getsize(video_path)
    meta = {
        "snippet": {
            "title": title[:100],
            "description": (description or "")[:4900],
            "tags": tags or ["autosparefinder", "carparts", "shorts"],
            "categoryId": "2",  # Autos & Vehicles
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    body = json.dumps(meta).encode()
    # step 1 — resumable init
    try:
        req = urllib.request.Request(
            f"{_UPLOAD}?uploadType=resumable&part=snippet,status", data=body, method="POST",
            headers={"Authorization": f"Bearer {tok}",
                     "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/mp4",
                     "X-Upload-Content-Length": str(size)})
        with urllib.request.urlopen(req, timeout=60) as r:
            up_url = r.getheader("Location")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"init {e.code}: {e.read().decode('utf-8','replace')[:250]}"}
    if not up_url:
        return {"ok": False, "error": "no resumable upload URL returned"}
    # step 2 — PUT the bytes
    try:
        with open(video_path, "rb") as f:
            data = f.read()
        put = urllib.request.Request(up_url, data=data, method="PUT",
                                     headers={"Authorization": f"Bearer {tok}",
                                              "Content-Type": "video/mp4",
                                              "Content-Length": str(size)})
        with urllib.request.urlopen(put, timeout=300) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"upload {e.code}: {e.read().decode('utf-8','replace')[:250]}"}
    vid = res.get("id")
    return {"ok": bool(vid), "id": vid,
            "url": (f"https://www.youtube.com/shorts/{vid}" if vid else None),
            "privacy": privacy, "error": None if vid else "no id in response"}
