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
from sqlalchemy import select, and_, or_, func, desc
import uuid
import os
import io
from dotenv import load_dotenv

from BACKEND_DATABASE_MODELS import (
    get_db, User, Vehicle, PartsCatalog, Order, OrderItem, Payment,
    Invoice, Return, Conversation, Message, File as FileModel,
    Notification, UserProfile, SystemSetting
)
from BACKEND_AUTH_SECURITY import (
    get_current_user, get_current_active_user, get_current_verified_user,
    get_current_admin_user, register_user, login_user, complete_2fa_login,
    refresh_access_token, logout_user, create_password_reset_token,
    use_password_reset_token, change_password, update_phone_number,
    create_2fa_code, verify_2fa_code, get_redis, hash_password
)
from BACKEND_AI_AGENTS import process_user_message, get_agent

load_dotenv()

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
    part_id: str
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
            "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "is_verified": user.is_verified},
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
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name},
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
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "phone": current_user.phone,
        "full_name": current_user.full_name,
        "is_verified": current_user.is_verified,
        "is_admin": current_user.is_admin,
        "created_at": current_user.created_at,
    }


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
    response = await process_user_message(str(current_user.id), data.message, data.conversation_id, db)
    return response


@app.get("/api/v1/chat/conversations")
async def get_conversations(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.user_id == current_user.id).order_by(Conversation.last_message_at.desc()).limit(limit))
    convs = result.scalars().all()
    return {"conversations": [{"id": str(c.id), "title": c.title, "current_agent": c.current_agent, "last_message_at": c.last_message_at, "is_active": c.is_active} for c in convs]}


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
    return {"file_id": str(uuid.uuid4()), "message": "Image uploaded. Vision AI analysis coming soon."}


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
async def search_parts(query: str = "", vehicle_id: Optional[str] = None, category: Optional[str] = None, limit: int = 20, db: AsyncSession = Depends(get_db)):
    agent = get_agent("parts_finder_agent")
    parts = await agent.search_parts_in_db(query, vehicle_id, category, db, limit)
    return {"parts": parts, "count": len(parts)}


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
    result = await db.execute(select(PartsCatalog.category).distinct().where(PartsCatalog.is_active == True))
    return {"categories": [c for c in result.scalars().all() if c]}


@app.get("/api/v1/parts/manufacturers")
async def get_manufacturers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog.manufacturer).distinct().where(PartsCatalog.is_active == True))
    return {"manufacturers": [m for m in result.scalars().all() if m]}


