"""Chat — all /api/v1/chat/* endpoints extracted from BACKEND_API_ROUTES.py."""

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sa_func, and_
import uuid
import os
import asyncio
import base64 as _b64
import json as _json

from BACKEND_DATABASE_MODELS import (
    get_pii_db, pii_session_factory, User, Conversation, Message, AgentRating,
)
from BACKEND_AUTH_SECURITY import (
    get_current_user, get_current_verified_user, get_redis, check_rate_limit,
    decode_access_token,
)
from BACKEND_AI_AGENTS import process_user_message, process_agent_response_for_message
from jose import JWTError
from routes.utils import _scan_bytes_for_virus, _guarded_task

router = APIRouter()


class ChatMessageRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field(..., max_length=2000)
    content_type: str = "text"


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

    # ── 3. Fire agent as asyncio background task (non-blocking) ──────────────
    async def _run_agent_bg():
        async with pii_session_factory() as bg_db:
            try:
                await process_agent_response_for_message(user_id, message, conv_id, bg_db)
            except Exception as exc:
                print(f"[BG AGENT FATAL] conv={conv_id}: {exc}")

    asyncio.create_task(_guarded_task(_run_agent_bg()))

    # ── 4. Return immediately — frontend will poll for the assistant reply ───
    return {
        "status": "processing",
        "conversation_id": conv_id,
        "user_message_id": msg_id,
        "created_at": user_msg.created_at.isoformat(),
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
    return {"conversations": [{"id": str(c.id), "title": c.title, "current_agent": c.current_agent, "last_message_at": c.last_message_at, "is_active": c.is_active, "message_count": counts.get(str(c.id), 0)} for c in convs]}


@router.get("/api/v1/chat/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": str(conv.id), "title": conv.title, "current_agent": conv.current_agent, "started_at": conv.started_at, "last_message_at": conv.last_message_at}


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
