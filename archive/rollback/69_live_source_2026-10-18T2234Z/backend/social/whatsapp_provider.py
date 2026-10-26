"""WhatsApp provider — Baileys bridge (replaces Twilio)."""
import asyncio
import os
import base64
import httpx

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3001/send")
TYPING_URL = BRIDGE_URL.replace("/send", "/typing")
DELETE_URL = BRIDGE_URL.replace("/send", "/delete")


def _il_digits(p: str) -> str:
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    return "972" + d[1:] if d.startswith("0") else d


def _owner_lid_jid(phone: str) -> str:
    """The owner's LID JID when `phone` is the owner's number, else "".

    Baileys 6.17.16 has no phone-number↔LID mapping. The owner's devices are LID-addressed,
    so a message encrypted on the phone-number session failed to decrypt on EVERY send
    (437/437 drew a retry from devices 0 and 33 → "בהמתנה להודעה זו"), while the retry,
    answered over the LID session, decrypted. A message sent straight to the LID drew no
    retry and the owner confirmed it displayed (2026-10-18). Rollback:
    OWNER_WHATSAPP_ROUTE_VIA_LID=0."""
    if os.getenv("OWNER_WHATSAPP_ROUTE_VIA_LID", "1").strip().lower() in ("0", "false", "no"):
        return ""
    lid = os.getenv("OWNER_WHATSAPP_LID", "").split(",")[0].strip()
    owner = _il_digits(os.getenv("OWNER_WHATSAPP_PHONE", ""))
    if not lid or not owner or _il_digits(phone) != owner:
        return ""
    return lid if lid.endswith("@lid") else f"{_il_digits(lid)}@lid"


def _route_reply_jid(reply_jid: str) -> str:
    if reply_jid.endswith("@s.whatsapp.net"):
        return _owner_lid_jid(reply_jid.split("@")[0]) or reply_jid
    return reply_jid


def _normalize_bridge_phone(to: str) -> str:
    phone = to.replace("whatsapp:", "").strip()
    if phone.endswith("@s.whatsapp.net"):
        phone_digits = phone.split("@")[0]
        if _owner_lid_jid(phone_digits):
            return _owner_lid_jid(phone_digits)
    elif "@" not in phone and _owner_lid_jid(phone):
        return _owner_lid_jid(phone)
    # A full JID (group "…@g.us" / user "…@s.whatsapp.net" / "…@lid") passes through
    # untouched — the bridge sends to it directly. Used for the owner updates group.
    if "@" in phone:
        return phone
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


# The bridge reconnects to WhatsApp 2-11 times a day (428/503 from WhatsApp's servers;
# median 1.1 s, longest 7.6 s over 121 events since 2026-09-21). A send arriving in that
# gap got a 503 and was dropped — there was no retry. Retry ONLY when nothing can have been
# sent: the bridge refused before calling WhatsApp (transient readiness state) or could not
# be reached. Never after a timeout or a 500 — the message may already be out.
_TRANSIENT_BRIDGE_ERRORS = (
    "WhatsApp socket not created yet",
    "WhatsApp socket connecting",
    "WhatsApp disconnected — reconnecting",
    "WhatsApp socket closed — reconnecting",
)
SEND_RETRY_BUDGET_S = float(os.getenv("WA_SEND_RETRY_BUDGET_S", "15"))


def _retryable(status: int, data: dict) -> bool:
    return status == 503 and (data.get("retryable") is True
                              or str(data.get("error") or "") in _TRANSIENT_BRIDGE_ERRORS)


async def _post_send(payload: dict, timeout: float) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SEND_RETRY_BUDGET_S
    delay = 1.0
    while True:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(BRIDGE_URL, json=payload)
            data = resp.json()
            if not _retryable(resp.status_code, data):
                return data
            last = data
        except httpx.ConnectError as exc:
            last = {"ok": False, "error": f"bridge unreachable: {exc}"}
        if loop.time() + delay > deadline:
            return last
        await asyncio.sleep(delay)
        delay = min(delay * 2, 5.0)


async def send_message(to: str, text: str, reply_jid: str = "") -> dict:
    """Send WhatsApp message via Baileys bridge."""
    # Invariant: never send an empty body. An empty message is a caller error, not a
    # bridge-down event — it must not enter the retry loop (root-fix 2026-10-02).
    if not text or not str(text).strip():
        return {"ok": False, "error": "INVALID_PAYLOAD: empty body", "key": None}
    payload = {"to": _normalize_bridge_phone(to), "text": text}
    if reply_jid:
        payload["reply_jid"] = _route_reply_jid(reply_jid)
    try:
        data = await _post_send(payload, 15.0)
        # "key" is the sent-message key (remoteJid/id/fromMe) — lets the caller later
        # delete-for-everyone (e.g. remove a verified 2FA code from the chat).
        return {"ok": data.get("ok", False), "error": data.get("error"), "key": data.get("key")}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "key": None}


async def delete_message(key: dict) -> dict:
    """Delete-for-everyone a message we sent, by its bridge message key. Best-effort."""
    if not key:
        return {"ok": False, "error": "no key"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(DELETE_URL, json={"key": key})
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
        payload["reply_jid"] = _route_reply_jid(reply_jid)

    try:
        data = await _post_send(payload, 30.0)
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
        payload["reply_jid"] = _route_reply_jid(reply_jid)

    try:
        data = await _post_send(payload, 30.0)
        return {"ok": data.get("ok", False), "error": data.get("error")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def send_typing(to: str, reply_jid: str = "") -> None:
    """Send WhatsApp typing indicator (composing presence) via Baileys bridge."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(TYPING_URL, json={"to": _normalize_bridge_phone(to), "reply_jid": _route_reply_jid(reply_jid)})
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
