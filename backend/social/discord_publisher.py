"""
social/discord_publisher.py — post NOA content to a Discord channel via an Incoming Webhook.

Simplest of all the platforms: NO app / OAuth. Create a webhook on the channel
(Server Settings → Integrations → Webhooks → New Webhook → Copy URL) and set:
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>

Uniform publisher contract (shared by every social/*_publisher.py):
    async def publish(content, *, media_url=None, hashtags=None, link=None) -> dict
    → {"ok": bool, "id": <post id|None>, "error": <str|None>, "not_configured": <bool>}

Author: AutoSpareFinder Agent — Last Updated: 2026-07-19
"""
import os

import httpx

PLATFORM = "discord"


def _webhook_url() -> str:
    return os.getenv("DISCORD_WEBHOOK_URL", "").strip()


def is_configured() -> bool:
    return bool(_webhook_url())


async def publish(content: str, *, media_url: str | None = None,
                  hashtags: list | None = None, link: str | None = None) -> dict:
    url = _webhook_url()
    if not url:
        return {"ok": False, "id": None, "not_configured": True,
                "error": "DISCORD_WEBHOOK_URL not set"}

    body = (content or "").strip()
    if link and link not in body:
        body = f"{body}\n{link}".strip()
    payload: dict = {"content": body[:2000]}  # Discord hard limit 2000 chars
    if media_url:
        payload["embeds"] = [{"image": {"url": media_url}}]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # ?wait=true → Discord returns the created message object (so we get an id)
            r = await client.post(url + ("&" if "?" in url else "?") + "wait=true", json=payload)
        if r.status_code in (200, 204):
            msg_id = None
            try:
                msg_id = r.json().get("id")
            except Exception:
                pass
            return {"ok": True, "id": msg_id or "sent", "error": None, "not_configured": False}
        return {"ok": False, "id": None, "not_configured": False,
                "error": f"discord {r.status_code}: {r.text[:150]}"}
    except Exception as exc:
        return {"ok": False, "id": None, "not_configured": False, "error": str(exc)[:180]}
