"""
Webhooks — /api/v1/webhooks/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  POST /api/v1/webhooks/whatsapp  (no JWT auth — Twilio calls this directly)
    POST /api/v1/webhooks/telegram  (no JWT auth — Telegram calls this directly)
"""
import os
import json
from datetime import datetime
from uuid import UUID as _UUID
import httpx
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from BACKEND_DATABASE_MODELS import (
    get_pii_db, async_session_factory,
    User, Conversation, Message, SystemSetting,
)
from BACKEND_AI_AGENTS import process_user_message

router = APIRouter()

WHATSAPP_ANON_USER_ID = _UUID("00000000-0000-0000-0000-000000000001")

_HUMAN_HANDOFF_TERMS = [
    "human",
    "real person",
    "live agent",
    "representative",
    "support agent",
    "customer service",
    "נציג",
    "נציגה",
    "בן אדם",
    "אדם אמיתי",
    "مندوب",
    "موظف",
    "شخص حقيقي",
    "بشر",
]

_URGENT_HANDOFF_TERMS = [
    "urgent",
    "asap",
    "now",
    "מיד",
    "דחוף",
    "עכשיו",
    "عاجل",
    "الآن",
]

_HANDOFF_SETTINGS_KEY = "support_handoff_settings"
_DEFAULT_HANDOFF_SETTINGS = {
    "ai_lock_during_handoff": True,
    "waiting_notice_cooldown_seconds": 120,
}


def _normalize_handoff_settings(raw: dict | None) -> dict:
    settings = dict(_DEFAULT_HANDOFF_SETTINGS)
    if not isinstance(raw, dict):
        return settings

    if isinstance(raw.get("ai_lock_during_handoff"), bool):
        settings["ai_lock_during_handoff"] = raw["ai_lock_during_handoff"]
    elif isinstance(raw.get("ai_lock_during_handoff"), str):
        val = raw.get("ai_lock_during_handoff", "").strip().lower()
        if val in {"1", "true", "yes", "on"}:
            settings["ai_lock_during_handoff"] = True
        elif val in {"0", "false", "no", "off"}:
            settings["ai_lock_during_handoff"] = False

    try:
        cooldown = int(raw.get("waiting_notice_cooldown_seconds"))
        settings["waiting_notice_cooldown_seconds"] = max(30, min(900, cooldown))
    except Exception:
        pass
    return settings


async def _load_handoff_settings() -> dict:
    settings = dict(_DEFAULT_HANDOFF_SETTINGS)
    try:
        async with async_session_factory() as cfg_db:
            row = (
                await cfg_db.execute(
                    select(SystemSetting).where(SystemSetting.key == _HANDOFF_SETTINGS_KEY)
                )
            ).scalar_one_or_none()
            if row and row.value:
                parsed = row.value
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                settings = _normalize_handoff_settings(parsed)
    except Exception:
        settings = dict(_DEFAULT_HANDOFF_SETTINGS)
    return settings


def _takeover_active(conversation: Conversation) -> bool:
    ctx = conversation.context if isinstance(conversation.context, dict) else {}
    return bool(ctx.get("admin_takeover_active"))


def _handoff_lock_active(conversation: Conversation, settings: dict) -> bool:
    if not bool(settings.get("ai_lock_during_handoff", True)):
        return False
    if _takeover_active(conversation):
        return True
    ctx = conversation.context if isinstance(conversation.context, dict) else {}
    status = str(ctx.get("human_handoff_status") or "none")
    if status not in {"requested", "active"}:
        return False
    return bool(ctx.get("human_handoff_lock_active", True))


def _wants_human_handoff(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"\s+", " ", text)
    return any(term in compact for term in _HUMAN_HANDOFF_TERMS)


def _handoff_priority(message: str) -> int:
    text = (message or "").strip().lower()
    if any(term in text for term in _URGENT_HANDOFF_TERMS):
        return 3
    if any(term in text for term in ("refund", "charge", "cancel", "תקלה", "חיוב", "ביטול", "استرجاع", "إلغاء")):
        return 2
    return 1


