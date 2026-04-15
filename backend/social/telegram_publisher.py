"""Telegram Bot API publisher — uses raw httpx, no extra library required."""
import os
import httpx
from typing import Optional, Union

_API_BASE   = "https://api.telegram.org/bot{token}/{method}"


def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _channel_id() -> str:
    return os.getenv("TELEGRAM_CHANNEL_ID", "").strip()


async def _telegram_api_post(method: str, payload: dict) -> dict:
    token = _bot_token()
    if not token:
        return {
            "ok": False,
            "description": "TELEGRAM_BOT_TOKEN not configured",
            "status_code": 500,
        }

    url = _API_BASE.format(token=token, method=method)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
        data = resp.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "description": str(exc), "status_code": 502}
    except ValueError:
        return {
            "ok": False,
            "description": "Telegram API returned non-JSON response",
            "status_code": resp.status_code,
        }

    if "ok" not in data:
        data["ok"] = resp.status_code < 400
    data.setdefault("status_code", resp.status_code)
    return data


async def send_telegram_message(chat_id: Union[int, str], text: str) -> dict:
    """Send a plain text message to a specific Telegram chat."""
    result = await _telegram_api_post(
        "sendMessage",
        {"chat_id": str(chat_id), "text": text, "parse_mode": "HTML"},
    )
    if result.get("ok"):
        message = result.get("result", {})
        return {"ok": True, "message_id": message.get("message_id")}
    return {"ok": False, "error": result.get("description", "Unknown Telegram API error")}


async def set_telegram_webhook(webhook_url: str, secret_token: Optional[str] = None) -> dict:
    """Configure Telegram webhook URL for this bot token."""
    payload = {
        "url": webhook_url,
        "allowed_updates": ["message", "edited_message"],
    }
    if secret_token:
        payload["secret_token"] = secret_token

    result = await _telegram_api_post("setWebhook", payload)
    if result.get("ok"):
        return {
            "ok": True,
            "description": result.get("description", "Webhook configured"),
        }
    return {"ok": False, "error": result.get("description", "Unknown Telegram API error")}


async def publish_to_telegram(content: str, image_url: str = None) -> dict:
    """POST content to the configured Telegram channel.

    Returns:
        {"ok": True,  "message_id": int}   on success
        {"ok": False, "error": str}         on failure / misconfiguration
    """
    channel_id = _channel_id()
    if not _bot_token() or not channel_id:
        return {
            "ok": False,
            "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not configured",
        }

    if image_url:
        result = await _telegram_api_post(
            "sendPhoto",
            {"chat_id": channel_id, "photo": image_url, "caption": content},
        )
    else:
        return await send_telegram_message(channel_id, content)

    if result.get("ok"):
        return {"ok": True, "message_id": result.get("result", {}).get("message_id")}
    return {"ok": False, "error": result.get("description", "Unknown Telegram API error")}
