"""
Webhooks — /api/v1/webhooks/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  POST /api/v1/webhooks/whatsapp      (no JWT auth — Twilio calls this directly)
  POST /api/v1/webhooks/telegram      (no JWT auth — Telegram calls this directly)
  GET  /api/v1/webhooks/tiktok        (TikTok URL verification challenge)
  POST /api/v1/webhooks/tiktok        (TikTok event notifications)
  GET  /api/v1/webhooks/tiktok-oauth  (TikTok OAuth callback for Content Posting API)
"""
import os
import json
import asyncio
import hmac
import hashlib
import logging
from datetime import datetime, timezone, timedelta
import uuid
from uuid import UUID as _UUID
from typing import Dict, Any
import httpx
import re
import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from BACKEND_DATABASE_MODELS import (
    get_pii_db, async_session_factory,
    User, Conversation, Message, SystemSetting,
)
from BACKEND_AI_AGENTS import process_user_message
import chat_requests as creq
import time

logger = logging.getLogger("webhooks")

TELEGRAM_ADMIN_TOKEN = os.getenv("TELEGRAM_ADMIN_BOT_TOKEN", "")
TELEGRAM_OWNER_ID = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")
TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
# Optional: TIKTOK_WEBHOOK_SECRET for HMAC verification of event payloads
TIKTOK_WEBHOOK_SECRET = os.getenv("TIKTOK_WEBHOOK_SECRET", "")

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# TikTok webhook — URL verification (GET) + event handler (POST)
# Registered in TikTok Developer Portal as callback URL:
#   https://www.autosparefinder.co.il/api/v1/webhooks/tiktok
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/v1/webhooks/tiktok")
async def tiktok_webhook_verify(request: Request):
    """
    TikTok sends a GET with ?challenge=<token> to verify the endpoint.
    We must return {"challenge": <token>} with HTTP 200.
    """
    challenge = request.query_params.get("challenge", "")
    logger.info("tiktok_webhook verify challenge=%s", challenge[:40] if challenge else "(none)")
    # Return the challenge to complete URL verification
    return {"challenge": challenge}


@router.post("/api/v1/webhooks/tiktok")
async def tiktok_webhook_event(request: Request):
    """
    Receives TikTok event notifications (video.publish.complete, etc.).
    Verifies HMAC signature if TIKTOK_WEBHOOK_SECRET is set.
    """
    body = await request.body()

    # Optional HMAC verification (TikTok sends X-TikTok-Signature header)
    if TIKTOK_WEBHOOK_SECRET:
        sig_header = request.headers.get("X-TikTok-Signature", "")
        if sig_header.startswith("sha256="):
            expected = "sha256=" + hmac.new(
                TIKTOK_WEBHOOK_SECRET.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, sig_header):
                logger.warning("tiktok_webhook invalid HMAC signature")
                raise HTTPException(status_code=403, detail="invalid signature")

    try:
        payload = json.loads(body)
    except Exception:
        payload = {}

    event = payload.get("event", "")
    publish_id = payload.get("publish_id", "") or payload.get("data", {}).get("publish_id", "")
    status = payload.get("status", "") or payload.get("data", {}).get("status", "")

    logger.info("tiktok_event event=%s publish_id=%s status=%s", event, publish_id, status)

    # Handle video.publish.complete
    if event in ("video.publish.complete", "video.upload.complete"):
        if status == "success":
            logger.info("tiktok_event: video published successfully publish_id=%s", publish_id)
        else:
            logger.warning("tiktok_event: video publish failed publish_id=%s status=%s", publish_id, status)

    return {"received": True, "event": event}


# ─────────────────────────────────────────────────────────────────────────────
# TikTok OAuth callback — handles redirect after user grants video.publish scope
# Redirect URI registered in TikTok Developer Portal:
#   https://www.autosparefinder.co.il/api/v1/webhooks/tiktok-oauth
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/v1/webhooks/tiktok-oauth")
async def tiktok_oauth_callback(request: Request):
    """
    TikTok redirects here after the user authorises the app.
    Exchanges the ?code= for an access token and stores it.
    """
    code  = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    error = request.query_params.get("error", "")

    if error:
        logger.error("tiktok_oauth error=%s", error)
        return {"ok": False, "error": error}

    if not code:
        raise HTTPException(status_code=400, detail="missing code")

    logger.info("tiktok_oauth code received state=%s", state)

    # Exchange code for access token
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                data={
                    "client_key":     TIKTOK_CLIENT_KEY,
                    "client_secret":  TIKTOK_CLIENT_SECRET,
                    "code":           code,
                    "grant_type":     "authorization_code",
                    "redirect_uri":   "https://www.autosparefinder.co.il/api/v1/webhooks/tiktok-oauth",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        data = r.json()
        access_token  = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")
        open_id       = data.get("open_id", "")
        scope         = data.get("scope", "")
        logger.info("tiktok_oauth token obtained open_id=%s scope=%s", open_id, scope)

        if access_token:
            # 2026-08-16 fix: this used to be a bare "# TODO: persist" — the
            # token was exchanged correctly then thrown away, so even a
            # completed owner consent left nothing usable for an analytics
            # collector. Persisted via the EXISTING SystemSetting table (no
            # new table) through tiktok_publisher.store_user_token().
            from social.tiktok_publisher import store_user_token
            async with async_session_factory() as db:
                await store_user_token(
                    db, access_token=access_token, refresh_token=refresh_token,
                    open_id=open_id, expires_in=data.get("expires_in", 0), scope=scope,
                )

        return {"ok": True, "open_id": open_id, "scope": scope}
    except Exception as exc:
        logger.error("tiktok_oauth token exchange failed: %s", exc)
        raise HTTPException(status_code=500, detail="token exchange failed")

# Owner-console background replies. The event loop keeps only a WEAK reference to a
# task, so anything spawned with `asyncio.create_task` and not stored can be collected
# mid-flight — the owner gets no answer and no error. Hold a strong reference until the
# task completes. See the owner-console block in the WhatsApp webhook below.
_OWNER_REPLY_TASKS: set = set()
# Ceiling for one owner reply. Cerebras 429-fallbacks can legitimately take ~60-90s;
# beyond that the owner should get an honest "too slow" instead of silence.
_OWNER_REPLY_TIMEOUT_S = int(os.getenv("OWNER_REPLY_TIMEOUT_S", "120"))


@router.post("/api/v1/webhooks/telegram-admin")
@router.post("/webhooks/telegram-admin")
async def telegram_admin_webhook(request: Request):
    """
    Handles approval/rejection callbacks from NOA's admin Telegram bot.
    """
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or header_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram secret token")

    data = await request.json()

    # Handle inline keyboard button press
    if "callback_query" in data:
        query = data["callback_query"]
        callback_data = query.get("data", "")
        chat_id = query["message"]["chat"]["id"]
        message_id = query["message"]["message_id"]

        async with httpx.AsyncClient(timeout=10.0) as client:
            # NOA sends platform-specific approvals (approve_tiktok / approve_instagram
            # / approve_facebook / approve_whatsapp) — the handler only matched the
            # legacy "approve_post", so approval taps did NOTHING (fixed 2026-07-05).
            if callback_data.startswith("approve_"):
                callback_platform = callback_data.replace("approve_", "") or "post"
                callback_data = "approve_post"
            else:
                callback_platform = ""
            if callback_data == "approve_post":
                # אשר פרסום
                from agents.memory import AgentMemory
                from BACKEND_DATABASE_MODELS import async_session_factory
                async with async_session_factory() as db:
                      mem = AgentMemory(db, agent_name="noa")
                      pending = await mem.get("pending_post")
                      if not pending:
                          history = await mem.get("post_history") or []
                          for event in reversed(history):
                              if isinstance(event, dict) and str(event.get("status") or "").lower() in ("awaiting_approval", "pending"):
                                  pending = event
                                  break

                      if pending:
                          sp_id = pending.get("social_post_id")
                          sp_row = None
                          if sp_id:
                              from sqlalchemy import select as _sel
                              from BACKEND_DATABASE_MODELS import SocialPost
                              sp_row = (await db.execute(_sel(SocialPost).where(SocialPost.id == sp_id))).scalar_one_or_none()

                          if sp_row:
                              # ROOT FIX (2026-09-19, FIXES_TRACKER #28): this used to be
                              # a SECOND, divergent approve+publish implementation —
                              # unconditionally re-published to Telegram no matter which
                              # button was tapped, then dispatched at most ONE additional
                              # platform (explicitly excluding "telegram"/"post"/"" from
                              # ever firing), then marked the WHOLE row 'published' the
                              # instant that lone Telegram send succeeded. A post approved
                              # for [discord, facebook, telegram, tiktok] could be marked
                              # fully "published" having only ever touched Telegram —
                              # Facebook/Discord/TikTok were never even attempted. It also
                              # never set approved_at (campaign_manager.approve_post_content()
                              # was never called here), unlike the canonical WhatsApp
                              # "אשר <id>" path. Delegating to the SAME canonical
                              # owner_console._approve_and_publish() used by WhatsApp means
                              # ANY approval channel (WhatsApp text or this Telegram button)
                              # now dispatches the FULL platforms list on the row, records
                              # approved_at correctly, and is idempotent against a
                              # double-approval race (a second tap/text is a safe no-op).
                              from agents.owner_console import _approve_and_publish
                              post = {
                                  "id": str(sp_row.id),
                                  "content": sp_row.content or "",
                                  "platforms": sp_row.platforms or [],
                              }
                              reply = await _approve_and_publish(db, post)
                              pending["status"] = "published" if "✅" in reply else "approved"
                              await mem.set("pending_post", pending)
                          else:
                              # Legacy pending_post entries with no social_post_id (from
                              # before _noa_enqueue_social_post existed) — best-effort
                              # single-platform publish, unchanged from prior behavior.
                              caption = pending.get("caption", "")
                              plat = (callback_platform or pending.get("platform") or "telegram").lower()
                              from social import registry
                              r = await registry.dispatch(plat, caption)
                              reply = (f"✅ פורסם ב-{plat.title()}" if r.get("ok")
                                       else f"⚠️ {plat.title()}: {r.get('error', 'נכשל')}")
                              pending["status"] = "published" if r.get("ok") else "approved"
                              await mem.set("pending_post", pending)
                      else:
                          reply = "⚠️ No pending NOA post was found for approval"

                # עדכן את ההודעה
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_ADMIN_TOKEN}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": reply,
                    }
                )

            elif callback_data == "edit_post":
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_ADMIN_TOKEN}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": "✏️ שלח את הטקסט המתוקן כהודעה חדשה — אשמור אותו ואשאל אם לפרסם.",
                    }
                )

            elif callback_data == "reject_post":
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_ADMIN_TOKEN}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": "❌ הפוסט נדחה.",
                    }
                )

            # Answer the callback query to remove loading state
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_ADMIN_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": query["id"]}
            )

    # Inbound engagement: a plain message to NOA's bot (DM, or a group mention/reply).
    # Record it into NOA's engagement inbox; the engagement loop drafts a reply for the
    # owner to approve. Additive + fully guarded — never affects the approval flow above.
    elif "message" in data:
        try:
            from social import engagement as _eng
            from BACKEND_DATABASE_MODELS import async_session_factory
            async with async_session_factory() as _edb:
                await _eng.ingest_telegram_update(_edb, data, owner_chat_id=TELEGRAM_OWNER_ID)
        except Exception as _ie:
            print(f"[telegram-admin] engagement ingest skipped: {_ie}", flush=True)

    return {"ok": True}

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