def _handoff_ack_message(message: str) -> str:
    text = (message or "")
    has_arabic = bool(re.search(r"[\u0600-\u06FF]", text))
    if has_arabic:
        return (
            "تم استلام طلبك للتحدث مع ممثل خدمة بشري.\n"
            "سيتم تحويل المحادثة إلى ممثل حقيقي بأسرع وقت.\n"
            "يمكنك الاستمرار في الكتابة هنا، ولن نفوّت رسائلك."
        )
    return (
        "קיבלנו את הבקשה שלך לנציג אנושי.\n"
        "השיחה תועבר לנציג/ה אמיתי/ת בהקדם האפשרי.\n"
        "אפשר להמשיך לכתוב כאן, ולא נפספס את ההודעות שלך."
    )


def _handoff_waiting_message(message: str) -> str:
    text = (message or "")
    has_arabic = bool(re.search(r"[\u0600-\u06FF]", text))
    if has_arabic:
        return (
            "طلبك للمحادثة مع ممثل بشري ما زال قيد المعالجة.\n"
            "الفريق يرى رسائلك الآن وسيعود إليك ممثل حقيقي بأقرب وقت."
        )
    return (
        "הפנייה שלך לנציג/ה אנושי/ת עדיין בטיפול.\n"
        "צוות השירות רואה את ההודעות שלך בזמן אמת ויחזור אליך בהקדם."
    )


def _handoff_waiting_notice_due(conversation: Conversation, cooldown_seconds: int) -> bool:
    ctx = conversation.context if isinstance(conversation.context, dict) else {}
    raw = str(ctx.get("human_handoff_last_waiting_notice_at") or "").strip()
    if not raw:
        return True
    try:
        elapsed = (datetime.utcnow() - datetime.fromisoformat(raw)).total_seconds()
        return elapsed >= max(30, cooldown_seconds)
    except Exception:
        return True


def _mark_handoff_waiting_notice(conversation: Conversation) -> None:
    ctx = dict(conversation.context or {})
    ctx["human_handoff_last_waiting_notice_at"] = datetime.utcnow().isoformat()
    conversation.context = ctx


def _apply_handoff_request(conversation: Conversation, reason: str, message: str) -> None:
    now = datetime.utcnow()
    ctx = dict(conversation.context or {})
    existing_priority = int(ctx.get("human_handoff_priority") or 1)
    incoming_priority = _handoff_priority(message)

    ctx["human_handoff_requested"] = True
    ctx["human_handoff_status"] = "requested"
    ctx["human_handoff_requested_at"] = str(ctx.get("human_handoff_requested_at") or "").strip() or now.isoformat()
    ctx["human_handoff_reason"] = (reason or "intent").strip()
    ctx["human_handoff_priority"] = max(existing_priority, incoming_priority)
    ctx["human_handoff_latest_user_text"] = (message or "").strip()[:500]
    ctx["human_handoff_lock_active"] = True
    ctx["human_handoff_feedback_required"] = False
    ctx["human_handoff_feedback_submitted"] = False
    ctx["human_handoff_feedback_rating"] = None
    ctx["human_handoff_feedback_text"] = None
    ctx["human_handoff_feedback_at"] = None
    ctx["human_handoff_resolved_at"] = None
    ctx["human_handoff_last_waiting_notice_at"] = None
    conversation.context = ctx


def _sanitize_for_telegram(text: str) -> str:
    import re
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Keep real backend links intact; do not rewrite API paths into simulated text.
    # Remove technical error messages
    text = re.sub(r'HTTP\s+\d+[^\n]*', '', text)
    text = re.sub(r'Ctrl\s*\+[^\n]*', '', text)
    text = re.sub(r'F5[^\n]*', '', text)
    text = re.sub(r'status[=:]\s*\d+[^\n]*', '', text)
    # Remove markdown that doesn't render in Telegram
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'#+\s+', '', text)
    # If response accidentally mixes Hebrew and Arabic, keep one dominant script.
    hebrew_count = len(re.findall(r'[\u0590-\u05FF]', text))
    arabic_count = len(re.findall(r'[\u0600-\u06FF]', text))
    if hebrew_count and arabic_count:
        dominant = "he" if hebrew_count >= arabic_count else "ar"
        cleaned_lines = []
        for line in text.splitlines():
            kept_tokens = []
            for token in line.split():
                has_he = bool(re.search(r'[\u0590-\u05FF]', token))
                has_ar = bool(re.search(r'[\u0600-\u06FF]', token))
                has_digits = bool(re.search(r'\d', token))
                # Drop corrupted mixed-script tokens like עבריתعربي.
                if has_he and has_ar:
                    continue
                if dominant == "he" and has_ar and not has_digits:
                    continue
                if dominant == "ar" and has_he and not has_digits:
                    continue
                kept_tokens.append(token)
            cleaned_lines.append(" ".join(kept_tokens))
        text = "\n".join(cleaned_lines)
    # Escape stray angle brackets so parse_mode=HTML will not treat them as tags
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    # Clean up extra blank lines
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    # Remove CJK (Chinese/Japanese/Korean) characters that leak from Qwen model
    import unicodedata
    def remove_cjk(text):
        result = []
        for char in text:
            cat = unicodedata.category(char)
            block = ord(char)
            # Allow: Hebrew, Arabic, Latin, digits, punctuation, emoji ranges
            is_cjk = (
                0x4E00 <= block <= 0x9FFF or   # CJK Unified
                0x3000 <= block <= 0x303F or   # CJK Symbols
                0xFF00 <= block <= 0xFFEF or   # Halfwidth/Fullwidth
                0x3040 <= block <= 0x30FF      # Hiragana/Katakana
            )
            if not is_cjk:
                result.append(char)
        return ''.join(result)
    text = remove_cjk(text)
    return text


