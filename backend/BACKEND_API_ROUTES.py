"""
==============================================================================
AUTO SPARE - API ROUTES (FastAPI)
==============================================================================
114 endpoints across 14 categories.
Imports: BACKEND_DATABASE_MODELS, BACKEND_AUTH_SECURITY, BACKEND_AI_AGENTS
==============================================================================
"""

from fastapi import FastAPI, Depends, HTTPException, status, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, validator
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc, text
import uuid
import os
import io
import asyncio
import httpx
from dotenv import load_dotenv

from BACKEND_DATABASE_MODELS import (
    get_db, async_session_factory, User, Vehicle, PartsCatalog, Order, OrderItem, Payment,
    Invoice, Return, Conversation, Message, File as FileModel,
    Notification, UserProfile, SystemSetting, SupplierPart, Supplier,
    CarBrand, SystemLog,
)
from BACKEND_AUTH_SECURITY import (
    get_current_user, get_current_active_user, get_current_verified_user,
    get_current_admin_user, register_user, login_user, complete_2fa_login,
    refresh_access_token, logout_user, create_password_reset_token,
    use_password_reset_token, change_password, update_phone_number,
    create_2fa_code, verify_2fa_code, get_redis, hash_password
)
from BACKEND_AI_AGENTS import process_user_message, process_agent_response_for_message, get_agent, OrdersAgent

load_dotenv()

# ==============================================================================
# DROPSHIPPING FULFILLMENT
# ==============================================================================

async def trigger_supplier_fulfillment(paid_orders: list, db: AsyncSession) -> None:
    """
    Called immediately after customer payment is confirmed.
    Business rule: NEVER touch suppliers before payment confirmed.

    The OrdersAgent automatically:
      1. Groups items by supplier
      2. Generates tracking numbers per supplier
      3. Advances order status: paid → supplier_ordered
      4. Notifies the customer with tracking details
      5. Records an admin audit log entry (pre-marked done)
    """
    USD_TO_ILS = float(os.getenv("USD_TO_ILS", "3.65"))
    agent = OrdersAgent()

    # Find all admin users for audit logs
    admins_res = await db.execute(select(User).where(User.is_admin == True))
    admins = admins_res.scalars().all()

    for order in paid_orders:
        # Load items with full supplier data
        items_res = await db.execute(
            select(OrderItem, SupplierPart, Supplier)
            .join(SupplierPart, OrderItem.supplier_part_id == SupplierPart.id)
            .join(Supplier, SupplierPart.supplier_id == Supplier.id)
            .where(OrderItem.order_id == order.id)
        )
        rows = items_res.all()

        if not rows:
            print(f"[Fulfillment] No supplier data for order {order.order_number} — marking processing for manual review")
            order.status = "processing"
            for admin in admins:
                db.add(Notification(
                    user_id=admin.id,
                    type="supplier_order",
                    title=f"⚠️ {order.order_number} – אין נתוני ספק",
                    message=f"ההזמנה {order.order_number} שולמה אך אין פריטים עם ספק מקושר. טיפול ידני נדרש.",
                    data={"order_id": str(order.id), "order_number": order.order_number, "needs_manual": True},
                ))
            continue

        # Group items by supplier
        by_supplier: dict = {}
        max_delivery_days = 14
        for oi, sp, sup in rows:
            key = str(sup.id)
            if key not in by_supplier:
                by_supplier[key] = {"supplier": sup, "items": [], "total_cost_ils": 0.0}
            cost_ils = float(sp.price_ils or 0) or (float(sp.price_usd or 0) * USD_TO_ILS)
            ship_ils = float(sp.shipping_cost_ils or 0) or (float(sp.shipping_cost_usd or 0) * USD_TO_ILS)
            item_cost = round((cost_ils + ship_ils) * oi.quantity, 2)
            by_supplier[key]["items"].append({
                "part_name": oi.part_name,
                "part_sku": oi.part_sku or "",
                "supplier_sku": sp.supplier_sku or "",
                "manufacturer": oi.manufacturer or "",
                "quantity": oi.quantity,
                "unit_cost_ils": round(cost_ils, 2),
                "shipping_ils": round(ship_ils, 2),
                "item_total_ils": item_cost,
                "warranty_months": sp.warranty_months or 12,
                "availability": sp.availability or "In Stock",
                "estimated_delivery_days": sp.estimated_delivery_days or 14,
            })
            by_supplier[key]["total_cost_ils"] += item_cost
            if (sp.estimated_delivery_days or 14) > max_delivery_days:
                max_delivery_days = sp.estimated_delivery_days or 14

        # ── Agent auto-fulfills: generates tracking, updates order, notifies customer ──
        all_tracking = await agent.auto_fulfill_order(order, by_supplier, db)

        # ── Admin audit log: pre-marked done (agent handled it) ──
        for sup_id, sup_data in by_supplier.items():
            sup = sup_data["supplier"]
            # Find the tracking the agent assigned to this supplier
            sup_tracking = next(
                (t for t in (all_tracking or []) if t["supplier"] == sup.name), {}
            )
            items_lines = "\n".join(
                f"  • {it['part_name']} ×{it['quantity']} — ₪{it['item_total_ils']:.2f}"
                for it in sup_data["items"]
            )
            for admin in admins:
                db.add(Notification(
                    user_id=admin.id,
                    type="supplier_order",
                    title=f"🤖 הסוכן הזמין מ-{sup.name} עבור {order.order_number}",
                    message=(
                        f"הסוכן ביצע הזמנה אוטומטית מ-{sup.name}.\n{items_lines}\n"
                        f"מספר מעקב: {sup_tracking.get('tracking_number', '—')} ({sup_tracking.get('carrier', '—')})"
                    ),
                    data={
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "supplier_id": str(sup.id),
                        "supplier_name": sup.name,
                        "supplier_website": sup.website or "",
                        "items": sup_data["items"],
                        "total_cost_ils": round(sup_data["total_cost_ils"], 2),
                        "currency": "ILS",
                        "tracking_number": sup_tracking.get("tracking_number", ""),
                        "tracking_url": sup_tracking.get("tracking_url", ""),
                        "carrier": sup_tracking.get("carrier", ""),
                        "auto_fulfilled": True,
                    },
                    read_at=datetime.utcnow(),   # pre-marked done — agent handled it
                ))

        print(f"[Fulfillment] Order {order.order_number}: agent auto-fulfilled {len(by_supplier)} supplier(s) → supplier_ordered")

    await db.flush()

# ==============================================================================
# APP INIT
# ==============================================================================

app = FastAPI(
    title="Auto Spare API",
    description="AI-powered auto parts marketplace – multi-agent system",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# SCHEMAS
# ==============================================================================

class RegisterRequest(BaseModel):
    email: EmailStr
    phone: str
    password: str
    full_name: str

    @validator("phone")
    def validate_phone(cls, v):
        if not v.startswith("05") or len(v) != 10:
            raise ValueError("Invalid Israeli phone number (must start with 05, 10 digits)")
        return v

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    trust_device: bool = False

class Login2FARequest(BaseModel):
    user_id: str
    code: str
    trust_device: bool = False

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class UpdatePhoneRequest(BaseModel):
    new_phone: str
    verification_code: str

class ChatMessageRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str
    content_type: str = "text"

class PartsSearchRequest(BaseModel):
    query: str
    vehicle_id: Optional[str] = None
    category: Optional[str] = None
    limit: int = 20

class VehicleIdentifyRequest(BaseModel):
    license_plate: str

class OrderItemCreate(BaseModel):
    part_id: Optional[str] = None
    supplier_part_id: str
    quantity: int = 1

class OrderCreate(BaseModel):
    items: List[OrderItemCreate]
    shipping_address: Dict[str, str]

class OrderCancelRequest(BaseModel):
    reason: str

class ReturnRequest(BaseModel):
    order_id: str
    reason: str
    description: Optional[str] = None

class NewsletterSubscribeRequest(BaseModel):
    email: EmailStr
    preferences: Optional[List[str]] = ["promotions"]

class CouponValidateRequest(BaseModel):
    code: str

class SupplierCreate(BaseModel):
    name: str
    country: str
    website: Optional[str] = None
    api_endpoint: Optional[str] = None
    priority: int = 0


# ==============================================================================
# 1. AUTH  /api/v1/auth  (15 endpoints)
# ==============================================================================

@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Register new user and send 2FA SMS"""
    user = await register_user(data.email, data.phone, data.password, data.full_name, db)
    await create_2fa_code(str(user.id), user.phone, db)
    return {
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name},
        "message": f"קוד אימות נשלח ל-{user.phone[-4:]}",
    }


@app.post("/api/v1/auth/login")
async def login(data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db), redis=Depends(get_redis)):
    """Login – returns tokens or triggers 2FA"""
    from BACKEND_AUTH_SECURITY import generate_device_fingerprint
    device_fp = generate_device_fingerprint(request)
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    try:
        user, access_token, refresh_token = await login_user(
            data.email, data.password, device_fp, ip, ua, data.trust_device, db, redis
        )
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "is_verified": user.is_verified, "is_admin": user.is_admin},
        }
    except HTTPException as e:
        if e.status_code == status.HTTP_202_ACCEPTED:
            return JSONResponse(status_code=202, content={
                "requires_2fa": True,
                "user_id": e.headers.get("X-User-ID"),
                "message": e.detail,
            })
        raise


@app.post("/api/v1/auth/verify-2fa")
async def verify_2fa(data: Login2FARequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Complete login with 2FA code"""
    from BACKEND_AUTH_SECURITY import generate_device_fingerprint
    device_fp = generate_device_fingerprint(request)
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    user, access_token, refresh_token = await complete_2fa_login(
        data.user_id, data.code, device_fp, ip, ua, data.trust_device, db
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "is_verified": user.is_verified, "is_admin": user.is_admin},
    }


