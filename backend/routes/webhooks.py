"""
Webhooks — /api/v1/webhooks/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  POST /api/v1/webhooks/whatsapp  (no JWT auth — Twilio calls this directly)
"""
import os
from datetime import datetime
from uuid import UUID as _UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from BACKEND_DATABASE_MODELS import get_pii_db, User, Conversation, Message
from BACKEND_AI_AGENTS import process_user_message

router = APIRouter()

WHATSAPP_ANON_USER_ID = _UUID("00000000-0000-0000-0000-000000000001")


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
        )
        reply_text = agent_result.get("response", "מצטערים, נתקלנו בבעיה. אנא נסה שוב.")
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