# ── Agent reply with late delivery (#71) ──────────────────────────────────────────────────────
# Before: asyncio.wait_for(process_user_message(...), 12) CANCELLED the agent at 12 s, told the customer
# (always in Hebrew) "שלח שוב את שאלתך" and discarded the answer. Now the agent runs as its own task in
# its own session; after WA_AGENT_REPLY_WAIT_S the customer gets an honest "the answer will follow" in
# their language, and the answer is sent the moment it is ready (overall deadline WA_AGENT_LATE_DEADLINE_S).
# A strong reference keeps the task alive (CLAUDE.md: a path that owes an answer needs a strong
# reference, a deadline and a fallback).
WA_AGENT_REPLY_WAIT_S = float(os.getenv("WA_AGENT_REPLY_WAIT_S", "12"))
WA_AGENT_LATE_DEADLINE_S = float(os.getenv("WA_AGENT_LATE_DEADLINE_S", "120"))
_AGENT_REPLY_TASKS: set = set()
WA_FAST_DELIVERY_WAIT_S = float(os.getenv("WA_FAST_DELIVERY_WAIT_S", "20"))

_WAIT_TEXT = {
    "he": "⏳ אני עדיין מחפש עבורך — התשובה תגיע בהודעה נפרדת בעוד רגע, אין צורך לשלוח שוב.",
    "ar": "⏳ ما زلت أبحث لك — سيصلك الرد في رسالة منفصلة بعد قليل، لا داعي لإعادة الإرسال.",
    "en": "⏳ Still searching for you — the answer will follow in a separate message shortly, no need to resend.",
}
_LATE_FAIL_TEXT = {
    "he": "מצטערים — לא הצלחתי להשלים את החיפוש הפעם. אפשר לשלוח שוב את השאלה.",
    "ar": "عذرًا — لم أتمكن من إكمال البحث هذه المرة. يمكنك إرسال سؤالك مرة أخرى.",
    "en": "Sorry — I could not finish this search. Please send your question again.",
}
_STILL_WORKING_TEXT = {
    "he": "⏳ עדיין עובד על השאלה הקודמת שלך — התשובה בדרך.",
    "ar": "⏳ ما زلت أعمل على سؤالك السابق — الرد في الطريق.",
    "en": "⏳ Still working on your previous question — the answer is on its way.",
}


def _reply_lang(text: str) -> str:
    t = text or ""
    if re.search(r"[\u0600-\u06FF]", t):
        return "ar"
    if re.search(r"[\u0590-\u05FF]", t):
        return "he"
    return "en" if re.search(r"[A-Za-z]", t) else "he"


def _keep(task) -> None:
    _AGENT_REPLY_TASKS.add(task)
    task.add_done_callback(_AGENT_REPLY_TASKS.discard)


async def run_agent_reply(run, render, send, *, lang: str, wait_s: float = None, deadline_s: float = None,
                          on_done=None) -> dict:
    """run(): coroutine → agent result (in its own session). render(result) → reply text.
    send(text): delivers a LATE message. Returns {"mode": "direct", "text"} when the answer came within
    wait_s (the caller sends it), {"mode": "error", "error"} when the agent failed fast, or
    {"mode": "deferred", "text": wait text} — the caller sends the wait text; the answer (or, after the
    deadline, the failure text) is delivered exactly once by `send`. on_done() runs when the work ends."""
    wait_s = WA_AGENT_REPLY_WAIT_S if wait_s is None else wait_s
    deadline_s = WA_AGENT_LATE_DEADLINE_S if deadline_s is None else deadline_s
    task = asyncio.ensure_future(run())
    _keep(task)
    done, _ = await asyncio.wait({task}, timeout=wait_s)
    if done:
        if on_done:
            await on_done()
        exc = task.exception()
        if exc is not None:
            return {"mode": "error", "error": exc}
        return {"mode": "direct", "text": render(task.result())}

    async def _late():
        try:
            try:
                result = await asyncio.wait_for(task, timeout=max(0.0, deadline_s - wait_s))   # cancels at the deadline
                text = render(result)
            except Exception as exc:                       # deadline, or the agent failed late
                print(f"[WhatsApp] late agent reply failed: {type(exc).__name__}")
                text = _LATE_FAIL_TEXT.get(lang, _LATE_FAIL_TEXT["he"])
            await send(text)
        finally:
            if on_done:
                await on_done()
    _keep(asyncio.ensure_future(_late()))
    return {"mode": "deferred", "text": _WAIT_TEXT.get(lang, _WAIT_TEXT["he"])}


async def _agent_inflight_claim(redis, user_key: str, body: str, ttl_s: int, rid: str = None) -> str:
    """'new' (claimed) | 'duplicate' (the SAME text is already being answered) | 'other' (a different
    question is in flight — answered separately). Redis unavailable → 'new' (never block a reply).
    With rid (#73) the claim names its request; a claim whose request has ended or whose owner process has no
    heartbeat (it died) is stale and is taken over — a resend must
    never get "still working" for an answer nobody is producing."""
    if redis is None:
        return "new"
    digest = hashlib.sha256((body or "").strip().encode("utf-8")).hexdigest()[:16]
    key = f"wa:agent_inflight:{user_key}:{digest}"
    try:
        if await redis.set(key, rid or "1", ex=ttl_s, nx=True):
            return "new"
        if rid is None:
            return "duplicate"
        holder = await redis.get(key)
        holder = holder.decode() if isinstance(holder, bytes) else holder
        rec = await creq.get(redis, holder) if holder and holder != "1" else None
        if rec and rec.get("status") in creq.OPEN_STATUSES and await creq.owner_alive(redis, rec.get("owner")):
            return "duplicate"
        await redis.set(key, rid, ex=ttl_s)
        return "new"
    except Exception:
        return "new"


async def _agent_inflight_release(redis, user_key: str, body: str) -> None:
    if redis is None:
        return
    digest = hashlib.sha256((body or "").strip().encode("utf-8")).hexdigest()[:16]
    try:
        await redis.delete(f"wa:agent_inflight:{user_key}:{digest}")
    except Exception:
        pass