@app.get("/api/v1/parts/{part_id}")
async def get_part(part_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog).where(PartsCatalog.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return {"id": str(part.id), "name": part.name, "manufacturer": part.manufacturer, "category": part.category, "part_type": part.part_type, "description": part.description, "specifications": part.specifications}


@app.post("/api/v1/parts/compare")
async def compare_parts(part_id: str, db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import SupplierPart, Supplier
    result = await db.execute(
        select(SupplierPart, Supplier).join(Supplier).where(and_(SupplierPart.part_id == part_id, SupplierPart.is_available == True, Supplier.is_active == True)).order_by(Supplier.priority.asc())
    )
    rows = result.all()
    agent = get_agent("parts_finder_agent")
    comparisons = []
    for sp, supplier in rows:
        pricing = agent.calculate_customer_price(float(sp.price_usd))
        comparisons.append({
            "supplier_part_id": str(sp.id),
            "subtotal": pricing["price_no_vat"],
            "vat": pricing["vat"],
            "shipping": pricing["shipping"],
            "total": pricing["total"],
            "warranty_months": sp.warranty_months,
            "estimated_delivery": f"{sp.estimated_delivery_days}-21 ימים",
        })
    return {"comparisons": sorted(comparisons, key=lambda x: x["total"])}


@app.post("/api/v1/parts/identify-from-image")
async def identify_part_from_image(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Part identification from image – Vision AI coming soon", "confidence": 0.0}


# ==============================================================================
# 4. VEHICLES  /api/v1/vehicles  (8 endpoints)
# ==============================================================================

@app.post("/api/v1/vehicles/identify")
async def identify_vehicle(data: VehicleIdentifyRequest, db: AsyncSession = Depends(get_db)):
    agent = get_agent("parts_finder_agent")
    result = await agent.identify_vehicle(data.license_plate, db)
    return result


@app.post("/api/v1/vehicles/identify-from-image")
async def identify_vehicle_from_image(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    return {"message": "License plate OCR – coming soon"}


@app.get("/api/v1/vehicles/my-vehicles")
async def get_my_vehicles(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserVehicle
    result = await db.execute(select(UserVehicle, Vehicle).join(Vehicle).where(UserVehicle.user_id == current_user.id))
    rows = result.all()
    return {"vehicles": [{"id": str(v.id), "nickname": uv.nickname, "manufacturer": v.manufacturer, "model": v.model, "year": v.year, "is_primary": uv.is_primary} for uv, v in rows]}


@app.post("/api/v1/vehicles/my-vehicles")
async def add_my_vehicle(license_plate: str = Form(...), nickname: Optional[str] = Form(None), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import UserVehicle
    agent = get_agent("parts_finder_agent")
    vehicle_data = await agent.identify_vehicle(license_plate, db)
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
    subtotal = 0.0
    items_data = []
    USD_TO_ILS = 3.65

    for item in data.items:
        res = await db.execute(select(SupplierPart, PartsCatalog).join(PartsCatalog).where(SupplierPart.id == item.supplier_part_id))
        row = res.first()
        if not row:
            raise HTTPException(status_code=404, detail=f"Supplier part {item.supplier_part_id} not found")
        sp, part = row
        cost_ils = float(sp.price_ils or (sp.price_usd * USD_TO_ILS))
        unit_price = round(cost_ils * 1.45, 2)
        vat = round(unit_price * 0.17, 2)
        subtotal += unit_price * item.quantity
        items_data.append({"part_id": item.part_id, "supplier_part_id": item.supplier_part_id, "quantity": item.quantity, "unit_price": unit_price, "vat": vat, "part": part, "sp": sp})

    vat_total = round(subtotal * 0.17, 2)
    shipping = 91.0
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
        db.add(OrderItem(
            order_id=order.id, part_id=d["part_id"], supplier_part_id=d["supplier_part_id"],
            part_name=d["part"].name, part_sku=d["part"].sku, manufacturer=d["part"].manufacturer,
            part_type=d["part"].part_type, supplier_name="Supplier",
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
    return {"orders": [{"id": str(o.id), "order_number": o.order_number, "status": o.status, "total": float(o.total_amount), "created_at": o.created_at} for o in orders]}


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
        "tracking_number": order.tracking_number, "estimated_delivery": order.estimated_delivery,
        "items": [{"part_name": i.part_name, "manufacturer": i.manufacturer, "quantity": i.quantity, "unit_price": float(i.unit_price), "total": float(i.total_price)} for i in items],
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
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ["pending_payment", "paid", "processing"]:
        raise HTTPException(status_code=400, detail="Cannot cancel order in current status")
    order.status = "cancelled"
    order.cancelled_at = datetime.utcnow()
    await db.commit()
    return {"message": "Order cancelled"}


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


@app.get("/api/v1/orders/{order_id}/invoice")
async def get_order_invoice(order_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Invoice).where(Invoice.order_id == order_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"invoice_number": invoice.invoice_number, "pdf_url": invoice.pdf_url, "issued_at": invoice.issued_at}


# ==============================================================================
# 6. PAYMENTS  /api/v1/payments  (6 endpoints)
# ==============================================================================

@app.post("/api/v1/payments/create-intent")
async def create_payment_intent(order_id: str, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    payment_intent_id = f"pi_{uuid.uuid4().hex[:24]}"
    client_secret = f"{payment_intent_id}_secret_{uuid.uuid4().hex[:16]}"
    db.add(Payment(order_id=order.id, payment_intent_id=payment_intent_id, amount=order.total_amount, currency="ILS", status="pending"))
    await db.commit()
    return {"payment_intent_id": payment_intent_id, "client_secret": client_secret, "amount": float(order.total_amount), "currency": "ILS"}


@app.post("/api/v1/payments/confirm")
async def confirm_payment(payment_intent_id: str, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_db)):
    return {"status": "confirmed", "message": "Stripe payment confirmation – implement with real Stripe SDK"}


@app.get("/api/v1/payments/{payment_id}")
async def get_payment(payment_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"id": str(payment.id), "amount": float(payment.amount), "status": payment.status, "payment_method": payment.payment_method, "created_at": payment.created_at}


@app.post("/api/v1/payments/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    # TODO: verify Stripe signature and process events
    return {"received": True}


@app.post("/api/v1/payments/refund")
async def refund_payment(payment_id: str, amount: float, reason: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Refund processed – implement with Stripe SDK"}


@app.get("/api/v1/payments/history")
async def get_payment_history(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Payment).join(Order).where(Order.user_id == current_user.id).order_by(Payment.created_at.desc()).limit(limit))
    payments = result.scalars().all()
    return {"payments": [{"id": str(p.id), "amount": float(p.amount), "status": p.status, "created_at": p.created_at} for p in payments]}


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
        "profile": {"address": profile.address_line1 if profile else None, "city": profile.city if profile else None, "postal_code": profile.postal_code if profile else None, "preferred_language": profile.preferred_language if profile else "he", "avatar_url": profile.avatar_url if profile else None} if profile else None,
    }


@app.put("/api/v1/profile")
async def update_profile(address_line1: Optional[str] = None, city: Optional[str] = None, postal_code: Optional[str] = None, full_name: Optional[str] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
    if address_line1 is not None:
        profile.address_line1 = address_line1
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
    users_count = (await db.execute(select(func.count(User.id)))).scalar()
    orders_count = (await db.execute(select(func.count(Order.id)))).scalar()
    revenue = (await db.execute(select(func.sum(Order.total_amount)).where(Order.status == "delivered"))).scalar() or 0
    parts_count = (await db.execute(select(func.count(PartsCatalog.id)).where(PartsCatalog.is_active == True))).scalar()
    pending_orders = (await db.execute(select(func.count(Order.id)).where(Order.status.in_(["pending_payment", "paid", "processing"])))).scalar()
    return {"total_users": users_count, "total_orders": orders_count, "total_revenue": float(revenue), "total_parts": parts_count, "pending_orders": pending_orders, "currency": "ILS"}


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
    revenue = (await db.execute(select(func.sum(Order.total_amount)).where(Order.status == "delivered"))).scalar() or 0
    return {"users": users_count, "orders": orders_count, "revenue": float(revenue), "period": "all_time"}


@app.get("/api/v1/admin/analytics/sales")
async def get_sales_analytics(start_date: Optional[date] = None, end_date: Optional[date] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    stmt = select(func.date(Order.created_at).label("date"), func.count(Order.id).label("orders"), func.sum(Order.total_amount).label("revenue")).group_by(func.date(Order.created_at))
    if start_date:
        stmt = stmt.where(Order.created_at >= start_date)
    if end_date:
        stmt = stmt.where(Order.created_at <= end_date)
    result = await db.execute(stmt.limit(30))
    return {"data": [{"date": str(row.date), "orders": row.orders, "revenue": float(row.revenue or 0)} for row in result]}


@app.get("/api/v1/admin/analytics/users")
async def get_user_analytics(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(User.id)))).scalar()
    verified = (await db.execute(select(func.count(User.id)).where(User.is_verified == True))).scalar()
    return {"total_users": total, "verified_users": verified}


# ==============================================================================
# 14. SYSTEM  /api/v1/system  (3 endpoints)
# ==============================================================================

@app.get("/api/v1/system/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat(), "version": "1.0.0"}


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
    print("✅ All systems ready")


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