@router.post("/api/v1/webhooks/whatsapp")
async def whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_pii_db)):
    """Inbound WhatsApp messages from Twilio.
    No JWT auth — Twilio calls this directly.
    Signature validated via X-Twilio-Signature.
    """
    from social.whatsapp_provider import get_whatsapp_provider, TwilioWhatsAppProvider

    provider   = get_whatsapp_provider()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_sig = request.headers.get("X-Twilio-Signature", "")

    # ── 1. Parse form body ────────────────────────────────────────────────────
    raw_data = dict(await request.form())

    # ── 2. Signature validation (skip in dev when token not configured) ───────
    if auth_token:
        if isinstance(provider, TwilioWhatsAppProvider):
            if not provider.validate_signature(auth_token, str(request.url), raw_data, twilio_sig):
                raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    else:
        print("[WhatsApp] WARNING: TWILIO_AUTH_TOKEN not set — signature validation skipped (dev mode only)")

    # ── 3. Parse incoming fields ──────────────────────────────────────────────
    parsed       = await provider.parse_incoming(raw_data)
    sender_phone = parsed["from"]        # e.g. "whatsapp:+972501234567"
    body         = parsed["body"].strip()
    profile_name = parsed["profile_name"]

    # Twilio sends status callbacks with empty Body — ignore silently
    if not sender_phone or not body:
        return Response(content="<Response/>", media_type="application/xml")

    # Normalise: strip "whatsapp:" prefix for DB lookup / agent routing
    phone_e164 = sender_phone.replace("whatsapp:", "").strip()

    # ── 4. Resolve user_id ────────────────────────────────────────────────────
    user_result = await db.execute(select(User).where(User.phone == phone_e164))
    user = user_result.scalar_one_or_none()
    conversation_user_id = user.id if user else WHATSAPP_ANON_USER_ID

    # ── 5. Find or create Conversation keyed on whatsapp_phone ───────────────
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.context["whatsapp_phone"].astext == phone_e164
        ).order_by(Conversation.last_message_at.desc()).limit(1)
    )
    conversation = conv_result.scalar_one_or_none()

    if not conversation:
        conversation = Conversation(
            user_id=conversation_user_id,
            title=f"WhatsApp {profile_name or phone_e164}",
            is_active=True,
            started_at=datetime.utcnow(),
            last_message_at=datetime.utcnow(),
            context={"whatsapp_phone": phone_e164, "profile_name": profile_name},
        )
        db.add(conversation)
        await db.flush()
    else:
        conversation.last_message_at = datetime.utcnow()

    conv_id = str(conversation.id)

    # ── 6. Persist user message ───────────────────────────────────────────────
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=body,
        content_type="text",
    )
    db.add(user_msg)
    await db.flush()

    handoff_settings = await _load_handoff_settings()

    if _takeover_active(conversation):
        # Conversation is in admin manual takeover mode: store inbound and skip bot response.
        await db.commit()
        return Response(content="<Response/>", media_type="application/xml")

    if _handoff_lock_active(conversation, handoff_settings):
        cooldown = int(handoff_settings.get("waiting_notice_cooldown_seconds") or 120)
        if _handoff_waiting_notice_due(conversation, cooldown):
            waiting_text = _handoff_waiting_message(body)
            send_result = await provider.send_message(sender_phone, waiting_text)
            if not send_result.get("ok"):
                safe_tail = phone_e164[-4:] if len(phone_e164) >= 4 else phone_e164
                print(f"[WhatsApp] Handoff waiting-notice send failed to ****{safe_tail}: {send_result.get('error')}")

            db.add(Message(
                conversation_id=conversation.id,
                role="assistant",
                agent_name="human_handoff_waiting",
                content=waiting_text,
                content_type="text",
                model_used="handoff_policy",
                tokens_used=0,
                created_at=datetime.utcnow(),
            ))
            _mark_handoff_waiting_notice(conversation)
            conversation.last_message_at = datetime.utcnow()
        await db.commit()
        return Response(content="<Response/>", media_type="application/xml")

    if _wants_human_handoff(body):
        _apply_handoff_request(conversation, reason="intent", message=body)
        ack_text = _handoff_ack_message(body)
        send_result = await provider.send_message(sender_phone, ack_text)
        if not send_result.get("ok"):
            safe_tail = phone_e164[-4:] if len(phone_e164) >= 4 else phone_e164
            print(f"[WhatsApp] Handoff ack send failed to ****{safe_tail}: {send_result.get('error')}")

        db.add(Message(
            conversation_id=conversation.id,
            role="assistant",
            agent_name="human_handoff",
            content=ack_text,
            content_type="text",
            model_used="handoff_policy",
            tokens_used=0,
            created_at=datetime.utcnow(),
        ))
        conversation.last_message_at = datetime.utcnow()
        await db.commit()
        return Response(content="<Response/>", media_type="application/xml")

    # ── 7. Route through Avi ──────────────────────────────────────────────────
    try:
        agent_result = await process_user_message(
            user_id=str(conversation_user_id),
            message=body,
            conversation_id=conv_id,
            db=db,
            source="whatsapp",
        )
        import re
        raw_reply = agent_result.get("response", "מצטערים, נתקלנו בבעיה. אנא נסה שוב.")
        reply_text = re.sub(r'<[^>]+>', '', raw_reply).strip()
    except Exception as exc:
        safe_phone = (phone_e164 or "")
        safe_tail = safe_phone[-4:] if len(safe_phone) >= 4 else safe_phone
        print(f"[WhatsApp] Agent error for ****{safe_tail}: {exc}")
        reply_text = "מצטערים, נתקלנו בבעיה. אנא נסה שוב."

    # ── 8. Send reply via WhatsApp API ────────────────────────────────────────
    send_result = await provider.send_message(sender_phone, reply_text)
    if not send_result["ok"]:
        safe_phone = (sender_phone or "")
        safe_tail = safe_phone[-4:] if len(safe_phone) >= 4 else safe_phone
        print(f"[WhatsApp] Send failed to ****{safe_tail}: {send_result['error']}")

    # ── 9. Persist assistant message ──────────────────────────────────────────
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply_text,
        content_type="text",
    )
    db.add(assistant_msg)
    await db.commit()

    # Empty TwiML — reply sent proactively via API, not TwiML verb
    return Response(content="<Response/>", media_type="application/xml")