# ── Durable chat-request lifecycle (#73/#74) — shared by WhatsApp and Telegram ────────────────
# The request record (chat_requests) is written BEFORE the agent task starts. Separate fields: processing
# `status`/`reason`, the agent's conversation turn (tagged in `messages.analysis.chat_request_id`, committed in
# the SAME transaction as the turn), transport `delivery`, and the authoritative history row (`history`).
# Before any re-run the turn is RECONCILED against the database: a turn that was already saved is reused, never
# produced twice. An outcome that cannot be reconciled stays `ambiguous` (open, retried, then an honest failure).
_ACTIVE_REQUESTS: set = set()          # rids this process is working on right now
_TERMINAL_DELIVERY = ("sent", "failed", "unknown")


def _transport(channel: str):
    """(send(rec, text) → result, classify(result) → sent|not_sent|invalid|rejected|unknown), resolved at call time."""
    if channel == "tg":
        from social import telegram_publisher as _tp

        async def _send(rec, text):
            return await _tp.send_telegram_message(rec["target"], _sanitize_for_telegram(text))
        return _send, creq.classify_telegram
    from social import whatsapp_provider as _wp

    async def _send(rec, text):
        return await _wp.send_message(rec["target"], text, reply_jid=rec.get("reply_jid") or "")
    return _send, creq.classify_whatsapp


def _render_agent_answer(agent_result):
    """(answer, "") for a genuine non-empty answer, else ("", "empty_answer" | "malformed_answer")."""
    if not isinstance(agent_result, dict):
        return "", "malformed_answer"
    raw = agent_result.get("response")
    if raw is None:
        return "", "empty_answer"
    if not isinstance(raw, str):
        return "", "malformed_answer"
    text = re.sub(r"<[^>]+>", "", raw).strip()
    return (text, "") if text else ("", "empty_answer")


async def _run_agent_for(rec: dict, rid: str, reuse_user_id: str = ""):
    """One agent run in its own PII session. Every conversation-turn row it creates is tagged with the request id
    inside the agent's own transaction, so "was this turn saved?" is answerable after any crash (and the unique
    index of migration 0040 makes a second live row per request impossible).
    reuse_user_id: the customer's question was already saved by an interrupted run — the re-run's own copy of it
    is created soft-deleted and points at the original, so the original stays the one visible question and no
    existing history row is altered."""
    from sqlalchemy import event
    from BACKEND_DATABASE_MODELS import pii_session_factory as _psf, Message as _Msg
    async with _psf() as _adb:
        def _tag(sess, _ctx, _inst):
            for obj in list(sess.new):
                if isinstance(obj, _Msg) and obj.role in ("user", "assistant"):
                    a = dict(obj.analysis or {})
                    if "chat_request_id" not in a:
                        a.update(chat_request_id=rid, chat_turn=obj.role)
                        if obj.role == "user" and reuse_user_id:
                            a["chat_duplicate_of"] = reuse_user_id
                            obj.deleted_at = datetime.utcnow()
                        obj.analysis = a
        event.listen(_adb.sync_session, "before_flush", _tag)
        # this task's context only: side-effecting agent code and the history loader see which request this is
        creq.CURRENT_REQUEST.set(rid)
        creq.REUSE_USER_ROW.set(reuse_user_id or "")
        _res = await process_user_message(user_id=rec["user_key"], message=rec["body"],
                                          conversation_id=rec.get("conv_id") or None, db=_adb,
                                          source="telegram" if rec.get("channel") == "tg" else "whatsapp")
        await _adb.commit()
        return _res


async def _reconcile_turn(rec: dict, rid: str) -> dict:
    """What the database says about this request's conversation turn. Raises when the database cannot answer.
    complete (assistant row saved) | partial (only the user row saved) | none."""
    if not rec.get("conv_id"):
        return {"state": "none"}
    from sqlalchemy import text as _t
    from BACKEND_DATABASE_MODELS import pii_session_factory as _psf
    async with _psf() as s:
        rows = (await s.execute(_t(
            "SELECT id, role, content FROM messages WHERE conversation_id = CAST(:c AS uuid) "
            "AND analysis->>'chat_request_id' = :rid AND analysis->>'chat_kind' IS NULL AND deleted_at IS NULL "
            "ORDER BY created_at"), {"c": rec["conv_id"], "rid": rid})).fetchall()
    asst = [r for r in rows if r[1] == "assistant"]
    if asst:
        return {"state": "complete", "answer": asst[-1][2] or "", "msg_id": str(asst[-1][0])}
    users = [str(r[0]) for r in rows if r[1] == "user"]
    return {"state": "partial", "user_ids": users} if users else {"state": "none"}


async def _try_reconcile(rec: dict, rid: str):
    try:
        return await _reconcile_turn(rec, rid)
    except Exception as exc:
        print(f"[Chat] rid={rid} reconcile failed: {type(exc).__name__}")
        return None


async def _history_failure_row(rid: str, rec: dict, text: str, reason: str) -> str:
    """The customer-facing failure message in the authoritative history (`messages`), once per request."""
    from sqlalchemy import text as _t
    from BACKEND_DATABASE_MODELS import pii_session_factory as _psf, Message as _Msg
    async with _psf() as s:
        row = (await s.execute(_t(
            "SELECT id FROM messages WHERE conversation_id = CAST(:c AS uuid) AND analysis->>'chat_request_id' = :rid "
            "AND analysis->>'chat_kind' = 'failure' AND deleted_at IS NULL LIMIT 1"),
            {"c": rec["conv_id"], "rid": rid})).fetchone()
        if row:
            return str(row[0])
        m = _Msg(conversation_id=rec["conv_id"], role="assistant", agent_name="chat_failure", content=text,
                 content_type="text", model_used="lifecycle",
                 analysis={"chat_request_id": rid, "chat_kind": "failure", "reason": reason, "delivery": "pending"})
        s.add(m)
        try:
            await s.commit()
        except IntegrityError:
            # a concurrent writer inserted it first (unique index, migration 0040): use that row
            await s.rollback()
            row = (await s.execute(_t(
                "SELECT id FROM messages WHERE analysis->>'chat_request_id' = :rid "
                "AND analysis->>'chat_kind' = 'failure' AND deleted_at IS NULL LIMIT 1"), {"rid": rid})).fetchone()
            if row is None:
                raise
            return str(row[0])
        return str(m.id)


async def _history_set_delivery(msg_id: str, state: str) -> None:
    from sqlalchemy import text as _t
    from BACKEND_DATABASE_MODELS import pii_session_factory as _psf
    async with _psf() as s:
        await s.execute(_t(
            "UPDATE messages SET analysis = COALESCE(analysis, '{}'::jsonb) || jsonb_build_object('delivery', "
            "CAST(:st AS text), 'delivery_at', CAST(:ts AS text)) WHERE id = CAST(:id AS uuid)"),
            {"st": state, "ts": datetime.now(timezone.utc).isoformat(), "id": msg_id})
        await s.commit()


async def _sync_history(r, rid: str) -> bool:
    """Make the history row match the record: the failure row exists (written BEFORE its send, delivery
    'pending'), and the outcome row carries the final delivery state. Idempotent; bounded on errors."""
    rec = await creq.get(r, rid)
    if rec is None or not rec.get("conv_id"):
        await creq.update(r, rid, history="failed", history_error="no_conversation")
        return False
    try:
        if rec.get("status") == "failed" and not rec.get("history_msg_id"):
            mid = await _history_failure_row(rid, rec, rec.get("failure_text") or "", rec.get("reason") or "")
            await creq.update(r, rid, history_msg_id=mid, history="pending")
            rec["history_msg_id"] = mid
        row = rec.get("history_msg_id") if rec.get("status") == "failed" else rec.get("answer_msg_id")
        if not row:
            await creq.update(r, rid, history="failed", history_error="no_history_row")
            return False
        if rec.get("delivery") in _TERMINAL_DELIVERY:
            await _history_set_delivery(row, rec["delivery"])
            await creq.update(r, rid, history="synced")
            return True
        await creq.update(r, rid, history="pending")
        return False
    except Exception as exc:
        n = await creq.incr(r, rid, "history_attempts")
        await creq.update(r, rid, history="failed" if n >= creq.MAX_HISTORY_ATTEMPTS else "pending",
                          history_error=type(exc).__name__)
        print(f"[Chat] rid={rid} history write failed ({n}): {type(exc).__name__}")
        return False


