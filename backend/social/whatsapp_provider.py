"""WhatsApp provider — Baileys bridge (replaces Twilio)."""
import os
import httpx

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3001/send")
TYPING_URL = BRIDGE_URL.replace("/send", "/typing")


def parse_incoming(data: dict) -> dict:
    """Parse incoming JSON from Baileys bridge."""
    return {
        "from": data.get("from", ""),
        "body": data.get("body", ""),
        "profile_name": data.get("profile_name", ""),
    }


async def send_message(to: str, text: str, reply_jid: str = "") -> dict:
    """Send WhatsApp message via Baileys bridge."""
    # Normalize: +9725X or 9725X → 05X
    phone = to.replace("whatsapp:", "").strip()
    if phone.startswith("+972"):
        phone = "0" + phone[4:]
    elif phone.startswith("972"):
        phone = "0" + phone[3:]
    payload = {"to": phone, "text": text}
    if reply_jid:
        payload["reply_jid"] = reply_jid
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(BRIDGE_URL, json=payload)
        data = resp.json()
        return {"ok": data.get("ok", False), "error": data.get("error")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def send_typing(to: str, reply_jid: str = "") -> None:
    """Send WhatsApp typing indicator (composing presence) via Baileys bridge."""
    phone = to.replace("whatsapp:", "").strip()
    if phone.startswith("+972"):
        phone = "0" + phone[4:]
    elif phone.startswith("972"):
        phone = "0" + phone[3:]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(TYPING_URL, json={"to": phone, "reply_jid": reply_jid})
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
