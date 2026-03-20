"""Telegram Bot API publisher — uses raw httpx, no extra library required."""
import os
import httpx

_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
_API_BASE   = "https://api.telegram.org/bot{token}/{method}"


async def publish_to_telegram(content: str, image_url: str = None) -> dict:
    """POST content to the configured Telegram channel.

    Returns:
        {"ok": True,  "message_id": int}   on success
        {"ok": False, "error": str}         on failure / misconfiguration
    """
    if not _BOT_TOKEN or not _CHANNEL_ID:
        return {
            "ok": False,
            "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not configured",
        }

    if image_url:
        method  = "sendPhoto"
        payload = {"chat_id": _CHANNEL_ID, "photo": image_url, "caption": content}
    else:
        method  = "sendMessage"
        payload = {"chat_id": _CHANNEL_ID, "text": content}

    url = _API_BASE.format(token=_BOT_TOKEN, method=method)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
        data = resp.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}

    if data.get("ok"):
        return {"ok": True, "message_id": data["result"]["message_id"]}
    return {"ok": False, "error": data.get("description", "Unknown Telegram API error")}