async def _deliver(r, rid: str, rec: dict, text: str, kind: str) -> str:
    """Send `text` once for rid and record the outcome. Never re-sends what was sent or may have been sent;
    retries (bounded across restarts) ONLY when the transport provably sent nothing."""
    send, classify = _transport(rec.get("channel") or "wa")
    cur = await creq.get(r, rid) or {}
    used = 0
    if cur.get("delivered_kind") == kind:
        if cur.get("delivery") in _TERMINAL_DELIVERY:
            return cur["delivery"]
        if cur.get("delivery") == "sending":          # the previous owner died mid-send: it may be on the phone
            await creq.update(r, rid, delivery="unknown", delivery_error="interrupted_during_send")
            print(f"[Chat] rid={rid} delivery=unknown (interrupted during send) kind={kind}")
            return "unknown"
        used = int(cur.get("delivery_attempts") or 0)
    else:
        await creq.update(r, rid, delivery_attempts=0)
    delays = list(creq.DELIVERY_RETRY_DELAYS_S)[used:]
    while True:
        n = await creq.incr(r, rid, "delivery_attempts")
        await creq.update(r, rid, delivery="sending", delivered_kind=kind)
        res = await send(rec, text)
        cls = classify(res)
        err = "" if cls == "sent" else str((res or {}).get("error") or "")[:120]
        if cls == "not_sent" and delays:
            await creq.update(r, rid, delivery="retrying", delivery_error=err)
            print(f"[Chat] rid={rid} delivery attempt {n} not sent (transport down) — retrying")
            await asyncio.sleep(delays.pop(0))
            continue
        state = "sent" if cls == "sent" else ("failed" if cls in ("not_sent", "invalid", "rejected") else "unknown")
        await creq.update(r, rid, delivery=state, delivery_error=err,
                          **({"delivered_at": time.time()} if state == "sent" else {}))
        print(f"[Chat] rid={rid} delivery={state} kind={kind} attempts={n}")
        return state


async def _finish_request(r, rid: str, rec: dict, outcome: str, answer: str = "", reason: str = "",
                          answer_msg_id: str = "") -> str:
    """Record the processing outcome, persist it in history, deliver it once, settle."""
    lang = rec.get("lang") or "he"
    if outcome == "answered":
        await creq.update(r, rid, status="answered", reason="", answer=answer, answer_msg_id=answer_msg_id,
                          answered_at=time.time())
        state = await _deliver(r, rid, rec, answer, "answer")
    else:
        text = _LATE_FAIL_TEXT.get(lang, _LATE_FAIL_TEXT["he"])
        await creq.update(r, rid, status="failed", reason=reason, failure_text=text, failed_at=time.time())
        await _sync_history(r, rid)                  # the intended failure message is on record BEFORE the send
        state = await _deliver(r, rid, rec, text, "failure")
    await _sync_history(r, rid)
    await _settle_request(r, rid, rec)
    return state


async def _settle_request(r, rid: str, rec: dict) -> None:
    cur = await creq.get(r, rid) or {}
    if rec.get("claim_body"):
        await _agent_inflight_release(r, rec["user_key"], rec["claim_body"])
    if cur.get("delivery") in _TERMINAL_DELIVERY and cur.get("history") in ("synced", "failed"):
        await creq.close(r, rid)
    if r is not None:
        print(f"[Chat] settled {creq.summary(rid, cur)}")


async def _outcome(rid: str, rec: dict, task, deadline_hit: bool) -> dict:
    if deadline_hit:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        rc = await _try_reconcile(rec, rid)
        if rc is None:
            return {"outcome": "ambiguous", "reason": "deadline_unreconciled"}
        if rc["state"] == "complete":
            ans, why = _render_agent_answer({"response": rc["answer"]})
            return ({"outcome": "answered", "answer": ans, "answer_msg_id": rc["msg_id"]} if ans
                    else {"outcome": "failed", "reason": why})
        return {"outcome": "failed", "reason": "deadline"}
    exc = task.exception()
    if exc is not None:
        print(f"[Chat] rid={rid} agent failed: {type(exc).__name__}")
        rc = await _try_reconcile(rec, rid)          # the turn may have been saved before the error
        if rc is None:
            return {"outcome": "ambiguous", "reason": f"exception_unreconciled:{type(exc).__name__}"}
        if rc["state"] == "complete":
            ans, _ = _render_agent_answer({"response": rc["answer"]})
            if ans:
                return {"outcome": "answered", "answer": ans, "answer_msg_id": rc["msg_id"]}
        return {"outcome": "failed", "reason": f"exception:{type(exc).__name__}"}
    result = task.result()
    ans, why = _render_agent_answer(result)
    if not ans:
        print(f"[Chat] rid={rid} agent produced no usable answer: {why}")
        return {"outcome": "failed", "reason": why}
    return {"outcome": "answered", "answer": ans, "answer_msg_id": str(result.get("message_id") or "")}


async def _process_request(r, rid: str, rec: dict, *, wait_s: float = None, deadline_s: float = None,
                           reuse_user_id: str = "") -> dict:
    """Run the agent for a persisted request. {"mode": "done"} when the outcome was produced within wait_s
    (delivered now), else {"mode": "deferred", "text": status note} — the outcome follows from a background task."""
    wait_s = WA_AGENT_REPLY_WAIT_S if wait_s is None else wait_s
    deadline_s = WA_AGENT_LATE_DEADLINE_S if deadline_s is None else deadline_s
    _ACTIVE_REQUESTS.add(rid)
    attempts = await creq.incr(r, rid, "attempts")
    await creq.update(r, rid, status="processing", owner=creq.BOOT_ID, processing_at=time.time())
    print(f"[Chat] rid={rid} processing attempt={attempts}")
    task = asyncio.ensure_future(_run_agent_for(rec, rid, reuse_user_id))
    _keep(task)

    async def _settle(deadline_hit=False):
        try:
            o = await _outcome(rid, rec, task, deadline_hit)
            if o["outcome"] == "ambiguous":
                await creq.update(r, rid, status="ambiguous", reason=o["reason"])
                print(f"[Chat] rid={rid} outcome ambiguous ({o['reason']}) — left open for reconciliation")
                return
            await _finish_request(r, rid, rec, o["outcome"], answer=o.get("answer", ""),
                                  reason=o.get("reason", ""), answer_msg_id=o.get("answer_msg_id", ""))
        finally:
            _ACTIVE_REQUESTS.discard(rid)

    done, _ = await asyncio.wait({task}, timeout=wait_s)
    if done:
        st = asyncio.ensure_future(_settle())
        _keep(st)
        await asyncio.wait({st}, timeout=WA_FAST_DELIVERY_WAIT_S)   # a transport outage continues in the background
        return {"mode": "done"}

    async def _late():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, deadline_s - wait_s))
        except asyncio.TimeoutError:
            print(f"[Chat] rid={rid} agent passed the {deadline_s:.0f}s deadline")
            await _settle(deadline_hit=True)
            return
        except Exception:
            pass
        await _settle()
    _keep(asyncio.ensure_future(_late()))
    return {"mode": "deferred", "text": _WAIT_TEXT.get(rec.get("lang") or "he", _WAIT_TEXT["he"])}


async def _recover_one(r, rid: str, rec: dict, out: dict) -> bool:
    """Act on one request this process now owns. True when a re-run was started (it manages its own state)."""
    status = rec.get("status")
    if status in creq.OPEN_STATUSES:
        rc = await _try_reconcile(rec, rid)
        if rc is None:
            n = await creq.incr(r, rid, "reconcile_attempts")
            if n >= creq.MAX_RECONCILE:
                await _finish_request(r, rid, rec, "failed", reason="unreconciled")
                out["failed"] += 1
            else:
                await creq.update(r, rid, status="ambiguous", reason=rec.get("reason") or "unreconciled")
                out["ambiguous"] += 1
            return False
        if rc["state"] == "complete":               # the turn was saved: reuse it, never run the agent again
            ans, why = _render_agent_answer({"response": rc["answer"]})
            if ans:
                await _finish_request(r, rid, rec, "answered", answer=ans, answer_msg_id=rc["msg_id"])
                out["reused"] += 1
            else:
                await _finish_request(r, rid, rec, "failed", reason=why)
                out["failed"] += 1
            return False
        age = time.time() - float(rec.get("created_at") or 0)
        if int(rec.get("attempts") or 0) < creq.MAX_ATTEMPTS and age < creq.MAX_AGE_S:
            # partial: the question row was saved, the answer was not — keep that row as THE question
            reuse = rc["user_ids"][0] if rc["state"] == "partial" else ""
            if reuse:
                await creq.update(r, rid, rerun_reuses_user_row=reuse)
            if rec.get("claim_body"):
                await _agent_inflight_claim(r, rec["user_key"], rec["claim_body"],
                                            int(WA_AGENT_LATE_DEADLINE_S) + 30, rid=rid)
            await _process_request(r, rid, rec, wait_s=0, reuse_user_id=reuse)
            out["rerun"] += 1
            return True
        await _finish_request(r, rid, rec, "failed", reason="interrupted")
        out["failed"] += 1
        return False
    if status == "answered":
        await _finish_request(r, rid, rec, "answered", answer=rec.get("answer") or "",
                              answer_msg_id=rec.get("answer_msg_id") or "")
        out["redelivered"] += 1
    elif status == "failed":
        await _finish_request(r, rid, rec, "failed", reason=rec.get("reason") or "")
        out["failed"] += 1
    return False


