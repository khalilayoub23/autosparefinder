"""WhatsApp provider — Baileys bridge (replaces Twilio)."""
import os
import base64
import httpx

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3001/send")
TYPING_URL = BRIDGE_URL.replace("/send", "/typing")


def _normalize_bridge_phone(to: str) -> str:
    phone = to.replace("whatsapp:", "").strip()
    if phone.startswith("+972"):
        return "0" + phone[4:]
    if phone.startswith("972"):
        return "0" + phone[3:]
    return phone


def parse_incoming(data: dict) -> dict:
    """Parse incoming JSON from Baileys bridge."""
    return {
        "from": data.get("from", ""),
        "body": data.get("body", ""),
        "profile_name": data.get("profile_name", ""),
        "media_kind": data.get("media_kind", ""),
        "media_base64": data.get("media_base64", ""),
        "media_mime": data.get("media_mime", ""),
        "media_caption": data.get("media_caption", ""),
        "media_too_large": bool(data.get("media_too_large")),
        "audio_ptt": bool(data.get("audio_ptt")),
    }


async def send_message(to: str, text: str, reply_jid: str = "") -> dict:
    """Send WhatsApp message via Baileys bridge."""
    payload = {"to": _normalize_bridge_phone(to), "text": text}
    if reply_jid:
        payload["reply_jid"] = reply_jid
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(BRIDGE_URL, json=payload)
        data = resp.json()
        return {"ok": data.get("ok", False), "error": data.get("error")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def send_image(to: str, image_bytes: bytes, mime_type: str = "image/jpeg", caption: str = "", reply_jid: str = "") -> dict:
    """Send WhatsApp image via Baileys bridge as base64 payload."""
    payload = {
        "to": _normalize_bridge_phone(to),
        "image_base64": base64.b64encode(image_bytes).decode("ascii"),
        "mime_type": mime_type,
        "caption": caption or "",
    }
    if reply_jid:
        payload["reply_jid"] = reply_jid

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(BRIDGE_URL, json=payload)
        data = resp.json()
        return {"ok": data.get("ok", False), "error": data.get("error")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def send_audio(
    to: str,
    audio_bytes: bytes,
    mime_type: str = "audio/ogg; codecs=opus",
    reply_jid: str = "",
    ptt: bool = True,
) -> dict:
    """Send WhatsApp audio/voice via Baileys bridge as base64 payload."""
    payload = {
        "to": _normalize_bridge_phone(to),
        "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
        "audio_mime": mime_type,
        "audio_ptt": bool(ptt),
    }
    if reply_jid:
        payload["reply_jid"] = reply_jid

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(BRIDGE_URL, json=payload)
        data = resp.json()
        return {"ok": data.get("ok", False), "error": data.get("error")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def send_typing(to: str, reply_jid: str = "") -> None:
    """Send WhatsApp typing indicator (composing presence) via Baileys bridge."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(TYPING_URL, json={"to": _normalize_bridge_phone(to), "reply_jid": reply_jid})
    except Exception:
        pass


def validate_signature(*args, **kwargs) -> bool:
    """No-op — Baileys uses internal auth, no webhook signature needed."""
    return True


def normalize_e164(phone: str) -> str:
    """Convert Israeli 05X format to E.164 (+972...)."""
    phone = phone.strip().lstrip("+")
    if phone.startswith("0"):
        phone = "972" + phone[1:]
    return "+" + phone


class WhatsAppProvider:
    async def send_message(self, to: str, text: str, reply_jid: str = "") -> dict:
        return await send_message(to, text, reply_jid=reply_jid)

    async def send_image(self, to: str, image_bytes: bytes, mime_type: str = "image/jpeg", caption: str = "", reply_jid: str = "") -> dict:
        return await send_image(to, image_bytes, mime_type=mime_type, caption=caption, reply_jid=reply_jid)

    async def send_audio(self, to: str, audio_bytes: bytes, mime_type: str = "audio/ogg; codecs=opus", reply_jid: str = "", ptt: bool = True) -> dict:
        return await send_audio(to, audio_bytes, mime_type=mime_type, reply_jid=reply_jid, ptt=ptt)

    async def send_typing(self, to: str, reply_jid: str = "") -> None:
        await send_typing(to, reply_jid=reply_jid)


_PROVIDER = WhatsAppProvider()


def get_whatsapp_provider() -> WhatsAppProvider:
    return _PROVIDER