@app.post("/api/v1/auth/refresh")
async def refresh_token(data: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    """Refresh access token"""
    new_access, new_refresh = await refresh_access_token(data.refresh_token, db)
    return {"access_token": new_access, "refresh_token": new_refresh, "token_type": "bearer"}


@app.post("/api/v1/auth/verify-email")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    return {"message": "Email verified"}


@app.post("/api/v1/auth/verify-phone")
async def verify_phone(code: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    success = await verify_2fa_code(str(current_user.id), code, db)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid code")
    current_user.is_verified = True
    await db.commit()
    return {"message": "Phone verified"}


@app.post("/api/v1/auth/send-2fa")
async def send_2fa(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await create_2fa_code(str(current_user.id), current_user.phone, db)
    return {"message": f"קוד נשלח ל-{current_user.phone[-4:]}"}


@app.post("/api/v1/auth/logout")
async def logout(current_user: User = Depends(get_current_user)):
    return {"message": "Logged out successfully"}


@app.get("/api/v1/auth/me")
async def get_me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await db.get(UserProfile, current_user.id)
    terms_accepted_at = None
    if profile is None:
        # try by user_id FK
        result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
        profile = result.scalar_one_or_none()
    if profile:
        terms_accepted_at = profile.terms_accepted_at
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "phone": current_user.phone,
        "full_name": current_user.full_name,
        "is_verified": current_user.is_verified,
        "is_admin": current_user.is_admin,
        "created_at": current_user.created_at,
        "terms_accepted_at": terms_accepted_at,
    }


@app.post("/api/v1/auth/accept-terms")
async def accept_terms(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Record that the logged-in user has accepted the privacy policy and terms of service."""
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    now = datetime.utcnow()
    if profile is None:
        profile = UserProfile(user_id=current_user.id, terms_accepted_at=now)
        db.add(profile)
    else:
        profile.terms_accepted_at = now
    await db.commit()
    return {"terms_accepted_at": now}


@app.post("/api/v1/auth/reset-password")
async def reset_password(data: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    await create_password_reset_token(data.email, db)
    return {"message": "אם המייל קיים במערכת, נשלח קישור לאיפוס סיסמה"}


@app.post("/api/v1/auth/reset-password/confirm")
async def reset_password_confirm(data: PasswordResetConfirmRequest, db: AsyncSession = Depends(get_db)):
    success = await use_password_reset_token(data.token, data.new_password, db)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    return {"message": "הסיסמה שונתה בהצלחה"}


@app.post("/api/v1/auth/change-password")
async def change_password_ep(data: ChangePasswordRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await change_password(current_user, data.current_password, data.new_password, db)
    return {"message": "הסיסמה שונתה בהצלחה"}


@app.get("/api/v1/auth/trusted-devices")
async def get_trusted_devices(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserSession
    result = await db.execute(
        select(UserSession).where(and_(
            UserSession.user_id == current_user.id,
            UserSession.is_trusted_device == True,
            UserSession.trusted_until > datetime.utcnow(),
            UserSession.revoked_at.is_(None),
        ))
    )
    sessions = result.scalars().all()
    return {"devices": [{"id": str(s.id), "device_name": s.device_name or "Unknown", "last_used": s.last_used_at, "trusted_until": s.trusted_until} for s in sessions]}


@app.post("/api/v1/auth/trust-device")
async def trust_device(device_fingerprint: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserSession
    result = await db.execute(
        select(UserSession).where(and_(
            UserSession.user_id == current_user.id,
            UserSession.device_fingerprint == device_fingerprint,
            UserSession.revoked_at.is_(None),
        )).order_by(UserSession.created_at.desc()).limit(1)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.is_trusted_device = True
    session.trusted_until = datetime.utcnow() + timedelta(days=180)
    await db.commit()
    return {"message": "Device trusted for 6 months"}


@app.delete("/api/v1/auth/trusted-devices/{device_id}")
async def delete_trusted_device(device_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserSession
    result = await db.execute(select(UserSession).where(and_(UserSession.id == device_id, UserSession.user_id == current_user.id)))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Device not found")
    session.is_trusted_device = False
    session.trusted_until = None
    await db.commit()
    return {"message": "Device trust removed"}


# ==============================================================================
# 2. CHAT  /api/v1/chat  (10 endpoints)
# ==============================================================================

@app.post("/api/v1/chat/message")
async def send_message(data: ChatMessageRequest, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
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
        async with async_session_factory() as bg_db:
            try:
                await process_agent_response_for_message(user_id, message, conv_id, bg_db)
            except Exception as exc:
                print(f"[BG AGENT FATAL] conv={conv_id}: {exc}")

    asyncio.create_task(_run_agent_bg())

    # ── 4. Return immediately — frontend will poll for the assistant reply ───
    return {
        "status": "processing",
        "conversation_id": conv_id,
        "user_message_id": msg_id,
        "created_at": user_msg.created_at.isoformat(),
    }


@app.get("/api/v1/chat/conversations")
async def get_conversations(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func as sa_func
    msg_counts_res = await db.execute(
        select(Message.conversation_id, sa_func.count(Message.id).label("cnt"))
        .group_by(Message.conversation_id)
    )
    counts = {str(row.conversation_id): row.cnt for row in msg_counts_res}
    result = await db.execute(select(Conversation).where(Conversation.user_id == current_user.id).order_by(Conversation.last_message_at.desc()).limit(limit))
    convs = result.scalars().all()
    return {"conversations": [{"id": str(c.id), "title": c.title, "current_agent": c.current_agent, "last_message_at": c.last_message_at, "is_active": c.is_active, "message_count": counts.get(str(c.id), 0)} for c in convs]}


@app.get("/api/v1/chat/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": str(conv.id), "title": conv.title, "current_agent": conv.current_agent, "started_at": conv.started_at, "last_message_at": conv.last_message_at}


@app.get("/api/v1/chat/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, current_user: User = Depends(get_current_user), limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")
    result = await db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).limit(limit))
    msgs = result.scalars().all()
    return {"messages": [{"id": str(m.id), "role": m.role, "agent_name": m.agent_name, "content": m.content, "content_type": m.content_type, "created_at": m.created_at} for m in msgs]}


@app.delete("/api/v1/chat/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()
    return {"message": "Conversation deleted"}


@app.post("/api/v1/chat/upload-image")
async def upload_image(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    """Upload an image and immediately run GPT-4o Vision to identify the part."""
    import base64 as _b64
    from openai import AsyncOpenAI
    import json as _json

    file_id = str(uuid.uuid4())
    identified_part = ""
    identified_part_en = ""
    confidence = 0.0
    possible_names: list = []

    github_token = os.getenv("GITHUB_TOKEN", "")
    if github_token:
        try:
            img_bytes = await file.read()
            if len(img_bytes) <= 10 * 1024 * 1024:  # 10 MB limit
                b64 = _b64.b64encode(img_bytes).decode()
                mime = file.content_type or "image/jpeg"
                client = AsyncOpenAI(
                    base_url="https://models.inference.ai.azure.com",
                    api_key=github_token,
                )
                resp = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "You are an expert automotive parts identifier. "
                                    "Look at this image and identify the car part shown. "
                                    "Respond ONLY with a JSON object, no markdown: "
                                    '{"part_name_he": "<name in Hebrew>", '
                                    '"part_name_en": "<name in English>", '
                                    '"possible_names": ["<alt 1>", "<alt 2>"], '
                                    '"confidence": <0.0-1.0>}'
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64}"},
                            },
                        ],
                    }],
                    max_tokens=200,
                )
                raw = resp.choices[0].message.content.strip().strip("`").removeprefix("json").strip()
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


@app.post("/api/v1/chat/upload-audio")
async def upload_audio(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Audio upload – transcription coming soon"}


@app.post("/api/v1/chat/upload-video")
async def upload_video(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Video upload – frame analysis coming soon"}


@app.websocket("/api/v1/chat/ws")
async def chat_websocket(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            response = {"type": "response", "content": "Echo: " + data.get("content", ""), "timestamp": datetime.utcnow().isoformat()}
            await websocket.send_json(response)
    except WebSocketDisconnect:
        pass


@app.post("/api/v1/chat/rate")
async def rate_agent(conversation_id: str, agent_name: str, rating: int, feedback: Optional[str] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import AgentRating
    db.add(AgentRating(conversation_id=conversation_id, user_id=current_user.id, agent_name=agent_name, rating=rating, feedback=feedback))
    await db.commit()
    return {"message": "Rating submitted"}


# ==============================================================================
# 3. PARTS  /api/v1/parts  (7 endpoints)
# ==============================================================================

@app.get("/api/v1/parts/search")
async def search_parts(
    query: str = "",
    vehicle_id: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "name",
    sort_dir: str = "asc",
    vehicle_manufacturer: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    agent = get_agent("parts_finder_agent")
    parts = await agent.search_parts_in_db(
        query, vehicle_id, category, db, limit, offset, sort_by, sort_dir,
        vehicle_manufacturer=vehicle_manufacturer,
    )

    # Total count — uses same alias-expanded search terms as search_parts_in_db
    count_stmt = select(func.count()).select_from(PartsCatalog).where(PartsCatalog.is_active == True)
    if vehicle_manufacturer:
        normalized_mfr = await agent.normalize_manufacturer(vehicle_manufacturer, db)
        words = vehicle_manufacturer.split()
        word_normalized = normalized_mfr
        for word in words:
            if len(word) >= 3:
                candidate = await agent.normalize_manufacturer(word, db)
                if candidate.lower() != word.lower():
                    word_normalized = candidate
                    break
        mfr_terms = {vehicle_manufacturer, normalized_mfr, word_normalized}
        count_stmt = count_stmt.where(or_(*[PartsCatalog.manufacturer.ilike(f"%{t}%") for t in mfr_terms]))
    if query:
        normalized = await agent.normalize_manufacturer(query, db)
        search_terms = {query, normalized} if normalized.lower() != query.lower() else {query}
        conditions = []
        for term in search_terms:
            conditions += [
                PartsCatalog.name.ilike(f"%{term}%"),
                PartsCatalog.manufacturer.ilike(f"%{term}%"),
                PartsCatalog.sku.ilike(f"%{term}%"),
                PartsCatalog.category.ilike(f"%{term}%"),
            ]
        count_stmt = count_stmt.where(or_(*conditions))
    if category:
        count_stmt = count_stmt.where(PartsCatalog.category.ilike(category))
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    return {"parts": parts, "count": len(parts), "total": total, "offset": offset, "limit": limit}


@app.post("/api/v1/parts/search-by-vehicle")
async def search_parts_by_vehicle(vehicle_id: str, category: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    agent = get_agent("parts_finder_agent")
    parts = await agent.search_parts_in_db("", vehicle_id, category, db)
    return {"vehicle": {"manufacturer": vehicle.manufacturer, "model": vehicle.model, "year": vehicle.year}, "parts": parts}


@app.get("/api/v1/parts/categories")
async def get_categories(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    result = await db.execute(
        select(PartsCatalog.category, func.count(PartsCatalog.id).label("cnt"))
        .where(PartsCatalog.is_active == True)
        .group_by(PartsCatalog.category)
        .order_by(func.count(PartsCatalog.id).desc())
    )
    rows = result.fetchall()
    categories = [r[0] for r in rows if r[0]]
    counts = {r[0]: r[1] for r in rows if r[0]}
    return {"categories": categories, "counts": counts, "total": len(categories)}


@app.get("/api/v1/parts/autocomplete")
async def autocomplete_parts(q: str = "", limit: int = 8, db: AsyncSession = Depends(get_db)):
    """Return distinct part names containing the query string (uses GIN trigram index)."""
    q = q.strip()
    if len(q) < 2:
        return {"suggestions": []}
    result = await db.execute(
        select(PartsCatalog.name, PartsCatalog.manufacturer, PartsCatalog.category)
        .where(PartsCatalog.is_active == True)
        .where(PartsCatalog.name.ilike(f"%{q}%"))
        .order_by(PartsCatalog.name)
        .limit(limit)
    )
    rows = result.fetchall()
    suggestions = [
        {"name": r[0], "manufacturer": r[1], "category": r[2]}
        for r in rows
    ]
    return {"suggestions": suggestions, "query": q}


async def get_manufacturers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog.manufacturer).distinct().where(PartsCatalog.is_active == True))
    return {"manufacturers": [m for m in result.scalars().all() if m]}


# ==============================================================================
# BRANDS REFERENCE  /api/v1/brands
# ==============================================================================

@app.get("/api/v1/brands")
async def get_brands(
    region: Optional[str] = None,
    group: Optional[str] = None,
    is_luxury: Optional[bool] = None,
    is_electric: Optional[bool] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return the car_brands reference table with optional filters."""
    stmt = select(CarBrand).where(CarBrand.is_active == True)
    if region:
        stmt = stmt.where(CarBrand.region == region)
    if group:
        stmt = stmt.where(CarBrand.group_name.ilike(f"%{group}%"))
    if is_luxury is not None:
        stmt = stmt.where(CarBrand.is_luxury == is_luxury)
    if is_electric is not None:
        stmt = stmt.where(CarBrand.is_electric_focused == is_electric)
    if q:
        stmt = stmt.where(
            CarBrand.name.ilike(f"%{q}%") | CarBrand.name_he.ilike(f"%{q}%")
        )
    stmt = stmt.order_by(CarBrand.name)
    result = await db.execute(stmt)
    brands = result.scalars().all()
    return {
        "brands": [
            {
                "id": str(b.id),
                "name": b.name,
                "name_he": b.name_he,
                "group_name": b.group_name,
                "country": b.country,
                "region": b.region,
                "is_luxury": b.is_luxury,
                "is_electric_focused": b.is_electric_focused,
                "website": b.website,
                "has_parts": False,  # enriched below
            }
            for b in brands
        ],
        "total": len(brands),
    }


@app.get("/api/v1/brands/with-parts")
async def get_brands_with_parts(db: AsyncSession = Depends(get_db)):
    """Return brands that have actual parts in parts_catalog, merged with registry info."""
    # Brands that have parts
    parts_result = await db.execute(
        select(PartsCatalog.manufacturer, func.count().label("parts_count"))
        .where(PartsCatalog.is_active == True)
        .group_by(PartsCatalog.manufacturer)
        .order_by(func.count().desc())
    )
    parts_by_mfr = {row[0]: row[1] for row in parts_result.fetchall() if row[0]}

    # All known brands
    brand_result = await db.execute(select(CarBrand).where(CarBrand.is_active == True).order_by(CarBrand.name))
    all_brands = brand_result.scalars().all()

    # Merge: known brands get parts count; parts-only manufacturers get minimal entry
    merged = []
    seen_names = set()
    for b in all_brands:
        # Exact case-insensitive match on canonical name
        count = 0
        for mfr_name in list(parts_by_mfr.keys()):
            if mfr_name and mfr_name.lower() == b.name.lower():
                count += parts_by_mfr[mfr_name]
                seen_names.add(mfr_name.lower())
        # Also match via aliases stored in car_brands
        aliases = b.aliases or []
        for alias in aliases:
            for mfr_name in list(parts_by_mfr.keys()):
                if mfr_name and mfr_name.lower() == alias.lower():
                    count += parts_by_mfr[mfr_name]
                    seen_names.add(mfr_name.lower())
        seen_names.add(b.name.lower())
        merged.append({
            "name": b.name, "name_he": b.name_he,
            "group_name": b.group_name, "country": b.country,
            "region": b.region, "is_luxury": b.is_luxury,
            "is_electric_focused": b.is_electric_focused,
            "website": b.website, "parts_count": count,
            "has_parts": count > 0,
            "aliases": aliases,
        })

    # Add any parts-only manufacturers not in car_brands registry
    for mfr_name, mfr_count in parts_by_mfr.items():
        if mfr_name and mfr_name.lower() not in seen_names:
            # Check if it matches any brand already included
            already = any(mfr_name.lower() in m["name"].lower() for m in merged)
            if not already:
                merged.append({
                    "name": mfr_name, "name_he": None, "group_name": None,
                    "country": None, "region": None, "is_luxury": False,
                    "is_electric_focused": False, "website": None,
                    "parts_count": mfr_count, "has_parts": True,
                })

    return {"brands": merged, "total": len(merged)}


@app.get("/api/v1/brands/{brand_name}/parts")
async def get_parts_by_brand(
    brand_name: str,
    category: Optional[str] = None,
    part_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Return parts for a specific brand (by canonical name or alias), with pricing from supplier_parts."""
    # Resolve brand aliases
    brand_result = await db.execute(
        select(CarBrand).where(CarBrand.is_active == True).where(
            or_(CarBrand.name.ilike(brand_name), CarBrand.name_he.ilike(brand_name))
        ).limit(1)
    )
    brand = brand_result.scalar_one_or_none()

    # Build manufacturer name set to search
    mfr_names: list[str] = [brand_name]
    if brand:
        mfr_names = [brand.name] + (brand.aliases or [])

    # Query parts
    stmt = (
        select(PartsCatalog)
        .where(PartsCatalog.is_active == True)
        .where(or_(*[PartsCatalog.manufacturer.ilike(m) for m in mfr_names]))
    )
    if category:
        stmt = stmt.where(PartsCatalog.category.ilike(f"%{category}%"))
    if part_type:
        stmt = stmt.where(PartsCatalog.part_type == part_type)

    # Count total
    count_stmt = (
        select(func.count(PartsCatalog.id))
        .where(PartsCatalog.is_active == True)
        .where(or_(*[PartsCatalog.manufacturer.ilike(m) for m in mfr_names]))
    )
    if category:
        count_stmt = count_stmt.where(PartsCatalog.category.ilike(f"%{category}%"))
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    stmt = stmt.order_by(PartsCatalog.category, PartsCatalog.name).offset(offset).limit(limit)
    result = await db.execute(stmt)
    parts = result.scalars().all()

    if not parts:
        return {"brand": brand.name if brand else brand_name, "brand_he": brand.name_he if brand else None,
                "total": total, "offset": offset, "limit": limit, "parts": []}

    from BACKEND_AI_AGENTS import PartsFinderAgent, get_supplier_shipping
    agent = PartsFinderAgent()

    # Batch fetch best supplier_part for all parts in one query (no N+1)
    part_ids = [part.id for part in parts]
    sp_batch = await db.execute(
        text("""
            SELECT DISTINCT ON (sp.part_id)
                sp.id AS sp_id, sp.part_id, sp.price_usd, sp.price_ils,
                sp.shipping_cost_usd, sp.shipping_cost_ils,
                sp.is_available, sp.warranty_months, sp.estimated_delivery_days,
                s.name AS supplier_name, s.country AS supplier_country
            FROM supplier_parts sp
            JOIN suppliers s ON sp.supplier_id = s.id
            WHERE sp.part_id = ANY(:pids) AND s.is_active = true
            ORDER BY sp.part_id, sp.is_available DESC, s.priority ASC
        """),
        {"pids": part_ids},
    )
    sp_map = {str(r.part_id): r for r in sp_batch.fetchall()}

    output = []
    for part in parts:
        sp_row = sp_map.get(str(part.id))
        pricing = None
        if sp_row:
            # Prefer stored ILS price (avoids exchange-rate round-trips)
            cost_ils = float(sp_row.price_ils or 0)
            ship_ils = float(sp_row.shipping_cost_ils or 0)
            delivery_fee = get_supplier_shipping(sp_row.supplier_name or "")
            if cost_ils > 0:
                pricing = agent.calculate_customer_price_from_ils(cost_ils, ship_ils, customer_shipping=delivery_fee)
            else:
                pricing = agent.calculate_customer_price(
                    float(sp_row.price_usd), float(sp_row.shipping_cost_usd or 0), customer_shipping=delivery_fee
                )
            pricing["availability"] = "in_stock" if sp_row.is_available else "on_order"
            pricing["warranty_months"] = sp_row.warranty_months
            pricing["estimated_delivery_days"] = sp_row.estimated_delivery_days
            pricing["supplier_part_id"] = str(sp_row.sp_id)

        output.append({
            "id": str(part.id),
            "sku": part.sku,
            "name": part.name,
            "manufacturer": part.manufacturer,
            "category": part.category,
            "part_type": part.part_type,
            "description": part.description,
            "compatible_vehicles": part.compatible_vehicles or [],
            "pricing": pricing,
        })

    return {
        "brand": brand.name if brand else brand_name,
        "brand_he": brand.name_he if brand else None,
        "total": total,
        "offset": offset,
        "limit": limit,
        "parts": output,
    }


@app.get("/api/v1/parts/search-by-vin")
async def search_parts_by_vin(
    vin: str,
    part_query: Optional[str] = "",
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Decode a VIN via NHTSA free API and search parts for that vehicle."""
    vin_clean = vin.strip().upper().replace("-", "")
    if len(vin_clean) != 17:
        raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters")

    nhtsa_url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin_clean}?format=json"
    vehicle_info = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(nhtsa_url)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("Results", [{}])[0]
            def nhtsa(key): return (results.get(key) or "").strip() or None
            manufacturer = nhtsa("Make") or nhtsa("Manufacturer") or ""
            model        = nhtsa("Model") or ""
            year_str     = nhtsa("ModelYear") or ""
            engine_cc    = nhtsa("DisplacementCC")
            fuel_type    = nhtsa("FuelTypePrimary")
            transmission = nhtsa("TransmissionStyle")
            drive_type   = nhtsa("DriveType")
            body_class   = nhtsa("BodyClass")
            doors        = nhtsa("Doors")
            plant_country = nhtsa("PlantCountry")
            vehicle_info = {
                "vin": vin_clean,
                "manufacturer": manufacturer,
                "model": model,
                "year": int(year_str) if year_str and year_str.isdigit() else 0,
                "engine_cc": engine_cc,
                "fuel_type": fuel_type,
                "transmission": transmission,
                "drive_type": drive_type,
                "body_class": body_class,
                "doors": doors,
                "country_of_origin": plant_country,
            }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[VIN] NHTSA error: {e}")
        raise HTTPException(status_code=502, detail="שגיאה בפענוח ה-VIN – נסה שוב")

    if not vehicle_info.get("manufacturer"):
        raise HTTPException(status_code=404, detail=f"לא נמצא מידע עבור VIN: {vin_clean}")

    agent = get_agent("parts_finder_agent")
    search_q = " ".join(filter(None, [vehicle_info["manufacturer"], part_query])).strip()
    parts_list = await agent.search_parts_in_db(search_q, None, category, db, limit=limit, offset=offset)

    from sqlalchemy import func
    from BACKEND_DATABASE_MODELS import PartsCatalog as PC2
    count_stmt = select(func.count()).select_from(PC2).where(PC2.is_active == True)
    if search_q:
        count_stmt = count_stmt.where(PC2.name.ilike(f"%{search_q}%") | PC2.manufacturer.ilike(f"%{search_q}%"))
    total = (await db.execute(count_stmt)).scalar_one()

    return {"vehicle": vehicle_info, "parts": parts_list, "total": total, "offset": offset, "limit": limit}


@app.get("/api/v1/parts/{part_id}")
async def get_part(part_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog).where(PartsCatalog.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return {"id": str(part.id), "name": part.name, "manufacturer": part.manufacturer, "category": part.category, "part_type": part.part_type, "description": part.description, "specifications": part.specifications}


@app.post("/api/v1/parts/compare")
async def compare_parts(part_id: str, db: AsyncSession = Depends(get_db)):
    """Return all supplier options for a part (in_stock first, then on_order fallback)."""
    # Try in_stock first
    result = await db.execute(
        select(SupplierPart, Supplier).join(Supplier)
        .where(and_(SupplierPart.part_id == part_id, SupplierPart.is_available == True, Supplier.is_active == True))
        .order_by(Supplier.priority.asc())
    )
    rows = result.all()

    # Fallback to on_order if nothing in stock
    if not rows:
        result2 = await db.execute(
            select(SupplierPart, Supplier).join(Supplier)
            .where(and_(SupplierPart.part_id == part_id, Supplier.is_active == True))
            .order_by(Supplier.priority.asc())
        )
        rows = result2.all()

    from BACKEND_AI_AGENTS import get_supplier_shipping as _get_ship
    agent = get_agent("parts_finder_agent")
    comparisons = []
    for sp, supplier in rows:
        cost_ils = float(sp.price_ils or 0)
        ship_ils = float(sp.shipping_cost_ils or 0)
        delivery_fee = _get_ship(supplier.name or "")
        if cost_ils > 0:
            pricing = agent.calculate_customer_price_from_ils(cost_ils, ship_ils, customer_shipping=delivery_fee)
        else:
            pricing = agent.calculate_customer_price(
                float(sp.price_usd),
                float(sp.shipping_cost_usd or 0),
                customer_shipping=delivery_fee,
            )
        comparisons.append({
            "supplier_part_id": str(sp.id),
            "supplier_name": supplier.name,
            "supplier_country": supplier.country or "",
            "availability": "in_stock" if sp.is_available else "on_order",
            "subtotal": pricing["price_no_vat"],
            "vat": pricing["vat"],
            "shipping": pricing["shipping"],
            "total": pricing["total"],
            "profit": pricing["profit"],
            "warranty_months": sp.warranty_months,
            "estimated_delivery": f"{sp.estimated_delivery_days}-{sp.estimated_delivery_days + 7} ימים",
        })
    return {"comparisons": sorted(comparisons, key=lambda x: (x["availability"] != "in_stock", x["total"]))}


@app.post("/api/v1/parts/identify-from-image")
async def identify_part_from_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Identify a car part from a photo using GPT-4o Vision, then search the DB."""
    import base64
    from openai import AsyncOpenAI

    # Read & encode image
    img_bytes = await file.read()
    if len(img_bytes) > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(status_code=413, detail="Image too large (max 10 MB)")
    b64 = base64.b64encode(img_bytes).decode()
    mime = file.content_type or "image/jpeg"

    identified_name = ""
    identified_en = ""
    confidence = 0.0
    possible_names: list = []

    github_token = os.getenv("GITHUB_TOKEN", "")
    if github_token:
        try:
            client = AsyncOpenAI(
                base_url="https://models.inference.ai.azure.com",
                api_key=github_token,
            )
            resp = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "You are an expert automotive parts identifier. "
                                    "Look at this image and identify the car part shown. "
                                    "Respond ONLY with a JSON object, no markdown: "
                                    '{"part_name_he": "<name in Hebrew>", '
                                    '"part_name_en": "<name in English>", '
                                    '"possible_names": ["<alt name 1>", "<alt name 2>"], '
                                    '"confidence": <0.0-1.0>, '
                                    '"description": "<brief description in Hebrew>"}'
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64}"},
                            },
                        ],
                    }
                ],
                max_tokens=300,
            )
            import json as _json
            raw = resp.choices[0].message.content.strip()
            # strip possible markdown code fences
            raw = raw.strip("`").removeprefix("json").strip()
            parsed = _json.loads(raw)
            identified_name = parsed.get("part_name_he") or parsed.get("part_name_en", "")
            identified_en = parsed.get("part_name_en", "")
            confidence = float(parsed.get("confidence", 0.0))
            possible_names = parsed.get("possible_names", [])
        except Exception as e:
            print(f"[Vision] GPT-4o Vision error: {e}")

    # Search the DB with identified name
    parts_results = []
    total = 0
    if identified_name or identified_en:
        agent = get_agent("parts_finder_agent")
        search_term = identified_en or identified_name
        parts_results = await agent.search_parts_in_db(search_term, None, None, db, limit=20, offset=0)
        # Count
        from sqlalchemy import func
        from BACKEND_DATABASE_MODELS import PartsCatalog
        count_stmt = select(func.count()).select_from(PartsCatalog).where(
            PartsCatalog.is_active == True,
            PartsCatalog.name.ilike(f"%{search_term}%")
        )
        total = (await db.execute(count_stmt)).scalar_one()

    return {
        "identified_part": identified_name,
        "identified_part_en": identified_en,
        "possible_names": possible_names,
        "confidence": confidence,
        "parts": parts_results,
        "total": total,
    }


# search-by-vin endpoint is defined above {part_id} to ensure correct route matching


# ==============================================================================
# 4. VEHICLES  /api/v1/vehicles  (8 endpoints)
# ==============================================================================

@app.post("/api/v1/vehicles/identify")
async def identify_vehicle(data: VehicleIdentifyRequest, db: AsyncSession = Depends(get_db)):
    agent = get_agent("parts_finder_agent")
    try:
        result = await agent.identify_vehicle(data.license_plate, db)
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=f"לוחית רישוי {data.license_plate} לא נמצאה במאגר משרד התחבורה")
        raise HTTPException(status_code=502, detail=f"שגיאה בקריאת מאגר הרכבים: {msg}")
    return result


@app.post("/api/v1/vehicles/identify-from-image")
async def identify_vehicle_from_image(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    return {"message": "License plate OCR – coming soon"}


@app.get("/api/v1/vehicles/my-vehicles")
async def get_my_vehicles(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserVehicle
    result = await db.execute(select(UserVehicle, Vehicle).join(Vehicle).where(UserVehicle.user_id == current_user.id))
    rows = result.all()
    vehicles = []
    for uv, v in rows:
        gov = v.gov_api_data or {}
        vehicles.append({
            "id": str(v.id),
            "nickname": uv.nickname,
            "is_primary": uv.is_primary,
            "license_plate": v.license_plate,
            "manufacturer": v.manufacturer,
            "model": v.model,
            "year": v.year,
            "engine_type": v.engine_type,
            "fuel_type": v.fuel_type or gov.get("fuel_type"),
            "color": gov.get("color"),
            "transmission": v.transmission or gov.get("transmission"),
            "engine_cc": gov.get("engine_cc"),
            "horsepower": gov.get("horsepower"),
            "vehicle_type": gov.get("vehicle_type"),
            "doors": gov.get("doors"),
            "seats": gov.get("seats"),
            "front_tire": gov.get("front_tire"),
            "rear_tire": gov.get("rear_tire"),
            "emissions_group": gov.get("emissions_group"),
            "last_test_date": gov.get("last_test_date"),
            "test_expiry_date": gov.get("test_expiry_date"),
            "ownership": gov.get("ownership"),
            "country_of_origin": gov.get("country_of_origin"),
        })
    return {"vehicles": vehicles}


@app.post("/api/v1/vehicles/my-vehicles")
async def add_my_vehicle(license_plate: str = Form(...), nickname: Optional[str] = Form(None), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserVehicle
    agent = get_agent("parts_finder_agent")
    try:
        vehicle_data = await agent.identify_vehicle(license_plate, db)
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=f"לוחית רישוי {license_plate} לא נמצאה במאגר משרד התחבורה")
        raise HTTPException(status_code=502, detail=f"שגיאה בקריאת מאגר הרכבים: {msg}")
    db.add(UserVehicle(user_id=current_user.id, vehicle_id=vehicle_data["id"], nickname=nickname, is_primary=False))
    await db.commit()
    return {"message": "Vehicle added", "vehicle": vehicle_data}


@app.put("/api/v1/vehicles/my-vehicles/{vehicle_id}")
async def update_my_vehicle(vehicle_id: str, nickname: Optional[str] = None, is_primary: Optional[bool] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserVehicle
    result = await db.execute(select(UserVehicle).where(and_(UserVehicle.vehicle_id == vehicle_id, UserVehicle.user_id == current_user.id)))
    uv = result.scalar_one_or_none()
    if not uv:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if nickname is not None:
        uv.nickname = nickname
    if is_primary is not None:
        uv.is_primary = is_primary
    await db.commit()
    return {"message": "Vehicle updated"}


@app.delete("/api/v1/vehicles/my-vehicles/{vehicle_id}")
async def delete_my_vehicle(vehicle_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserVehicle
    result = await db.execute(select(UserVehicle).where(and_(UserVehicle.vehicle_id == vehicle_id, UserVehicle.user_id == current_user.id)))
    uv = result.scalar_one_or_none()
    if not uv:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    await db.delete(uv)
    await db.commit()
    return {"message": "Vehicle removed"}


@app.post("/api/v1/vehicles/my-vehicles/set-primary")
async def set_primary_vehicle(vehicle_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserVehicle
    result = await db.execute(select(UserVehicle).where(UserVehicle.user_id == current_user.id))
    for uv in result.scalars().all():
        uv.is_primary = (str(uv.vehicle_id) == vehicle_id)
    await db.commit()
    return {"message": "Primary vehicle updated"}


@app.get("/api/v1/vehicles/{vehicle_id}/compatible-parts")
async def get_compatible_parts(vehicle_id: str, category: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    return {"parts": [], "message": "Compatibility filter coming soon"}


# ==============================================================================
# 5. ORDERS  /api/v1/orders  (7 endpoints)
# ==============================================================================

@app.post("/api/v1/orders", status_code=status.HTTP_201_CREATED)
async def create_order(data: OrderCreate, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import SupplierPart
    from BACKEND_DATABASE_MODELS import Supplier as SupplierModel
    from BACKEND_AI_AGENTS import get_supplier_shipping as _get_ship2
    subtotal = 0.0
    items_data = []
    USD_TO_ILS = 3.65
    # Track unique suppliers in this order → charge delivery fee once per supplier origin
    supplier_delivery_fees: dict[str, float] = {}  # supplier_id -> delivery_fee

    for item in data.items:
        res = await db.execute(
            select(SupplierPart, PartsCatalog, SupplierModel)
            .join(PartsCatalog, SupplierPart.part_id == PartsCatalog.id)
            .join(SupplierModel, SupplierPart.supplier_id == SupplierModel.id)
            .where(SupplierPart.id == item.supplier_part_id)
        )
        row = res.first()
        if not row:
            raise HTTPException(status_code=404, detail=f"חלק {item.supplier_part_id} לא נמצא. נסה לרענן את הדף ולהוסיף את החלק מחדש לסל.")
        sp, part, supplier_rec = row
        cost_ils = float(sp.price_ils or 0) or (float(sp.price_usd or 0) * USD_TO_ILS)
        ship_ils = float(sp.shipping_cost_ils or 0)
        total_cost_ils = cost_ils + ship_ils  # total procurement cost (part + supplier shipping)
        delivery_fee = _get_ship2(supplier_rec.name or "")  # customer delivery fee for this supplier origin
        # Record each unique supplier's delivery fee (charged once per supplier, not per item)
        supplier_delivery_fees[str(supplier_rec.id)] = delivery_fee
        unit_price = round(total_cost_ils * 1.45, 2)  # 45% markup on total cost
        vat = round(unit_price * 0.17, 2)
        subtotal += unit_price * item.quantity
        items_data.append({
            "part_id": item.part_id or str(part.id),
            "supplier_part_id": item.supplier_part_id,
            "quantity": item.quantity,
            "unit_price": unit_price,
            "vat": vat,
            "part": part,
            "sp": sp,
            "supplier_name": supplier_rec.name,
        })

    vat_total = round(subtotal * 0.17, 2)
    # Sum delivery fees for each unique supplier origin (Israel + Germany = ₪29 + ₪91 = ₪120)
    shipping = round(sum(supplier_delivery_fees.values()), 2)
    total = round(subtotal + vat_total + shipping, 2)
    order_number = f"AUTO-2026-{str(uuid.uuid4())[:8].upper()}"

    order = Order(
        order_number=order_number, user_id=current_user.id, status="pending_payment",
        subtotal=subtotal, vat_amount=vat_total, shipping_cost=shipping,
        total_amount=total, shipping_address=data.shipping_address,
    )
    db.add(order)
    await db.flush()

    for d in items_data:
        try:
            _part_id = uuid.UUID(str(d["part_id"])) if d["part_id"] else None
            _sp_id   = uuid.UUID(str(d["supplier_part_id"]))
        except (ValueError, AttributeError):
            _part_id = None
            _sp_id   = None
        db.add(OrderItem(
            order_id=order.id,
            part_id=_part_id,
            supplier_part_id=_sp_id,
            part_name=d["part"].name, part_sku=d["part"].sku, manufacturer=d["part"].manufacturer,
            part_type=d["part"].part_type, supplier_name=d["supplier_name"],
            quantity=d["quantity"], unit_price=d["unit_price"], vat_amount=d["vat"],
            total_price=(d["unit_price"] + d["vat"]) * d["quantity"], warranty_months=d["sp"].warranty_months,
        ))

    await db.commit()
    await db.refresh(order)
    return {"order_id": str(order.id), "order_number": order.order_number, "status": order.status, "subtotal": float(order.subtotal), "vat": float(order.vat_amount), "shipping": float(order.shipping_cost), "total": float(order.total_amount)}


@app.get("/api/v1/orders")
async def get_orders(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(Order.user_id == current_user.id).order_by(Order.created_at.desc()).limit(limit))
    orders = result.scalars().all()
    return {"orders": [{"id": str(o.id), "order_number": o.order_number, "status": o.status, "total": float(o.total_amount), "created_at": o.created_at, "tracking_number": o.tracking_number, "tracking_url": o.tracking_url, "estimated_delivery": o.estimated_delivery} for o in orders]}


@app.get("/api/v1/orders/{order_id}")
async def get_order(order_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    result = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))
    items = result.scalars().all()
    return {
        "id": str(order.id), "order_number": order.order_number, "status": order.status,
        "subtotal": float(order.subtotal), "vat": float(order.vat_amount),
        "shipping": float(order.shipping_cost), "total": float(order.total_amount),
        "tracking_number": order.tracking_number, "tracking_url": order.tracking_url, "estimated_delivery": order.estimated_delivery,
        "items": [{"part_id": str(i.part_id) if i.part_id else None, "supplier_part_id": str(i.supplier_part_id) if i.supplier_part_id else None, "part_name": i.part_name, "manufacturer": i.manufacturer, "quantity": i.quantity, "unit_price": float(i.unit_price), "total": float(i.total_price)} for i in items],
    }


@app.get("/api/v1/orders/{order_id}/track")
async def track_order(order_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"order_number": order.order_number, "status": order.status, "tracking_number": order.tracking_number, "tracking_url": order.tracking_url, "estimated_delivery": order.estimated_delivery}


@app.put("/api/v1/orders/{order_id}/cancel")
async def cancel_order(order_id: str, data: OrderCancelRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    import stripe as stripe_sdk

    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ["pending_payment", "paid", "processing"]:
        raise HTTPException(status_code=400, detail="Cannot cancel order in current status")

    was_paid = order.status in ["paid", "processing"]
    order.status = "cancelled"
    order.cancelled_at = datetime.utcnow()

    refund_id = None
    refund_amount = None

    # ── Auto-refund via Stripe if the order was already paid ─────────────────
    if was_paid:
        pay_res = await db.execute(
            select(Payment).where(and_(Payment.order_id == order.id, Payment.status == "paid"))
        )
        payment = pay_res.scalar_one_or_none()

        if payment and payment.payment_intent_id:
            stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
            if stripe_key and not stripe_key.startswith("sk_test_xxxxx"):
                stripe_sdk.api_key = stripe_key
                try:
                    # payment_intent_id stores the Checkout Session ID (cs_...)
                    # Retrieve session to get the actual payment_intent (pi_...)
                    session_obj = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: stripe_sdk.checkout.Session.retrieve(payment.payment_intent_id)
                    )
                    pi_id = session_obj.payment_intent
                    if pi_id:
                        stripe_refund = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: stripe_sdk.Refund.create(
                                payment_intent=pi_id,
                                reason="requested_by_customer",
                            )
                        )
                        refund_id = stripe_refund.id
                        refund_amount = float(stripe_refund.amount) / 100  # agorot → ILS

                        # Update Payment record
                        payment.status = "refunded"
                        payment.refunded_at = datetime.utcnow()
                        payment.refund_amount = refund_amount
                        payment.refund_reason = data.reason or "ביטול על ידי לקוח"
                        # Auto-create Invoice record on refund
                        existing_inv = (await db.execute(
                            select(Invoice).where(Invoice.order_id == order.id)
                        )).scalar_one_or_none()
                        if not existing_inv:
                            db.add(Invoice(
                                invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}",
                                order_id=order.id,
                                user_id=current_user.id,
                                business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                                issued_at=datetime.utcnow(),
                            ))
                except Exception as stripe_err:
                    # Don't block cancellation; just log the refund failure
                    print(f"[Stripe refund error] {stripe_err}")

        # Create a Return record to track the refund
        ret_number = f"REF-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}"
        db.add(Return(
            return_number=ret_number,
            order_id=order.id,
            user_id=current_user.id,
            reason="cancellation",
            description=data.reason or "ביטול על ידי לקוח",
            original_amount=order.total_amount,
            refund_amount=refund_amount or order.total_amount,
            status="approved" if refund_id else "pending",
        ))

        db.add(Notification(
            user_id=current_user.id,
            title="ביטול והחזר כספי" + (" ✅" if refund_id else " 🔄"),
            message=(
                f"הזמנה {order.order_number} בוטלה. "
                + (f"החזר כספי של ₪{refund_amount:.2f} נשלח לכרטיס האשראי שלך." if refund_id
                   else "בקשת ההחזר הכספי בטיפול.")
            ),
            type="refund_initiated",
        ))

    await db.commit()
    return {
        "message": "Order cancelled",
        "refund_initiated": was_paid,
        "refund_id": refund_id,
        "refund_amount": refund_amount,
    }


@app.post("/api/v1/orders/{order_id}/return")
async def create_order_return(order_id: str, reason: str, description: Optional[str] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return_number = f"RET-2026-{str(uuid.uuid4())[:8].upper()}"
    ret = Return(return_number=return_number, order_id=order.id, user_id=current_user.id, reason=reason, description=description, original_amount=order.total_amount, status="pending")
    db.add(ret)
    await db.commit()
    await db.refresh(ret)
    return {"return_id": str(ret.id), "return_number": ret.return_number, "status": "pending"}


@app.delete("/api/v1/orders/{order_id}")
async def delete_order(order_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ["pending_payment", "cancelled"]:
        raise HTTPException(status_code=400, detail="ניתן למחוק רק הזמנות שבוטלו או שממתינות לתשלום")
    # Delete child records that have non-nullable FKs first
    pay_res = await db.execute(select(Payment).where(Payment.order_id == order.id))
    for pay in pay_res.scalars().all():
        await db.delete(pay)
    inv_res = await db.execute(select(Invoice).where(Invoice.order_id == order.id))
    for inv in inv_res.scalars().all():
        await db.delete(inv)
    await db.flush()
    await db.delete(order)
    await db.commit()
    return {"message": "Order deleted"}


@app.get("/api/v1/orders/{order_id}/invoice")
async def get_order_invoice(
    order_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate and stream a Hebrew PDF invoice for a paid order."""
    from fastapi.responses import StreamingResponse
    from invoice_generator import generate_invoice_pdf

    # Load order (must belong to requesting user)
    ord_res = await db.execute(
        select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id))
    )
    order = ord_res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    INVOICE_ALLOWED = {"paid", "processing", "shipped", "delivered", "refunded"}
    if order.status not in INVOICE_ALLOWED:
        raise HTTPException(status_code=402, detail="החשבונית זמינה רק לאחר אישור תשלום")

    # Load order items
    items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    items = items_res.scalars().all()

    # Get or auto-create invoice record
    inv_res = await db.execute(select(Invoice).where(Invoice.order_id == order.id))
    invoice = inv_res.scalar_one_or_none()
    if not invoice:
        invoice = Invoice(
            invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}",
            order_id=order.id,
            user_id=current_user.id,
            business_number=os.getenv("COMPANY_NUMBER", "060633880"),
            issued_at=order.updated_at or datetime.utcnow(),
        )
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)

    # Generate PDF bytes
    pdf_bytes = generate_invoice_pdf(order, items, current_user, invoice)

    filename = f"invoice_{invoice.invoice_number}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ==============================================================================