async def recover_agent_requests(r=None) -> dict:
    """One recovery pass over open requests: those of a dead owner are taken over (atomic, so concurrent passes
    never both act), and this process's own unfinished ones (ambiguous / history pending) are retried."""
    if r is None:
        try:
            from BACKEND_AUTH_SECURITY import get_redis as _gr
            r = await _gr()
        except Exception:
            r = None
    out = {"taken": 0, "rerun": 0, "reused": 0, "redelivered": 0, "failed": 0, "ambiguous": 0}
    if r is None:
        return out
    await creq.heartbeat(r)
    for rid, rec in await creq.open_requests(r):
        # Reserve BEFORE any await: check-and-add with no await in between is atomic within this process, so two
        # overlapping passes can never both act on one request (found by test R5: a pass that snapshotted the
        # record just after another pass's take-over saw "owner = me, not active" and re-ran the agent).
        if rid in _ACTIVE_REQUESTS:
            continue
        _ACTIVE_REQUESTS.add(rid)
        rerun = False
        try:
            cur = await creq.get(r, rid) or rec
            owner = cur.get("owner") or ""
            if owner != creq.BOOT_ID:
                if await creq.owner_alive(r, owner) or not await creq.take_over(r, rid, owner):
                    continue                              # alive elsewhere, or another process took it first
                await creq.update(r, rid, recovered="1")
                out["taken"] += 1
                print(f"[Chat] taking over {creq.summary(rid, cur)} from a dead process")
            if (await creq.get(r, rid) or {}).get("closed_at"):
                continue                                  # finished between the snapshot and now
            rerun = await _recover_one(r, rid, await creq.get(r, rid) or cur, out)
        finally:
            if not rerun:
                _ACTIVE_REQUESTS.discard(rid)
    if any(out.values()):
        print(f"[Chat] request recovery: {out}")
    return out


async def chat_request_recovery_loop(interval_s: float = None) -> None:
    """Supervised: heartbeat + one recovery pass every interval (also the first thing after a restart)."""
    interval_s = float(os.getenv("CHAT_REQ_RECOVERY_INTERVAL_S", "20")) if interval_s is None else interval_s
    while True:
        try:
            await recover_agent_requests()
        except Exception as exc:
            print(f"[Chat] recovery pass failed: {type(exc).__name__}")
        await asyncio.sleep(interval_s)


async def _inbound_claim(redis, key: str, rid: str) -> bool:
    """Inbound dedup that survives a crash: the key names the process handling the message; a redelivery is
    ignored while that process is alive or after it finished ("done"), and processed again only when the
    process died before persisting a request record for it."""
    if redis is None:
        return True
    if await redis.set(key, creq.BOOT_ID, ex=3600, nx=True):
        return True
    val = await redis.get(key)
    val = val.decode() if isinstance(val, bytes) else (val or "")
    if val in ("done", "1") or await creq.owner_alive(redis, val):    # "1" = written by the pre-#74 code
        return False
    if await creq.get(redis, rid) is not None:                       # persisted: recovery owns it
        return False
    await redis.set(key, creq.BOOT_ID, ex=3600)
    print(f"[Chat] rid={rid} redelivery after the handling process died — processing it")
    return True


async def _inbound_done(request: Request) -> None:
    key = getattr(request.state, "inbound_key", None)
    if not key:
        return
    try:
        from BACKEND_AUTH_SECURITY import get_redis as _gr
        _r = await _gr()
        if _r is not None:
            await _r.set(key, "done", ex=3600)
    except Exception:
        pass


@router.post("/api/v1/webhooks/whatsapp")
async def whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_pii_db)):
    """Inbound WhatsApp messages from Baileys bridge. The message id is marked "done" when handling ends, so a
    redelivery is processed again only if the handling process died first (#74)."""
    try:
        return await _whatsapp_webhook_impl(request, db)
    finally:
        await _inbound_done(request)


