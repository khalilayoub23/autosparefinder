"""Chat — all /api/v1/chat/* endpoints extracted from BACKEND_API_ROUTES.py."""

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sa_func, and_
import uuid
import os
import asyncio
import base64 as _b64
import json as _json
import re

from BACKEND_DATABASE_MODELS import (
    get_pii_db, pii_session_factory, async_session_factory,
    User, Conversation, Message, AgentRating, SystemSetting,
)
from BACKEND_AUTH_SECURITY import (
    get_current_user, get_current_verified_user, get_redis, check_rate_limit,
    decode_access_token,
)
from BACKEND_AI_AGENTS import process_user_message, process_agent_response_for_message
from jose import JWTError
from routes.utils import _scan_bytes_for_virus, _guarded_task

router = APIRouter()

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
    "לדבר עם מישהו",
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
    "חירום",
    "عاجل",
    "الآن",
]

_DEFAULT_HANDOFF_SETTINGS: Dict[str, Any] = {
    "sla_target_seconds": 300,
    "avg_handle_minutes": 6,
    "queue_eta_floor_seconds": 60,
    "escalation_after_seconds": 420,
    "ai_lock_during_handoff": True,
    "feedback_required_on_resolve": True,
}


def _normalize_handoff_settings(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    settings = dict(_DEFAULT_HANDOFF_SETTINGS)
    if not isinstance(raw, dict):
        return settings

    def _to_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            return fallback
        return max(minimum, min(maximum, parsed))

    def _to_bool(value: Any, fallback: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            val = value.strip().lower()
            if val in {"1", "true", "yes", "on"}:
                return True
            if val in {"0", "false", "no", "off"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return fallback

    settings["sla_target_seconds"] = _to_int(raw.get("sla_target_seconds"), settings["sla_target_seconds"], 60, 1800)
    settings["avg_handle_minutes"] = _to_int(raw.get("avg_handle_minutes"), settings["avg_handle_minutes"], 1, 30)
    settings["queue_eta_floor_seconds"] = _to_int(raw.get("queue_eta_floor_seconds"), settings["queue_eta_floor_seconds"], 15, 600)
    settings["escalation_after_seconds"] = _to_int(raw.get("escalation_after_seconds"), settings["escalation_after_seconds"], 60, 3600)
    settings["ai_lock_during_handoff"] = _to_bool(raw.get("ai_lock_during_handoff"), settings["ai_lock_during_handoff"])
    settings["feedback_required_on_resolve"] = _to_bool(raw.get("feedback_required_on_resolve"), settings["feedback_required_on_resolve"])
    return settings


async def _load_handoff_settings() -> Dict[str, Any]:
    settings = dict(_DEFAULT_HANDOFF_SETTINGS)
    try:
        async with async_session_factory() as cfg_db:
            row = (
                await cfg_db.execute(
                    select(SystemSetting).where(SystemSetting.key == "support_handoff_settings")
                )
            ).scalar_one_or_none()
            if row and row.value:
                parsed: Any = row.value
                if isinstance(parsed, str):
                    parsed = _json.loads(parsed)
                settings = _normalize_handoff_settings(parsed)
    except Exception:
        # Keep chat flow alive even if settings are malformed or catalog DB is unavailable.
        settings = dict(_DEFAULT_HANDOFF_SETTINGS)
    return settings


def _takeover_active(conversation: Conversation) -> bool:
    ctx = conversation.context if isinstance(conversation.context, dict) else {}
    return bool(ctx.get("admin_takeover_active"))


def _handoff_lock_active(conversation: Conversation, settings: Dict[str, Any]) -> bool:
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


def _handoff_ack_message() -> str:
    return (
        "קיבלתי את הבקשה שלך לנציג אנושי.\n"
        "נציג/ה אמיתי/ת יצטרף לשיחה בהקדם האפשרי.\n"
        "בינתיים אפשר להמשיך לכתוב כאן, ואנחנו לא נפספס שום הודעה."
    )


def _handoff_waiting_message() -> str:
    return (
        "הנציג/ה האנושי/ת כבר קיבל/ה את הפנייה שלך ונמצא/ת בתור לטיפול.\n"
        "אנחנו ממשיכים לעדכן את הצוות בזמן אמת, ותשובה אנושית תגיע בהקדם."
    )


def _handoff_priority_effective(priority: int, wait_seconds: Optional[int], settings: Dict[str, Any]) -> tuple[int, bool]:
    escalation_after = int(settings.get("escalation_after_seconds") or 420)
    is_escalated = wait_seconds is not None and wait_seconds >= escalation_after
    return (max(priority, 4) if is_escalated else priority, is_escalated)


async def _build_handoff_queue_map(db: AsyncSession, settings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = (
        await db.execute(
            select(Conversation)
            .where(Conversation.deleted_at.is_(None))
            .order_by(Conversation.last_message_at.desc())
            .limit(1200)
        )
    ).scalars().all()

    queue_items: list[Dict[str, Any]] = []
    for conv in rows:
        ctx = conv.context if isinstance(conv.context, dict) else {}
        if _takeover_active(conv):
            continue
        if str(ctx.get("human_handoff_status") or "none") != "requested":
            continue

        requested_at = str(ctx.get("human_handoff_requested_at") or "").strip() or None
        wait_seconds = None
        if requested_at:
            try:
                wait_seconds = max(0, int((datetime.utcnow() - datetime.fromisoformat(requested_at)).total_seconds()))
            except Exception:
                wait_seconds = None

        priority = int(ctx.get("human_handoff_priority") or 1)
        effective_priority, escalated = _handoff_priority_effective(priority, wait_seconds, settings)
        queue_items.append(
            {
                "conversation_id": str(conv.id),
                "priority": priority,
                "effective_priority": effective_priority,
                "wait_seconds": int(wait_seconds or 0),
                "escalated": escalated,
                "requested_at": requested_at or "",
            }
        )

    queue_items.sort(
        key=lambda item: (
            -int(item.get("effective_priority") or 1),
            -int(item.get("wait_seconds") or 0),
            str(item.get("requested_at") or ""),
        )
    )

    avg_handle = int(settings.get("avg_handle_minutes") or 6)
    eta_floor = int(settings.get("queue_eta_floor_seconds") or 60)
    queue_size = len(queue_items)
    queue_map: Dict[str, Dict[str, Any]] = {}
    for idx, item in enumerate(queue_items):
        pos = idx + 1
        eta_seconds = max(eta_floor, (pos - 1) * avg_handle * 60)
        queue_map[str(item["conversation_id"])] = {
            "queue_position": pos,
            "queue_size": queue_size,
            "eta_seconds": eta_seconds,
            "effective_priority": int(item.get("effective_priority") or int(item.get("priority") or 1)),
            "escalated": bool(item.get("escalated")),
        }
    return queue_map


def _conversation_handoff_meta(
    conversation: Conversation,
    queue_meta: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ctx = conversation.context if isinstance(conversation.context, dict) else {}
    requested_at = str(ctx.get("human_handoff_requested_at") or "").strip()
    wait_seconds = None
    if requested_at and not _takeover_active(conversation):
        try:
            wait_seconds = max(0, int((datetime.utcnow() - datetime.fromisoformat(requested_at)).total_seconds()))
        except Exception:
            wait_seconds = None
    raw_rating = ctx.get("human_handoff_feedback_rating")
    feedback_rating = None
    if raw_rating is not None:
        try:
            feedback_rating = int(raw_rating)
        except Exception:
            feedback_rating = None
    queue_meta = queue_meta or {}
    settings = settings or _DEFAULT_HANDOFF_SETTINGS
    eta_seconds = queue_meta.get("eta_seconds")
    eta_minutes = None
    if eta_seconds is not None:
        eta_minutes = max(1, int(round(int(eta_seconds) / 60)))

    return {
        "human_handoff_requested": bool(ctx.get("human_handoff_requested")),
        "human_handoff_status": str(ctx.get("human_handoff_status") or "none"),
        "human_handoff_requested_at": requested_at or None,
        "human_handoff_reason": str(ctx.get("human_handoff_reason") or "").strip() or None,
        "human_handoff_priority": int(ctx.get("human_handoff_priority") or 1),
        "human_handoff_wait_seconds": wait_seconds,
        "human_handoff_escalated": bool(queue_meta.get("escalated")),
        "human_handoff_effective_priority": int(queue_meta.get("effective_priority") or int(ctx.get("human_handoff_priority") or 1)),
        "human_handoff_queue_position": queue_meta.get("queue_position"),
        "human_handoff_queue_size": queue_meta.get("queue_size"),
        "human_handoff_eta_seconds": eta_seconds,
        "human_handoff_eta_minutes": eta_minutes,
        "human_handoff_sla_target_seconds": int(settings.get("sla_target_seconds") or 300),
        "human_handoff_lock_active": _handoff_lock_active(conversation, settings),
        "human_handoff_feedback_required": bool(ctx.get("human_handoff_feedback_required")),
        "human_handoff_feedback_submitted": bool(ctx.get("human_handoff_feedback_submitted")),
        "human_handoff_feedback_rating": feedback_rating,
        "human_handoff_feedback_at": str(ctx.get("human_handoff_feedback_at") or "").strip() or None,
        "human_handoff_feedback_text": str(ctx.get("human_handoff_feedback_text") or "").strip() or None,
    }


def _apply_handoff_request(conversation: Conversation, reason: str, message: str, requester_user_id: str) -> Dict[str, Any]:
    now = datetime.utcnow()
    ctx = dict(conversation.context or {})

    existing_priority = int(ctx.get("human_handoff_priority") or 1)
    incoming_priority = _handoff_priority(message)
    priority = max(existing_priority, incoming_priority)

    requested_at = str(ctx.get("human_handoff_requested_at") or "").strip() or now.isoformat()
    ctx["human_handoff_requested"] = True
    ctx["human_handoff_status"] = "requested"
    ctx["human_handoff_requested_at"] = requested_at
    ctx["human_handoff_reason"] = (reason or "manual_button").strip()
    ctx["human_handoff_priority"] = priority
    ctx["human_handoff_requester_user_id"] = requester_user_id
    ctx["human_handoff_latest_user_text"] = (message or "").strip()[:500]
    ctx["human_handoff_lock_active"] = True
    ctx["human_handoff_feedback_required"] = False
    ctx["human_handoff_feedback_submitted"] = False
    ctx["human_handoff_feedback_rating"] = None
    ctx["human_handoff_feedback_text"] = None
    ctx["human_handoff_feedback_at"] = None
    ctx["human_handoff_resolved_at"] = None
    conversation.context = ctx
    return _conversation_handoff_meta(conversation)


class ChatMessageRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field(..., max_length=2000)
    content_type: str = "text"


class HumanHandoffRequest(BaseModel):
    conversation_id: Optional[str] = None
    note: Optional[str] = Field(default="", max_length=2000)


class HumanHandoffFeedbackRequest(BaseModel):
    conversation_id: str
    rating: int = Field(..., ge=1, le=5)
    feedback: Optional[str] = Field(default="", max_length=1000)


@router.post("/api/v1/chat/message")
async def send_message(data: ChatMessageRequest, request: Request, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:chat:{ip}', 20, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    # ── 1. Get or create conversation (fast DB write only) ──────────────────
    conversation = None
    if data.conversation_id:
        result = await db.execute(select(Conversation).where(Conversation.id == data.conversation_id))
        conversation = result.scalar_one_or_none()
    if not conversation:
        conversation = Conversation(
            user_id=current_user.id,
            title=data.message[:60] + ("..." if len(data.message) > 60 else ""),
            is_active=True,
            started_at=datetime.utcnow(),
            last_message_at=datetime.utcnow(),
        )
        db.add(conversation)
        await db.flush()
    else:
        conversation.last_message_at = datetime.utcnow()

    # ── 2. Save user message immediately ─────────────────────────────────────
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=data.message,
        content_type="text",
    )
    db.add(user_msg)
    await db.commit()
    await db.refresh(user_msg)

    conv_id   = str(conversation.id)
    msg_id    = str(user_msg.id)
    user_id   = str(current_user.id)
    message   = data.message

    if _takeover_active(conversation):
        return {
            "status": "takeover",
            "conversation_id": conv_id,
            "user_message_id": msg_id,
            "created_at": user_msg.created_at.isoformat(),
        }

    handoff_settings = await _load_handoff_settings()
    if _handoff_lock_active(conversation, handoff_settings):
        queue_map = await _build_handoff_queue_map(db, handoff_settings)
        return {
            "status": "handoff_waiting",
            "conversation_id": conv_id,
            "user_message_id": msg_id,
            "created_at": user_msg.created_at.isoformat(),
            "message": _handoff_waiting_message(),
            "handoff": _conversation_handoff_meta(
                conversation,
                queue_meta=queue_map.get(conv_id),
                settings=handoff_settings,
            ),
        }

    if _wants_human_handoff(message):
        _apply_handoff_request(conversation, reason="intent", message=message, requester_user_id=str(current_user.id))
        queue_map = await _build_handoff_queue_map(db, handoff_settings)
        db.add(Message(
            conversation_id=conversation.id,
            role="assistant",
            agent_name="human_handoff",
            content=_handoff_ack_message(),
            content_type="text",
            model_used="handoff_policy",
            tokens_used=0,
            created_at=datetime.utcnow(),
        ))
        conversation.last_message_at = datetime.utcnow()
        await db.commit()
        return {
            "status": "handoff_requested",
            "conversation_id": conv_id,
            "user_message_id": msg_id,
            "created_at": user_msg.created_at.isoformat(),
            "handoff": _conversation_handoff_meta(
                conversation,
                queue_meta=queue_map.get(conv_id),
                settings=handoff_settings,
            ),
        }

    # ── 3. Fire agent as asyncio background task (non-blocking) ──────────────
    async def _run_agent_bg():
        async with pii_session_factory() as bg_db:
            try:
                await process_agent_response_for_message(
                    user_id,
                    message,
                    conv_id,
                    bg_db,
                    source="web",
                )
            except Exception as exc:
                print(f"[BG AGENT FATAL] conv={conv_id}: {exc}")
                # Save a visible error message so the user isn't left with a stuck spinner
                try:
                    async with pii_session_factory() as err_db:
                        err_db.add(Message(
                            conversation_id=conv_id,
                            role="assistant",
                            agent_name="service_agent",
                            content="מצטער, נתקלתי בבעיה טכנית. אנא נסה שוב בעוד מספר שניות.",
                            content_type="text",
                        ))
                        await err_db.commit()
                except Exception:
                    pass

    asyncio.create_task(_guarded_task(_run_agent_bg()))

    # ── 4. Return immediately — frontend will poll for the assistant reply ───
    return {
        "status": "processing",
        "conversation_id": conv_id,
        "user_message_id": msg_id,
        "created_at": user_msg.created_at.isoformat(),
    }


@router.post("/api/v1/chat/handoff/request")
async def request_human_handoff(
    body: HumanHandoffRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    conversation = None
    if body.conversation_id:
        result = await db.execute(select(Conversation).where(and_(Conversation.id == body.conversation_id, Conversation.user_id == current_user.id)))
        conversation = result.scalar_one_or_none()

    note = (body.note or "").strip()
    fallback_text = "אני רוצה לדבר עם נציג אנושי"
    trigger_text = note or fallback_text
    handoff_settings = await _load_handoff_settings()

    if not conversation:
        conversation = Conversation(
            user_id=current_user.id,
            title="בקשה לנציג אנושי",
            is_active=True,
            started_at=datetime.utcnow(),
            last_message_at=datetime.utcnow(),
        )
        db.add(conversation)
        await db.flush()

        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content=trigger_text,
            content_type="text",
            created_at=datetime.utcnow(),
        ))

    ctx = dict(conversation.context or {})
    already_requested = bool(ctx.get("human_handoff_requested"))
    _apply_handoff_request(conversation, reason="manual_button", message=trigger_text, requester_user_id=str(current_user.id))
    queue_map = await _build_handoff_queue_map(db, handoff_settings)
    meta = _conversation_handoff_meta(
        conversation,
        queue_meta=queue_map.get(str(conversation.id)),
        settings=handoff_settings,
    )

    if not already_requested:
        db.add(Message(
            conversation_id=conversation.id,
            role="assistant",
            agent_name="human_handoff",
            content=_handoff_ack_message(),
            content_type="text",
            model_used="handoff_policy",
            tokens_used=0,
            created_at=datetime.utcnow(),
        ))

    conversation.last_message_at = datetime.utcnow()
    await db.commit()

    return {
        "ok": True,
        "conversation_id": str(conversation.id),
        "handoff": meta,
        "already_requested": already_requested,
    }


@router.post("/api/v1/chat/handoff/feedback")
async def submit_handoff_feedback(
    body: HumanHandoffFeedbackRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    conversation = (
        await db.execute(
            select(Conversation).where(
                and_(
                    Conversation.id == body.conversation_id,
                    Conversation.user_id == current_user.id,
                )
            )
        )
    ).scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    ctx = dict(conversation.context or {})
    status = str(ctx.get("human_handoff_status") or "none")
    feedback_required = bool(ctx.get("human_handoff_feedback_required"))
    if status not in {"awaiting_feedback", "resolved"} and not feedback_required:
        raise HTTPException(status_code=409, detail="No human-handoff feedback is currently required")
    if bool(ctx.get("human_handoff_feedback_submitted")):
        raise HTTPException(status_code=409, detail="Feedback already submitted for this handoff")

    feedback_text = (body.feedback or "").strip()
    now_iso = datetime.utcnow().isoformat()
    ctx["human_handoff_feedback_required"] = False
    ctx["human_handoff_feedback_submitted"] = True
    ctx["human_handoff_feedback_rating"] = int(body.rating)
    ctx["human_handoff_feedback_text"] = feedback_text or None
    ctx["human_handoff_feedback_at"] = now_iso
    ctx["human_handoff_status"] = "resolved"
    ctx["human_handoff_requested"] = False
    ctx["human_handoff_lock_active"] = False
    ctx["human_handoff_resolved_at"] = now_iso
    conversation.context = ctx
    conversation.last_message_at = datetime.utcnow()

    db.add(
        AgentRating(
            conversation_id=conversation.id,
            user_id=current_user.id,
            agent_name="human_handoff",
            rating=int(body.rating),
            feedback=feedback_text or None,
        )
    )
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            agent_name="human_handoff_feedback",
            content="תודה רבה על המשוב שלך. אנחנו ממשיכים לשפר את השירות בכל פנייה.",
            content_type="text",
            model_used="handoff_feedback",
            tokens_used=0,
            created_at=datetime.utcnow(),
        )
    )
    settings = await _load_handoff_settings()
    await db.commit()
    return {
        "ok": True,
        "conversation_id": str(conversation.id),
        "handoff": _conversation_handoff_meta(conversation, settings=settings),
    }


@router.get("/api/v1/chat/conversations")
async def get_conversations(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
    msg_counts_res = await db.execute(
        select(Message.conversation_id, sa_func.count(Message.id).label("cnt"))
        .group_by(Message.conversation_id)
    )
    counts = {str(row.conversation_id): row.cnt for row in msg_counts_res}
    result = await db.execute(select(Conversation).where(Conversation.user_id == current_user.id).order_by(Conversation.last_message_at.desc()).limit(limit))
    convs = result.scalars().all()
    handoff_settings = await _load_handoff_settings()
    queue_map = await _build_handoff_queue_map(db, handoff_settings)
    return {
        "conversations": [
            {
                "id": str(c.id),
                "title": c.title,
                "current_agent": c.current_agent,
                "last_message_at": c.last_message_at,
                "is_active": c.is_active,
                "message_count": counts.get(str(c.id), 0),
                "admin_takeover_active": _takeover_active(c),
                **_conversation_handoff_meta(
                    c,
                    queue_meta=queue_map.get(str(c.id)),
                    settings=handoff_settings,
                ),
            }
            for c in convs
        ]
    }


@router.get("/api/v1/chat/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    handoff_settings = await _load_handoff_settings()
    queue_map = await _build_handoff_queue_map(db, handoff_settings)
    return {
        "id": str(conv.id),
        "title": conv.title,
        "current_agent": conv.current_agent,
        "started_at": conv.started_at,
        "last_message_at": conv.last_message_at,
        "admin_takeover_active": _takeover_active(conv),
        **_conversation_handoff_meta(
            conv,
            queue_meta=queue_map.get(str(conv.id)),
            settings=handoff_settings,
        ),
    }


@router.get("/api/v1/chat/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, current_user: User = Depends(get_current_user), limit: int = 100, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")
    result = await db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).limit(limit))
    msgs = result.scalars().all()
    return {"messages": [{"id": str(m.id), "role": m.role, "agent_name": m.agent_name, "content": m.content, "content_type": m.content_type, "created_at": m.created_at} for m in msgs]}


@router.delete("/api/v1/chat/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()
    return {"message": "Conversation deleted"}


@router.post("/api/v1/chat/upload-image")
async def upload_image(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_pii_db), request: Request = None, redis=Depends(get_redis)):
    """Upload an image and immediately run GPT-4o Vision to identify the part."""
    from hf_client import hf_vision

    if redis and request:
        allowed = await check_rate_limit(redis, f"upload_image:{current_user.id}", 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="יותר מדי בקשות — נסה שוב בעוד דקה")
    file_id = str(uuid.uuid4())
    identified_part = ""
    identified_part_en = ""
    confidence = 0.0
    possible_names: list = []

    img_bytes = await file.read()
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large — maximum 10 MB")
    _ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if file.content_type not in _ALLOWED_IMAGE_MIMES:
        raise HTTPException(status_code=415, detail=f"Unsupported image type: {file.content_type}")
    _img_scan, _img_virus = _scan_bytes_for_virus(img_bytes)
    if _img_scan == "infected":
        raise HTTPException(status_code=400, detail=f"File rejected: malware detected ({_img_virus})")

    try:
        if len(img_bytes) <= 10 * 1024 * 1024:  # always True (size validated above)
            b64 = _b64.b64encode(img_bytes).decode()
            mime = file.content_type or "image/jpeg"
            prompt = (
                "You are an expert automotive parts identifier. "
                "Look at this image and identify the car part shown. "
                "Respond ONLY with a JSON object, no markdown: "
                '{"part_name_he": "<SHORT Hebrew name as used in Israeli auto parts catalogs>", '
                '"part_name_en": "<name in English>", '
                '"possible_names": ["<alt Hebrew name 1>", "<alt Hebrew name 2>", "<alt Hebrew name 3>"], '
                '"confidence": <0.0-1.0>. '
                'IMPORTANT: part_name_he and ALL possible_names must be SHORT Hebrew terms '
                '(1-3 words) exactly as written in Israeli auto parts price lists, '
                'e.g. "מצערת", "בית מצערת", "מסנן אוויר", "משאבת מים". '
                'Do NOT use English words in possible_names.}'
            )
            raw = await hf_vision(b64, prompt, mime=mime)
            raw = raw.strip().strip("`").removeprefix("json").strip()
            parsed = _json.loads(raw)
            identified_part = parsed.get("part_name_he") or parsed.get("part_name_en", "")
            identified_part_en = parsed.get("part_name_en", "")
            confidence = float(parsed.get("confidence", 0.0))
            possible_names = parsed.get("possible_names", [])
    except Exception as e:
        print(f"[Chat Vision] error: {e}")

    return {
        "file_id": file_id,
        "identified_part": identified_part,
        "identified_part_en": identified_part_en,
        "confidence": confidence,
        "possible_names": possible_names,
    }


@router.post("/api/v1/chat/upload-audio")
async def upload_audio(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = None,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """
    Receive an audio file, transcribe via Hugging Face Whisper, then pass the
    transcription to the router agent as a normal chat message.
    """
    from hf_client import hf_audio

    if redis and request:
        allowed = await check_rate_limit(redis, f"upload_audio:{current_user.id}", 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="יותר מדי בקשות — נסה שוב בעוד דקה")
    if not os.getenv("HF_TOKEN", ""):
        raise HTTPException(status_code=503, detail="שירות התמלול אינו זמין כרגע")

    # ── 1. Read & validate ────────────────────────────────────────────────────
    audio_bytes = await file.read()

    _AUDIO_MAX = 25 * 1024 * 1024  # 25 MB
    if len(audio_bytes) > _AUDIO_MAX:
        raise HTTPException(status_code=413, detail="הקובץ גדול מדי — מקסימום 25 MB")

    _ALLOWED_AUDIO_MIMES = {"audio/webm", "audio/mp4", "audio/mpeg", "audio/ogg", "audio/wav"}
    if file.content_type not in _ALLOWED_AUDIO_MIMES:
        raise HTTPException(status_code=415, detail=f"Unsupported audio type: {file.content_type}")

    # ── 2. Virus scan ─────────────────────────────────────────────────────────
    _scan_status, _virus_name = _scan_bytes_for_virus(audio_bytes)
    if _scan_status == "infected":
        raise HTTPException(status_code=400, detail=f"הקובץ נדחה: זוהה וירוס ({_virus_name})")

    # ── 3. Transcribe via Hugging Face Whisper ────────────────────────────────
    transcription = ""
    detected_language = ""
    try:
        transcription = (await hf_audio(audio_bytes)).strip()
        detected_language = ""
    except Exception as exc:
        print(f"[AudioUpload] Whisper error: {exc}")
        raise HTTPException(status_code=502, detail="שגיאה בתמלול — נסה שוב")

    if not transcription:
        raise HTTPException(status_code=422, detail="לא ניתן היה לתמלל את הקובץ")

    # ── 4. Route transcription through Avi (router agent) ─────────────────────
    agent_response = ""
    conversation_id_out = None
    try:
        result = await process_user_message(
            user_id=str(current_user.id),
            message=transcription,
            conversation_id=conversation_id,
            db=db,
            source="web",
        )
        agent_response    = result.get("response", "")
        conversation_id_out = result.get("conversation_id")
    except Exception as exc:
        print(f"[AudioUpload] Agent error: {exc}")
        # Non-fatal — return transcription even if agent fails

    return {
        "transcription":   transcription,
        "agent_response":  agent_response,
        "language":        detected_language,
        "conversation_id": conversation_id_out,
    }


@router.post("/api/v1/chat/upload-video")
async def upload_video(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f"rate:upload_video:{ip}", 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="יותר מדי בקשות — נסה שוב בעוד דקה")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Video too large (max 50 MB)")

    allowed_mimes = {"video/mp4", "video/webm", "video/ogg"}
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in allowed_mimes:
        raise HTTPException(status_code=415, detail="Unsupported video type")

    scan_status, virus_name = _scan_bytes_for_virus(content)
    if scan_status == "infected":
        raise HTTPException(status_code=400, detail=f"File rejected: malware detected ({virus_name})")

    return {"message": "Video upload – frame analysis coming soon"}


@router.websocket("/api/v1/chat/ws")
async def chat_websocket(websocket: WebSocket, token: Optional[str] = None, db: AsyncSession = Depends(get_pii_db)):
    """Authenticated WebSocket. Client must pass ?token=<access_token> as a query param."""
    if not token:
        await websocket.close(code=4001, reason="Authentication required")
        return
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise JWTError("No user id in token")
    except (JWTError, Exception):
        await websocket.close(code=4003, reason="Invalid or expired token")
        return

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            response = {"type": "response", "content": "Echo: " + data.get("content", ""), "timestamp": datetime.utcnow().isoformat()}
            await websocket.send_json(response)
    except WebSocketDisconnect:
        pass


@router.post("/api/v1/chat/rate")
async def rate_agent(conversation_id: str, agent_name: str, rating: int, feedback: Optional[str] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    db.add(AgentRating(conversation_id=conversation_id, user_id=current_user.id, agent_name=agent_name, rating=rating, feedback=feedback))
    await db.commit()
    return {"message": "Rating submitted"}
