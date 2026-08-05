"""Telegram Bot API publisher — uses raw httpx, no extra library required."""
import html
import os
import re
import httpx
from typing import Optional, Union

_API_BASE   = "https://api.telegram.org/bot{token}/{method}"

_TG_SITE_URL = "https://autosparefinder.co.il/?utm_source=telegram&utm_medium=social&utm_campaign=noa"


def _linkify_for_telegram(content: str) -> str:
    """Make the CTA a PRESSABLE hyperlink on Telegram (owner 2026-08-04: "pressable words
    that the link is installed in"). Telegram sends with parse_mode=HTML, so we (1) HTML-
    escape the body, then (2) wrap the CTA line — the "👈 … autosparefinder.co.il" phrase,
    or a bare domain elsewhere — in an <a href> anchor. Only Telegram supports this;
    Facebook/Instagram/TikTok captions are plain text and keep the clean bare domain."""
    esc = html.escape(content or "", quote=False)   # neutralise stray < > &
    anchored = re.sub(
        r"👈[^\n]*?autosparefinder\.co\.il",
        lambda m: f'<a href="{_TG_SITE_URL}">{m.group(0)}</a>',
        esc, count=1,
    )
    if anchored == esc:   # no CTA line — anchor a bare domain if present
        anchored = re.sub(
            r"\bautosparefinder\.co\.il\b",
            f'<a href="{_TG_SITE_URL}">autosparefinder.co.il</a>',
            esc, count=1,
        )
    return anchored


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


async def _telegram_api_post_multipart(method: str, data: dict, files: dict) -> dict:
    token = _bot_token()
    if not token:
        return {
            "ok": False,
            "description": "TELEGRAM_BOT_TOKEN not configured",
            "status_code": 500,
        }

    url = _API_BASE.format(token=token, method=method)
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, data=data, files=files)
        parsed = resp.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "description": str(exc), "status_code": 502}
    except ValueError:
        return {
            "ok": False,
            "description": "Telegram API returned non-JSON response",
            "status_code": resp.status_code,
        }

    if "ok" not in parsed:
        parsed["ok"] = resp.status_code < 400
    parsed.setdefault("status_code", resp.status_code)
    return parsed


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


async def send_telegram_photo(
    chat_id: Union[int, str],
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    caption: str = "",
) -> dict:
    filename = "chat_image.jpg"
    if mime_type == "image/png":
        filename = "chat_image.png"
    elif mime_type == "image/webp":
        filename = "chat_image.webp"
    elif mime_type == "image/gif":
        filename = "chat_image.gif"

    payload = {
        "chat_id": str(chat_id),
        "caption": caption or "",
        "parse_mode": "HTML",
    }
    result = await _telegram_api_post_multipart(
        "sendPhoto",
        data=payload,
        files={"photo": (filename, image_bytes, mime_type)},
    )
    if result.get("ok"):
        message = result.get("result", {})
        return {"ok": True, "message_id": message.get("message_id")}
    return {"ok": False, "error": result.get("description", "Unknown Telegram API error")}


async def send_telegram_chat_action(chat_id: Union[int, str], action: str = "typing") -> dict:
    """Send Telegram chat action (e.g. typing/upload_photo) for UX feedback."""
    result = await _telegram_api_post(
        "sendChatAction",
        {"chat_id": str(chat_id), "action": action},
    )
    if result.get("ok"):
        return {"ok": True}
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

    tg_content = _linkify_for_telegram(content)   # pressable CTA hyperlink (Telegram only)
    if image_url:
        result = await _telegram_api_post(
            "sendPhoto",
            {"chat_id": channel_id, "photo": image_url, "caption": tg_content,
             "parse_mode": "HTML"},
        )
    else:
        return await send_telegram_message(channel_id, tg_content)

    if result.get("ok"):
        return {"ok": True, "message_id": result.get("result", {}).get("message_id")}
    return {"ok": False, "error": result.get("description", "Unknown Telegram API error")}