async def _whatsapp_webhook_impl(request: Request, db: AsyncSession):
    """Inbound WhatsApp messages from Baileys bridge."""
    from social.whatsapp_provider import parse_incoming, send_message as wa_send

    try:
        raw_data = await request.json()
    except Exception:
        return Response(content="<Response/>", media_type="text/xml")

    parsed = parse_incoming(raw_data)
    sender_phone = parsed.get("from", "")
    body = parsed.get("body", "").strip()
    profile_name = parsed.get("profile_name", "")
    reply_jid = raw_data.get("reply_jid", "")

    # De-duplicate at ingress (same pattern as the Telegram webhook) — WhatsApp
    # delivers the same message in multiple upserts, which caused every user
    # message to be processed and answered twice (found 2026-07-05).
    wa_message_id = str(raw_data.get("message_id") or "").strip()
    if wa_message_id:
        try:
            from BACKEND_AUTH_SECURITY import get_redis
            redis = await get_redis()
            if not await _inbound_claim(redis, f"wa:msg:{wa_message_id}", f"wa:{wa_message_id}"):
                return Response(content="<Response/>", media_type="text/xml")
            request.state.inbound_key = f"wa:msg:{wa_message_id}"
        except Exception:
            pass  # dedup is best-effort; never block message handling

    media_kind = str(parsed.get("media_kind") or "").strip().lower()
    media_b64 = str(parsed.get("media_base64") or "").strip()
    media_mime = str(parsed.get("media_mime") or "").strip().lower()
    media_caption = str(parsed.get("media_caption") or "").strip()
    media_too_large = bool(parsed.get("media_too_large"))

    if not sender_phone or (not body and not media_kind):
        return Response(content="<Response/>", media_type="text/xml")

    try:
        from social.whatsapp_provider import send_typing as wa_typing
        await wa_typing(sender_phone, reply_jid=reply_jid)
    except Exception:
        pass

    phone_e164 = sender_phone.replace("whatsapp:", "").strip()

    # ── OWNER COMMAND CONSOLE ─────────────────────────────────────────────────
    # The owner's WhatsApp messages are NOT customer traffic — route them to his
    # private console: chat with AVI/NOA (owner mode), live system status, and
    # approve/reject the NOA posts they send him. Text messages only (media falls
    # through). On any error, fall through to normal handling so nothing is lost.
    try:
        from agents.owner_console import is_owner, process_owner_message, OWNER_PHONE
        if is_owner(phone_e164) and body:
            # Reply to OWNER_WHATSAPP_PHONE; social/whatsapp_provider routes that number to
            # the owner's LID (the only session his devices decrypt — 2026-10-18). Never
            # build "<lid>@s.whatsapp.net": that conversion delivers nowhere (2026-07-24).
            # Generate + send the reply in the BACKGROUND and ack the webhook immediately:
            # AVI/NOA replies can take 20-90s under Cerebras rate-limits, which would
            # otherwise block the webhook and time out the bridge.
            _dest = OWNER_PHONE or sender_phone
            _body = body

            async def _owner_reply_bg():
                try:
                    from social.whatsapp_provider import send_message as _send
                    try:
                        # A hung provider call must not turn into SILENCE. Without a
                        # deadline the task waits forever and the owner simply never
                        # hears back — indistinguishable from "the agent ignored me".
                        r = await asyncio.wait_for(
                            process_owner_message(_body, source="whatsapp"),
                            timeout=_OWNER_REPLY_TIMEOUT_S,
                        )
                    except asyncio.TimeoutError:
                        r = ("⚠️ לקח לי יותר מדי זמן לענות (עומס על מנוע ה-AI). "
                             "נסה שוב, או כתוב *סטטוס* לנתונים מיידיים.")
                    # An empty reply is also silence — say something rather than nothing.
                    if not (r or "").strip():
                        r = "לא הצלחתי לנסח תשובה. כתוב *עזרה* לרשימת הפקודות."
                    await _send(to=_dest, text=r)
                except Exception as _e:
                    print(f"[owner_console] bg reply failed: {_e}", flush=True)
                    try:
                        from social.whatsapp_provider import send_message as _send2
                        await _send2(to=_dest,
                                     text=f"⚠️ שגיאה בעיבוד ההודעה: {str(_e)[:100]}")
                    except Exception:
                        pass

            # STRONG REFERENCE REQUIRED. The event loop holds only a WEAK reference to
            # a task, so a bare `create_task(...)` can be garbage-collected mid-await —
            # the owner's message is accepted, no reply is ever sent, and nothing is
            # logged. That is the intermittent "some agents didn't respond" the owner
            # reported (2026-07-29). Keep it alive until it finishes.
            _t = asyncio.create_task(_owner_reply_bg())
            _OWNER_REPLY_TASKS.add(_t)
            _t.add_done_callback(_OWNER_REPLY_TASKS.discard)
            return Response(content="<Response/>", media_type="text/xml")
    except Exception as _oe:
        print(f"[owner_console] error, falling through: {_oe}")

    user_result = await db.execute(select(User).where(User.phone == phone_e164))
    user = user_result.scalar_one_or_none()
    conversation_user_id = user.id if user else WHATSAPP_ANON_USER_ID

    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.context["whatsapp_phone"].astext == phone_e164
        ).order_by(Conversation.last_message_at.desc()).limit(1)
    )
    conversation = conv_result.scalar_one_or_none()

    # Conversation freshness (added 2026-07-05): the latest conversation was
    # reused FOREVER, so a customer saying "hi" after weeks got the bot
    # "continuing" a months-old thread ("ממשיכים עם Citroen BERLINGO 2013…").
    # If the last message is older than 24h, start a clean conversation.
    if conversation is not None and conversation.last_message_at is not None:
        _age = datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None) - conversation.last_message_at
        if _age > timedelta(hours=24):
            conversation = None

    is_new_conversation = conversation is None

    if not conversation:
        conversation = Conversation(
            user_id=conversation_user_id,
            title=f"WhatsApp {profile_name or phone_e164}",
            is_active=True,
            started_at=datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
            last_message_at=datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
            context={
                "whatsapp_phone": phone_e164,
                "profile_name": profile_name,
                "whatsapp_reply_jid": reply_jid or None,
            },
        )
        db.add(conversation)
        await db.flush()
        await db.commit()
        await db.refresh(conversation)
    else:
        is_new_conversation = False
        conversation.last_message_at = datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None)
        ctx = conversation.context if isinstance(conversation.context, dict) else {}
        ctx["whatsapp_phone"] = phone_e164
        if profile_name:
            ctx["profile_name"] = profile_name
        if reply_jid:
            ctx["whatsapp_reply_jid"] = reply_jid
        conversation.context = ctx

    conv_id = str(conversation.id)

    async def _persist_assistant_after_send(
        send_result: dict,
        text: str,
        agent_name: str = "",
        model_used: str = "",
    ) -> None:
        if not send_result.get("ok"):
            return
        _msg_kwargs: Dict[str, Any] = {
            "conversation_id": conversation.id,
            "role": "assistant",
            "content": text,
            "content_type": "text",
            "created_at": datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
        }
        if agent_name:
            _msg_kwargs["agent_name"] = agent_name
        if model_used:
            _msg_kwargs["model_used"] = model_used
            _msg_kwargs["tokens_used"] = 0
        db.add(Message(**_msg_kwargs))
        conversation.last_message_at = datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None)

    media_bytes = None
    user_content_type = "text"
    user_analysis = None

    if media_kind:
        if media_too_large and not media_b64:
            too_large_text = "הקובץ גדול מדי לעיבוד. נסה לשלוח קובץ קצר יותר או תמונה דחוסה יותר."
            send_result = await wa_send(sender_phone, too_large_text, reply_jid=reply_jid)
            await _persist_assistant_after_send(
                send_result=send_result,
                text=too_large_text,
                agent_name="media_guard",
                model_used="media_guard",
            )
            await db.commit()
            return Response(content="<Response/>", media_type="application/xml")

        if media_b64:
            raw_media = media_b64
            if raw_media.startswith("data:"):
                _, _, payload = raw_media.partition(",")
                raw_media = payload.strip()
            try:
                media_bytes = base64.b64decode(raw_media, validate=True)
            except (ValueError, binascii.Error):
                invalid_text = "לא הצלחתי לקרוא את קובץ המדיה. נסה לשלוח שוב."
                send_result = await wa_send(sender_phone, invalid_text, reply_jid=reply_jid)
                await _persist_assistant_after_send(
                    send_result=send_result,
                    text=invalid_text,
                    agent_name="media_guard",
                    model_used="media_guard",
                )
                await db.commit()
                return Response(content="<Response/>", media_type="application/xml")

            if not media_bytes:
                empty_text = "הקובץ שנשלח ריק. נסה לשלוח שוב."
                send_result = await wa_send(sender_phone, empty_text, reply_jid=reply_jid)
                await _persist_assistant_after_send(
                    send_result=send_result,
                    text=empty_text,
                    agent_name="media_guard",
                    model_used="media_guard",
                )
                await db.commit()
                return Response(content="<Response/>", media_type="application/xml")

        if media_kind == "image":
            user_content_type = "image"
            preview_mime = media_mime or "image/jpeg"
            if media_bytes:
                preview_limit = max(65536, int(os.getenv("WHATSAPP_IMAGE_PREVIEW_MAX_BYTES", "1048576")))
                if len(media_bytes) <= preview_limit:
                    canonical_b64 = base64.b64encode(media_bytes).decode("ascii")
                    user_analysis = {
                        "image_data_url": f"data:{preview_mime};base64,{canonical_b64}",
                        "media_kind": "image",
                        "media_mime": preview_mime,
                        "media_bytes": len(media_bytes),
                    }
                else:
                    user_analysis = {
                        "media_kind": "image",
                        "media_mime": preview_mime,
                        "media_bytes": len(media_bytes),
                    }
        elif media_kind in {"audio", "voice"}:
            user_content_type = "audio"
            if media_bytes:
                user_analysis = {
                    "media_kind": "audio",
                    "media_mime": media_mime or "audio/ogg; codecs=opus",
                    "media_bytes": len(media_bytes),
                }

        # Vision runs on EVERY image (fixed 2026-07-05) — previously an image
        # sent WITH a caption skipped analysis entirely (`if not body:` gated
        # it), so the bot answered the caption and silently ignored the image.
        if media_kind == "image" and media_bytes and body:
            try:
                from hf_client import hf_vision
                _img_prompt = (
                    "תאר מה מוצג בתמונה בקצרה ובמדויק (חלק רכב, צילום מסך של שגיאה, "
                    f"מסמך וכו'). ההודעה המצורפת מהלקוח: {body}"
                )
                _img_analysis = await hf_vision(
                    base64.b64encode(media_bytes).decode("ascii"),
                    _img_prompt,
                    mime=media_mime or "image/jpeg",
                )
                if (_img_analysis or "").strip():
                    body = f"{body}\n[תוכן התמונה שצורפה]: {_img_analysis.strip()}"
            except Exception as exc:
                print(f"[WhatsApp] Vision-with-caption failed (continuing with caption only): {exc}")

        if not body:
            try:
                if media_kind == "image":
                    if not media_bytes:
                        raise RuntimeError("missing image bytes")
                    from hf_client import hf_vision
                    vision_prompt = media_caption or "זהה את החלק המרכזי/הקדמי שהתמונה ממוקדת עליו (לא הצינור או הכיסוי הגדול שברקע) והצע חלפים מתאימים"
                    inferred = await hf_vision(
                        base64.b64encode(media_bytes).decode("ascii"),
                        vision_prompt,
                        mime=media_mime or "image/jpeg",
                    )
                    body = (inferred or "").strip()
                    if not body:
                        fail_text = "קיבלתי את התמונה אבל לא הצלחתי לזהות ממנה פרטים. נסה תמונה ברורה יותר."
                        send_result = await wa_send(sender_phone, fail_text, reply_jid=reply_jid)
                        await _persist_assistant_after_send(
                            send_result=send_result,
                            text=fail_text,
                            agent_name="vision_fallback",
                            model_used="vision",
                        )
                        await db.commit()
                        return Response(content="<Response/>", media_type="application/xml")
                elif media_kind in {"audio", "voice"}:
                    if not media_bytes:
                        raise RuntimeError("missing audio bytes")
                    from hf_client import hf_audio
                    transcript = await hf_audio(media_bytes)
                    body = (transcript or "").strip()
                    if not body:
                        fail_text = "קיבלתי את ההקלטה אבל לא הצלחתי לתמלל אותה. נסה הקלטה קצרה וברורה יותר."
                        send_result = await wa_send(sender_phone, fail_text, reply_jid=reply_jid)
                        await _persist_assistant_after_send(
                            send_result=send_result,
                            text=fail_text,
                            agent_name="audio_fallback",
                            model_used="audio",
                        )
                        await db.commit()
                        return Response(content="<Response/>", media_type="application/xml")
                else:
                    body = media_caption or "נשלח קובץ מדיה"
            except Exception as exc:
                safe_tail = phone_e164[-4:] if len(phone_e164) >= 4 else phone_e164
                print(f"[WhatsApp] Media processing failed for ****{safe_tail}: {exc}")
                fail_text = "אירעה שגיאה בעיבוד המדיה. נסה לשלוח שוב או כתוב את הבקשה בטקסט."
                send_result = await wa_send(sender_phone, fail_text, reply_jid=reply_jid)
                await _persist_assistant_after_send(
                    send_result=send_result,
                    text=fail_text,
                    agent_name="media_fallback",
                    model_used="media",
                )
                await db.commit()
                return Response(content="<Response/>", media_type="application/xml")

    body = body.strip() if body else ""
    if not body:
        body = media_caption or "נשלח קובץ מדיה"

    import random
    hebrew_female_names = ["ענת", "דנא", "מאיה", "שירה"]
    hebrew_male_names = ["יוסי", "ליאור", "כרם", "עמית"]
    arabic_female_names = ["لينا", "سارة", "نور", "رنا"]
    arabic_male_names = ["كرم", "محمد", "أحمد", "خالد"]

    is_arabic_speaker = any(c in body for c in "ابتةثجحخدذرزسشصضطظعغفقكلمنهوي")

    worker = None
    if isinstance(conversation.context, dict):
        worker = conversation.context.get("agent_name")
    if not worker:
        if is_arabic_speaker:
            worker = random.choice(arabic_female_names + arabic_male_names)
        else:
            worker = random.choice(hebrew_female_names + hebrew_male_names)
        conversation.context = {**conversation.context, "agent_name": worker}

    is_greeting_only = body.strip() in ("/start", "/התחל", "start", "hello", "hi", "היי", "הי", "هلا", "مرحبا", "שלום", "")
    if is_new_conversation:
        is_female = worker in (hebrew_female_names + arabic_female_names)
        if is_arabic_speaker:
            role_ar = "ممثلة" if is_female else "ممثل"
            if is_greeting_only:
                welcome = (
                    f"أهلاً وسهلاً! 👋\n"
                    f"أنا {worker}، {role_ar} خدمة العملاء في AutoSpareFinder.\n"
                    "كيف أستطيع مساعدتك اليوم؟\n"
                    "هل تبحث عن قطعة غيار، أو تريد متابعة طلب، أو لديك استفسار؟ 😊"
                )
                await db.commit()
                await wa_send(sender_phone, welcome, reply_jid=reply_jid)
                return Response(content="<Response/>", media_type="application/xml")
            else:
                welcome = (
                    f"أهلاً وسهلاً! 👋 أنا {worker}، {role_ar} خدمة العملاء في AutoSpareFinder.\n"
                    "بكل سرور سأساعدك — دعنا نجد ما تحتاجه! 🔍"
                )
                await wa_send(sender_phone, welcome, reply_jid=reply_jid)
        else:
            role_he = "נציגת" if is_female else "נציג"
            if is_greeting_only:
                welcome = (
                    f"שלום וברוכים הבאים! 👋\n"
                    f"אני {worker}, {role_he} השירות של AutoSpareFinder.\n"
                    "איך אוכל לעזור לך היום?\n"
                    "מחפש חלק לרכב, רוצה לעקוב אחר הזמנה, או שיש לך שאלה? 😊"
                )
                await db.commit()
                await wa_send(sender_phone, welcome, reply_jid=reply_jid)
                return Response(content="<Response/>", media_type="application/xml")
            else:
                welcome = (
                    f"היי! 👋 אני {worker}, {role_he} השירות של AutoSpareFinder.\n"
                    "בשמחה אעזור לך — בוא נמצא מה שאתה מחפש! 🔍"
                )
                await wa_send(sender_phone, welcome, reply_jid=reply_jid)
        from sqlalchemy import update as _update
        await db.execute(
            _update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(context=conversation.context)
        )
        await db.commit()
    elif body in ("/start", "/התחל", "start", "hello", "hi", "היי", "הי", "هلا", "مرحبا", "שלום"):
        pass

    # NOTE (2026-07-05): do NOT insert the user message here for the AI path —
    # process_customer_message() in BACKEND_AI_AGENTS.py saves it, and inserting
    # in both places stored every customer message TWICE (doubling the history
    # fed to the model). Only the early-exit human-handoff paths below, which
    # never reach the agent, persist it themselves.
    def _user_msg_row() -> Message:
        return Message(
            conversation_id=conversation.id,
            role="user",
            content=body,
            content_type=user_content_type,
            analysis=user_analysis,
        )

    handoff_settings = await _load_handoff_settings()

    if _takeover_active(conversation):
        db.add(_user_msg_row())
        await db.commit()
        return Response(content="<Response/>", media_type="application/xml")

    if _handoff_lock_active(conversation, handoff_settings):
        db.add(_user_msg_row())
        cooldown = int(handoff_settings.get("waiting_notice_cooldown_seconds") or 120)
        if _handoff_waiting_notice_due(conversation, cooldown):
            waiting_text = _handoff_waiting_message(body)
            send_result = await wa_send(sender_phone, waiting_text, reply_jid=reply_jid)
            if not send_result.get("ok"):
                safe_tail = phone_e164[-4:] if len(phone_e164) >= 4 else phone_e164
                print(f"[WhatsApp] Handoff waiting-notice send failed to ****{safe_tail}: {send_result.get('error')}")

            await _persist_assistant_after_send(
                send_result=send_result,
                text=waiting_text,
                agent_name="human_handoff_waiting",
                model_used="handoff_policy",
            )
        await db.commit()
        return Response(content="<Response/>", media_type="application/xml")

    if _wants_human_handoff(body):
        db.add(_user_msg_row())
        _apply_handoff_request(conversation, reason="intent", message=body)
        ack_text = _handoff_ack_message(body)
        send_result = await wa_send(sender_phone, ack_text, reply_jid=reply_jid)
        if not send_result.get("ok"):
            safe_tail = phone_e164[-4:] if len(phone_e164) >= 4 else phone_e164
            print(f"[WhatsApp] Handoff ack send failed to ****{safe_tail}: {send_result.get('error')}")

        await _persist_assistant_after_send(
            send_result=send_result,
            text=ack_text,
            agent_name="human_handoff",
            model_used="handoff_policy",
        )
        await db.commit()
        return Response(content="<Response/>", media_type="application/xml")

    import re as _re

    # Route ALL messages (including purchase intent) through the AI agent.
    # The agent already handles "אני רוצה להזמין" by calling create_checkout_link()
    # and returning a real Stripe payment URL. The old short-circuit was bypassing this.
    # The agent runs in ITS OWN session so a slow answer can be delivered after this request ends
    # (#71). Commit the conversation updates made above first, so that session sees them and the two
    # sessions never write conversation.context over each other.
    await db.commit()
    _lang = _reply_lang(body)
    _user_key = str(conversation_user_id)
    try:
        from BACKEND_AUTH_SECURITY import get_redis as _gr
        _ir = await _gr()
    except Exception:
        _ir = None
    # #73/#74: the request is persisted (Redis, AOF) BEFORE any asynchronous handoff, so a restart, a slow agent
    # or a transport outage can never lose a question the customer was told would be answered.
    _rid = f"wa:{wa_message_id}" if wa_message_id else f"wa:r{uuid.uuid4().hex[:16]}"
    _claim = await _agent_inflight_claim(_ir, _user_key, body, int(WA_AGENT_LATE_DEADLINE_S) + 30, rid=_rid)
    if _claim == "duplicate":
        # The same question is already being answered (the customer resent it): no second agent run,
        # no second answer — one honest note instead.
        print(f"[Chat] rid={_rid} duplicate of an in-flight question — status note only")
        await wa_send(sender_phone, _STILL_WORKING_TEXT[_lang], reply_jid=reply_jid)
        await db.commit()
        return Response(content="<Response/>", media_type="application/xml")

    _rec = {"channel": "wa", "target": sender_phone, "reply_jid": reply_jid or "", "user_key": _user_key,
            "conv_id": conv_id, "body": body, "lang": _lang, "claim_body": body}
    try:
        if not await creq.create(_ir, _rid, **_rec):
            print(f"[Chat] rid={_rid} already persisted — recovery owns it")
            return Response(content="<Response/>", media_type="application/xml")
    except Exception as exc:                 # Redis down: answer anyway, just without durability
        print(f"[Chat] rid={_rid} request record not persisted: {type(exc).__name__}")
        _ir = None
    _out = await _process_request(_ir, _rid, _rec)
    if _out["mode"] == "deferred":
        # The interim note is a STATUS, recorded apart from the outcome's delivery; never counted as the answer.
        _st = await wa_send(sender_phone, _out["text"], reply_jid=reply_jid)
        try:
            await creq.update(_ir, _rid, status_note=creq.classify_whatsapp(_st), status_note_text=_out["text"])
        except Exception as exc:
            print(f"[Chat] rid={_rid} status note bookkeeping failed: {type(exc).__name__}")

    await db.commit()

    return Response(content="<Response/>", media_type="application/xml")


