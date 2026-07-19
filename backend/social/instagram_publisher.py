"""
social/instagram_publisher.py — post to Instagram via the Meta Graph API (IG Business).

Instagram posts REQUIRE an image or video — there is no text-only post. NOA supplies a
clean part thumbnail (from the thumbnail pipeline) as media_url; the copy + hashtags become
the caption. Two-step Graph flow: create a media container, then publish it.

Setup (docs/SOCIAL_SETUP.md):
  1. Same Meta app as Facebook. Link an Instagram *Business/Creator* account to the Page.
  2. Token needs: instagram_basic, instagram_content_publish, pages_read_engagement.
  3. Get the IG user id: GET /{page_id}?fields=instagram_business_account&access_token=...
Env:
    INSTAGRAM_USER_ID=<ig business account id>
    INSTAGRAM_ACCESS_TOKEN=<token>     # may reuse FACEBOOK_PAGE_TOKEN
    GRAPH_API_VERSION=v21.0            # optional

Note: media_url must be a PUBLIC https image Meta can fetch — our thumbnails qualify
(served + Cloudflare-cached at https://autosparefinder.co.il/api/v1/thumbnails/...).

Author: AutoSpareFinder Agent — Last Updated: 2026-07-19
"""
import os

import httpx

PLATFORM = "instagram"


def _cfg():
    return (os.getenv("INSTAGRAM_USER_ID", "").strip(),
            (os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
             or os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()),
            os.getenv("GRAPH_API_VERSION", "v21.0").strip())


def is_configured() -> bool:
    uid, tok, _ = _cfg()
    return bool(uid and tok)


async def publish(content: str, *, media_url: str | None = None,
                  hashtags: list | None = None, link: str | None = None) -> dict:
    uid, tok, ver = _cfg()
    if not (uid and tok):
        return {"ok": False, "id": None, "not_configured": True,
                "error": "INSTAGRAM_USER_ID / access token not set"}
    if not media_url:
        return {"ok": False, "id": None, "not_configured": False,
                "error": "instagram requires an image (media_url) — no text-only posts"}

    caption = (content or "").strip()
    if hashtags:
        tags = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
        if tags not in caption:
            caption = f"{caption}\n\n{tags}".strip()
    base = f"https://graph.facebook.com/{ver}/{uid}"
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            # Step 1 — create the media container
            r1 = await client.post(f"{base}/media",
                                   data={"image_url": media_url, "caption": caption, "access_token": tok})
            j1 = r1.json() if r1.content else {}
            creation_id = j1.get("id")
            if r1.status_code != 200 or not creation_id:
                return {"ok": False, "id": None, "not_configured": False,
                        "error": f"instagram container {r1.status_code}: {str(j1.get('error') or r1.text)[:160]}"}
            # Step 2 — publish the container
            r2 = await client.post(f"{base}/media_publish",
                                   data={"creation_id": creation_id, "access_token": tok})
            j2 = r2.json() if r2.content else {}
            if r2.status_code == 200 and j2.get("id"):
                return {"ok": True, "id": j2["id"], "error": None, "not_configured": False}
            return {"ok": False, "id": None, "not_configured": False,
                    "error": f"instagram publish {r2.status_code}: {str(j2.get('error') or r2.text)[:160]}"}
    except Exception as exc:
        return {"ok": False, "id": None, "not_configured": False, "error": str(exc)[:180]}
