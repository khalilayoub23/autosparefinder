"""
Webhooks — /api/v1/webhooks/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  POST /api/v1/webhooks/whatsapp  (no JWT auth — Twilio calls this directly)
    POST /api/v1/webhooks/telegram  (no JWT auth — Telegram calls this directly)
"""
import os
from datetime import datetime
from uuid import UUID as _UUID
import httpx

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from BACKEND_DATABASE_MODELS import get_pii_db, User, Conversation, Message
from BACKEND_AI_AGENTS import process_user_message

router = APIRouter()

WHATSAPP_ANON_USER_ID = _UUID("00000000-0000-0000-0000-000000000001")


def _sanitize_for_telegram(text: str) -> str:
    import re
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Replace internal URLs with friendly Hebrew text
    text = text.replace('/api/v1/customers/cart', 'העגלה שלך באתר')
    text = text.replace('/api/v1/customers/', 'האזור האישי שלך באתר')
    text = re.sub(r'/api/v1/[^\s\n]+', 'האתר שלנו', text)
    text = text.replace('/parts', 'חיפוש חלקים באתר')
    text = text.replace('/orders', 'ההזמנות שלך באתר')
    text = text.replace('/wishlist', 'רשימת המשאלות שלך באתר')
    text = text.replace('/profile', 'הפרופיל שלך באתר')
    text = text.replace('/reviews', 'הביקורות שלך באתר')
    # Remove technical error messages
    text = re.sub(r'HTTP\s+\d+[^\n]*', '', text)
    text = re.sub(r'Ctrl\s*\+[^\n]*', '', text)
    text = re.sub(r'F5[^\n]*', '', text)
    text = re.sub(r'status[=:]\s*\d+[^\n]*', '', text)
    # Remove markdown that doesn't render in Telegram
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'#+\s+', '', text)
    # Clean up extra blank lines
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
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
        is_new_conversation = conversation is None or not history_rows if 'history_rows' in dir() else True
        if text.strip() in ("/start", "/התחל", "start", "hello", "hi", "היי", "הי", "هلا", "مرحبا", "שלום") and not conversation:
            female_names = ["ענת", "דנה", "מאיה", "שירה"]
            male_names = ["יוסי", "מוחמד", "ליאור", "כרם"]
            import random
            all_names = female_names + male_names
            worker = random.choice(all_names)
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