# 6. PAYMENTS  /api/v1/payments  (real Stripe Checkout)
# ==============================================================================

def _get_frontend_url(request: Request) -> str:
    """Auto-detect frontend URL: Codespaces or localhost."""
    codespace = os.getenv("CODESPACE_NAME", "")
    if codespace:
        domain = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
        return f"https://{codespace}-5173.{domain}"
    return os.getenv("FRONTEND_URL", "http://localhost:5173")


@app.post("/api/v1/payments/create-checkout")
async def create_checkout_session(
    order_id: str,
    request: Request,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a real Stripe Checkout Session and return the redirect URL."""
    import stripe as stripe_sdk

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key or stripe_key.startswith("sk_test_xxxxx"):
        raise HTTPException(status_code=503, detail="Stripe not configured. Add STRIPE_SECRET_KEY to .env")

    stripe_sdk.api_key = stripe_key

    # Load order
    result = await db.execute(
        select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "pending_payment":
        raise HTTPException(status_code=400, detail=f"Order is already {order.status}")

    # Load order items for line items
    items_result = await db.execute(
        select(OrderItem).where(OrderItem.order_id == order.id)
    )
    order_items = items_result.scalars().all()

    frontend_url = _get_frontend_url(request)

    # Build Stripe line items
    line_items = []
    for item in order_items:
        line_items.append({
            "price_data": {
                "currency": "ils",
                "product_data": {
                    "name": item.part_name,
                    "description": f"{item.manufacturer} | אחריות {item.warranty_months} חודשים",
                },
                "unit_amount": int(float(item.total_price) / item.quantity * 100),  # agorot per unit
            },
            "quantity": item.quantity,
        })

    # Add shipping as a line item if not zero
    if order.shipping_cost and float(order.shipping_cost) > 0:
        line_items.append({
            "price_data": {
                "currency": "ils",
                "product_data": {"name": "משלוח"},
                "unit_amount": int(float(order.shipping_cost) * 100),
            },
            "quantity": 1,
        })

    # Create Stripe Checkout Session (async)
    session = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: stripe_sdk.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=f"{frontend_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/cart",
            customer_email=current_user.email,
            metadata={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "user_id": str(current_user.id),
            },
            locale="auto",
        )
    )

    # Save pending payment record
    db.add(Payment(
        order_id=order.id,
        payment_intent_id=session.id,
        amount=order.total_amount,
        currency="ILS",
        status="pending",
    ))
    await db.commit()

    return {
        "checkout_url": session.url,
        "session_id": session.id,
        "amount": float(order.total_amount),
        "currency": "ILS",
    }


class MultiCheckoutRequest(BaseModel):
    order_ids: List[str]


@app.post("/api/v1/payments/create-multi-checkout")
async def create_multi_checkout_session(
    payload: MultiCheckoutRequest,
    request: Request,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a single Stripe Checkout Session for multiple pending orders."""
    import stripe as stripe_sdk

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key or stripe_key.startswith("sk_test_xxxxx"):
        raise HTTPException(status_code=503, detail="Stripe not configured. Add STRIPE_SECRET_KEY to .env")
    stripe_sdk.api_key = stripe_key

    if not payload.order_ids:
        raise HTTPException(status_code=400, detail="No order IDs provided")

    # Load & validate all orders belong to this user and are pending_payment
    orders_res = await db.execute(
        select(Order).where(and_(Order.id.in_(payload.order_ids), Order.user_id == current_user.id))
    )
    orders = orders_res.scalars().all()

    if len(orders) != len(payload.order_ids):
        raise HTTPException(status_code=404, detail="One or more orders not found")

    non_pending = [o.order_number for o in orders if o.status != "pending_payment"]
    if non_pending:
        raise HTTPException(status_code=400, detail=f"הזמנות אלו אינן ממתינות לתשלום: {', '.join(non_pending)}")

    # Build combined Stripe line items
    line_items = []
    for order in orders:
        items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
        for item in items_res.scalars().all():
            line_items.append({
                "price_data": {
                    "currency": "ils",
                    "product_data": {
                        "name": f"[{order.order_number}] {item.part_name}",
                        "description": f"{item.manufacturer} | אחריות {item.warranty_months} חודשים",
                    },
                    "unit_amount": int(float(item.total_price) / item.quantity * 100),
                },
                "quantity": item.quantity,
            })
        if order.shipping_cost and float(order.shipping_cost) > 0:
            line_items.append({
                "price_data": {
                    "currency": "ils",
                    "product_data": {"name": f"משלוח [{order.order_number}]"},
                    "unit_amount": int(float(order.shipping_cost) * 100),
                },
                "quantity": 1,
            })

    frontend_url = _get_frontend_url(request)
    session = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: stripe_sdk.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=f"{frontend_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/orders",
            customer_email=current_user.email,
            metadata={
                "order_ids": ",".join(str(o.id) for o in orders),
                "order_count": str(len(orders)),
                "user_id": str(current_user.id),
            },
            locale="auto",
        )
    )

    # Create a pending Payment record per order
    for order in orders:
        db.add(Payment(
            order_id=order.id,
            payment_intent_id=session.id,
            amount=order.total_amount,
            currency="ILS",
            status="pending",
        ))
    await db.commit()

    return {
        "checkout_url": session.url,
        "session_id": session.id,
        "order_count": len(orders),
        "total_amount": sum(float(o.total_amount) for o in orders),
        "currency": "ILS",
    }