@router.post("/api/v1/webhooks/telegram")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_pii_db)):
    """Inbound Telegram messages from Telegram Bot API webhook."""
    from social.telegram_publisher import send_telegram_message

    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")

    if secret and header_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram secret token")

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram payload")

    # Telegram can retry the same update when network/edge returns transient 5xx.
    # De-duplicate at ingress so users don't receive repeated bot messages.
    update_id = update.get("update_id")
    if isinstance(update_id, int):
        try:
            from BACKEND_AUTH_SECURITY import get_redis
            redis = await get_redis()
            dedup_key = f"tg:update:{update_id}"
            is_new = await redis.set(dedup_key, "1", ex=3600, nx=True)
            if not is_new:
                return {"ok": True, "ignored": True, "reason": "duplicate_update"}
        except Exception as exc:
            print(f"[Telegram] Dedup check skipped: {exc}")
            return {"ok": True, "ignored": True, "reason": "redis_unavailable"}

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return {"ok": True, "ignored": True, "reason": "unsupported_update"}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True, "ignored": True, "reason": "missing_chat_id"}

    text = (message.get("text") or "").strip()
    voice = message.get("voice") or message.get("audio")
    photo = message.get("photo")
    document = message.get("document")

    # Handle voice messages
    if not text and voice:
        file_id = voice.get("file_id")
        try:
            # Get file path from Telegram
            token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            async with httpx.AsyncClient(timeout=15.0) as client:
                file_info = await client.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}")
                file_path = file_info.json()["result"]["file_path"]
                audio_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
                audio_bytes = audio_resp.content
            from hf_client import hf_audio
            text = await hf_audio(audio_bytes)
            if not text:
                await send_telegram_message(chat_id, "מצטערים, לא הצלחתי להבין את ההקלטה. נסה שוב או כתוב את בקשתך בטקסט. 😊")
                return {"ok": True}
        except Exception as exc:
            print(f"[Telegram] Voice processing failed: {exc}")
            await send_telegram_message(chat_id, "מצטערים, אירעה שגיאה בעיבוד ההקלטה. נסה לכתוב את בקשתך. 😊")
            return {"ok": True}

    # Handle photo messages
    if not text and photo:
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            largest_photo = max(photo, key=lambda p: p.get("file_size", 0))
            file_id = largest_photo.get("file_id")
            async with httpx.AsyncClient(timeout=15.0) as client:
                file_info = await client.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}")
                file_path = file_info.json()["result"]["file_path"]
                photo_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
                image_bytes = photo_resp.content
            import base64
            image_b64 = base64.b64encode(image_bytes).decode()
            from hf_client import hf_vision
            caption = message.get("caption") or "זהה את החלק בתמונה והצע חלפים מתאימים"
            text = await hf_vision(image_b64, caption)
            if not text:
                await send_telegram_message(chat_id, "מצטערים, לא הצלחתי לזהות את התמונה. נסה תמונה ברורה יותר. 😊")
                return {"ok": True}
        except Exception as exc:
            print(f"[Telegram] Photo processing failed: {exc}")
            await send_telegram_message(chat_id, "מצטערים, אירעה שגיאה בעיבוד התמונה. נסה שוב. 😊")
            return {"ok": True}

    if not text:
        return {"ok": True, "ignored": True, "reason": "non_text_message"}

    from_user = message.get("from") or {}
    tg_user_id = str(from_user.get("id") or chat_id)
    tg_username = (from_user.get("username") or "").strip()
    tg_name = (from_user.get("first_name") or "").strip() or tg_username or tg_user_id

    conv_id = None
    used_fallback = False

    try:
        conv_result = await db.execute(
            select(Conversation).where(
                Conversation.context["telegram_chat_id"].astext == str(chat_id)
            ).order_by(Conversation.last_message_at.desc()).limit(1)
        )
        conversation = conv_result.scalar_one_or_none()

        # ── Welcome message for new conversations or /start ──────────────────
        is_new_conversation = conversation is None
        if text.strip() in ("/start", "/התחל", "start", "hello", "hi", "היי", "הי", "هلا", "مرحبا", "שלום") and (
            is_new_conversation or text.strip() in ("/start", "/התחל", "start")
        ):
            female_names = ["ענת", "דנה", "מאיה", "שירה"]
            male_names = ["יוסי", "מוחמד", "ליאור", "כרם"]
            import random
            all_names = female_names + male_names
            worker = None
            if conversation and isinstance(conversation.context, dict):
                worker = conversation.context.get("agent_name")
            if not worker:
                worker = random.choice(all_names)

            if not conversation:
                conversation = Conversation(
                    user_id=WHATSAPP_ANON_USER_ID,
                    title=f"Telegram {tg_name}",
                    is_active=True,
                    started_at=datetime.utcnow(),
                    last_message_at=datetime.utcnow(),
                    context={
                        "telegram_chat_id": str(chat_id),
                        "telegram_user_id": tg_user_id,
                        "telegram_username": tg_username or None,
                        "agent_name": worker,
                    },
                )
                db.add(conversation)
                await db.flush()
                await db.commit()
            elif isinstance(conversation.context, dict) and not conversation.context.get("agent_name"):
                conversation.context["agent_name"] = worker
                await db.commit()

            is_female = worker in female_names
            role = "נציגת" if is_female else "נציג"
            if any(c in text for c in "ابتةثجحخدذرزسشصضطظعغفقكلمنهوي"):
                role_ar = "ممثلة" if is_female else "ممثل"
                welcome = f"أهلاً وسهلاً! 👋\nأنا {worker}، {role_ar} خدمة العملاء في AutoSpareFinder.\nكيف أستطيع مساعدتك اليوم؟\nهل تبحث عن قطعة غيار، أو تريد متابعة طلب، أو لديك استفسار؟ 😊"
            else:
                welcome = f"שלום וברוכים הבאים! 👋\nאני {worker}, {role} השירות של AutoSpareFinder.\nאיך אוכל לעזור לך היום?\nמחפש חלק לרכב, רוצה לעקוב אחר הזמנה, או שיש לך שאלה? 😊"
            await send_telegram_message(chat_id, welcome)
            return {"ok": True}

        if not conversation:
            conversation = Conversation(
                user_id=WHATSAPP_ANON_USER_ID,
                title=f"Telegram {tg_name}",
                is_active=True,
                started_at=datetime.utcnow(),
                last_message_at=datetime.utcnow(),
                context={
                    "telegram_chat_id": str(chat_id),
                    "telegram_user_id": tg_user_id,
                    "telegram_username": tg_username or None,
                },
            )
            db.add(conversation)
            await db.flush()
        else:
            conversation.last_message_at = datetime.utcnow()

        conv_id = str(conversation.id)

        handoff_settings = await _load_handoff_settings()

        if _takeover_active(conversation):
            db.add(Message(
                conversation_id=conversation.id,
                role="user",
                content=text,
                content_type="text",
            ))
            await db.commit()
            return {
                "ok": True,
                "conversation_id": conv_id,
                "takeover": True,
                "delivered": False,
            }

        if _handoff_lock_active(conversation, handoff_settings):
            cooldown = int(handoff_settings.get("waiting_notice_cooldown_seconds") or 120)
            if _handoff_waiting_notice_due(conversation, cooldown):
                waiting_text = _sanitize_for_telegram(_handoff_waiting_message(text))
                send_result = await send_telegram_message(chat_id, waiting_text)
                if not send_result.get("ok"):
                    print(f"[Telegram] Handoff waiting-notice send failed for chat {chat_id}: {send_result.get('error')}")

                db.add(Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    agent_name="human_handoff_waiting",
                    content=waiting_text,
                    content_type="text",
                    model_used="handoff_policy",
                    tokens_used=0,
                    created_at=datetime.utcnow(),
                ))
                _mark_handoff_waiting_notice(conversation)
                conversation.last_message_at = datetime.utcnow()
            await db.commit()
            return {
                "ok": True,
                "conversation_id": conv_id,
                "handoff_waiting": True,
            }

        if _wants_human_handoff(text):
            _apply_handoff_request(conversation, reason="intent", message=text)
            ack_text = _sanitize_for_telegram(_handoff_ack_message(text))
            send_result = await send_telegram_message(chat_id, ack_text)
            if not send_result.get("ok"):
                print(f"[Telegram] Handoff ack send failed for chat {chat_id}: {send_result.get('error')}")

            db.add(Message(
                conversation_id=conversation.id,
                role="assistant",
                agent_name="human_handoff",
                content=ack_text,
                content_type="text",
                model_used="handoff_policy",
                tokens_used=0,
                created_at=datetime.utcnow(),
            ))
            conversation.last_message_at = datetime.utcnow()
            await db.commit()
            return {
                "ok": True,
                "conversation_id": conv_id,
                "handoff_requested": True,
                "delivered": bool(send_result.get("ok")),
            }

        agent_result = await process_user_message(
            user_id=str(conversation.user_id),
            message=text,
            conversation_id=conv_id,
            db=db,
            source="telegram",
        )
        raw_reply = agent_result.get("response", "מצטערים, נתקלנו בבעיה. אנא נסה שוב.")
        reply_text = _sanitize_for_telegram(raw_reply)
    except Exception as exc:
        import traceback
        await db.rollback()
        used_fallback = True
        print(f"[Telegram] FULL ERROR for chat {chat_id}:")
        print(traceback.format_exc())
        reply_text = "תודה על פנייתך! 😊 נציג שירות יחזור אליך בהקדם."

    send_result = await send_telegram_message(chat_id, reply_text)
    if not send_result.get("ok"):
        print(f"[Telegram] Send failed for chat {chat_id}: {send_result.get('error')}")

    return {
        "ok": True,
        "conversation_id": conv_id,
        "fallback": used_fallback,
        "delivered": bool(send_result.get("ok")),
    }