@router.post("/api/v1/webhooks/telegram")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_pii_db)):
    """Inbound Telegram messages from Telegram Bot API webhook. The update id is marked "done" when handling
    ends, so a redelivered update is processed again only if the handling process died first (#74)."""
    try:
        return await _telegram_webhook_impl(request, db)
    finally:
        await _inbound_done(request)


async def _telegram_webhook_impl(request: Request, db: AsyncSession):
    """Inbound Telegram messages from Telegram Bot API webhook."""
    from social.telegram_publisher import send_telegram_message, send_telegram_chat_action

    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")

    if not secret or header_secret != secret:
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
            if not await _inbound_claim(redis, dedup_key, f"tg:{update_id}"):
                return {"ok": True, "ignored": True, "reason": "duplicate_update"}
            request.state.inbound_key = dedup_key
        except Exception as exc:
            # #74: dedup is best-effort — Redis being down must not drop the customer's message (it used to
            # return "ignored" here, so every message was lost while Redis was unavailable).
            print(f"[Telegram] Dedup check skipped: {type(exc).__name__}")

    # message_reaction_count (2026-08-16 — deep Telegram capability
    # verification): campaign-post reaction totals, a real engagement signal
    # previously invisible to the bot (not in allowed_updates). Handled here
    # rather than in feedback_analyzer.py's poll-based collectors because
    # Telegram only PUSHES this data via webhook — there is no read-only
    # "get reaction count for message X" call to poll. Correlates via
    # social_posts.external_post_ids['telegram'] == message_id (set at
    # publish time by registry.dispatch) and uses social_posts.campaign_id
    # (the FK) for attribution — same principle as every other collector
    # this pass, just event-driven instead of scheduled.
    reaction_update = update.get("message_reaction_count")
    if isinstance(reaction_update, dict):
        try:
            r_chat = reaction_update.get("chat") or {}
            r_message_id = reaction_update.get("message_id")
            reactions = reaction_update.get("reactions") or []
            total = sum(int(r.get("total_count") or 0) for r in reactions)
            if r_message_id is not None:
                import sqlalchemy as sa
                from social.feedback_analyzer import _insert_engagement_event
                async with async_session_factory() as cat_db:
                    row = (await cat_db.execute(sa.text("""
                        SELECT id, campaign_id FROM social_posts
                        WHERE external_post_ids->>'telegram' = :mid
                          AND 'telegram' = ANY(platforms)
                        LIMIT 1
                    """), {"mid": str(r_message_id)})).fetchone()
                    if row:
                        await _insert_engagement_event(
                            cat_db, post_id=str(row.id), platform="telegram",
                            external_post_id=str(r_message_id),
                            campaign_id=str(row.campaign_id) if row.campaign_id else None,
                            likes=total,
                        )
                        await cat_db.commit()
        except Exception as exc:
            logger.warning("telegram_webhook: message_reaction_count handling failed: %s", exc)
        return {"ok": True, "handled": "message_reaction_count"}

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return {"ok": True, "ignored": True, "reason": "unsupported_update"}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True, "ignored": True, "reason": "missing_chat_id"}

    typing_task: asyncio.Task | None = None

    async def _start_typing() -> None:
        nonlocal typing_task
        if typing_task and not typing_task.done():
            return

        async def _typing_keepalive() -> None:
            while True:
                await send_telegram_chat_action(chat_id, "typing")
                await asyncio.sleep(4)

        await send_telegram_chat_action(chat_id, "typing")
        typing_task = asyncio.create_task(_typing_keepalive())

    async def _stop_typing() -> None:
        nonlocal typing_task
        if not typing_task:
            return
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
        typing_task = None


    text = (message.get("text") or "").strip()
    voice = message.get("voice") or message.get("audio")
    photo = message.get("photo")
    document = message.get("document")

    conv_id = None
    used_fallback = False

    try:
        if text or voice or photo or document:
            await _start_typing()

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
                caption = message.get("caption") or "זהה את החלק המרכזי/הקדמי שהתמונה ממוקדת עליו (לא הצינור או הכיסוי הגדול שברקע) והצע חלפים מתאימים"
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

        try:
            conv_result = await db.execute(
                select(Conversation).where(
                    Conversation.context["telegram_chat_id"].astext == str(chat_id)
                ).order_by(Conversation.last_message_at.desc()).limit(1)
            )
            conversation = conv_result.scalar_one_or_none()

            # Conversation freshness (parity with WhatsApp, 2026-07-05): don't
            # resurrect a stale thread — >24h idle starts a clean conversation.
            if conversation is not None and conversation.last_message_at is not None:
                _tg_age = datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None) - conversation.last_message_at
                if _tg_age > timedelta(hours=24):
                    conversation = None

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
                        started_at=datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
                        last_message_at=datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
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
                # Fix 5: send welcome in background so we return 200 instantly (<50ms)
                asyncio.create_task(send_telegram_message(chat_id, welcome))
                return {"ok": True}

            if not conversation:
                conversation = Conversation(
                    user_id=WHATSAPP_ANON_USER_ID,
                    title=f"Telegram {tg_name}",
                    is_active=True,
                    started_at=datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
                    last_message_at=datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
                    context={
                        "telegram_chat_id": str(chat_id),
                        "telegram_user_id": tg_user_id,
                        "telegram_username": tg_username or None,
                    },
                )
                db.add(conversation)
                await db.flush()
            else:
                conversation.last_message_at = datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None)

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
                        created_at=datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
                    ))
                    _mark_handoff_waiting_notice(conversation)
                    conversation.last_message_at = datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None)
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
                    created_at=datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None),
                ))
                conversation.last_message_at = datetime.now(tz=timezone(timedelta(hours=3))).replace(tzinfo=None)
                await db.commit()
                return {
                    "ok": True,
                    "conversation_id": conv_id,
                    "handoff_requested": True,
                    "delivered": bool(send_result.get("ok")),
                }

            # #74: the 12 s wait_for used to CANCEL the agent and ask the customer (always in Hebrew) to resend.
            # Now the request is persisted first and shares the WhatsApp lifecycle: status note after the wait,
            # the answer (or one honest failure) delivered and tracked separately, recovery after a restart.
            await db.commit()
            _lang = _reply_lang(text)
            try:
                from BACKEND_AUTH_SECURITY import get_redis as _gr
                _ir = await _gr()
            except Exception:
                _ir = None
            _rid = f"tg:{update_id}" if isinstance(update_id, int) else f"tg:r{uuid.uuid4().hex[:16]}"
            _user_key = str(conversation.user_id)
            _claim = await _agent_inflight_claim(_ir, _user_key, text, int(WA_AGENT_LATE_DEADLINE_S) + 30, rid=_rid)
            if _claim == "duplicate":
                print(f"[Chat] rid={_rid} duplicate of an in-flight question — status note only")
                _st = await send_telegram_message(chat_id, _sanitize_for_telegram(_STILL_WORKING_TEXT[_lang]))
                return {"ok": True, "conversation_id": conv_id, "request_id": _rid, "mode": "duplicate",
                        "delivered": bool(_st.get("ok"))}
            _rec = {"channel": "tg", "target": str(chat_id), "user_key": _user_key, "conv_id": conv_id,
                    "body": text, "lang": _lang, "claim_body": text}
            try:
                if not await creq.create(_ir, _rid, **_rec):
                    return {"ok": True, "ignored": True, "reason": "already_persisted", "request_id": _rid}
            except Exception as exc:
                print(f"[Chat] rid={_rid} request record not persisted: {type(exc).__name__}")
                _ir = None
            _out = await _process_request(_ir, _rid, _rec)
            _cur = {}
            try:                                     # the request is in flight: never fall into the except below
                if _out["mode"] == "deferred":
                    _st = await send_telegram_message(chat_id, _sanitize_for_telegram(_out["text"]))
                    await creq.update(_ir, _rid, status_note=creq.classify_telegram(_st),
                                      status_note_text=_out["text"])
                _cur = await creq.get(_ir, _rid) or {}
            except Exception as exc:
                print(f"[Chat] rid={_rid} status note bookkeeping failed: {type(exc).__name__}")
            return {"ok": True, "conversation_id": conv_id, "request_id": _rid, "mode": _out["mode"],
                    "delivery": _cur.get("delivery") or "unknown"}
        except Exception:
            import traceback
            await db.rollback()
            used_fallback = True
            print(f"[Telegram] FULL ERROR for chat {chat_id}:")
            print(traceback.format_exc())
            # #74: an honest failure (there is no "a representative will call you back" mechanism), and the
            # webhook reports the REAL send result instead of always claiming delivered.
            reply_text = _sanitize_for_telegram(_LATE_FAIL_TEXT[_reply_lang(text)])

        send_result = await send_telegram_message(chat_id, reply_text)

        return {
            "ok": True,
            "conversation_id": conv_id,
            "fallback": used_fallback,
            "delivered": bool(send_result.get("ok")),
        }
    finally:
        await _stop_typing()