@app.get("/api/v1/payments/verify-session")
async def verify_checkout_session(
    session_id: str,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Called after Stripe redirects back — verifies payment and marks order(s) paid."""
    import stripe as stripe_sdk

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key or stripe_key.startswith("sk_test_xxxxx"):
        raise HTTPException(status_code=503, detail="Stripe not configured")

    stripe_sdk.api_key = stripe_key

    session = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: stripe_sdk.checkout.Session.retrieve(session_id)
    )

    # ── MULTI-ORDER SESSION ────────────────────────────────────────────────────
    order_ids_str = session.metadata.get("order_ids")
    if order_ids_str:
        order_id_list = [oid.strip() for oid in order_ids_str.split(",") if oid.strip()]
        orders_res = await db.execute(
            select(Order).where(and_(Order.id.in_(order_id_list), Order.user_id == current_user.id))
        )
        multi_orders = orders_res.scalars().all()
        if not multi_orders:
            raise HTTPException(status_code=404, detail="Orders not found")

        if session.payment_status == "paid":
            for ord in multi_orders:
                if ord.status == "pending_payment":
                    ord.status = "paid"
            # Update all payment records tied to this session
            pays_res = await db.execute(select(Payment).where(Payment.payment_intent_id == session_id))
            for pay in pays_res.scalars().all():
                pay.status = "paid"
                pay.paid_at = datetime.utcnow()
                pay.payment_method = session.payment_method_types[0] if session.payment_method_types else "card"
            # Create invoices
            for ord in multi_orders:
                inv_num = f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(ord.id)[:8].upper()}"
                db.add(Invoice(
                    invoice_number=inv_num,
                    order_id=ord.id,
                    user_id=current_user.id,
                    business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                ))
            paid_nums = ", ".join(o.order_number for o in multi_orders)
            db.add(Notification(
                user_id=current_user.id,
                title="תשלום התקבל ✅",
                message=f"{len(multi_orders)} הזמנות אושרו: {paid_nums}",
                type="payment_success",
            ))
            # ── Dropshipping: notify admin(s) per supplier & advance → processing
            await trigger_supplier_fulfillment(list(multi_orders), db)
            await db.commit()

        return {
            "status": session.payment_status,
            "is_multi": True,
            "orders": [{"order_id": str(o.id), "order_number": o.order_number,
                         "order_status": o.status, "amount": float(o.total_amount)} for o in multi_orders],
            "order_number": multi_orders[0].order_number,
            "order_id": str(multi_orders[0].id),
            "amount": sum(float(o.total_amount) for o in multi_orders),
        }

    # ── SINGLE-ORDER SESSION ───────────────────────────────────────────────────
    order_id = session.metadata.get("order_id")
    if not order_id:
        raise HTTPException(status_code=400, detail="Invalid session metadata")

    result = await db.execute(
        select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if session.payment_status == "paid" and order.status == "pending_payment":
        order.status = "paid"
        pay_result = await db.execute(select(Payment).where(Payment.payment_intent_id == session_id))
        payment = pay_result.scalar_one_or_none()
        if payment:
            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            payment.payment_method = session.payment_method_types[0] if session.payment_method_types else "card"
        invoice_number = f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}"
        db.add(Invoice(
            invoice_number=invoice_number,
            order_id=order.id,
            user_id=current_user.id,
            business_number=os.getenv("COMPANY_NUMBER", "060633880"),
        ))
        db.add(Notification(
            user_id=current_user.id,
            title="תשלום התקבל ✅",
            message=f"הזמנה {order.order_number} אושרה.",
            type="payment_success",
        ))
        # ── Dropshipping: notify admin(s) per supplier & advance → processing ─
        await trigger_supplier_fulfillment([order], db)
        await db.commit()

    return {
        "status": session.payment_status,
        "order_status": order.status,
        "order_number": order.order_number,
        "order_id": str(order.id),
        "amount": float(order.total_amount),
    }


@app.post("/api/v1/payments/create-intent")
async def create_payment_intent_legacy(order_id: str, request: Request, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    """Legacy endpoint – redirects to create-checkout."""
    return await create_checkout_session(order_id, request, current_user, db)


@app.post("/api/v1/payments/confirm")
async def confirm_payment(payment_intent_id: str, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    return {"status": "redirect_to_stripe", "message": "Use /payments/create-checkout to get a Stripe Checkout URL"}


@app.get("/api/v1/payments/refunds/list")
async def list_refunds(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Return all refund records for the current user (from Return table + safety-net from payments)."""
    # 1. Return records (manual returns + cancellation refunds)
    rets_res = await db.execute(
        select(Return, Order).join(Order, Return.order_id == Order.id).where(
            Return.user_id == current_user.id
        ).order_by(desc(Return.requested_at))
    )
    return_rows = rets_res.all()

    # 2. Payments that were refunded (status=refunded)
    pays_res = await db.execute(
        select(Payment, Order).join(Order, Payment.order_id == Order.id).where(
            and_(Order.user_id == current_user.id, Payment.status == "refunded")
        ).order_by(desc(Payment.refunded_at))
    )
    refunded_payments = pays_res.all()

    # 3. Safety-net: cancelled orders with still-paid payment and no Return record
    orphan_res = await db.execute(
        select(Payment, Order).join(Order, Payment.order_id == Order.id).where(
            and_(Order.user_id == current_user.id, Order.status == "cancelled", Payment.status == "paid")
        )
    )
    orphan_rows = orphan_res.all()

    # Build combined list
    order_ids_covered = set()
    items = []

    for ret, order in return_rows:
        order_ids_covered.add(str(order.id))
        items.append({
            "id": str(ret.id),
            "type": "cancellation" if ret.reason == "cancellation" else "return",
            "return_number": ret.return_number,
            "order_number": order.order_number,
            "order_id": str(order.id),
            "reason": ret.reason,
            "description": ret.description,
            "original_amount": float(ret.original_amount) if ret.original_amount else None,
            "refund_amount": float(ret.refund_amount) if ret.refund_amount else None,
            "status": ret.status,
            "date": ret.requested_at.isoformat() if ret.requested_at else None,
        })

    for payment, order in refunded_payments:
        if str(order.id) in order_ids_covered:
            continue
        order_ids_covered.add(str(order.id))
        items.append({
            "id": str(payment.id),
            "type": "cancellation",
            "return_number": f"REF-{str(payment.id)[:8].upper()}",
            "order_number": order.order_number,
            "order_id": str(order.id),
            "reason": payment.refund_reason or "ביטול",
            "description": None,
            "original_amount": float(payment.amount) if payment.amount else None,
            "refund_amount": float(payment.refund_amount) if payment.refund_amount else None,
            "status": "approved",
            "date": payment.refunded_at.isoformat() if payment.refunded_at else None,
        })

    # Safety-net: show as "בטיפול" if no Return/refund record exists
    for payment, order in orphan_rows:
        if str(order.id) in order_ids_covered:
            continue
        order_ids_covered.add(str(order.id))
        items.append({
            "id": str(payment.id),
            "type": "cancellation",
            "return_number": f"REF-{str(order.id)[:8].upper()}",
            "order_number": order.order_number,
            "order_id": str(order.id),
            "reason": "cancellation",
            "description": "ביטול על ידי לקוח",
            "original_amount": float(order.total_amount) if order.total_amount else None,
            "refund_amount": float(order.total_amount) if order.total_amount else None,
            "status": "pending",
            "date": order.cancelled_at.isoformat() if order.cancelled_at else None,
        })

    items.sort(key=lambda x: x["date"] or "", reverse=True)
    return {"refunds": items}


@app.get("/api/v1/payments/{payment_id}")
async def get_payment(payment_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"id": str(payment.id), "amount": float(payment.amount), "status": payment.status, "payment_method": payment.payment_method, "created_at": payment.created_at}


@app.post("/api/v1/payments/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe webhook for async payment confirmation (backup to verify-session)."""
    import stripe as stripe_sdk
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_sdk.api_key = os.getenv("STRIPE_SECRET_KEY", "")

    try:
        if webhook_secret and not webhook_secret.startswith("whsec_xxxxx"):
            event = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: stripe_sdk.Webhook.construct_event(payload, sig_header, webhook_secret)
            )
        else:
            event = stripe_sdk.Event.construct_from(
                {"type": "raw", "data": {"object": {}}}, stripe_sdk.api_key
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event.type == "checkout.session.completed":
        session = event.data.object
        if session.payment_status == "paid":
            orders_to_fulfill = []

            # Single-order
            order_id = session.metadata.get("order_id")
            if order_id:
                res = await db.execute(select(Order).where(Order.id == order_id))
                order = res.scalar_one_or_none()
                if order and order.status == "pending_payment":
                    order.status = "paid"
                    orders_to_fulfill.append(order)

            # Multi-order
            order_ids_str = session.metadata.get("order_ids", "")
            if order_ids_str:
                oid_list = [x.strip() for x in order_ids_str.split(",") if x.strip()]
                res = await db.execute(select(Order).where(Order.id.in_(oid_list)))
                for ord in res.scalars().all():
                    if ord.status == "pending_payment":
                        ord.status = "paid"
                        orders_to_fulfill.append(ord)

            if orders_to_fulfill:
                await trigger_supplier_fulfillment(orders_to_fulfill, db)
                # Auto-create Invoice record for every newly-paid order
                for ord in orders_to_fulfill:
                    existing = (await db.execute(
                        select(Invoice).where(Invoice.order_id == ord.id)
                    )).scalar_one_or_none()
                    if not existing:
                        db.add(Invoice(
                            invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(ord.id)[:8].upper()}",
                            order_id=ord.id,
                            user_id=ord.user_id,
                            business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                            issued_at=datetime.utcnow(),
                        ))
                await db.commit()

    return {"received": True}


@app.post("/api/v1/payments/refund")
async def refund_payment(
    payment_id: str,
    amount: float,
    reason: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin: manually refund a payment via Stripe."""
    import stripe as stripe_sdk

    result = await db.execute(
        select(Payment).options().where(Payment.id == payment_id)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status not in ("paid", "succeeded"):
        raise HTTPException(status_code=400, detail=f"Payment status '{payment.status}' cannot be refunded")

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key or stripe_key.startswith("sk_test_xxxxx"):
        raise HTTPException(status_code=503, detail="Stripe not configured")

    stripe_sdk.api_key = stripe_key
    try:
        # payment_intent_id field stores the Checkout Session ID (cs_...)
        session_obj = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: stripe_sdk.checkout.Session.retrieve(payment.payment_intent_id)
        )
        pi_id = session_obj.payment_intent
        if not pi_id:
            raise HTTPException(status_code=400, detail="No payment intent found for this session")

        refund_cents = int(amount * 100)
        stripe_refund = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: stripe_sdk.Refund.create(
                payment_intent=pi_id,
                amount=refund_cents,
                reason="requested_by_customer",
            )
        )
        refund_ils = float(stripe_refund.amount) / 100

        payment.status = "refunded"
        payment.refunded_at = datetime.utcnow()
        payment.refund_amount = refund_ils
        payment.refund_reason = reason

        # Get order for notification + Invoice
        order_res = await db.execute(select(Order).where(Order.id == payment.order_id))
        order = order_res.scalar_one_or_none()
        if order:
            db.add(Notification(
                user_id=order.user_id,
                type="refund",
                title="החזר כספי אושר על ידי מנהל",
                message=f"החזר כספי של ₪{refund_ils:.2f} בוצע עבור הזמנה {order.order_number}.",
            ))
            # Ensure an Invoice record exists (credit note for the refund)
            existing_inv = (await db.execute(
                select(Invoice).where(Invoice.order_id == order.id)
            )).scalar_one_or_none()
            if not existing_inv:
                db.add(Invoice(
                    invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}",
                    order_id=order.id,
                    user_id=order.user_id,
                    business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                    issued_at=datetime.utcnow(),
                ))

        await db.commit()
        return {"message": "Refund processed", "refund_id": stripe_refund.id, "amount": refund_ils}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/payments/history")
async def get_payment_history(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Payment).join(Order).where(Order.user_id == current_user.id).order_by(Payment.created_at.desc()).limit(limit))
    payments = result.scalars().all()
    return {"payments": [{"id": str(p.id), "amount": float(p.amount), "status": p.status, "created_at": p.created_at} for p in payments]}


# ==============================================================================
# 6b. ADMIN SUPPLIER ORDERS  /api/v1/admin/supplier-orders  (2 endpoints)
# ==============================================================================

@app.get("/api/v1/admin/supplier-orders")
async def get_admin_supplier_orders(
    pending_only: bool = False,
    limit: int = 200,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin: list all supplier purchase tasks generated after customer payments."""
    stmt = (
        select(Notification)
        .where(and_(
            Notification.user_id == current_user.id,
            Notification.type == "supplier_order",
        ))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if pending_only:
        stmt = stmt.where(Notification.read_at.is_(None))

    result = await db.execute(stmt)
    notifs = result.scalars().all()
    return {
        "supplier_orders": [
            {
                "id": str(n.id),
                "title": n.title,
                "message": n.message,
                "data": n.data or {},
                "is_done": n.read_at is not None,
                "done_at": n.read_at,
                "created_at": n.created_at,
            }
            for n in notifs
        ],
        "pending_count": sum(1 for n in notifs if n.read_at is None),
    }


@app.put("/api/v1/admin/supplier-orders/{notification_id}/done")
async def mark_supplier_order_done(
    notification_id: str,
    tracking_number: Optional[str] = None,
    tracking_url: Optional[str] = None,
    carrier: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin: mark a supplier purchase task as ordered, optionally recording a tracking number."""
    result = await db.execute(
        select(Notification).where(and_(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
            Notification.type == "supplier_order",
        ))
    )
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Supplier order task not found")

    # Extract order_id from notification data and update the order
    order_id = (n.data or {}).get("order_id")
    if order_id:
        order_res = await db.execute(select(Order).where(Order.id == order_id))
        order = order_res.scalar_one_or_none()
        if order:
            if tracking_number:
                order.tracking_number = tracking_number.strip()
                order.status = "supplier_ordered"
                if tracking_url:
                    order.tracking_url = tracking_url.strip()
                carrier_label = carrier or "ספק"
                db.add(Notification(
                    user_id=order.user_id,
                    type="order_update",
                    title="\U0001f4e6 \u05d4\u05d7\u05dc\u05e7\u05d9\u05dd \u05d4\u05d5\u05d6\u05de\u05e0\u05d5 \u2013 \u05d9\u05e9 \u05de\u05e1\u05e4\u05e8 \u05de\u05e2\u05e7\u05d1!",
                    message=(
                        f"\u05d4\u05d6\u05de\u05e0\u05d4 {order.order_number} \u05d4\u05d5\u05d6\u05de\u05e0\u05d4 \u05de\u05d4\u05e1\u05e4\u05e7.\n"
                        f"\u05de\u05e1\u05e4\u05e8 \u05de\u05e2\u05e7\u05d1 {carrier_label}: {tracking_number}\n"
                        + (f"\u05e7\u05d9\u05e9\u05d5\u05e8 \u05de\u05e2\u05e7\u05d1: {tracking_url}" if tracking_url else "")
                    ),
                    data={"order_id": str(order.id), "order_number": order.order_number, "tracking_number": tracking_number, "tracking_url": tracking_url},
                ))
            else:
                # No tracking yet — still advance status so customer sees progress
                if order.status in ("processing", "paid"):
                    order.status = "supplier_ordered"
                db.add(Notification(
                    user_id=order.user_id,
                    type="order_update",
                    title="\U0001f6d2 \u05d4\u05d4\u05d6\u05de\u05e0\u05d4 \u05d4\u05d5\u05e2\u05d1\u05e8\u05d4 \u05dc\u05e1\u05e4\u05e7",
                    message=f"\u05d4\u05d6\u05de\u05e0\u05d4 {order.order_number} \u05d4\u05d5\u05d6\u05de\u05e0\u05d4 \u05de\u05d4\u05e1\u05e4\u05e7 \u05d5\u05d1\u05d3\u05e8\u05da \u05d0\u05dc\u05d9\u05da. \u05de\u05e1\u05e4\u05e8 \u05de\u05e2\u05e7\u05d1 \u05d9\u05e2\u05d5\u05d3\u05db\u05df \u05d1\u05d4\u05e7\u05d3\u05dd.",
                    data={"order_id": str(order.id), "order_number": order.order_number},
                ))

    n.read_at = datetime.utcnow()
    await db.commit()
    return {"message": "\u05e1\u05d5\u05de\u05df \u05db\u05d4\u05d5\u05d6\u05de\u05df"}


# ==============================================================================
# 7. INVOICES  /api/v1/invoices  (4 endpoints)
# ==============================================================================

@app.get("/api/v1/invoices")
async def get_invoices(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Invoice).where(Invoice.user_id == current_user.id).order_by(Invoice.issued_at.desc()).limit(limit))
    invoices = result.scalars().all()
    return {"invoices": [{"id": str(i.id), "invoice_number": i.invoice_number, "order_id": str(i.order_id), "pdf_url": i.pdf_url, "issued_at": i.issued_at} for i in invoices]}


@app.get("/api/v1/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"id": str(invoice.id), "invoice_number": invoice.invoice_number, "pdf_url": invoice.pdf_url, "business_number": invoice.business_number, "issued_at": invoice.issued_at}


@app.get("/api/v1/invoices/{invoice_id}/download")
async def download_invoice(invoice_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"download_url": invoice.pdf_url}


@app.post("/api/v1/invoices/{invoice_id}/resend")
async def resend_invoice(invoice_id: str, email: Optional[EmailStr] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"message": f"Invoice sent to {email or current_user.email}"}


# ==============================================================================
# 8. RETURNS  /api/v1/returns  (6 endpoints)
# ==============================================================================

@app.post("/api/v1/returns", status_code=status.HTTP_201_CREATED)
async def create_return(data: ReturnRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(and_(Order.id == data.order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ["delivered", "shipped"]:
        raise HTTPException(status_code=400, detail="Order cannot be returned in current status")
    return_number = f"RET-2026-{str(uuid.uuid4())[:8].upper()}"
    ret = Return(return_number=return_number, order_id=order.id, user_id=current_user.id, reason=data.reason, description=data.description, original_amount=order.total_amount, status="pending")
    db.add(ret)
    await db.commit()
    await db.refresh(ret)
    return {"return_id": str(ret.id), "return_number": ret.return_number, "status": ret.status, "message": "Return request created. We'll review it within 24 hours."}


@app.get("/api/v1/returns")
async def get_returns(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Return).where(Return.user_id == current_user.id).order_by(Return.requested_at.desc()))
    returns = result.scalars().all()
    return {"returns": [{"id": str(r.id), "return_number": r.return_number, "order_id": str(r.order_id), "reason": r.reason, "status": r.status, "refund_amount": float(r.refund_amount) if r.refund_amount else None, "requested_at": r.requested_at} for r in returns]}


@app.get("/api/v1/returns/{return_id}")
async def get_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    return {"id": str(ret.id), "return_number": ret.return_number, "status": ret.status, "reason": ret.reason, "description": ret.description, "original_amount": float(ret.original_amount), "refund_amount": float(ret.refund_amount) if ret.refund_amount else None, "requested_at": ret.requested_at, "approved_at": ret.approved_at}


@app.post("/api/v1/returns/{return_id}/track")
async def track_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    return {"return_number": ret.return_number, "status": ret.status, "tracking_number": ret.tracking_number}


@app.put("/api/v1/returns/{return_id}/cancel")
async def cancel_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status not in ["pending", "approved"]:
        raise HTTPException(status_code=400, detail="Cannot cancel return in current status")
    await db.delete(ret)
    await db.commit()
    return {"message": "Return cancelled"}


@app.post("/api/v1/returns/{return_id}/approve")
async def approve_return(return_id: str, refund_percentage: int = 100, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Return).where(Return.id == return_id))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    ret.status = "approved"
    ret.approved_at = datetime.utcnow()
    ret.refund_percentage = refund_percentage
    ret.refund_amount = (ret.original_amount * refund_percentage) / 100
    await db.commit()
    return {"message": "Return approved", "refund_amount": float(ret.refund_amount)}


# ==============================================================================
# 9. FILES  /api/v1/files  (4 endpoints)
# ==============================================================================

@app.post("/api/v1/files/upload")
async def upload_file(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    allowed = ["image/jpeg", "image/png", "image/webp", "audio/mpeg", "audio/wav", "video/mp4"]
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="File type not allowed")
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 25MB)")
    stored_filename = f"{uuid.uuid4()}_{file.filename}"
    ftype = "image" if "image" in (file.content_type or "") else ("audio" if "audio" in (file.content_type or "") else "video")
    file_record = FileModel(user_id=current_user.id, original_filename=file.filename, stored_filename=stored_filename, file_type=ftype, mime_type=file.content_type, file_size_bytes=len(content), storage_path=f"/uploads/{stored_filename}", expires_at=datetime.utcnow() + timedelta(days=30))
    db.add(file_record)
    await db.commit()
    await db.refresh(file_record)
    return {"file_id": str(file_record.id), "url": f"/api/v1/files/{file_record.id}", "expires_at": file_record.expires_at}


@app.get("/api/v1/files/{file_id}")
async def get_file(file_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FileModel).where(and_(FileModel.id == file_id, FileModel.user_id == current_user.id)))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return {"id": str(f.id), "filename": f.original_filename, "file_type": f.file_type, "size_bytes": f.file_size_bytes, "url": f.cdn_url or f.storage_path, "expires_at": f.expires_at}


@app.delete("/api/v1/files/{file_id}")
async def delete_file(file_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FileModel).where(and_(FileModel.id == file_id, FileModel.user_id == current_user.id)))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    f.deleted_at = datetime.utcnow()
    await db.commit()
    return {"message": "File deleted"}


# ==============================================================================
# 10. PROFILE  /api/v1/profile  (7 endpoints)
# ==============================================================================

@app.get("/api/v1/profile")
async def get_profile(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    return {
        "user": {"id": str(current_user.id), "email": current_user.email, "phone": current_user.phone, "full_name": current_user.full_name, "is_verified": current_user.is_verified},
        "profile": {"address": profile.address_line1 if profile else None, "apartment": profile.address_line2 if profile else None, "city": profile.city if profile else None, "postal_code": profile.postal_code if profile else None, "preferred_language": profile.preferred_language if profile else "he", "avatar_url": profile.avatar_url if profile else None} if profile else None,
    }


@app.put("/api/v1/profile")
async def update_profile(address_line1: Optional[str] = None, address_line2: Optional[str] = None, city: Optional[str] = None, postal_code: Optional[str] = None, full_name: Optional[str] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
    if address_line1 is not None:
        profile.address_line1 = address_line1
    if address_line2 is not None:
        profile.address_line2 = address_line2
    if city is not None:
        profile.city = city
    if postal_code is not None:
        profile.postal_code = postal_code
    if full_name is not None:
        current_user.full_name = full_name
    await db.commit()
    return {"message": "Profile updated"}


@app.post("/api/v1/profile/avatar")
async def upload_avatar(file: UploadFile = File(...), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"avatar_url": "https://cdn.autospare.com/avatars/coming-soon.jpg"}


@app.delete("/api/v1/profile/avatar")
async def delete_avatar(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Avatar deleted"}


@app.post("/api/v1/profile/update-phone")
async def update_phone(data: UpdatePhoneRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await update_phone_number(current_user, data.new_phone, data.verification_code, db)
    return {"message": "Phone number updated"}


@app.get("/api/v1/profile/marketing-preferences")
async def get_marketing_preferences(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    return {"marketing_consent": profile.marketing_consent if profile else False, "newsletter_subscribed": profile.newsletter_subscribed if profile else False, "preferences": profile.marketing_preferences if profile else {}}


@app.put("/api/v1/profile/marketing-preferences")
async def update_marketing_preferences(marketing_consent: Optional[bool] = None, newsletter_subscribed: Optional[bool] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
    if marketing_consent is not None:
        profile.marketing_consent = marketing_consent
    if newsletter_subscribed is not None:
        profile.newsletter_subscribed = newsletter_subscribed
    await db.commit()
    return {"message": "Preferences updated"}


@app.get("/api/v1/profile/order-history")
async def get_order_history_summary(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count(Order.id).label("total"), func.sum(Order.total_amount).label("spent")).where(Order.user_id == current_user.id))
    stats = result.first()
    return {"total_orders": stats.total or 0, "total_spent": float(stats.spent or 0)}


# ==============================================================================
# 11. MARKETING  /api/v1/marketing  (7 endpoints)
# ==============================================================================

@app.post("/api/v1/marketing/subscribe")
async def subscribe_newsletter(data: NewsletterSubscribeRequest, db: AsyncSession = Depends(get_db)):
    return {"message": "Subscribed successfully"}


@app.post("/api/v1/marketing/validate-coupon")
async def validate_coupon(data: CouponValidateRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"valid": True, "code": data.code, "discount_type": "percentage", "discount_value": 10}


@app.get("/api/v1/marketing/coupons")
async def get_available_coupons(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"coupons": []}


@app.post("/api/v1/marketing/apply-coupon")
async def apply_coupon(order_id: str, coupon_code: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"discount": 0, "message": "Coupon system coming soon"}


@app.get("/api/v1/marketing/promotions")
async def get_active_promotions(db: AsyncSession = Depends(get_db)):
    return {"promotions": [{"code": "WELCOME10", "description": "10% on first order", "discount_type": "percentage", "value": 10}]}


@app.post("/api/v1/marketing/referral")
async def create_referral(email: EmailStr, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Referral sent", "referral_link": f"https://autospare.com?ref={str(current_user.id)[:8]}"}


@app.get("/api/v1/marketing/loyalty-points")
async def get_loyalty_points(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"points": 0, "tier": "bronze", "next_tier": "silver", "points_needed": 500}


# ==============================================================================
# 12. NOTIFICATIONS  /api/v1/notifications  (5 endpoints)
# ==============================================================================

@app.get("/api/v1/notifications")
async def get_notifications(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Notification).where(Notification.user_id == current_user.id).order_by(Notification.created_at.desc()).limit(limit))
    notifs = result.scalars().all()
    return {"notifications": [{"id": str(n.id), "type": n.type, "title": n.title, "message": n.message, "read_at": n.read_at, "created_at": n.created_at} for n in notifs]}


@app.get("/api/v1/notifications/unread-count")
async def get_unread_count(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count(Notification.id)).where(and_(Notification.user_id == current_user.id, Notification.read_at.is_(None))))
    return {"unread_count": result.scalar() or 0}


@app.put("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Notification).where(and_(Notification.id == notification_id, Notification.user_id == current_user.id)))
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.read_at = datetime.utcnow()
    await db.commit()
    return {"message": "Marked as read"}


@app.put("/api/v1/notifications/read-all")
async def mark_all_read(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Notification).where(and_(Notification.user_id == current_user.id, Notification.read_at.is_(None))))
    notifs = result.scalars().all()
    for n in notifs:
        n.read_at = datetime.utcnow()
    await db.commit()
    return {"message": f"Marked {len(notifs)} notifications as read"}


@app.delete("/api/v1/notifications/{notification_id}")
async def delete_notification(notification_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Notification).where(and_(Notification.id == notification_id, Notification.user_id == current_user.id)))
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.delete(n)
    await db.commit()
    return {"message": "Notification deleted"}


# ==============================================================================
# 13. ADMIN  /api/v1/admin  (18 endpoints)
# ==============================================================================

@app.get("/api/v1/admin/stats")
async def get_admin_stats(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    users_count   = (await db.execute(select(func.count(User.id)))).scalar()
    orders_count  = (await db.execute(select(func.count(Order.id)))).scalar()
    parts_count   = (await db.execute(select(func.count(PartsCatalog.id)).where(PartsCatalog.is_active == True))).scalar()
    pending_orders = (await db.execute(select(func.count(Order.id)).where(Order.status.in_(["pending_payment", "paid", "processing"])))).scalar()

    # Orders grouped by status
    status_rows = (await db.execute(
        select(Order.status, func.count(Order.id).label("cnt"))
        .group_by(Order.status)
    )).fetchall()
    orders_by_status = {r[0]: r[1] for r in status_rows}

    # Gross revenue: sum of all payments that were ever successfully paid
    gross_revenue = (await db.execute(
        select(func.sum(Payment.amount)).where(
            Payment.status.in_(["paid", "refunded"])
        )
    )).scalar() or 0

    # Refunds issued
    refunds_total = (await db.execute(
        select(func.sum(Payment.refund_amount)).where(Payment.status == "refunded")
    )).scalar() or 0

    # Net revenue after refunds
    net_revenue = float(gross_revenue) - float(refunds_total)

    # Profit calculation based on net_revenue (from Payments — reliable source)
    # net_revenue already excludes refunds and includes VAT + shipping.
    # price_no_vat  = net_revenue / 1.17  (remove 17% VAT)
    # profit        = price_no_vat × (45 / 145)  ← 45% markup portion
    # cost          = price_no_vat - profit       ← supplier cost
    MARGIN_RATE = 0.45
    VAT_RATE = 0.17
    paid_statuses = ["paid", "processing", "shipped", "delivered"]

    price_no_vat_net = float(net_revenue) / (1 + VAT_RATE)           # strip VAT
    profit_total     = round(price_no_vat_net * (MARGIN_RATE / (1 + MARGIN_RATE)), 2)  # 45/145
    cost_total       = round(price_no_vat_net - profit_total, 2)
    margin_pct       = round((profit_total / cost_total * 100) if cost_total > 0 else 0, 1)  # ≈ 45%

    # Average order value
    avg_order = (await db.execute(
        select(func.avg(Order.total_amount)).where(Order.status.in_(paid_statuses))
    )).scalar() or 0

    return {
        "total_users": users_count,
        "total_orders": orders_count,
        "total_revenue": round(net_revenue, 2),
        "gross_revenue": round(float(gross_revenue), 2),
        "refunds_total": round(float(refunds_total), 2),
        "total_parts": parts_count,
        "pending_orders": pending_orders,
        "orders_by_status": orders_by_status,
        "profit_total": profit_total,
        "cost_total": cost_total,
        "margin_pct": margin_pct,
        "avg_order_value": round(float(avg_order), 2),
        "currency": "ILS",
    }


@app.get("/api/v1/admin/users")
async def get_admin_users(current_user: User = Depends(get_current_admin_user), limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.created_at.desc()).limit(limit))
    users = result.scalars().all()
    return {"users": [{"id": str(u.id), "email": u.email, "full_name": u.full_name, "phone": u.phone[-4:] if u.phone else None, "is_verified": u.is_verified, "is_admin": u.is_admin, "is_active": u.is_active, "created_at": u.created_at} for u in users]}


@app.put("/api/v1/admin/users/{user_id}")
async def update_admin_user(user_id: str, is_active: Optional[bool] = None, is_admin: Optional[bool] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if is_active is not None:
        user.is_active = is_active
    if is_admin is not None:
        user.is_admin = is_admin
    await db.commit()
    return {"message": "User updated"}


@app.get("/api/v1/admin/suppliers")
async def get_admin_suppliers(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import Supplier
    result = await db.execute(select(Supplier))
    suppliers = result.scalars().all()
    return {"suppliers": [{"id": str(s.id), "name": s.name, "country": s.country, "is_active": s.is_active, "priority": s.priority, "reliability_score": float(s.reliability_score)} for s in suppliers]}


@app.post("/api/v1/admin/suppliers")
async def create_supplier(data: SupplierCreate, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import Supplier
    supplier = Supplier(name=data.name, country=data.country, website=data.website, api_endpoint=data.api_endpoint, priority=data.priority, is_active=True)
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    return {"id": str(supplier.id), "message": "Supplier created"}


@app.put("/api/v1/admin/suppliers/{supplier_id}")
async def update_supplier(supplier_id: str, is_active: Optional[bool] = None, priority: Optional[int] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import Supplier
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    if is_active is not None:
        supplier.is_active = is_active
    if priority is not None:
        supplier.priority = priority
    await db.commit()
    return {"message": "Supplier updated"}


@app.delete("/api/v1/admin/suppliers/{supplier_id}")
async def delete_supplier(supplier_id: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import Supplier
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    await db.delete(supplier)
    await db.commit()
    return {"message": "Supplier deleted"}


@app.post("/api/v1/admin/suppliers/{supplier_id}/sync")
async def sync_supplier_catalog(supplier_id: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Sync started", "job_id": str(uuid.uuid4())}


# ==============================================================================
# 13c. ADMIN ORDERS  /api/v1/admin/orders  (2 endpoints)
# ==============================================================================

@app.get("/api/v1/admin/orders")
async def get_all_orders_admin(
    status: Optional[str] = None,
    limit: int = 200,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin: get ALL orders from all users, with user info."""
    stmt = (
        select(Order, User)
        .join(User, Order.user_id == User.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Order.status == status)
    result = await db.execute(stmt)
    rows = result.all()
    return {
        "orders": [
            {
                "id": str(o.id),
                "order_number": o.order_number,
                "status": o.status,
                "total": float(o.total_amount),
                "created_at": o.created_at,
                "user_email": u.email,
                "user_name": u.full_name,
            }
            for o, u in rows
        ]
    }


@app.put("/api/v1/admin/orders/{order_id}/status")
async def update_order_status_admin(
    order_id: str,
    new_status: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin: update order status and notify the customer."""
    allowed = ["pending_payment", "paid", "processing", "shipped", "delivered", "cancelled"]
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status. Allowed: {allowed}")

    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    old_status = order.status
    order.status = new_status
    if new_status == "shipped" and not order.shipped_at:
        order.shipped_at = datetime.utcnow()
    if new_status == "delivered" and not order.delivered_at:
        order.delivered_at = datetime.utcnow()
    if new_status == "cancelled" and not order.cancelled_at:
        order.cancelled_at = datetime.utcnow()

    status_labels = {
        "pending_payment": "ממתין לתשלום",
        "paid": "שולם",
        "processing": "בטיפול",
        "shipped": "נשלח",
        "delivered": "נמסר",
        "cancelled": "בוטל",
    }
    db.add(Notification(
        user_id=order.user_id,
        type="order_update",
        title="עדכון סטטוס הזמנה",
        message=f"הזמנה {order.order_number} עודכנה: {status_labels.get(new_status, new_status)}",
    ))
    await db.commit()
    return {"message": "Status updated", "old": old_status, "new": new_status}


@app.get("/api/v1/admin/social/posts")
async def get_scheduled_posts(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    return {"posts": []}


@app.post("/api/v1/admin/social/posts")
async def create_social_post(content: str, platforms: List[str], schedule_time: Optional[datetime] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    return {"post_id": str(uuid.uuid4()), "status": "pending_approval"}


@app.put("/api/v1/admin/social/posts/{post_id}")
async def update_social_post(post_id: str, content: Optional[str] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Post updated"}


@app.delete("/api/v1/admin/social/posts/{post_id}")
async def delete_social_post(post_id: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Post deleted"}


@app.get("/api/v1/admin/social/analytics")
async def get_social_analytics(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    return {"followers": {"facebook": 0, "instagram": 0, "tiktok": 0}, "engagement": {"likes": 0, "comments": 0, "shares": 0}}


@app.post("/api/v1/admin/social/generate-content")
async def generate_social_content(topic: str, platform: str, tone: str = "professional", current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    agent = get_agent("social_media_manager_agent")
    content = await agent.generate_post(topic, platform, tone)
    return {"content": content, "status": "pending_approval"}


@app.get("/api/v1/admin/analytics/dashboard")
async def get_analytics_dashboard(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    users_count = (await db.execute(select(func.count(User.id)))).scalar()
    orders_count = (await db.execute(select(func.count(Order.id)))).scalar()
    revenue = (await db.execute(select(func.sum(Order.total_amount)).where(Order.status.in_(["paid", "processing", "shipped", "delivered"])))).scalar() or 0
    return {"users": users_count, "orders": orders_count, "revenue": float(revenue), "period": "all_time"}


@app.get("/api/v1/admin/analytics/sales")
async def get_sales_analytics(start_date: Optional[date] = None, end_date: Optional[date] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from datetime import timedelta
    # Default: last 30 days ending today
    d_end   = end_date   or date.today()
    d_start = start_date or (d_end - timedelta(days=29))

    stmt = (
        select(
            func.date(Order.created_at).label("date"),
            func.count(Order.id).label("orders"),
            func.sum(Order.total_amount).label("revenue"),
        )
        .where(Order.created_at >= d_start)
        .where(Order.created_at < d_end + timedelta(days=1))
        .group_by(func.date(Order.created_at))
        .order_by(func.date(Order.created_at))
    )
    result = await db.execute(stmt)
    rows = {str(row.date): {"orders": row.orders, "revenue": float(row.revenue or 0)} for row in result}

    # Fill every day in range with 0 if no orders
    data = []
    current = d_start
    while current <= d_end:
        ds = str(current)
        data.append({"date": ds, "orders": rows.get(ds, {}).get("orders", 0), "revenue": rows.get(ds, {}).get("revenue", 0.0)})
        current += timedelta(days=1)

    return {"data": data}


@app.get("/api/v1/admin/analytics/users")
async def get_user_analytics(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(User.id)))).scalar()
    verified = (await db.execute(select(func.count(User.id)).where(User.is_verified == True))).scalar()
    return {"total_users": total, "verified_users": verified}


# ==============================================================================
# 13b. AGENTS CONTROL PANEL  /api/v1/admin/agents
# ==============================================================================

AGENTS_METADATA = {
    "router_agent": {
        "display_name": "Router Agent",
        "name_he": "סוכן ניתוב",
        "description": "Automatically routes messages to the appropriate specialized agent based on intent detection.",
        "description_he": "מנתב הודעות לסוכן המתאים על פי זיהוי כוונה",
        "capabilities": ["Intent detection", "Language detection", "Confidence scoring", "Context routing"],
        "model": "gpt-4o",
        "temperature": 0.1,
        "type": "internal",
        "icon": "GitBranch",
        "color": "gray",
    },
    "parts_finder_agent": {
        "display_name": "Parts Finder Agent",
        "name_he": "סוכן חיפוש חלקים",
        "description": "Finds auto parts by vehicle, category, or part number. Identifies vehicles from Israeli license plates via gov API.",
        "description_he": "מאתר חלקי רכב לפי רכב, קטגוריה או מספר חלק. זיהוי רכב ממספר לוחית ישראלי",
        "capabilities": ["Part search", "Vehicle identification (gov.il)", "Price comparison", "Image-based part ID"],
        "model": "gpt-4o",
        "temperature": 0.3,
        "type": "customer",
        "icon": "Search",
        "color": "blue",
    },
    "sales_agent": {
        "display_name": "Sales Agent",
        "name_he": "סוכן מכירות",
        "description": "Smart upselling and cross-selling. Presents Good/Better/Best options. Never reveals supplier names.",
        "description_he": "מכירה חכמה עם Good/Better/Best. לא חושף שמות ספקים",
        "capabilities": ["Product recommendations", "Upselling", "Bundle suggestions", "Price negotiation"],
        "model": "gpt-4o",
        "temperature": 0.7,
        "type": "customer",
        "icon": "TrendingUp",
        "color": "green",
    },
    "orders_agent": {
        "display_name": "Orders Agent",
        "name_he": "סוכן הזמנות",
        "description": "Manages order lifecycle from placement to delivery. Handles cancellations and returns. Dropshipping-aware.",
        "description_he": "ניהול מחזור חיי הזמנה. ביטולים וחזרות. תואם דרופשיפינג",
        "capabilities": ["Order status", "Tracking", "Cancellation", "Returns", "Dropshipping flow"],
        "model": "gpt-4o",
        "temperature": 0.3,
        "type": "customer",
        "icon": "Package",
        "color": "orange",
    },
    "finance_agent": {
        "display_name": "Finance Agent",
        "name_he": "סוכן פיננסי",
        "description": "Handles payments, invoices, and refunds. Licensed business (מס׳ עוסק: 060633880). VAT 17%, refund policy.",
        "description_he": "תשלומים, חשבוניות, החזרים. עוסק מורשה מס׳ 060633880",
        "capabilities": ["Payment questions", "Invoice generation", "Refund calculations", "VAT breakdowns"],
        "model": "gpt-4o",
        "temperature": 0.2,
        "type": "customer",
        "icon": "DollarSign",
        "color": "yellow",
    },
    "service_agent": {
        "display_name": "Service Agent",
        "name_he": "סוכן שירות לקוחות",
        "description": "Default fallback agent. Handles general questions, complaints, and technical support with empathy.",
        "description_he": "סוכן ברירת מחדל. שאלות כלליות, תלונות, תמיכה טכנית",
        "capabilities": ["General support", "Complaint handling", "Technical questions", "Escalation"],
        "model": "gpt-4o",
        "temperature": 0.8,
        "type": "customer",
        "icon": "HeartHandshake",
        "color": "pink",
    },
    "security_agent": {
        "display_name": "Security Agent",
        "name_he": "סוכן אבטחה",
        "description": "Handles login issues, 2FA, password reset, and suspicious activity. Strict identity verification.",
        "description_he": "בעיות כניסה, 2FA, איפוס סיסמה, פעילות חשודה",
        "capabilities": ["2FA support", "Password reset", "Account unlock", "Suspicious activity"],
        "model": "gpt-4o",
        "temperature": 0.2,
        "type": "customer",
        "icon": "Shield",
        "color": "red",
    },
    "marketing_agent": {
        "display_name": "Marketing Agent",
        "name_he": "סוכן שיווק",
        "description": "Manages promotions, coupons, referral program (100₪ + 10%), and loyalty points.",
        "description_he": "קופונים, תוכנית הפניות (100₪ + 10%), נקודות נאמנות",
        "capabilities": ["Coupon management", "Referral program", "Loyalty points", "Newsletter"],
        "model": "gpt-4o",
        "temperature": 0.7,
        "type": "customer",
        "icon": "Megaphone",
        "color": "purple",
    },
    "supplier_manager_agent": {
        "display_name": "Supplier Manager Agent",
        "name_he": "סוכן ניהול ספקים",
        "description": "Background agent. Daily price sync at 02:00. Manages 3 active suppliers. Does NOT interact with customers.",
        "description_he": "סוכן רקע. סנכרון מחירים יומי 02:00. לא משוחח עם לקוחות",
        "capabilities": ["Price sync", "Catalog updates", "Availability monitoring", "Supplier performance"],
        "model": "gpt-4o",
        "temperature": 0.1,
        "type": "admin",
        "icon": "Truck",
        "color": "indigo",
    },
    "social_media_manager_agent": {
        "display_name": "Social Media Manager Agent",
        "name_he": "סוכן מנהל מדיה חברתית",
        "description": "Generates content for Facebook, Instagram, TikTok, LinkedIn, Telegram. All posts need approval before publish.",
        "description_he": "יצירת תוכן לפייסבוק, אינסטגרם, טיקטוק, לינקדאין, טלגרם",
        "capabilities": ["Content generation", "Post scheduling", "Platform-specific tone", "Hashtag generation"],
        "model": "gpt-4o",
        "temperature": 0.9,
        "type": "admin",
        "icon": "Share2",
        "color": "teal",
    },
}

@app.get("/api/v1/admin/agents")
async def list_agents(current_user: User = Depends(get_current_admin_user)):
    from BACKEND_AI_AGENTS import AGENT_MAP
    github_token = os.getenv("GITHUB_TOKEN", "")
    ai_status = "active" if github_token else "mocked"

    agents = []
    for name, meta in AGENTS_METADATA.items():
        agents.append({
            "name": name,
            **meta,
            "ai_status": ai_status,
            "is_loaded": name in AGENT_MAP,
        })
    return {
        "agents": agents,
        "total": len(agents),
        "ai_status": ai_status,
        "github_token_set": bool(github_token),
    }


@app.post("/api/v1/admin/agents/{agent_name}/test")
async def test_agent(
    agent_name: str,
    body: dict,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from BACKEND_AI_AGENTS import get_agent, AGENT_MAP
    if agent_name not in AGENT_MAP:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    agent = get_agent(agent_name)
    try:
        response = await agent.process(
            message=message,
            conversation_history=[],
            db=db,
        )
        return {"agent": agent_name, "response": response, "status": "ok"}
    except Exception as e:
        return {"agent": agent_name, "response": str(e), "status": "error"}


# ==============================================================================
# 14a. ADMIN PARTS IMPORT  /api/v1/admin/parts/import
# ==============================================================================

@app.post("/api/v1/admin/parts/import")
async def import_parts_excel(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Import parts from Excel (.xlsx / .xls) file.
    Supported columns (case-insensitive):
      sku / pin / part_number / מקט  → sku (required)
      name / part_name / שם          → name (required)
      category / קטגוריה             → category
      manufacturer / יצרן            → manufacturer
      part_type / סוג / type         → part_type
      description / תיאור            → description
      base_price / price / מחיר      → base_price
      compatible_vehicles / רכבים    → compatible_vehicles (comma-separated)
    Rows with an existing SKU are updated; new SKUs are created.
    """
    import openpyxl, io as _io

    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="יש להעלות קובץ Excel (.xlsx או .xls) בלבד")

    contents = await file.read()
    try:
        wb = openpyxl.load_workbook(_io.BytesIO(contents), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=400, detail="לא ניתן לפתוח את הקובץ - ודא שהוא קובץ Excel תקין")

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(status_code=400, detail="הקובץ ריק")

    # detect header row
    header = [str(c).strip().lower() if c else '' for c in rows[0]]

    _SKU   = {'sku', 'pin', 'part_number', 'מקט', 'מק"ט', 'part number'}
    _NAME  = {'name', 'part_name', 'שם', 'שם חלק', 'part name'}
    _CAT   = {'category', 'קטגוריה', 'cat'}
    _MFR   = {'manufacturer', 'יצרן', 'brand', 'מותג'}
    _TYPE  = {'part_type', 'type', 'סוג', 'סוג חלק'}
    _DESC  = {'description', 'תיאור', 'desc'}
    _PRICE = {'base_price', 'price', 'מחיר', 'base price'}
    _COMPAT= {'compatible_vehicles', 'רכבים', 'רכבים תואמים', 'compatible vehicles'}

    def _col(names):
        for i, h in enumerate(header):
            if h in names:
                return i
        return None

    ci = {k: _col(v) for k, v in {
        'sku': _SKU, 'name': _NAME, 'category': _CAT,
        'manufacturer': _MFR, 'part_type': _TYPE, 'description': _DESC,
        'base_price': _PRICE, 'compatible_vehicles': _COMPAT,
    }.items()}

    if ci['sku'] is None or ci['name'] is None:
        raise HTTPException(
            status_code=400,
            detail=f"לא נמצאו עמודות חובה (sku/pin ו-name). כותרות שנמצאו: {', '.join(header)}"
        )

    def _get(row, key):
        idx = ci.get(key)
        if idx is None or idx >= len(row):
            return None
        v = row[idx]
        return str(v).strip() if v is not None else None

    created = updated = skipped = 0
    errors = []

    for row_num, row in enumerate(rows[1:], start=2):
        sku_val = _get(row, 'sku')
        name_val = _get(row, 'name')
        if not sku_val or not name_val:
            skipped += 1
            continue
        try:
            price_raw = _get(row, 'base_price')
            try:
                price = float(price_raw) if price_raw else None
            except (ValueError, TypeError):
                price = None

            compat_raw = _get(row, 'compatible_vehicles')
            compat = [v.strip() for v in compat_raw.split(',')] if compat_raw else []

            existing = (await db.execute(
                select(PartsCatalog).where(PartsCatalog.sku == sku_val)
            )).scalars().first()

            if existing:
                existing.name          = name_val
                existing.category      = _get(row, 'category') or existing.category
                existing.manufacturer  = _get(row, 'manufacturer') or existing.manufacturer
                existing.part_type     = _get(row, 'part_type') or existing.part_type
                existing.description   = _get(row, 'description') or existing.description
                if price is not None:
                    existing.base_price = price
                if compat:
                    existing.compatible_vehicles = compat
                existing.updated_at = datetime.utcnow()
                updated += 1
            else:
                db.add(PartsCatalog(
                    sku=sku_val,
                    name=name_val,
                    category=_get(row, 'category'),
                    manufacturer=_get(row, 'manufacturer'),
                    part_type=_get(row, 'part_type') or 'Aftermarket',
                    description=_get(row, 'description'),
                    base_price=price,
                    compatible_vehicles=compat,
                    is_active=True,
                ))
                created += 1
        except Exception as e:
            errors.append(f"שורה {row_num}: {str(e)}")

    await db.commit()
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "total_processed": created + updated,
    }


# ==============================================================================
# 14. SYSTEM  /api/v1/system  (3 endpoints)
# ==============================================================================

@app.get("/api/v1/system/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat(), "version": "1.0.0"}


# ── Admin: price-sync status & manual trigger ─────────────────────────────────
@app.get("/api/v1/admin/price-sync/status")
async def price_sync_status(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Return last price-sync log entry and how long until next run."""
    last = (await db.execute(
        select(SystemLog)
        .where(SystemLog.logger_name == "supplier_manager_agent")
        .order_by(SystemLog.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    if not last:
        return {"last_sync": None, "next_sync_in_h": 0, "status": "never_run"}

    elapsed_h = (datetime.utcnow() - last.created_at).total_seconds() / 3600
    next_in_h = max(0.0, PRICE_SYNC_INTERVAL_H - elapsed_h)
    return {
        "last_sync": last.created_at.isoformat(),
        "message": last.message,
        "elapsed_h": round(elapsed_h, 2),
        "next_sync_in_h": round(next_in_h, 2),
        "interval_h": PRICE_SYNC_INTERVAL_H,
    }


@app.post("/api/v1/admin/price-sync/run")
async def trigger_price_sync(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger an immediate price sync (runs in background, returns instantly)."""
    from BACKEND_AI_AGENTS import SupplierManagerAgent

    async def _run():
        async with async_session_factory() as session:
            try:
                agent = SupplierManagerAgent()
                await agent.sync_prices(session)
            except Exception as e:
                print(f"[PriceSync manual] error: {e}")

    asyncio.create_task(_run())
    return {"status": "started", "message": "Price sync triggered in background"}


@app.get("/api/v1/system/settings")
async def get_public_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemSetting).where(SystemSetting.is_public == True))
    settings = result.scalars().all()
    return {s.key: s.value for s in settings}


@app.get("/api/v1/system/version")
async def get_version():
    return {"version": "1.0.0", "build": "2026.02.28", "environment": os.getenv("ENVIRONMENT", "development")}


# ==============================================================================
# EVENTS & ERROR HANDLERS
# ==============================================================================

@app.on_event("startup")
async def startup():
    print("🚀 Auto Spare API starting...")
    print(f"   Environment: {os.getenv('ENVIRONMENT', 'development')}")
    asyncio.create_task(_price_sync_loop())
    print("✅ All systems ready — price-sync scheduler started")


# ── Background price-sync loop ────────────────────────────────────────────────
PRICE_SYNC_INTERVAL_H = int(os.getenv("PRICE_SYNC_INTERVAL_H", "24"))  # hours


async def _price_sync_loop():
    """
    Runs the SupplierManagerAgent.sync_prices() every PRICE_SYNC_INTERVAL_H hours.
    On first start, checks the last SystemLog entry: if < interval ago, waits the
    remainder; otherwise runs immediately.
    """
    from BACKEND_AI_AGENTS import SupplierManagerAgent
    interval_s = PRICE_SYNC_INTERVAL_H * 3600

    # Determine how long to wait before the first run
    first_wait = 0
    try:
        async with async_session_factory() as db:
            last_log = (await db.execute(
                select(SystemLog)
                .where(SystemLog.logger_name == "supplier_manager_agent")
                .order_by(SystemLog.created_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if last_log and last_log.created_at:
                elapsed = (datetime.utcnow() - last_log.created_at).total_seconds()
                first_wait = max(0, interval_s - elapsed)
    except Exception as e:
        print(f"[PriceSync] could not check last run: {e}")

    if first_wait > 0:
        print(f"[PriceSync] last sync was recent — next run in {first_wait/3600:.1f}h")
    else:
        print("[PriceSync] no recent sync found — running now")

    await asyncio.sleep(first_wait)

    while True:
        try:
            async with async_session_factory() as db:
                agent = SupplierManagerAgent()
                report = await agent.sync_prices(db)
                print(
                    f"[PriceSync] ✅ done — "
                    f"updated={report['parts_updated']:,}  "
                    f"avail_changes={report['availability_changes']}  "
                    f"errors={len(report['errors'])}"
                )
        except Exception as exc:
            print(f"[PriceSync] ❌ error: {exc}")
        await asyncio.sleep(interval_s)


@app.on_event("shutdown")
async def shutdown():
    from BACKEND_AUTH_SECURITY import close_redis
    await close_redis()
    print("👋 Auto Spare API shut down")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail, "status_code": exc.status_code})


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    print(f"[ERROR] Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"error": "Internal server error", "status_code": 500})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("BACKEND_API_ROUTES:app", host="0.0.0.0", port=8000, reload=True)
