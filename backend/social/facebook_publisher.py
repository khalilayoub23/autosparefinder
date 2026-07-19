"""
social/facebook_publisher.py — post NOA content to a Facebook Page via the Meta Graph API.

Setup (see docs/SOCIAL_SETUP.md for the click-by-click):
  1. Create a Meta app at https://developers.facebook.com/ (Business type).
  2. Add the "Facebook Login" + "Pages" products; get a PAGE access token with
     scopes: pages_manage_posts, pages_read_engagement (long-lived token recommended).
  3. Find the Page ID (Page → About → Page transparency).
Env:
    FACEBOOK_PAGE_ID=<numeric page id>
    FACEBOOK_PAGE_TOKEN=<long-lived page access token>
    GRAPH_API_VERSION=v21.0            # optional, defaults below

Text+link → POST /{page_id}/feed ; image → POST /{page_id}/photos (caption+url).

Author: AutoSpareFinder Agent — Last Updated: 2026-07-19
"""
import os

import httpx

PLATFORM = "facebook"


def _cfg():
    return (os.getenv("FACEBOOK_PAGE_ID", "").strip(),
            os.getenv("FACEBOOK_PAGE_TOKEN", "").strip(),
            os.getenv("GRAPH_API_VERSION", "v21.0").strip())


def is_configured() -> bool:
    pid, tok, _ = _cfg()
    return bool(pid and tok)


async def publish(content: str, *, media_url: str | None = None,
                  hashtags: list | None = None, link: str | None = None) -> dict:
    pid, tok, ver = _cfg()
    if not (pid and tok):
        return {"ok": False, "id": None, "not_configured": True,
                "error": "FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN not set"}

    message = (content or "").strip()
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            if media_url:
                # Photo post — caption carries the copy; image is the part thumbnail.
                r = await client.post(
                    f"https://graph.facebook.com/{ver}/{pid}/photos",
                    data={"url": media_url, "caption": message, "access_token": tok},
                )
            else:
                data = {"message": message, "access_token": tok}
                if link:
                    data["link"] = link
                r = await client.post(f"https://graph.facebook.com/{ver}/{pid}/feed", data=data)
        j = r.json() if r.content else {}
        if r.status_code == 200 and (j.get("id") or j.get("post_id")):
            return {"ok": True, "id": j.get("post_id") or j.get("id"),
                    "error": None, "not_configured": False}
        return {"ok": False, "id": None, "not_configured": False,
                "error": f"facebook {r.status_code}: {str(j.get('error') or r.text)[:180]}"}
    except Exception as exc:
        return {"ok": False, "id": None, "not_configured": False, "error": str(exc)[:180]}
