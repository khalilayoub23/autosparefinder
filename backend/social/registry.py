"""
social/registry.py — one place that maps a platform name → its publisher.

Every publisher exposes the same contract:
    async def publish(content, *, media_url=None, hashtags=None, link=None) -> dict
    def is_configured() -> bool
    PLATFORM: str
so callers (the admin publish endpoint, NOA's approval→publish path) never branch per
platform — they just call dispatch(platform, ...). Adding a platform = add its module here.

telegram/tiktok keep their existing module APIs and are adapted to the uniform contract.

Author: AutoSpareFinder Agent — Last Updated: 2026-07-19
"""
import re

from social import (discord_publisher, facebook_publisher, instagram_publisher,
                    reddit_publisher, x_publisher)

# platform slug → module implementing publish()/is_configured()
_MODS = {
    discord_publisher.PLATFORM: discord_publisher,
    facebook_publisher.PLATFORM: facebook_publisher,
    instagram_publisher.PLATFORM: instagram_publisher,
    x_publisher.PLATFORM: x_publisher,
    reddit_publisher.PLATFORM: reddit_publisher,
}

# Platforms that CANNOT post text-only — they need an image/video (media_url).
MEDIA_REQUIRED = {"instagram", "tiktok"}

ALL_PLATFORMS = sorted(set(_MODS) | {"telegram", "tiktok"})


def _split_caption_hashtags(content: str):
    hashtags = [f"#{m.group(1)}" for m in re.finditer(r"#([A-Za-z0-9_֐-׿]+)", content or "")]
    caption = re.sub(r"\s+", " ", re.sub(r"#[^\s#]+", " ", content or "")).strip()
    return caption, hashtags


def is_configured(platform: str) -> bool:
    p = (platform or "").strip().lower()
    if p == "telegram":
        import os
        return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHANNEL_ID", "").strip())
    if p == "tiktok":
        import os
        return bool(os.getenv("TIKTOK_CLIENT_KEY", "").strip() and os.getenv("TIKTOK_CLIENT_SECRET", "").strip())
    mod = _MODS.get(p)
    return bool(mod and mod.is_configured())


def configured_platforms() -> list:
    return [p for p in ALL_PLATFORMS if is_configured(p)]


async def dispatch(platform: str, content: str, *, media_url: str | None = None,
                   hashtags: list | None = None, link: str | None = None) -> dict:
    """Publish `content` to one platform. Uniform result:
    {"ok", "id", "error", "not_configured"}."""
    p = (platform or "").strip().lower()

    # Legacy modules with their own signatures — adapt to the uniform result.
    if p == "telegram":
        from social.telegram_publisher import publish_to_telegram
        r = await publish_to_telegram(content)
        return {"ok": bool(r.get("ok")), "id": r.get("message_id"),
                "error": None if r.get("ok") else str(r.get("description") or r.get("error"))[:180],
                "not_configured": "not configured" in str(r.get("description", "")).lower()}
    if p == "tiktok":
        from social.tiktok_publisher import post_text_content
        caption, tags = _split_caption_hashtags(content)
        r = await post_text_content(caption=caption, hashtags=hashtags or tags)
        return {"ok": bool(r.get("ok")), "id": r.get("post_id") or r.get("publish_id"),
                "error": None if r.get("ok") else str(r.get("error"))[:180],
                "not_configured": str(r.get("error", "")).lower().startswith("no access token")}

    mod = _MODS.get(p)
    if not mod:
        return {"ok": False, "id": None, "not_configured": False, "error": f"unknown platform '{platform}'"}
    return await mod.publish(content, media_url=media_url, hashtags=hashtags, link=link)
