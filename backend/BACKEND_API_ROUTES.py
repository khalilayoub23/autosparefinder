"""
==============================================================================
AUTO SPARE - API ROUTES (FastAPI)
==============================================================================
114 endpoints across 14 categories.
Imports: BACKEND_DATABASE_MODELS, BACKEND_AUTH_SECURITY, BACKEND_AI_AGENTS
==============================================================================
"""

from fastapi import FastAPI, Depends, HTTPException, status, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, Response
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime, date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc, text
import logging
import uuid
from uuid import UUID as _UUID
import os
import io
import asyncio
import httpx
from dotenv import load_dotenv

# Sentinel user for anonymous WhatsApp conversations (no registered account found)
WHATSAPP_ANON_USER_ID = _UUID("00000000-0000-0000-0000-000000000001")

from BACKEND_DATABASE_MODELS import (
    get_db, get_pii_db, async_session_factory, pii_session_factory, User, Vehicle, PartsCatalog, Order, OrderItem, Payment,
    Invoice, Return, Conversation, Message, File as FileModel,
    Notification, UserProfile, SystemSetting, SupplierPart, Supplier,
    CarBrand, SystemLog, USD_TO_ILS, ApprovalQueue, SocialPost, JobFailure, AuditLog,
)
from BACKEND_AUTH_SECURITY import (
    get_current_user, get_current_active_user, get_current_verified_user,
    get_current_admin_user, get_current_super_admin, register_user, login_user, complete_2fa_login,
    refresh_access_token, logout_user, create_password_reset_token,
    use_password_reset_token, change_password, update_phone_number,
    create_2fa_code, verify_2fa_code, get_redis, hash_password, publish_notification,
    check_rate_limit
)
from BACKEND_AI_AGENTS import process_user_message, process_agent_response_for_message, get_agent, OrdersAgent
from auto_backup import _backup_loop

load_dotenv()

logger = logging.getLogger(__name__)

BLOCKED_SETTINGS = {
    "jwt_secret", "jwt_refresh_secret", "stripe_secret_key",
    "stripe_webhook_secret", "hf_token", "database_url",
    "database_pii_url", "redis_url", "encryption_key",
    "twilio_auth_token", "sendgrid_api_key",
}

# Fire-and-forget task semaphore: cap unbounded asyncio.create_task(_guarded_task()) fan-out.
_TASK_SEMAPHORE = asyncio.Semaphore(50)


async def _guarded_task(coro) -> None:
    """Acquire the shared semaphore before running a fire-and-forget coroutine."""
    async with _TASK_SEMAPHORE:
        await coro


class SuperAdminSettingCreateBody(BaseModel):
    key: str
    value: Optional[str] = None
    value_type: str = "string"
    description: Optional[str] = None
    is_public: bool = False


class SuperAdminSettingUpdateBody(BaseModel):
    value: Optional[str] = None
    value_type: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None


class SuperAdminUserRoleUpdateBody(BaseModel):
    is_admin: bool
    is_super_admin: bool
    role: Optional[str] = None


def _is_blocked_setting_key(key: str) -> bool:
    return key.strip().lower() in BLOCKED_SETTINGS


async def _write_audit_log(
    db: AsyncSession,
    current_user: User,
    action: str,
    entity_type: str,
    entity_id: Optional[_UUID] = None,
    old_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
    request: Optional[Request] = None,
) -> None:
    db.add(
        AuditLog(
            user_id=current_user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=old_value,
            new_value=new_value,
            ip_address=request.client.host if (request and request.client) else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )
    await db.commit()


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
    # USD_TO_ILS is imported from BACKEND_DATABASE_MODELS (single source of truth)
    agent = OrdersAgent()

    # Find all admin users for audit logs
    admins_res = await db.execute(select(User).where(User.is_admin == True))
    admins = admins_res.scalars().all()

    for order in paid_orders:
        # Load items from PII DB only (OrderItem already stores all needed fields)
        items_res = await db.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )
        order_items = items_res.scalars().all()

        if not order_items:
            print(f"[Fulfillment] No items for order {order.order_number} — marking processing for manual review")
            order.status = "processing"
            for admin in admins:
                _title = f"⚠️ {order.order_number} – אין נתוני ספק"
                _msg = f"ההזמנה {order.order_number} שולמה אך אין פריטים. טיפול ידני נדרש."
                db.add(Notification(
                    user_id=admin.id,
                    type="supplier_order",
                    title=_title,
                    message=_msg,
                    data={"order_id": str(order.id), "order_number": order.order_number, "needs_manual": True},
                ))
                asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "supplier_order", "title": _title, "message": _msg})))
            continue

        # Group items by supplier_name (already stored on OrderItem)
        by_supplier: dict = {}
        for oi in order_items:
            sup_name = oi.supplier_name or "ספק לא ידוע"
            if sup_name not in by_supplier:
                by_supplier[sup_name] = {"items": [], "total_cost_ils": 0.0}
            item_cost = float(oi.total_price or (oi.unit_price * oi.quantity))
            by_supplier[sup_name]["items"].append({
                "part_name": oi.part_name,
                "part_sku": oi.part_sku or "",
                "supplier_sku": "",
                "manufacturer": oi.manufacturer or "",
                "quantity": oi.quantity,
                "unit_cost_ils": float(oi.unit_price or 0),
                "shipping_ils": 0.0,
                "item_total_ils": round(item_cost, 2),
                "warranty_months": oi.warranty_months or 12,
                "availability": "In Stock",
                "estimated_delivery_days": 14,
            })
            by_supplier[sup_name]["total_cost_ils"] += item_cost

        # Build by_supplier_id-compatible dict for agent
        # Build by_supplier_id-compatible dict for agent.
        # Derive the supplier country from the known SUPPLIER_SHIPPING_RATES mapping
        # so the fulfillment agent selects the correct carrier (H-7 fix).
        _SUPPLIER_COUNTRY_MAP = {
            "AutoParts Pro IL": "il",
            "Global Parts Hub": "de",
            "EastAuto Supply":  "cn",
        }
        by_supplier_for_agent = {
            f"name:{k}": {
                "supplier": type("S", (), {
                    "id": f"name:{k}",
                    "name": k,
                    "website": "",
                    "country": _SUPPLIER_COUNTRY_MAP.get(k, "il"),
                })(),
                "items": v["items"],
                "total_cost_ils": v["total_cost_ils"],
            }
            for k, v in by_supplier.items()
        }

        # ── Agent auto-fulfills: generates tracking, updates order, notifies customer ──
        try:
            all_tracking = await agent.auto_fulfill_order(order, by_supplier_for_agent, db)
        except Exception as e:
            print(f"[Fulfillment] Agent fulfill error: {e}")
            all_tracking = []

        # ── Admin audit log ──
        for sup_name, sup_data in by_supplier.items():
            sup_tracking = next(
                (t for t in (all_tracking or []) if t.get("supplier") == sup_name), {}
            )
            items_lines = "\n".join(
                f"  • {it['part_name']} ×{it['quantity']} — ₪{it['item_total_ils']:.2f}"
                for it in sup_data["items"]
            )
            for admin in admins:
                _title2 = f"🤖 הסוכן הזמין מ-{sup_name} עבור {order.order_number}"
                _msg2 = (
                    f"הסוכן ביצע הזמנה אוטומטית מ-{sup_name}.\n{items_lines}\n"
                    f"מספר מעקב: {sup_tracking.get('tracking_number', '—')} ({sup_tracking.get('carrier', '—')})"
                )
                db.add(Notification(
                    user_id=admin.id,
                    type="supplier_order",
                    title=_title2,
                    message=_msg2,
                    data={
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "supplier_name": sup_name,
                        "items": sup_data["items"],
                        "total_cost_ils": round(sup_data["total_cost_ils"], 2),
                        "currency": "ILS",
                        "tracking_number": sup_tracking.get("tracking_number", ""),
                        "tracking_url": sup_tracking.get("tracking_url", ""),
                        "carrier": sup_tracking.get("carrier", ""),
                        "auto_fulfilled": True,
                    },
                    read_at=datetime.utcnow(),
                ))
                asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "supplier_order", "title": _title2, "message": _msg2})))

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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-Idempotency-Key"],
)

if os.getenv("ENVIRONMENT", "development") == "production":
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    app.add_middleware(HTTPSRedirectMiddleware)

# ==============================================================================
# SUPPLIER MASKING  — customer-facing routes never expose real supplier names.
# Uses a deterministic hash so the same supplier always maps to the same number,
# regardless of which worker process handles the request.
# ==============================================================================
import hashlib as _hashlib

def _mask_supplier(name: str) -> str:
    """Return a deterministic numbered alias for a supplier name (e.g. 'ספק #3').
    The number is derived from the supplier name via SHA-256, so it is consistent
    across all worker processes and restarts without shared state.
    """
    if not name:
        return "ספק"
    # Take first 4 hex bytes of SHA-256 → 0..4294967295, map to 1..9999
    digest = int(_hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
    num = (digest % 9999) + 1
    return f"ספק #{num}"


async def _cart_to_response(items: list, cat_db: AsyncSession) -> list:
    """
    Convert CartItem ORM rows → camelCase dicts matching the mobile cartStore.ts CartItem shape:
        id, partId, name, price, quantity, imageUrl, supplierId, supplierName, stockAvailable
    Fetches part + supplier details from the catalog DB in a single JOIN query.
    """
    from BACKEND_DATABASE_MODELS import SupplierPart, PartsCatalog, Supplier as SupplierModel, PartImage

    if not items:
        return []

    sp_ids = [i.supplier_part_id for i in items]
    rows = await cat_db.execute(
        select(SupplierPart, PartsCatalog, SupplierModel)
        .join(PartsCatalog, SupplierPart.part_id == PartsCatalog.id)
        .join(SupplierModel, SupplierPart.supplier_id == SupplierModel.id)
        .where(SupplierPart.id.in_(sp_ids))
    )
    catalog: dict = {str(r.SupplierPart.id): r for r in rows}

    # Fetch primary images for all parts in one query
    part_ids = [r[1].id for r in catalog.values()]
    img_res = await cat_db.execute(
        select(PartImage)
        .where(and_(PartImage.part_id.in_(part_ids), PartImage.is_primary == True))
    )
    images: dict = {str(r.part_id): r.url for r in img_res.scalars()}

    result = []
    for item in items:
        row = catalog.get(str(item.supplier_part_id))
        if not row:  # supplier_part deleted from catalog — skip silently
            continue
        sp, part, supplier = row.SupplierPart, row.PartsCatalog, row.SupplierModel
        result.append({
            "id":             str(item.id),
            "partId":         str(item.part_id),
            "name":           part.name,
            "price":          float(item.unit_price),
            "quantity":       item.quantity,
            "imageUrl":       images.get(str(part.id)),
            "supplierId":     str(sp.supplier_id),
            "supplierName":   _mask_supplier(supplier.name),
            "stockAvailable": sp.stock_quantity if sp.stock_quantity is not None else 99,
        })
    return result


# ==============================================================================
# VIRUS SCANNING — ClamAV via clamd (graceful degradation when daemon absent)
# ==============================================================================
import clamd as _clamd

def _scan_bytes_for_virus(content: bytes) -> tuple:
    """
    Scan raw bytes with ClamAV daemon.
    Returns: ('clean', None) | ('infected', '<VirusName>') | ('skipped', None)
    Tries Unix socket first, falls back to TCP, then skips gracefully.
    """
    for _make_scanner in (
        lambda: _clamd.ClamdUnixSocket(),
        lambda: _clamd.ClamdNetworkSocket(host=os.getenv("CLAMD_HOST", "clamav"), port=3310),
    ):
        try:
            scanner = _make_scanner()
            result = scanner.instream(io.BytesIO(content))
            status, virus_name = result.get("stream", ("skipped", None))
            return (status.lower(), virus_name)
        except Exception:
            continue
    # ClamAV daemon unavailable — skip scan (dev/CI without ClamAV)
    return ("skipped", None)


# ==============================================================================
# SCHEMAS
# ==============================================================================

_VALID_CUSTOMER_TYPES = {"individual", "mechanic", "garage", "retailer", "fleet"}

class RegisterRequest(BaseModel):
    email: EmailStr
    phone: str = Field(..., max_length=20)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., max_length=100)
    customer_type: str = "individual"

    @validator("customer_type")
    def validate_customer_type(cls, v):
        if v not in _VALID_CUSTOMER_TYPES:
            raise ValueError(f"customer_type must be one of: {', '.join(sorted(_VALID_CUSTOMER_TYPES))}")
        return v

    @validator("phone")
    def validate_phone(cls, v):
        # Accept Israeli domestic (05XXXXXXXX) or international E.164 (+XXXXXXXXXXX)
        if v.startswith("+"):
            if len(v) < 8 or len(v) > 16 or not v[1:].isdigit():
                raise ValueError("Invalid international phone number")
        elif not v.startswith("05") or len(v) != 10 or not v.isdigit():
            raise ValueError("Invalid Israeli phone number (must start with 05, 10 digits)")
        return v

    @validator("password")
    def validate_password_strength(cls, v):
        min_len = int(os.getenv("PASSWORD_MIN_LENGTH", 8))
        if len(v) < min_len:
            raise ValueError(f"Password must be at least {min_len} characters long")
        # Require at least one digit and one letter
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter")
        return v

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    trust_device: bool = False

class Login2FARequest(BaseModel):
    user_id: str
    code: str = Field(..., max_length=10)
    trust_device: bool = False

class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., max_length=256)

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(..., max_length=256)
    new_password: str = Field(..., min_length=8, max_length=128)

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=8, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)

class UpdatePhoneRequest(BaseModel):
    new_phone: str = Field(..., max_length=20)
    verification_code: str = Field(..., max_length=10)

class ChatMessageRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field(..., max_length=2000)
    content_type: str = "text"

class PartsSearchRequest(BaseModel):
    query: str = Field(..., max_length=200)
    vehicle_id: Optional[str] = None
    category: Optional[str] = None
    limit: int = 20

class VehicleIdentifyRequest(BaseModel):
    license_plate: str = Field(..., max_length=15)

class OrderItemCreate(BaseModel):
    part_id: Optional[str] = None
    supplier_part_id: str
    quantity: int = 1

class OrderCreate(BaseModel):
    items: List[OrderItemCreate]
    shipping_address: Dict[str, str]

class OrderCancelRequest(BaseModel):
    reason: str = Field(..., max_length=500)

class ReturnRequest(BaseModel):
    order_id: str
    reason: str = Field(..., max_length=500)
    description: Optional[str] = Field(None, max_length=1000)

class NewsletterSubscribeRequest(BaseModel):
    email: EmailStr
    preferences: Optional[List[str]] = ["promotions"]

class CouponValidateRequest(BaseModel):
    code: str = Field(..., max_length=50)

class SupplierCreate(BaseModel):
    name: str
    country: str
    website: Optional[str] = None
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    priority: int = 0
    reliability_score: float = 5.0
    supports_express: bool = False
    express_carrier: Optional[str] = None
    express_base_cost_usd: Optional[float] = None


class SupplierUpdateBody(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None
    website: Optional[str] = None
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    priority: Optional[int] = None
    reliability_score: Optional[float] = None
    is_active: Optional[bool] = None
    supports_express: Optional[bool] = None
    express_carrier: Optional[str] = None
    express_base_cost_usd: Optional[float] = None


class CreateSocialPostRequest(BaseModel):
    content: str = Field(..., max_length=5000)
    platforms: List[str]
    schedule_time: Optional[datetime] = None


class UpdateSocialPostRequest(BaseModel):
    content: Optional[str] = Field(None, max_length=5000)
    platforms: Optional[List[str]] = None
    schedule_time: Optional[datetime] = None


# ==============================================================================
# 1. AUTH  /api/v1/auth  (15 endpoints)
# ==============================================================================

@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Register new user and send 2FA SMS"""
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:register:{ip}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    user = await register_user(data.email, data.phone, data.password, data.full_name, db)
    # Persist customer_type on the auto-created profile
    profile_res = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = profile_res.scalar_one_or_none()
    if profile:
        profile.customer_type = data.customer_type
    else:
        db.add(UserProfile(user_id=user.id, customer_type=data.customer_type))
    await db.commit()
    await create_2fa_code(str(user.id), user.phone, db)
    return {
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "customer_type": data.customer_type},
        "message": f"קוד אימות נשלח ל-{user.phone[-4:]}",
    }


@app.post("/api/v1/auth/login")
async def login(data: LoginRequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Login – returns tokens or triggers 2FA"""
    from BACKEND_AUTH_SECURITY import generate_device_fingerprint
    device_fp = generate_device_fingerprint(request)
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:login:{ip}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
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
async def verify_2fa(data: Login2FARequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Complete login with 2FA code"""
    from BACKEND_AUTH_SECURITY import generate_device_fingerprint
    device_fp = generate_device_fingerprint(request)
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:verify_2fa:{ip}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
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
async def refresh_token(data: RefreshTokenRequest, db: AsyncSession = Depends(get_pii_db)):
    """Refresh access token"""
    new_access, new_refresh = await refresh_access_token(data.refresh_token, db)
    return {"access_token": new_access, "refresh_token": new_refresh, "token_type": "bearer"}


@app.post("/api/v1/auth/verify-email")
async def verify_email(token: str, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Validate an email verification token (re-uses the PasswordReset table as
    a lightweight token store — a dedicated EmailVerification table can replace
    this when full email verification flow is implemented)."""
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:verify_email:{ip}', 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    from BACKEND_DATABASE_MODELS import PasswordReset
    from datetime import datetime
    if not token:
        raise HTTPException(status_code=400, detail="Token required")
    result = await db.execute(
        select(PasswordReset).where(
            and_(
                PasswordReset.token == token,
                PasswordReset.used_at.is_(None),
                PasswordReset.expires_at > datetime.utcnow(),
            )
        )
    )
    reset = result.scalar_one_or_none()
    if not reset:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    # Mark user as verified
    user_result = await db.execute(select(User).where(User.id == reset.user_id))
    user = user_result.scalar_one_or_none()
    if user:
        user.is_verified = True
    reset.used_at = datetime.utcnow()
    await db.commit()
    return {"message": "Email verified"}


@app.post("/api/v1/auth/verify-phone")
async def verify_phone(code: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    success = await verify_2fa_code(str(current_user.id), code, db)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid code")
    current_user.is_verified = True
    await db.commit()
    return {"message": "Phone verified"}


@app.post("/api/v1/auth/send-2fa")
async def send_2fa(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    await create_2fa_code(str(current_user.id), current_user.phone, db)
    return {"message": f"קוד נשלח ל-{current_user.phone[-4:]}"}


@app.post("/api/v1/auth/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    # Revoke the current session token so it can no longer be used
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if token:
        await logout_user(token, db)
    return {"message": "Logged out successfully"}


@app.get("/api/v1/auth/me")
async def get_me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def accept_terms(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def reset_password(data: PasswordResetRequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:reset_password:{ip}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    await create_password_reset_token(data.email, db)
    return {"message": "אם המייל קיים במערכת, נשלח קישור לאיפוס סיסמה"}


@app.post("/api/v1/auth/reset-password/confirm")
async def reset_password_confirm(data: PasswordResetConfirmRequest, db: AsyncSession = Depends(get_pii_db)):
    success = await use_password_reset_token(data.token, data.new_password, db)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    return {"message": "הסיסמה שונתה בהצלחה"}


@app.post("/api/v1/auth/change-password")
async def change_password_ep(data: ChangePasswordRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    await change_password(current_user, data.current_password, data.new_password, db)
    return {"message": "הסיסמה שונתה בהצלחה"}


@app.get("/api/v1/auth/trusted-devices")
async def get_trusted_devices(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def trust_device(device_fingerprint: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def delete_trusted_device(device_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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


@app.get("/api/v1/chat/conversations")
async def get_conversations(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
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
async def get_conversation(conversation_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": str(conv.id), "title": conv.title, "current_agent": conv.current_agent, "started_at": conv.started_at, "last_message_at": conv.last_message_at}


@app.get("/api/v1/chat/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, current_user: User = Depends(get_current_user), limit: int = 100, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")
    result = await db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).limit(limit))
    msgs = result.scalars().all()
    return {"messages": [{"id": str(m.id), "role": m.role, "agent_name": m.agent_name, "content": m.content, "content_type": m.content_type, "created_at": m.created_at} for m in msgs]}


@app.delete("/api/v1/chat/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.user_id == current_user.id)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()
    return {"message": "Conversation deleted"}


@app.post("/api/v1/chat/upload-image")
async def upload_image(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_pii_db), request: Request = None, redis=Depends(get_redis)):
    """Upload an image and immediately run GPT-4o Vision to identify the part."""
    import base64 as _b64
    from hf_client import hf_vision
    import json as _json

    if redis and request:
        await check_rate_limit(redis, f"upload_image:{current_user.id}", 10, 60)
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


@app.post("/api/v1/chat/upload-audio")
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
        await check_rate_limit(redis, f"upload_audio:{current_user.id}", 10, 60)
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


@app.post("/api/v1/chat/upload-video")
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


@app.websocket("/api/v1/chat/ws")
async def chat_websocket(websocket: WebSocket, token: Optional[str] = None, db: AsyncSession = Depends(get_pii_db)):
    """Authenticated WebSocket. Client must pass ?token=<access_token> as a query param."""
    from BACKEND_AUTH_SECURITY import decode_access_token
    from jose import JWTError
    # Validate token before accepting the connection
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


@app.post("/api/v1/chat/rate")
async def rate_agent(conversation_id: str, agent_name: str, rating: int, feedback: Optional[str] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    from BACKEND_DATABASE_MODELS import AgentRating
    db.add(AgentRating(conversation_id=conversation_id, user_id=current_user.id, agent_name=agent_name, rating=rating, feedback=feedback))
    await db.commit()
    return {"message": "Rating submitted"}


# ==============================================================================
# 3. PARTS  /api/v1/parts  (7 endpoints)
# ==============================================================================

@app.get("/api/v1/parts/search")
async def search_parts(
    query: str = Query(default="", max_length=200),
    vehicle_id: Optional[str] = None,
    category: Optional[str] = None,
    per_type: Optional[int] = None,        # override system_settings.search_results_per_type
    sort_by: str = "price_ils",            # cheapest first by default
    vehicle_manufacturer: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """
    Search the parts catalogue and return results grouped by part type.

    Response shape:
    {
      "original":    {"part": {...} | null, "suppliers": [...]},
      "oem":         {"part": {...} | null, "suppliers": [...]},
      "aftermarket": {"part": {...} | null, "suppliers": [...]},
      "results_per_type": <int>,
      "query": <str>
    }

    Suppliers are sorted price_ils ASC (cheapest first).
    The `per_type` param caps how many supplier offers are returned per type
    (default: system_settings.search_results_per_type → 4).
    Text search is powered by Meilisearch when available; falls back to ILIKE.
    """
    ip = request.client.host if (request and request.client) else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:search:{ip}', 30, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    # ── Resolve results_per_type ─────────────────────────────────────────────
    if per_type is None:
        try:
            ss_res = await db.execute(
                text("SELECT value FROM system_settings WHERE key = 'search_results_per_type' LIMIT 1")
            )
            row_ss = ss_res.fetchone()
            per_type = int(row_ss[0]) if row_ss else 4
        except Exception:
            per_type = 4

    # ── Meilisearch text lookup (optional) ───────────────────────────────────
    # meili_ids: List[str]  → ranked UUIDs from Meilisearch (use unnest JOIN)
    # meili_ids: None       → Meilisearch unavailable → fall back to ILIKE
    # meili_ids: []         → Meilisearch returned 0 hits → short-circuit empty
    meili_ids: Optional[List[str]] = None
    _meili_url = os.getenv("MEILI_URL", "")
    if query and _meili_url:
        try:
            async with httpx.AsyncClient(timeout=2.0) as _mc:
                _resp = await _mc.post(
                    f"{_meili_url}/indexes/parts/search",
                    headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                    json={"q": query, "limit": 200, "attributesToRetrieve": ["id"]},
                )
                _resp.raise_for_status()
                meili_ids = [h["id"] for h in _resp.json().get("hits", [])]
        except Exception:
            meili_ids = None  # keep ILIKE fallback

    # ── Short-circuit when Meilisearch found zero hits ────────────────────────
    if meili_ids is not None and len(meili_ids) == 0:
        return {
            "original":         {"part": None, "suppliers": []},
            "oem":              {"part": None, "suppliers": []},
            "aftermarket":      {"part": None, "suppliers": []},
            "results_per_type": per_type,
            "query":            query,
        }

    # ── pgvector: embed the query and find nearest neighbours ────────────────
    # Runs only when Meilisearch returned results (meili_ids is a non-empty list).
    # vec_score: {id_str → cosine_similarity}  (empty dict if unavailable)
    _route_vec_score: Dict[str, float] = {}
    if meili_ids and query:
        from hf_client import hf_embed
        try:
            _qvec = await hf_embed(query, timeout=3.0)

            if _qvec:
                _vrows = (await db.execute(
                    text("""
                        SELECT id::text,
                               1 - (embedding <=> CAST(:qvec AS vector)) AS sim
                        FROM parts_catalog
                        WHERE is_active = TRUE
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> CAST(:qvec AS vector)
                        LIMIT 50
                    """),
                    {"qvec": str(_qvec)},
                )).fetchall()
                _route_vec_score = {r[0]: float(r[1]) for r in _vrows}
        except Exception:
            _route_vec_score = {}  # degrade silently to Meilisearch-only

    # ── Hybrid re-rank: 0.6 × meili_score + 0.4 × vec_score ─────────────────
    if _route_vec_score:
        _meili_scores = {uid: 1.0 / (i + 1) for i, uid in enumerate(meili_ids)}
        _all_ids = list(dict.fromkeys(list(_meili_scores) + list(_route_vec_score)))
        _combined = {
            uid: 0.6 * _meili_scores.get(uid, 0.0) + 0.4 * _route_vec_score.get(uid, 0.0)
            for uid in _all_ids
        }
        meili_ids = sorted(_combined, key=_combined.__getitem__, reverse=True)

    # ── Build shared WHERE conditions ────────────────────────────────────────
    conditions: List[str] = ["pc.is_active = TRUE"]
    params: Dict[str, Any] = {}

    # Text filter: if Meilisearch is live use id-array join (no ILIKE needed);
    # if it's unavailable fall back to the original ILIKE clause.
    if query and meili_ids is None:
        conditions.append(
            "(pc.name ILIKE :q OR pc.sku ILIKE :q OR pc.manufacturer ILIKE :q "
            "OR pc.category ILIKE :q OR pc.oem_number ILIKE :q)"
        )
        params["q"]       = f"%{query}%"
        params["q_exact"] = query
        params["q_start"] = f"{query}%"

    if category:
        conditions.append("pc.category ILIKE :cat")
        params["cat"] = f"%{category}%"

    if vehicle_manufacturer:
        # Normalize to catalog brand name: vehicle.manufacturer may be Hebrew
        # (e.g. "סיטרואן ספרד") while parts_catalog stores English ("Citroen").
        # Look up car_brands by name, name_he, or aliases to find all variants.
        try:
            brand_row = (await db.execute(text("""
                SELECT name, name_he, aliases FROM car_brands
                WHERE name ILIKE :vmfr_lookup
                   OR name_he ILIKE :vmfr_lookup
                   OR :vmfr_lookup ILIKE CONCAT('%', name_he, '%')
                   OR EXISTS (
                       SELECT 1 FROM unnest(aliases) a
                       WHERE :vmfr_lookup ILIKE CONCAT('%', a, '%')
                          OR a ILIKE :vmfr_lookup
                   )
                LIMIT 1
            """), {"vmfr_lookup": vehicle_manufacturer})).fetchone()
        except Exception:
            brand_row = None

        if brand_row:
            variants = list({brand_row[0], brand_row[1], *(brand_row[2] or [])})
            vmfr_clauses = []
            for idx, v in enumerate(variants):
                if v:
                    k = f"vmfr_{idx}"
                    vmfr_clauses.append(f"pc.manufacturer ILIKE :{k}")
                    params[k] = f"%{v}%"
            if vmfr_clauses:
                conditions.append(f"({' OR '.join(vmfr_clauses)})")
        else:
            conditions.append("pc.manufacturer ILIKE :vmfr")
            params["vmfr"] = f"%{vehicle_manufacturer}%"

    if vehicle_id:
        conditions.append(
            "(pc.compatible_vehicles::text ILIKE :vid "
            "OR EXISTS (SELECT 1 FROM part_vehicle_fitment pvf "
            "           WHERE pvf.part_id = pc.id AND pvf.vehicle_id = :vid_exact))"
        )
        params["vid"] = f"%{vehicle_id}%"
        params["vid_exact"] = vehicle_id

    where_sql = " AND ".join(conditions)

    # ── ILIKE relevance score (only used when Meilisearch is unavailable) ─────
    if query and meili_ids is None:
        relevance_sql = """
                CASE
                    WHEN pc.name ILIKE :q_exact THEN 4
                    WHEN pc.name ILIKE :q_start THEN 3
                    WHEN LENGTH(pc.name) - LENGTH(:q_exact) <= 5 THEN 2
                    ELSE 1
                END DESC,"""
        score_col = """,
                    CASE
                        WHEN pc.name ILIKE :q_exact THEN 4
                        WHEN pc.name ILIKE :q_start THEN 3
                        WHEN LENGTH(pc.name) - LENGTH(:q_exact) <= 5 THEN 2
                        ELSE 1
                    END AS match_score"""
    else:
        relevance_sql = ""
        score_col = ""

    # ── Helper: fetch one part per type + its supplier list ──────────────────
    async def _fetch_type(part_type_values: list) -> Dict[str, Any]:
        type_params = {**params, "pt": part_type_values, "lim": per_type}
        _unsafe_sql_tokens = (";", "--", "/*", "*/")
        if any(tok in where_sql for tok in _unsafe_sql_tokens):
            raise HTTPException(status_code=400, detail="unsafe_query_rejected")

        if meili_ids:
            # ── Meilisearch path: rank-preserving unnest JOIN ─────────────────
            # UUIDs come from our own index — hex+dash only, no SQL injection risk.
            uuid_array = "{" + ",".join(meili_ids) + "}"
            part_row = (await db.execute(
                text(f"""
                    SELECT
                        pc.id, pc.sku, pc.name, pc.name_he, pc.manufacturer,
                        pc.category, pc.part_type, pc.base_price,
                        pc.min_price_ils, pc.max_price_ils, pc.description,
                        pc.oem_number, pc.barcode, pc.weight_kg,
                        pc.is_safety_critical, pc.part_condition,
                        pc.created_at, pc.updated_at
                    FROM parts_catalog pc
                    JOIN (
                        SELECT t.id::uuid AS ranked_id, t.pos
                        FROM unnest(:uuid_arr::text[]) WITH ORDINALITY AS t(id, pos)
                    ) ranked ON ranked.ranked_id = pc.id
                    WHERE {where_sql} AND pc.part_type = ANY(:pt)
                    ORDER BY ranked.pos ASC,
                    (
                        SELECT COUNT(*) FROM supplier_parts sp
                        WHERE sp.part_id = pc.id AND sp.is_available = TRUE
                    ) DESC
                    LIMIT 1
                """),
                {**type_params, "uuid_arr": uuid_array},
            )).fetchone()
        else:
            # ── ILIKE fallback path ───────────────────────────────────────────
            if relevance_sql and any(tok in relevance_sql for tok in _unsafe_sql_tokens):
                raise HTTPException(status_code=400, detail="unsafe_query_rejected")
            part_row = (await db.execute(
                text(f"""
                    SELECT
                        pc.id, pc.sku, pc.name, pc.name_he, pc.manufacturer,
                        pc.category, pc.part_type, pc.base_price,
                        pc.min_price_ils, pc.max_price_ils, pc.description,
                        pc.oem_number, pc.barcode, pc.weight_kg,
                        pc.is_safety_critical, pc.part_condition,
                        pc.created_at, pc.updated_at{score_col}
                    FROM parts_catalog pc
                    WHERE {where_sql} AND pc.part_type = ANY(:pt)
                    ORDER BY {relevance_sql}
                    (
                        SELECT COUNT(*) FROM supplier_parts sp
                        WHERE sp.part_id = pc.id AND sp.is_available = TRUE
                    ) DESC,
                    pc.base_price ASC NULLS LAST
                    LIMIT 1
                """),
                type_params,
            )).fetchone()

            # Reject loose ILIKE-only matches (score == 1)
            if query and score_col and part_row is not None:
                if part_row[-1] == 1:
                    return {"part": None, "suppliers": []}

        if not part_row:
            return {"part": None, "suppliers": []}

        part_id_str = str(part_row[0])

        # All available supplier offers for this part, sorted cheapest first
        sup_rows = (await db.execute(
            text("""
                SELECT
                    sp.id            AS sp_id,
                    s.name           AS supplier_name,
                    s.country        AS supplier_country,
                    sp.supplier_sku,
                    sp.price_usd,
                    sp.price_ils,
                    sp.shipping_cost_ils,
                    sp.availability,
                    sp.warranty_months,
                    sp.estimated_delivery_days,
                    sp.stock_quantity,
                    sp.supplier_url,
                    sp.express_available,
                    sp.express_price_ils,
                    sp.express_delivery_days,
                    sp.express_cutoff_time,
                    sp.last_checked_at
                FROM supplier_parts sp
                JOIN suppliers s ON s.id = sp.supplier_id
                WHERE sp.part_id = :part_id AND sp.is_available = TRUE
                ORDER BY COALESCE(sp.price_ils, sp.price_usd * 3.72) ASC
                LIMIT :lim
            """),
            {"part_id": part_id_str, "lim": per_type},
        )).fetchall()

        part_dict = {
            "id":               str(part_row[0]),
            "sku":              part_row[1],
            "name":             part_row[2],
            "name_he":          part_row[3],
            "manufacturer":     part_row[4],
            "category":         part_row[5],
            "part_type":        part_row[6],
            "base_price":       float(part_row[7]) if part_row[7] else None,
            "min_price_ils":    float(part_row[8]) if part_row[8] else None,
            "max_price_ils":    float(part_row[9]) if part_row[9] else None,
            "description":      part_row[10],
            "oem_number":       part_row[11],
            "barcode":          part_row[12],
            "weight_kg":        float(part_row[13]) if part_row[13] else None,
            "is_safety_critical": part_row[14],
            "part_condition":   part_row[15],
            "created_at":       part_row[16].isoformat() if part_row[16] else None,
            "updated_at":       part_row[17].isoformat() if part_row[17] else None,
        }

        suppliers_list = []
        for sp in sup_rows:
            price_ils = float(sp[5]) if sp[5] else (float(sp[4]) * 3.72 if sp[4] else None)
            suppliers_list.append({
                "supplier_part_id":      str(sp[0]),
                "supplier_name":         _mask_supplier(sp[1]),
                "supplier_country":      "",
                "supplier_sku":          sp[3],
                "price_usd":             float(sp[4]) if sp[4] else None,
                "price_ils":             round(price_ils, 2) if price_ils else None,
                "shipping_cost_ils":     float(sp[6]) if sp[6] else None,
                "availability":          sp[7],
                "warranty_months":       sp[8],
                "estimated_delivery_days": sp[9],
                "stock_quantity":        sp[10],
                "supplier_url":          sp[11],
                "express_available":     sp[12],
                "express_price_ils":     float(sp[13]) if sp[13] else None,
                "express_delivery_days": sp[14],
                "express_cutoff_time":   sp[15],
                "last_checked_at":       sp[16].isoformat() if sp[16] else None,
            })

        return {"part": part_dict, "suppliers": suppliers_list}

    # ── Run all 3 type queries (concurrently) ────────────────────────────────
    original_res, oem_res, aftermarket_res = await asyncio.gather(
        _fetch_type(["Original"]),                         # מקורי / OEM original
        _fetch_type(["OEM"]),                              # OEM equivalent
        _fetch_type(["Aftermarket", "Refurbished"]),       # חליפי / aftermarket
    )

    return {
        "original":         original_res,
        "oem":              oem_res,
        "aftermarket":      aftermarket_res,
        "results_per_type": per_type,
        "query":            query,
    }


@app.post("/api/v1/parts/search-by-vehicle")
async def search_parts_by_vehicle(vehicle_id: str, category: Optional[str] = None, db: AsyncSession = Depends(get_db), pii_db: AsyncSession = Depends(get_pii_db), request: Request = None, redis=Depends(get_redis)):
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f'rate:search_by_vehicle:{ip}', 30, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    result = await pii_db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
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
async def autocomplete_parts(q: str = "", limit: int = 8, db: AsyncSession = Depends(get_db), request: Request = None, redis=Depends(get_redis)):
    """Return distinct part names containing the query string (uses GIN trigram index)."""
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f'rate:autocomplete:{ip}', 30, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
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


@app.get("/api/v1/parts/manufacturers")
async def get_manufacturers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog.manufacturer).distinct().where(PartsCatalog.is_active == True))
    return {"manufacturers": [m for m in result.scalars().all() if m]}


@app.get("/api/v1/parts/models")
async def get_models(manufacturer: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """Return distinct car models extracted from compatible_vehicles JSON, optionally filtered by manufacturer."""
    import re as _re
    sql = text("""
        SELECT DISTINCT elem->>'model_year' AS model_year
        FROM parts_catalog,
             jsonb_array_elements(compatible_vehicles) AS elem
        WHERE compatible_vehicles IS NOT NULL
          AND compatible_vehicles::text LIKE '%model_year%'
          AND (:mfr_like IS NULL OR compatible_vehicles::text ILIKE :mfr_like)
          AND elem->>'model_year' IS NOT NULL
        ORDER BY model_year
    """)
    result = await db.execute(sql, {"mfr_like": f"%{manufacturer}%" if manufacturer else None})
    raw = [row[0] for row in result.fetchall() if row[0]]
    # Two-pass year stripping so year is always separated into the year dropdown:
    # Pass 1 — strip 4-digit era year (19xx/20xx) AND everything that follows,
    #   using \s* so it catches both "CAMARO 2021US" and "CAMARO2019 US":
    #   "SONIC 2014 1.4TURBO" → "SONIC"
    #   "SAVANA 2017 NEW"     → "SAVANA"
    #   "CAMARO 2021US"       → "CAMARO"
    #   "CAMARO2019 US"       → "CAMARO"
    _era_year_re = _re.compile(r'\s*(?:19|20)\d{2}(?=[^\d]|$).*$')
    # Pass 2 — strip remaining trailing 2-digit years or year-ranges:
    #   "CAVALIER 99" → "CAVALIER"
    #   "CAVALIER 96-67" → "CAVALIER"
    _trail_num_re = _re.compile(r'\s+\d[\d\-/\.]*\s*$')
    # Deduplicate case-insensitively: keep the shortest/cleanest variant per normalised key
    models_map: dict[str, str] = {}  # normalised_key → best display value
    for my in raw:
        model = _era_year_re.sub('', my).strip()   # pass 1
        model = _trail_num_re.sub('', model).strip()  # pass 2
        # clean extra spaces
        model = _re.sub(r'\s{2,}', ' ', model).strip()
        if not model or model.replace('-', '').replace(' ', '').isdigit():
            continue
        key = model.upper()
        # Among duplicates keep the shorter, cleaner form
        existing = models_map.get(key)
        if existing is None or len(model) < len(existing):
            models_map[key] = model
    models = sorted(models_map.values())
    return {"models": models, "total": len(models)}


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
    request: Request = None,
    redis=Depends(get_redis),
):
    """Decode a VIN via NHTSA free API, cache in vehicles table, and search parts."""
    if redis and request:
        _vin_ip = request.client.host if request.client else "anon"
        await check_rate_limit(redis, f"search_by_vin:{_vin_ip}", 10, 60)
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
            year_int     = int(year_str) if year_str and year_str.isdigit() else 0
            engine_type  = f"{fuel_type or 'Unknown'} {engine_cc}cc" if engine_cc else fuel_type
            vehicle_info = {
                "vin": vin_clean,
                "manufacturer": manufacturer,
                "model": model,
                "year": year_int,
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

    # ── Cache VIN in vehicles table (catalog DB) ─────────────────────────────
    cached_vehicle_id: Optional[str] = None
    try:
        vin_row = (await db.execute(
            select(Vehicle).where(Vehicle.vin == vin_clean)
        )).scalar_one_or_none()
        if vin_row:
            cached_vehicle_id = str(vin_row.id)
        else:
            new_vehicle = Vehicle(
                manufacturer = vehicle_info["manufacturer"],
                model        = vehicle_info["model"],
                year         = vehicle_info["year"] or 0,
                vin          = vin_clean,
                engine_type  = engine_type,
                fuel_type    = vehicle_info["fuel_type"],
                transmission = vehicle_info["transmission"],
            )
            db.add(new_vehicle)
            await db.flush()
            cached_vehicle_id = str(new_vehicle.id)
            await db.commit()
        vehicle_info["id"] = cached_vehicle_id
    except Exception as e:
        print(f"[VIN] vehicle cache error (non-fatal): {e}")
        await db.rollback()

    # ── Search parts ──────────────────────────────────────────────────────────
    agent = get_agent("parts_finder_agent")
    search_q = (part_query or "").strip()
    parts_list = await agent.search_parts_in_db(
        search_q,
        cached_vehicle_id,
        category,
        db,
        limit=limit,
        offset=offset,
        vehicle_manufacturer=vehicle_info["manufacturer"],
    )

    return {
        "vehicle": vehicle_info,
        "parts": parts_list,
        # len(parts_list) reflects actual search results (Meilisearch / ILIKE)
        "total": len(parts_list),
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/v1/parts/{part_id}")
async def get_part(part_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartsCatalog).where(PartsCatalog.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return {"id": str(part.id), "name": part.name, "manufacturer": part.manufacturer, "category": part.category, "part_type": part.part_type, "description": part.description, "specifications": part.specifications}


@app.post("/api/v1/parts/compare")
async def compare_parts(part_id: str, db: AsyncSession = Depends(get_db), request: Request = None, redis=Depends(get_redis)):
    """Return all supplier options for a part (in_stock first, then on_order fallback)."""
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f'rate:parts_compare:{ip}', 30, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
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
            "supplier_name": _mask_supplier(supplier.name),
            "supplier_country": "",
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
    vehicle_make:  Optional[str] = Form(None),
    vehicle_model: Optional[str] = Form(None),
    vehicle_year:  Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """Identify a car part from a photo using GPT-4o Vision.

    Flow:
    1. Hash the image → check part_diagram_cache (DB) for an instant answer.
    2. If not cached: pre-fetch catalog part names for the vehicle and build a
       context-rich prompt (acts as a digital parts diagram).
    3. Call GPT-4o Vision with vehicle + catalog context.
    4. Save the result to part_diagram_cache so future identical searches skip GPT.
    """
    import base64
    import hashlib
    import json as _json
    from hf_client import hf_vision
    from BACKEND_DATABASE_MODELS import PartDiagramCache

    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f'rate:identify_part_image:{ip}', 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    # ── Read & hash image ────────────────────────────────────────────────────
    img_bytes = await file.read()
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 10 MB)")
    image_hash = hashlib.sha256(img_bytes).hexdigest()
    b64  = base64.b64encode(img_bytes).decode()
    mime = file.content_type or "image/jpeg"

    identified_name = ""
    identified_en   = ""
    confidence      = 0.0
    possible_names: list = []
    cache_hit       = False

    # ── 1. Check diagram cache ───────────────────────────────────────────────
    try:
        cache_row = (await db.execute(
            text("""
                SELECT part_name_he, part_name_en, possible_names, confidence
                FROM part_diagram_cache
                WHERE image_hash = :h
                  AND (vehicle_make ILIKE :mk OR (:mk IS NULL AND vehicle_make IS NULL))
                ORDER BY times_seen DESC
                LIMIT 1
            """),
            {"h": image_hash, "mk": vehicle_make},
        )).fetchone()
        if cache_row:
            identified_name = cache_row[0]
            identified_en   = cache_row[1] or ""
            possible_names  = cache_row[2] or []
            confidence      = float(cache_row[3] or 0)
            cache_hit       = True
            # Increment times_seen counter
            await db.execute(
                text("""
                    UPDATE part_diagram_cache SET times_seen = times_seen + 1, updated_at = NOW()
                    WHERE image_hash = :h AND (vehicle_make ILIKE :mk OR (:mk IS NULL AND vehicle_make IS NULL))
                """),
                {"h": image_hash, "mk": vehicle_make},
            )
            await db.commit()
    except Exception as e:
        print(f"[Vision] Cache lookup error: {e}")

    # ── 2 & 3. GPT call if no cache hit ─────────────────────────────────────
    if not cache_hit:
        # Pre-fetch catalog names for this vehicle → "digital diagram"
        catalog_hint   = ""
        vehicle_context = ""
        if vehicle_make:
            try:
                brand_row = (await db.execute(text("""
                    SELECT name, aliases FROM car_brands
                    WHERE name ILIKE :m OR name_he ILIKE :m
                       OR :m ILIKE CONCAT('%', name_he, '%')
                       OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE a ILIKE :m OR :m ILIKE CONCAT('%',a,'%'))
                    LIMIT 1
                """), {"m": vehicle_make})).fetchone()
                mfr_variants = list({vehicle_make, *((brand_row[1] or []) if brand_row else [])})
                if brand_row and brand_row[0]:
                    mfr_variants.append(brand_row[0])
                mfr_filters = [PartsCatalog.manufacturer.ilike(f"%{v}%") for v in mfr_variants if v]
                catalog_rows = []
                if mfr_filters:
                    catalog_rows = (await db.execute(
                        select(PartsCatalog.name)
                        .distinct()
                        .where(PartsCatalog.is_active == True, or_(*mfr_filters))
                        .order_by(PartsCatalog.name)
                        .limit(120)
                    )).fetchall()
                if catalog_rows:
                    names_csv = ", ".join(r[0] for r in catalog_rows)
                    label = vehicle_make + (f" {vehicle_model}" if vehicle_model else "") + (f" {vehicle_year}" if vehicle_year else "")
                    catalog_hint = (
                        f"Our catalog contains these Hebrew part names for {label}: [{names_csv}]. "
                        "Select the BEST matching name from this list as part_name_he if it visually matches the image. "
                        "If nothing matches, use your own SHORT Hebrew name (1–3 words)."
                    )
            except Exception as e:
                print(f"[Vision] Catalog hint error: {e}")

            vehicle_context = (
                f"The vehicle in question is a {vehicle_make}"
                + (f" {vehicle_model}" if vehicle_model else "")
                + (f" (year {vehicle_year})" if vehicle_year else "")
                + ". "
            )

        if os.getenv("HF_TOKEN", ""):
            try:
                prompt = (
                    "You are an expert automotive parts identifier for an Israeli auto parts store. "
                    + vehicle_context
                    + catalog_hint
                    + " Look at this image and identify the car part shown. "
                    "Think step by step: 1) What vehicle system does this part belong to? "
                    "2) What is the exact part? "
                    "3) Does it match a name from the catalog list above? "
                    "Respond ONLY with a valid JSON object, no markdown: "
                    '{"part_name_he": "<best Hebrew name — prefer exact catalog match>", '
                    '"part_name_en": "<English name>", '
                    '"possible_names": ["<alt 1>","<alt 2>","<alt 3>","<alt 4>","<alt 5>","<alt 6>"], '
                    '"confidence": <0.0-1.0>, '
                    '"description": "<brief Hebrew description>"}. '
                    'IMPORTANT: part_name_he and ALL possible_names must be SHORT Hebrew terms '
                    '(1-3 words) as written in Israeli auto parts price lists. '
                    'Do NOT use English in possible_names.'
                )
                raw = await hf_vision(b64, prompt, mime=(file.content_type or "image/jpeg"))
                raw = raw.strip().strip("`").removeprefix("json").strip()
                parsed = _json.loads(raw)
                identified_name = parsed.get("part_name_he") or parsed.get("part_name_en", "")
                identified_en   = parsed.get("part_name_en", "")
                confidence      = float(parsed.get("confidence", 0.0))
                possible_names  = parsed.get("possible_names", [])
            except Exception as e:
                print(f"[Vision] HF Vision error: {e}")

        # ── 4. Persist to diagram cache ──────────────────────────────────────
        if identified_name:
            try:
                await db.execute(
                    text("""
                        INSERT INTO part_diagram_cache
                            (id, image_hash, vehicle_make, vehicle_model, vehicle_year,
                             part_name_he, part_name_en, possible_names, confidence,
                             times_seen, created_at, updated_at)
                        VALUES
                            (gen_random_uuid(), :h, :mk, :mo, :yr,
                             :phe, :pen, :pn, :conf,
                             1, NOW(), NOW())
                        ON CONFLICT (image_hash, vehicle_make, vehicle_model)
                        DO UPDATE SET
                            part_name_he   = EXCLUDED.part_name_he,
                            part_name_en   = EXCLUDED.part_name_en,
                            possible_names = EXCLUDED.possible_names,
                            confidence     = EXCLUDED.confidence,
                            times_seen     = part_diagram_cache.times_seen + 1,
                            updated_at     = NOW()
                    """),
                    {
                        "h":    image_hash,
                        "mk":   vehicle_make,
                        "mo":   vehicle_model,
                        "yr":   vehicle_year,
                        "phe":  identified_name,
                        "pen":  identified_en,
                        "pn":   possible_names,
                        "conf": confidence,
                    },
                )
                await db.commit()
            except Exception as e:
                print(f"[Vision] Cache save error: {e}")

    # Search the DB with the identified Hebrew name (most accurate match)
    parts_results = []
    total = 0
    search_term = identified_name or identified_en
    if search_term:
        agent = get_agent("parts_finder_agent")
        parts_results = await agent.search_parts_in_db(search_term, None, None, db, limit=20, offset=0)
        from sqlalchemy import func
        from BACKEND_DATABASE_MODELS import PartsCatalog
        count_stmt = select(func.count()).select_from(PartsCatalog).where(
            PartsCatalog.is_active == True,
            PartsCatalog.name.ilike(f"%{search_term}%"),
        )
        total = (await db.execute(count_stmt)).scalar_one()

    return {
        "identified_part":    identified_name,
        "identified_part_en": identified_en,
        "possible_names":     possible_names,
        "confidence":         confidence,
        "cache_hit":          cache_hit,
        "parts":              parts_results,
        "total":              total,
    }


# search-by-vin endpoint is defined above {part_id} to ensure correct route matching


# ==============================================================================
# 4. VEHICLES  /api/v1/vehicles  (8 endpoints)
# ==============================================================================

@app.post("/api/v1/vehicles/identify")
async def identify_vehicle(data: VehicleIdentifyRequest, db: AsyncSession = Depends(get_pii_db), request: Request = None, redis=Depends(get_redis)):
    if redis and request:
        _iv_ip = request.client.host if request.client else "anon"
        await check_rate_limit(redis, f"identify_vehicle:{_iv_ip}", 10, 60)
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
async def identify_vehicle_from_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """Extract license plate from image via HF vision OCR, then look up vehicle via data.gov.il"""
    # Rate limit
    if redis and request:
        ip = request.client.host if request.client else 'unknown'
        allowed = await check_rate_limit(redis, f'rate:identify_img:{ip}', 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    # Validate file
    img_bytes = await file.read()
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='Image too large (max 10 MB)')
    _ALLOWED = {'image/jpeg', 'image/png', 'image/webp'}
    if (file.content_type or '').split(';')[0].strip() not in _ALLOWED:
        raise HTTPException(status_code=415, detail='Unsupported image type')

    # OCR via Hugging Face Vision
    if not os.getenv("HF_TOKEN", ""):
        raise HTTPException(status_code=503, detail='ocr_service_unavailable')

    import base64 as _b64
    from hf_client import hf_vision
    b64 = _b64.b64encode(img_bytes).decode()
    try:
        plate_raw = await hf_vision(
            b64,
            'Extract the Israeli license plate number from this image. Return ONLY the digits and dashes, for example: 123-45-678. If no plate is visible return empty string.',
            mime=(file.content_type or "image/jpeg"),
        )
        plate_raw = plate_raw.strip()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f'ocr_failed: {str(e)[:100]}')

    # Clean plate number — keep only digits and dashes
    import re
    plate = re.sub(r'[^0-9\-]', '', plate_raw).strip('-')
    if not plate or len(plate) < 5:
        raise HTTPException(status_code=422, detail='no_plate_detected')

    # Reuse NIR agent's identify_vehicle (data.gov.il + DB cache)
    agent = get_agent('parts_finder_agent')
    try:
        result = await agent.identify_vehicle(plate, db)
        result['plate_extracted'] = plate
        result['ocr_raw'] = plate_raw
        return result
    except Exception as e:
        raise HTTPException(status_code=404, detail=f'vehicle_not_found: {str(e)[:100]}')


@app.get("/api/v1/vehicles/my-vehicles")
async def get_my_vehicles(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def add_my_vehicle(license_plate: str = Form(...), nickname: Optional[str] = Form(None), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def update_my_vehicle(vehicle_id: str, nickname: Optional[str] = None, is_primary: Optional[bool] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def delete_my_vehicle(vehicle_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    from BACKEND_DATABASE_MODELS import UserVehicle
    result = await db.execute(select(UserVehicle).where(and_(UserVehicle.vehicle_id == vehicle_id, UserVehicle.user_id == current_user.id)))
    uv = result.scalar_one_or_none()
    if not uv:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    await db.delete(uv)
    await db.commit()
    return {"message": "Vehicle removed"}


@app.post("/api/v1/vehicles/my-vehicles/set-primary")
async def set_primary_vehicle(vehicle_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def create_order(data: OrderCreate, current_user: User = Depends(get_current_verified_user), cat_db: AsyncSession = Depends(get_db), db: AsyncSession = Depends(get_pii_db)):
    from BACKEND_DATABASE_MODELS import SupplierPart
    from BACKEND_DATABASE_MODELS import Supplier as SupplierModel
    from BACKEND_AI_AGENTS import get_supplier_shipping as _get_ship2
    subtotal = 0.0
    items_data = []
    # USD_TO_ILS is imported from BACKEND_DATABASE_MODELS (single source of truth)
    # Track unique suppliers in this order → charge delivery fee once per supplier origin
    supplier_delivery_fees: dict[str, float] = {}  # supplier_id -> delivery_fee

    for item in data.items:
        res = await cat_db.execute(
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
        vat = round(unit_price * 0.18, 2)
        subtotal += unit_price * item.quantity
        items_data.append({
            "part_id": item.part_id or str(part.id),
            "supplier_part_id": item.supplier_part_id,
            "quantity": item.quantity,
            "unit_price": unit_price,
            "vat": vat,
            "part": part,
            "sp": sp,
            "supplier_name": _mask_supplier(supplier_rec.name),
        })

    vat_total = round(subtotal * 0.18, 2)
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
async def get_orders(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Order).where(Order.user_id == current_user.id).order_by(Order.created_at.desc()).limit(limit))
    orders = result.scalars().all()
    return {"orders": [{"id": str(o.id), "order_number": o.order_number, "status": o.status, "total": float(o.total_amount), "created_at": o.created_at, "tracking_number": o.tracking_number, "tracking_url": o.tracking_url, "estimated_delivery": o.estimated_delivery} for o in orders]}


@app.get("/api/v1/orders/{order_id}")
async def get_order(order_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def track_order(order_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"order_number": order.order_number, "status": order.status, "tracking_number": order.tracking_number, "tracking_url": order.tracking_url, "estimated_delivery": order.estimated_delivery}


@app.put("/api/v1/orders/{order_id}/cancel")
async def cancel_order(order_id: str, data: OrderCancelRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
                    session_obj = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: stripe_sdk.checkout.Session.retrieve(payment.payment_intent_id)
                    )
                    pi_id = session_obj.payment_intent
                    if pi_id:
                        stripe_refund = await asyncio.get_running_loop().run_in_executor(
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

        _cancel_title = "ביטול והחזר כספי" + (" ✅" if refund_id else " 🔄")
        _cancel_msg = (
            f"הזמנה {order.order_number} בוטלה. "
            + (f"החזר כספי של ₪{refund_amount:.2f} נשלח לכרטיס האשראי שלך." if refund_id
               else "בקשת ההחזר הכספי בטיפול.")
        )
        db.add(Notification(
            user_id=current_user.id,
            title=_cancel_title,
            message=_cancel_msg,
            type="refund_initiated",
        ))
        asyncio.create_task(_guarded_task(publish_notification(str(current_user.id), {"type": "refund_initiated", "title": _cancel_title, "message": _cancel_msg})))

    await db.commit()
    return {
        "message": "Order cancelled",
        "refund_initiated": was_paid,
        "refund_id": refund_id,
        "refund_amount": refund_amount,
    }


@app.post("/api/v1/orders/{order_id}/return")
async def create_order_return(
    order_id: str,
    data: ReturnRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return_number = f"RET-2026-{str(uuid.uuid4())[:8].upper()}"
    ret = Return(return_number=return_number, order_id=order.id, user_id=current_user.id, reason=data.reason, description=data.description, original_amount=order.total_amount, status="pending")
    db.add(ret)
    await db.commit()
    await db.refresh(ret)
    return {"return_id": str(ret.id), "return_number": ret.return_number, "status": "pending"}


@app.delete("/api/v1/orders/{order_id}")
async def delete_order(order_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ["pending_payment", "cancelled"]:
        raise HTTPException(status_code=400, detail="ניתן למחוק רק הזמנות שבוטלו או שממתינות לתשלום")
    # Delete child records that have non-nullable FKs first
    # Returns must be deleted before the Order (no CASCADE defined)
    ret_res = await db.execute(select(Return).where(Return.order_id == order.id))
    for ret in ret_res.scalars().all():
        await db.delete(ret)
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
    inline: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
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
    INVOICE_ALLOWED = {"paid", "processing", "supplier_ordered", "confirmed", "shipped", "delivered", "refunded"}
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
    disposition = f'inline; filename="{filename}"' if inline else f'attachment; filename="{filename}"'
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(pdf_bytes)),
            "X-Frame-Options": "SAMEORIGIN",
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
    db: AsyncSession = Depends(get_pii_db),
    redis=Depends(get_redis),
):
    """Create a Stripe Checkout Session (or simulate payment if Stripe not configured)."""
    import stripe as stripe_sdk

    if redis:
        allowed = await check_rate_limit(redis, f'rate:create_checkout:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_configured = bool(stripe_key and not stripe_key.startswith("sk_test_xxxxx"))

    # Load order
    result = await db.execute(
        select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ("pending_payment", "confirmed"):
        raise HTTPException(status_code=400, detail=f"Order is already {order.status}")

    # ── Live price validation ──────────────────────────────────────────────
    from decimal import Decimal
    _rate = USD_TO_ILS
    try:
        async with async_session_factory() as _cat:
            _ss = (await _cat.execute(
                text("SELECT value FROM system_settings WHERE key = 'ils_per_usd' LIMIT 1")
            )).fetchone()
            if _ss:
                _rate = float(_ss[0])
    except Exception:
        pass

    _items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    _order_items = _items_res.scalars().all()
    _price_changed = False
    _max_shipping = 0.0
    _new_items_total = Decimal("0")

    async with async_session_factory() as _cat:
        for _item in _order_items:
            if not _item.part_id:
                _new_items_total += Decimal(str(float(_item.total_price)))
                continue
            _sp_row = (await _cat.execute(
                text("""
                    SELECT
                        COALESCE(price_ils, price_usd * :rate) AS cost_ils,
                        COALESCE(shipping_cost_ils, shipping_cost_usd * :rate) AS ship_ils
                    FROM supplier_parts
                    WHERE part_id = :part_id AND is_available = TRUE
                    ORDER BY COALESCE(price_ils, price_usd * :rate) ASC
                    LIMIT 1
                """),
                {"part_id": str(_item.part_id), "rate": _rate},
            )).fetchone()

            if _sp_row:
                _live_unit = round(float(_sp_row[0]) * 1.45 * 1.18, 2)
                _live_ship = float(_sp_row[1]) if _sp_row[1] is not None else 91.0
                _max_shipping = max(_max_shipping, _live_ship)
            else:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "part_unavailable",
                        "message": "אחד או יותר מהחלקים אינם זמינים כרגע. אנא צור קשר עם שירות הלקוחות.",
                    },
                )

            if abs(_live_unit - round(float(_item.unit_price), 2)) > 0.01:
                _price_changed = True
                _item.unit_price = _live_unit
                _item.total_price = round(_live_unit * _item.quantity, 2)
            _new_items_total += Decimal(str(float(_item.total_price)))

    if _max_shipping > 0 and abs(_max_shipping - round(float(order.shipping_cost), 2)) > 0.01:
        _price_changed = True
        order.shipping_cost = round(_max_shipping, 2)

    if _price_changed:
        order.total_amount = round(float(_new_items_total) + float(order.shipping_cost), 2)
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "price_updated",
                "new_total": float(order.total_amount),
                "message": "המחיר עודכן. אנא אשר את ההזמנה מחדש.",
            },
        )
    # ── End price validation ───────────────────────────────────────────────

    frontend_url = _get_frontend_url(request)

    # ── Simulated payment (no Stripe key) ─────────────────────────────────
    if not stripe_configured:
        sim_session_id = f"SIM-{str(uuid.uuid4())[:12].upper()}"
        # Mark order as confirmed immediately
        order.status = "confirmed"
        existing_pay = await db.execute(
            select(Payment).where(Payment.order_id == order.id)
        )
        if not existing_pay.scalar_one_or_none():
            db.add(Payment(
                order_id=order.id,
                payment_intent_id=sim_session_id,
                amount=order.total_amount,
                currency="ILS",
                status="paid",
            ))
        await db.commit()
        # Trigger supplier fulfillment in background with its own session
        # (request db will close after handler returns)
        _order_id = order.id
        async def _fulfill_bg():
            async with pii_session_factory() as bg_db:
                try:
                    result2 = await bg_db.execute(select(Order).where(Order.id == _order_id))
                    bg_order = result2.scalar_one_or_none()
                    if bg_order:
                        await trigger_supplier_fulfillment([bg_order], bg_db)
                        await bg_db.commit()
                except Exception as _e:
                    print(f"[Fulfillment BG] error: {_e}")
        asyncio.create_task(_guarded_task(_fulfill_bg()))
        return {
            "checkout_url": f"{frontend_url}/payment/success?session_id={sim_session_id}&simulated=1",
            "session_id": sim_session_id,
            "amount": float(order.total_amount),
            "currency": "ILS",
        }
    # ── Real Stripe Checkout ───────────────────────────────────────────────
    stripe_sdk.api_key = stripe_key

    # Load order items for line items
    items_result = await db.execute(
        select(OrderItem).where(OrderItem.order_id == order.id)
    )
    order_items = items_result.scalars().all()

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
    session = await asyncio.get_running_loop().run_in_executor(
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
            idempotency_key=f"order:{order.id}",  # Gap 6: Idempotency
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
    db: AsyncSession = Depends(get_pii_db),
    redis=Depends(get_redis),
):
    """Create a single Stripe Checkout Session for multiple pending orders."""
    import stripe as stripe_sdk

    if redis:
        allowed = await check_rate_limit(redis, f'rate:create_multi_checkout:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

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

    # ── Live price validation ──────────────────────────────────────────────
    from decimal import Decimal
    _rate = USD_TO_ILS
    try:
        async with async_session_factory() as _cat:
            _ss = (await _cat.execute(
                text("SELECT value FROM system_settings WHERE key = 'ils_per_usd' LIMIT 1")
            )).fetchone()
            if _ss:
                _rate = float(_ss[0])
    except Exception:
        pass

    _updated_orders: list[str] = []
    async with async_session_factory() as _cat:
        for _order in orders:
            _items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == _order.id))
            _order_items = _items_res.scalars().all()
            _order_changed = False
            _max_shipping = 0.0
            _new_items_total = Decimal("0")

            for _item in _order_items:
                if not _item.part_id:
                    _new_items_total += Decimal(str(float(_item.total_price)))
                    continue
                _sp_row = (await _cat.execute(
                    text("""
                        SELECT
                            COALESCE(price_ils, price_usd * :rate) AS cost_ils,
                            COALESCE(shipping_cost_ils, shipping_cost_usd * :rate) AS ship_ils
                        FROM supplier_parts
                        WHERE part_id = :part_id AND is_available = TRUE
                        ORDER BY COALESCE(price_ils, price_usd * :rate) ASC
                        LIMIT 1
                    """),
                    {"part_id": str(_item.part_id), "rate": _rate},
                )).fetchone()

                if _sp_row:
                    _live_unit = round(float(_sp_row[0]) * 1.45 * 1.18, 2)
                    _live_ship = float(_sp_row[1]) if _sp_row[1] is not None else 91.0
                    _max_shipping = max(_max_shipping, _live_ship)
                else:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "part_unavailable",
                            "message": "אחד או יותר מהחלקים אינם זמינים כרגע. אנא צור קשר עם שירות הלקוחות.",
                        },
                    )

                if abs(_live_unit - round(float(_item.unit_price), 2)) > 0.01:
                    _order_changed = True
                    _item.unit_price = _live_unit
                    _item.total_price = round(_live_unit * _item.quantity, 2)
                _new_items_total += Decimal(str(float(_item.total_price)))

            if _max_shipping > 0 and abs(_max_shipping - round(float(_order.shipping_cost), 2)) > 0.01:
                _order_changed = True
                _order.shipping_cost = round(_max_shipping, 2)

            if _order_changed:
                _order.total_amount = round(float(_new_items_total) + float(_order.shipping_cost), 2)
                _updated_orders.append(_order.order_number)

    if _updated_orders:
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "price_updated",
                "updated_orders": _updated_orders,
                "message": "המחיר עודכן בחלק מההזמנות. אנא אשר מחדש.",
            },
        )
    # ── End price validation ───────────────────────────────────────────────

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
    session = await asyncio.get_running_loop().run_in_executor(
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
            idempotency_key=f"orders:{':'.join(str(o.id) for o in orders)}",  # Gap 6: Idempotency
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
    db: AsyncSession = Depends(get_pii_db),
):
    """Called after Stripe redirects back — verifies payment and marks order(s) paid."""
    import stripe as stripe_sdk

    # ── Simulated payment (session_id starts with SIM-) ───────────────────────
    if session_id.startswith("SIM-"):
        # Find the payment record linked to this simulated session
        pay_res = await db.execute(select(Payment).where(Payment.payment_intent_id == session_id))
        pay = pay_res.scalar_one_or_none()
        if not pay:
            raise HTTPException(status_code=404, detail="תשלום סימולציה לא נמצא")
        ord_res = await db.execute(
            select(Order).where(and_(Order.id == pay.order_id, Order.user_id == current_user.id))
        )
        order = ord_res.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="הזמנה לא נמצאה")
        # Ensure order is marked confirmed and payment paid
        order.status = "confirmed"
        pay.status = "paid"
        pay.paid_at = datetime.utcnow()
        pay.payment_method = "simulated"
        # Create invoice if not already exists
        inv_check = await db.execute(select(Invoice).where(Invoice.order_id == order.id))
        if not inv_check.scalar_one_or_none():
            inv_num = f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}"
            db.add(Invoice(
                invoice_number=inv_num,
                order_id=order.id,
                user_id=current_user.id,
                business_number=os.getenv("COMPANY_NUMBER", "060633880"),
            ))
        await db.commit()
        return {
            "status": "paid",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "amount": float(order.total_amount),
            "currency": "ILS",
        }

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key or stripe_key.startswith("sk_test_xxxxx"):
        raise HTTPException(status_code=503, detail="Stripe not configured")

    stripe_sdk.api_key = stripe_key

    session = await asyncio.get_running_loop().run_in_executor(
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
            _multi_msg = f"{len(multi_orders)} הזמנות אושרו: {paid_nums}"
            db.add(Notification(
                user_id=current_user.id,
                title="תשלום התקבל ✅",
                message=_multi_msg,
                type="payment_success",
            ))
            asyncio.create_task(_guarded_task(publish_notification(str(current_user.id), {"type": "payment_success", "title": "תשלום התקבל ✅", "message": _multi_msg})))
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
        _single_pay_msg = f"הזמנה {order.order_number} אושרה."
        db.add(Notification(
            user_id=current_user.id,
            title="תשלום התקבל ✅",
            message=_single_pay_msg,
            type="payment_success",
        ))
        asyncio.create_task(_guarded_task(publish_notification(str(current_user.id), {"type": "payment_success", "title": "תשלום התקבל ✅", "message": _single_pay_msg})))
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
async def create_payment_intent_legacy(order_id: str, request: Request, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Legacy endpoint – redirects to create-checkout."""
    if redis:
        allowed = await check_rate_limit(redis, f'rate:create_intent:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    return await create_checkout_session(order_id, request, current_user, db, redis)


@app.post("/api/v1/payments/confirm")
async def confirm_payment(payment_intent_id: str, current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_pii_db), request: Request = None, redis=Depends(get_redis)):
    if redis:
        allowed = await check_rate_limit(redis, f'rate:confirm_payment:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    return {"status": "redirect_to_stripe", "message": "Use /payments/create-checkout to get a Stripe Checkout URL"}


@app.get("/api/v1/payments/refunds/list")
async def list_refunds(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def get_payment(payment_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    # Join with Order to enforce ownership — prevents IDOR
    result = await db.execute(
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(
            and_(
                Payment.id == payment_id,
                Order.user_id == current_user.id,
            )
        )
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"id": str(payment.id), "amount": float(payment.amount), "status": payment.status, "payment_method": payment.payment_method, "created_at": payment.created_at}


@app.post("/api/v1/payments/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_pii_db)):
    """Stripe webhook for async payment confirmation (backup to verify-session)."""
    import stripe as stripe_sdk
    from BACKEND_DATABASE_MODELS import StripeWebhookLog
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_sdk.api_key = os.getenv("STRIPE_SECRET_KEY", "")

    # Reject webhook silently (return 400) when secret is not configured — never
    # fall through to forged-event handling.
    if not webhook_secret:
        raise HTTPException(status_code=400, detail="Webhook secret not configured")

    try:
        event = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: stripe_sdk.Webhook.construct_event(payload, sig_header, webhook_secret)
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ── Gap 6: Idempotency check (webhook deduplication) ────────────────────────────────
    # Check if we've already processed this exact event
    event_id = event.get("id", "")
    event_type = event.get("type", "")
    
    existing_log = None
    if event_id:
        result = await db.execute(
            select(StripeWebhookLog).where(StripeWebhookLog.event_id == event_id)
        )
        existing_log = result.scalar_one_or_none()
    
    # If we've seen this event AND processed it successfully, return 200 immediately
    if existing_log and existing_log.processed:
        logger.info(f"[Webhook] Deduped event {event_id} (already processed)")
        return {"received": True, "status": "deduped"}
    
    # If we've seen it but processing failed, retry processing it
    if existing_log:
        logger.info(f"[Webhook] Retrying failed event {event_id}")
    else:
        # First time seeing this event — create a log entry
        if event_id:
            existing_log = StripeWebhookLog(
                event_id=event_id,
                event_type=event_type,
                payload=event,
                processed=False,
            )
            db.add(existing_log)
            await db.commit()

    # ── Process the webhook event ──────────────────────────────────────────────────────
    processing_error = None
    try:
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
    except Exception as e:
        processing_error = str(e)[:500]
        logger.error(f"[Webhook] Error processing event {event_id}: {processing_error}")

    # ── Mark event as processed (or failed) in log ─────────────────────────────────────
    if existing_log:
        existing_log.processed = (processing_error is None)
        existing_log.result = {
            "processed": existing_log.processed,
            "error": processing_error,
            "processed_at": datetime.utcnow().isoformat(),
        }
        await db.commit()

    # Return 200 to Stripe regardless (prevents redelivery)
    return {"received": True}


# ==============================================================================
# WHATSAPP WEBHOOK  /api/v1/webhooks/whatsapp
# ==============================================================================

@app.post("/api/v1/webhooks/whatsapp")
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


@app.post("/api/v1/payments/refund")
async def refund_payment(
    payment_id: str,
    amount: float,
    reason: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """Admin: manually refund a payment via Stripe."""
    import stripe as stripe_sdk

    if redis:
        allowed = await check_rate_limit(redis, f'rate:refund:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

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
        session_obj = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: stripe_sdk.checkout.Session.retrieve(payment.payment_intent_id)
        )
        pi_id = session_obj.payment_intent
        if not pi_id:
            raise HTTPException(status_code=400, detail="No payment intent found for this session")

        refund_cents = int(amount * 100)
        stripe_refund = await asyncio.get_running_loop().run_in_executor(
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
            _refund_msg = f"החזר כספי של ₪{refund_ils:.2f} בוצע עבור הזמנה {order.order_number}."
            db.add(Notification(
                user_id=order.user_id,
                type="refund",
                title="החזר כספי אושר על ידי מנהל",
                message=_refund_msg,
            ))
            asyncio.create_task(_guarded_task(publish_notification(str(order.user_id), {"type": "refund", "title": "החזר כספי אושר על ידי מנהל", "message": _refund_msg})))
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
async def get_payment_history(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
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
    db: AsyncSession = Depends(get_pii_db),
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
    db: AsyncSession = Depends(get_pii_db),
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
                _track_title = "📦 החלקים הוזמנו – יש מספר מעקב!"
                _track_msg = (
                    f"הזמנה {order.order_number} הוזמנה מהספק.\n"
                    f"מספר מעקב {carrier_label}: {tracking_number}\n"
                    + (f"קישור מעקב: {tracking_url}" if tracking_url else "")
                )
                db.add(Notification(
                    user_id=order.user_id,
                    type="order_update",
                    title=_track_title,
                    message=_track_msg,
                    data={"order_id": str(order.id), "order_number": order.order_number, "tracking_number": tracking_number, "tracking_url": tracking_url},
                ))
                asyncio.create_task(_guarded_task(publish_notification(str(order.user_id), {"type": "order_update", "title": _track_title, "message": _track_msg})))
            else:
                # No tracking yet — still advance status so customer sees progress
                if order.status in ("processing", "paid"):
                    order.status = "supplier_ordered"
                _notrack_title = "🛒 ההזמנה הועברה לספק"
                _notrack_msg = f"הזמנה {order.order_number} הוזמנה מהספק ובדרך אליך. מספר מעקב יעודכן בהקדם."
                db.add(Notification(
                    user_id=order.user_id,
                    type="order_update",
                    title=_notrack_title,
                    message=_notrack_msg,
                    data={"order_id": str(order.id), "order_number": order.order_number},
                ))
                asyncio.create_task(_guarded_task(publish_notification(str(order.user_id), {"type": "order_update", "title": _notrack_title, "message": _notrack_msg})))

    n.read_at = datetime.utcnow()
    await db.commit()
    return {"message": "\u05e1\u05d5\u05de\u05df \u05db\u05d4\u05d5\u05d6\u05de\u05df"}


# ==============================================================================
# 7. INVOICES  /api/v1/invoices  (4 endpoints)
# ==============================================================================

@app.get("/api/v1/invoices")
async def get_invoices(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Invoice).where(Invoice.user_id == current_user.id).order_by(Invoice.issued_at.desc()).limit(limit))
    invoices = result.scalars().all()
    return {"invoices": [{"id": str(i.id), "invoice_number": i.invoice_number, "order_id": str(i.order_id), "pdf_url": i.pdf_url, "issued_at": i.issued_at} for i in invoices]}


@app.get("/api/v1/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"id": str(invoice.id), "invoice_number": invoice.invoice_number, "pdf_url": invoice.pdf_url, "business_number": invoice.business_number, "issued_at": invoice.issued_at}


@app.get("/api/v1/invoices/{invoice_id}/download")
async def download_invoice(invoice_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"download_url": invoice.pdf_url}


@app.post("/api/v1/invoices/{invoice_id}/resend")
async def resend_invoice(invoice_id: str, email: Optional[EmailStr] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"message": f"Invoice sent to {email or current_user.email}"}


# ==============================================================================
# 8. RETURNS  /api/v1/returns  (6 endpoints)
# ==============================================================================

# Policy constants — per Refund Policy v1.0 (Feb 2026)
_FULL_REFUND_REASONS = {"defective", "wrong_part", "damaged_in_transit"}
_RETURN_WINDOW_DAYS = int(os.getenv("RETURN_WINDOW_DAYS", "14"))


@app.post("/api/v1/returns", status_code=status.HTTP_201_CREATED)
async def create_return(data: ReturnRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    # ── 1. Validate order ownership and status ─────────────────────────────────
    result = await db.execute(select(Order).where(and_(Order.id == data.order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ["delivered", "shipped"]:
        raise HTTPException(status_code=400, detail="Order cannot be returned in current status")

    # ── 2. Duplicate guard ────────────────────────────────────────────────────
    existing = (await db.execute(
        select(Return.id).where(
            and_(
                Return.order_id == order.id,
                Return.status.notin_(["cancelled", "rejected"]),
            )
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="An active return request already exists for this order.")

    # ── 3. 14-day window — only when delivered_at is known ───────────────────
    if order.delivered_at:
        days_since = (datetime.utcnow() - order.delivered_at).days
        if days_since > _RETURN_WINDOW_DAYS:
            raise HTTPException(
                status_code=400,
                detail=f"Return window expired. Returns must be requested within {_RETURN_WINDOW_DAYS} days of delivery (it has been {days_since} days).",
            )

    # ── 4. Fraud score ────────────────────────────────────────────────────────
    fraud_score = 0.0

    # +0.3 if user has >2 returns in the last 90 days
    ninety_days_ago = datetime.utcnow() - timedelta(days=90)
    recent_returns_count = (await db.execute(
        select(func.count()).select_from(Return).where(
            and_(
                Return.user_id == current_user.id,
                Return.requested_at >= ninety_days_ago,
            )
        )
    )).scalar_one()
    if recent_returns_count > 2:
        fraud_score += 0.3

    # +0.3 if order was delivered less than 24 hours ago
    if order.delivered_at and (datetime.utcnow() - order.delivered_at).total_seconds() < 86400:
        fraud_score += 0.3

    # +0.2 if reason is changed_mind or other
    if data.reason in ("changed_mind", "other"):
        fraud_score += 0.2

    # +0.2 if order total > 500 ILS
    if order.total_amount and order.total_amount > 500:
        fraud_score += 0.2

    fraud_score = round(min(fraud_score, 1.0), 2)
    ret_status = "pending_review" if fraud_score >= 0.5 else "pending"

    # ── 5. Create Return row ──────────────────────────────────────────────────
    return_number = f"RET-{datetime.utcnow().year}-{str(uuid.uuid4())[:8].upper()}"
    ret = Return(
        return_number=return_number,
        order_id=order.id,
        user_id=current_user.id,
        reason=data.reason,
        description=data.description,
        original_amount=order.total_amount,
        status=ret_status,
    )
    db.add(ret)
    await db.flush()   # obtain ret.id before writing approval_queue

    # ── 6. Approval queue — every return goes through the queue ───────────────
    db.add(ApprovalQueue(
        entity_type="return",
        entity_id=ret.id,
        action="review_return",
        payload={
            "return_number": return_number,
            "order_number": order.order_number,
            "order_id": str(order.id),
            "user_id": str(current_user.id),
            "user_email": current_user.email,
            "reason": data.reason,
            "description": data.description,
            "original_amount": float(order.total_amount),
            "fraud_score": fraud_score,
            "flagged": fraud_score >= 0.5,
        },
        status="pending",
        requested_by=current_user.id,
    ))

    # ── 7. Notify customer ────────────────────────────────────────────────────
    _ret_open_title = f"📦 בקשת החזרה נפתחה — {return_number}"
    _ret_open_msg = (
        f"קיבלנו את בקשת ההחזרה שלך עבור הזמנה {order.order_number}.\n"
        f"נסיבה: {data.reason}. נחזור אליך תוך 24 שעות."
    )
    db.add(Notification(
        user_id=current_user.id,
        type="return_update",
        title=_ret_open_title,
        message=_ret_open_msg,
        data={"return_number": return_number, "order_number": order.order_number, "reason": data.reason},
    ))
    asyncio.create_task(_guarded_task(publish_notification(str(current_user.id), {"type": "return_update", "title": _ret_open_title, "message": _ret_open_msg})))

    await db.commit()
    await db.refresh(ret)
    return {
        "return_id": str(ret.id),
        "return_number": ret.return_number,
        "status": ret.status,
        "fraud_score": fraud_score,
        "message": "Return request created. We'll review it within 24 hours.",
    }


@app.get("/api/v1/returns")
async def get_returns(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(Return.user_id == current_user.id).order_by(Return.requested_at.desc()))
    returns = result.scalars().all()
    # Fetch order_numbers in one shot
    order_ids = list({r.order_id for r in returns})
    order_map = {}
    if order_ids:
        ord_res = await db.execute(select(Order.id, Order.order_number).where(Order.id.in_(order_ids)))
        order_map = {row.id: row.order_number for row in ord_res.all()}
    return {"returns": [
        {
            "id": str(r.id),
            "return_number": r.return_number,
            "order_id": str(r.order_id),
            "order_number": order_map.get(r.order_id, ""),
            "reason": r.reason,
            "description": r.description,
            "status": r.status,
            "original_amount": float(r.original_amount) if r.original_amount else None,
            "refund_amount": float(r.refund_amount) if r.refund_amount else None,
            "requested_at": r.requested_at,
            "approved_at": r.approved_at,
        }
        for r in returns
    ]}


@app.get("/api/v1/returns/{return_id}")
async def get_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    return {"id": str(ret.id), "return_number": ret.return_number, "status": ret.status, "reason": ret.reason, "description": ret.description, "original_amount": float(ret.original_amount), "refund_amount": float(ret.refund_amount) if ret.refund_amount else None, "requested_at": ret.requested_at, "approved_at": ret.approved_at}


@app.post("/api/v1/returns/{return_id}/track")
async def track_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    return {"return_number": ret.return_number, "status": ret.status, "tracking_number": ret.tracking_number}


@app.put("/api/v1/returns/{return_id}/cancel")
async def cancel_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status not in ["pending", "approved"]:
        raise HTTPException(status_code=400, detail="Cannot cancel return in current status")
    await db.delete(ret)
    await db.commit()
    return {"message": "Return cancelled"}


@app.get("/api/v1/returns/{return_id}/invoice")
async def get_return_invoice(
    return_id: str,
    inline: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Generate and stream a Hebrew PDF credit note for an approved return."""
    from fastapi.responses import StreamingResponse
    from invoice_generator import generate_credit_note_pdf

    ret_res = await db.execute(
        select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id))
    )
    ret = ret_res.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status not in ("approved", "completed"):
        raise HTTPException(status_code=402, detail="הודעת הזיכוי זמינה רק לאחר אישור ההחזרה")

    # Fetch original order items to list on the credit note
    items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == ret.order_id))
    items = items_res.scalars().all()

    # Attach order_number and shipping_cost as plain attributes (avoids lazy-load in generator)
    ord_res = await db.execute(
        select(Order.order_number, Order.shipping_cost).where(Order.id == ret.order_id)
    )
    order_row = ord_res.one_or_none()
    ret.order_number = (order_row[0] if order_row else None) or str(ret.order_id)[:8].upper()  # type: ignore[attr-defined]
    ret._shipping_cost = float(order_row[1] or 0) if order_row else 0.0  # type: ignore[attr-defined]

    pdf_bytes = generate_credit_note_pdf(ret, items, current_user)

    filename = f"credit_note_{ret.return_number}.pdf"
    disposition = f'inline; filename="{filename}"' if inline else f'attachment; filename="{filename}"'
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(pdf_bytes)),
            "X-Invoice-Number": ret.return_number,
        },
    )


@app.post("/api/v1/returns/{return_id}/approve")
async def approve_return(return_id: str, refund_percentage: int = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(Return.id == return_id))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status not in ["pending"]:
        raise HTTPException(status_code=400, detail=f"Cannot approve return in status: {ret.status}")

    # Apply policy-based refund percentage if not explicitly overridden (policy §3)
    if refund_percentage is None:
        refund_percentage = 100 if ret.reason in _FULL_REFUND_REASONS else 90

    original = float(ret.original_amount or 0)

    if refund_percentage == 100:
        # Full refund — return everything including shipping
        handling_fee_amount = 0
        refund_calc = original
    else:
        # Partial refund — 10% handling fee applied to PART PRICE ONLY (excluding shipping)
        ord_res = await db.execute(select(Order).where(Order.id == ret.order_id))
        order_for_fee = ord_res.scalar_one_or_none()
        shipping_cost = float(order_for_fee.shipping_cost or 0) if order_for_fee else 0
        parts_base = max(0.0, original - shipping_cost)
        handling_fee_pct = 100 - refund_percentage
        handling_fee_amount = round(parts_base * handling_fee_pct / 100, 2)
        # Refund = parts × 90% − return shipping fee (customer bears return shipping cost)
        return_shipping_fee = shipping_cost
        refund_calc = round(parts_base - handling_fee_amount - return_shipping_fee, 2)

    ret.status = "approved"
    ret.approved_at = datetime.utcnow()
    ret.refund_percentage = refund_percentage
    ret.refund_amount = refund_calc
    ret.handling_fee = handling_fee_amount if handling_fee_amount > 0 else None

    shipping_note = (
        "\nעלות השילוח החזרה תכוסה על ידינו."
        if ret.reason in _FULL_REFUND_REASONS
        else "\nשים לב: עלות משלוח ההחזרה באחריות הלקוח."
    )

    # Notify customer of approval
    _ret_approve_title = f"✅ בקשת ההחזרה אושרה — {ret.return_number}"
    _ret_approve_msg = (
        f"בקשת ההחזרה שלך {ret.return_number} אושרה (החזר {refund_percentage}%).\n"
        f"זיכוי של ₪{float(ret.refund_amount):.2f} יועבר לכרטיס האשראי שלך תוך 7-14 ימי עסקים."
        + shipping_note
    )
    db.add(Notification(
        user_id=ret.user_id,
        type="return_update",
        title=_ret_approve_title,
        message=_ret_approve_msg,
        data={"return_number": ret.return_number, "refund_amount": float(ret.refund_amount), "refund_percentage": refund_percentage, "handling_fee": float(handling_fee_amount)},
    ))
    asyncio.create_task(_guarded_task(publish_notification(str(ret.user_id), {"type": "return_update", "title": _ret_approve_title, "message": _ret_approve_msg})))

    await db.commit()
    return {"message": "Return approved", "refund_amount": float(ret.refund_amount), "refund_percentage": refund_percentage, "handling_fee": float(handling_fee_amount)}


@app.post("/api/v1/returns/{return_id}/reject", tags=["Returns"])
async def reject_return(
    return_id: str,
    reason: str = "הבקשה לא עומדת בתנאי מדיניות ההחזרה",
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Admin: reject a return request with an optional reason."""
    result = await db.execute(select(Return).where(Return.id == return_id))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status not in ["pending"]:
        raise HTTPException(status_code=400, detail=f"Cannot reject return in status: {ret.status}")
    ret.status = "rejected"
    ret.rejection_reason = reason
    ret.rejected_at = datetime.utcnow()

    # Notify customer of rejection
    _ret_reject_title = f"❌ בקשת ההחזרה נדחתה — {ret.return_number}"
    _ret_reject_msg = (
        f"לצערנו, בקשת ההחזרה {ret.return_number} נדחתה.\n"
        f"סיבה: {reason}\n"
        "לשאלות פנה לשירות הלקוחות: support@autospare.com"
    )
    db.add(Notification(
        user_id=ret.user_id,
        type="return_update",
        title=_ret_reject_title,
        message=_ret_reject_msg,
        data={"return_number": ret.return_number, "rejection_reason": reason},
    ))
    asyncio.create_task(_guarded_task(publish_notification(str(ret.user_id), {"type": "return_update", "title": _ret_reject_title, "message": _ret_reject_msg})))

    await db.commit()
    return {"message": "Return rejected", "return_number": ret.return_number}


@app.get("/api/v1/admin/returns", tags=["Returns"])
async def admin_get_returns(
    status_filter: str = "",
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Admin: list all returns, optionally filtered by status."""
    q = select(Return).order_by(Return.requested_at.desc())
    if status_filter:
        q = q.where(Return.status == status_filter)
    result = await db.execute(q)
    returns = result.scalars().all()

    # Fetch order numbers + customer names in one shot
    order_ids = list({r.order_id for r in returns})
    user_ids = list({r.user_id for r in returns})
    order_map = {}
    user_map = {}
    if order_ids:
        ord_res = await db.execute(select(Order.id, Order.order_number).where(Order.id.in_(order_ids)))
        order_map = {row.id: row.order_number for row in ord_res.all()}
    if user_ids:
        usr_res = await db.execute(select(User.id, User.full_name, User.email).where(User.id.in_(user_ids)))
        user_map = {row.id: {"name": row.full_name, "email": row.email} for row in usr_res.all()}

    return {"returns": [
        {
            "id": str(r.id),
            "return_number": r.return_number,
            "order_id": str(r.order_id),
            "order_number": order_map.get(r.order_id, ""),
            "user_id": str(r.user_id),
            "user_name": user_map.get(r.user_id, {}).get("name", ""),
            "user_email": user_map.get(r.user_id, {}).get("email", ""),
            "reason": r.reason,
            "description": r.description,
            "status": r.status,
            "original_amount": float(r.original_amount) if r.original_amount else None,
            "refund_amount": float(r.refund_amount) if r.refund_amount else None,
            "refund_percentage": r.refund_percentage,
            "requested_at": r.requested_at,
            "approved_at": r.approved_at,
        }
        for r in returns
    ]}


# ==============================================================================
# 9. FILES  /api/v1/files  (4 endpoints)
# ==============================================================================

@app.post("/api/v1/files/upload")
async def upload_file(file: UploadFile = File(...), current_user: User = Depends(get_current_verified_user), db: AsyncSession = Depends(get_pii_db)):
    allowed = ["image/jpeg", "image/png", "image/webp", "audio/mpeg", "audio/wav", "video/mp4"]
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="File type not allowed")
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 25MB)")
    # Virus scan before persisting anything
    scan_status, virus_name = _scan_bytes_for_virus(content)
    if scan_status == "infected":
        raise HTTPException(status_code=400, detail=f"File rejected: malware detected ({virus_name})")
    stored_filename = f"{uuid.uuid4()}_{file.filename}"
    ftype = "image" if "image" in (file.content_type or "") else ("audio" if "audio" in (file.content_type or "") else "video")
    file_record = FileModel(
        user_id=current_user.id,
        original_filename=file.filename,
        stored_filename=stored_filename,
        file_type=ftype,
        mime_type=file.content_type,
        file_size_bytes=len(content),
        storage_path=f"/uploads/{stored_filename}",
        expires_at=datetime.utcnow() + timedelta(days=30),
        virus_scan_status=scan_status,
        virus_scan_at=datetime.utcnow() if scan_status != "skipped" else None,
    )
    db.add(file_record)
    await db.commit()
    await db.refresh(file_record)
    return {"file_id": str(file_record.id), "url": f"/api/v1/files/{file_record.id}", "expires_at": file_record.expires_at}


@app.get("/api/v1/files/{file_id}")
async def get_file(file_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(FileModel).where(and_(FileModel.id == file_id, FileModel.user_id == current_user.id)))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return {"id": str(f.id), "filename": f.original_filename, "file_type": f.file_type, "size_bytes": f.file_size_bytes, "url": f.cdn_url or f.storage_path, "expires_at": f.expires_at}


@app.delete("/api/v1/files/{file_id}")
async def delete_file(file_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def get_profile(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    return {
        "user": {"id": str(current_user.id), "email": current_user.email, "phone": current_user.phone, "full_name": current_user.full_name, "is_verified": current_user.is_verified},
        "profile": {"address": profile.address_line1 if profile else None, "apartment": profile.address_line2 if profile else None, "city": profile.city if profile else None, "postal_code": profile.postal_code if profile else None, "preferred_language": profile.preferred_language if profile else "he", "avatar_url": profile.avatar_url if profile else None} if profile else None,
    }


@app.put("/api/v1/profile")
async def update_profile(address_line1: Optional[str] = None, address_line2: Optional[str] = None, city: Optional[str] = None, postal_code: Optional[str] = None, full_name: Optional[str] = None, phone: Optional[str] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
    if phone is not None and phone.strip() != (current_user.phone or ''):
        from sqlalchemy import update as sa_update
        from fastapi import HTTPException
        existing = await db.execute(select(User).where(User.phone == phone.strip(), User.id != current_user.id))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="מספר הטלפון כבר רשום לחשבון אחר")
        await db.execute(sa_update(User).where(User.id == current_user.id).values(phone=phone.strip()))
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise
    return {"message": "Profile updated"}


@app.post("/api/v1/profile/avatar")
async def upload_avatar(file: UploadFile = File(...), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db), request: Request = None, redis=Depends(get_redis)):
    if redis and request:
        ip = request.client.host if request.client else "unknown"
        allowed = await check_rate_limit(redis, f"rate:upload_avatar:{ip}", 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="יותר מדי בקשות — נסה שוב בעוד דקה")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Avatar too large (max 5 MB)")

    allowed_mimes = {"image/jpeg", "image/png", "image/webp"}
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in allowed_mimes:
        raise HTTPException(status_code=415, detail="Unsupported image type")

    scan_status, virus_name = _scan_bytes_for_virus(content)
    if scan_status == "infected":
        raise HTTPException(status_code=400, detail=f"File rejected: malware detected ({virus_name})")

    return {"avatar_url": "https://cdn.autospare.com/avatars/coming-soon.jpg"}


@app.delete("/api/v1/profile/avatar")
async def delete_avatar(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    return {"message": "Avatar deleted"}


@app.post("/api/v1/profile/update-phone")
async def update_phone(data: UpdatePhoneRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    await update_phone_number(current_user, data.new_phone, data.verification_code, db)
    return {"message": "Phone number updated"}


@app.get("/api/v1/profile/marketing-preferences")
async def get_marketing_preferences(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    return {"marketing_consent": profile.marketing_consent if profile else False, "newsletter_subscribed": profile.newsletter_subscribed if profile else False, "preferences": profile.marketing_preferences if profile else {}}


@app.put("/api/v1/profile/marketing-preferences")
async def update_marketing_preferences(marketing_consent: Optional[bool] = None, newsletter_subscribed: Optional[bool] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def get_order_history_summary(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(func.count(Order.id).label("total"), func.sum(Order.total_amount).label("spent")).where(Order.user_id == current_user.id))
    stats = result.first()
    return {"total_orders": stats.total or 0, "total_spent": float(stats.spent or 0)}


# ==============================================================================
# 11. MARKETING  /api/v1/marketing  (7 endpoints)
# ==============================================================================

@app.post("/api/v1/marketing/subscribe")
async def subscribe_newsletter(data: NewsletterSubscribeRequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:subscribe:{ip}', 3, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    return {"message": "Subscribed successfully"}


@app.post("/api/v1/marketing/validate-coupon")
async def validate_coupon(data: CouponValidateRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    return {"valid": True, "code": data.code, "discount_type": "percentage", "discount_value": 10}


@app.get("/api/v1/marketing/coupons")
async def get_available_coupons(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    return {"coupons": []}


@app.post("/api/v1/marketing/apply-coupon")
async def apply_coupon(order_id: str, coupon_code: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    return {"discount": 0, "message": "Coupon system coming soon"}


@app.get("/api/v1/marketing/promotions")
async def get_active_promotions(db: AsyncSession = Depends(get_db)):
    return {"promotions": [{"code": "WELCOME10", "description": "10% on first order", "discount_type": "percentage", "value": 10}]}


@app.post("/api/v1/marketing/referral")
async def create_referral(email: EmailStr, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    return {"message": "Referral sent", "referral_link": f"https://autospare.com?ref={str(current_user.id)[:8]}"}


@app.get("/api/v1/marketing/loyalty-points")
async def get_loyalty_points(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    return {"points": 0, "tier": "bronze", "next_tier": "silver", "points_needed": 500}


# ==============================================================================
# 12. NOTIFICATIONS  /api/v1/notifications  (6 endpoints)
# ==============================================================================

_SSE_HEARTBEAT_INTERVAL = 30  # seconds

@app.get("/api/v1/notifications/stream")
async def notifications_stream(
    current_user: User = Depends(get_current_verified_user),
    redis=Depends(get_redis),
):
    """SSE stream: subscribe to user:{user_id}:notifications Redis Pub/Sub channel."""
    user_id = str(current_user.id)

    async def event_generator():
        if not redis:
            yield {"event": "connected", "data": ""}
            return

        channel = f"user:{user_id}:notifications"
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            yield {"event": "connected", "data": ""}
            last_heartbeat = asyncio.get_running_loop().time()
            while True:
                now = asyncio.get_running_loop().time()
                if now - last_heartbeat >= _SSE_HEARTBEAT_INTERVAL:
                    yield {"event": "heartbeat", "data": ""}
                    last_heartbeat = now
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.1
                )
                if message and message["type"] == "message":
                    yield {"event": "notification", "data": message["data"]}
                else:
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return EventSourceResponse(event_generator())


@app.get("/api/v1/notifications")
async def get_notifications(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Notification).where(Notification.user_id == current_user.id).order_by(Notification.created_at.desc()).limit(limit))
    notifs = result.scalars().all()
    return {"notifications": [{"id": str(n.id), "type": n.type, "title": n.title, "message": n.message, "read_at": n.read_at, "created_at": n.created_at} for n in notifs]}


@app.get("/api/v1/notifications/unread-count")
async def get_unread_count(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(func.count(Notification.id)).where(and_(Notification.user_id == current_user.id, Notification.read_at.is_(None))))
    return {"unread_count": result.scalar() or 0}


@app.put("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Notification).where(and_(Notification.id == notification_id, Notification.user_id == current_user.id)))
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.read_at = datetime.utcnow()
    await db.commit()
    return {"message": "Marked as read"}


@app.put("/api/v1/notifications/read-all")
async def mark_all_read(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Notification).where(and_(Notification.user_id == current_user.id, Notification.read_at.is_(None))))
    notifs = result.scalars().all()
    for n in notifs:
        n.read_at = datetime.utcnow()
    await db.commit()
    return {"message": f"Marked {len(notifs)} notifications as read"}


@app.delete("/api/v1/notifications/{notification_id}")
async def delete_notification(notification_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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
async def get_admin_stats(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db), cat_db: AsyncSession = Depends(get_db)):
    users_count   = (await db.execute(select(func.count(User.id)))).scalar()
    orders_count  = (await db.execute(select(func.count(Order.id)))).scalar()
    parts_count   = (await cat_db.execute(select(func.count(PartsCatalog.id)).where(PartsCatalog.is_active == True))).scalar()
    pending_orders = (await db.execute(select(func.count(Order.id)).where(Order.status.in_(["pending_payment", "paid", "processing", "supplier_ordered", "confirmed"])))).scalar()

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

    # Refunds issued — two sources:
    # 1. Payment-level refunds (cancellations processed through Stripe)
    payment_refunds = (await db.execute(
        select(func.sum(Payment.refund_amount)).where(Payment.status == "refunded")
    )).scalar() or 0
    # 2. Return-level refunds (approved returns via the returns workflow)
    return_refunds = (await db.execute(
        select(func.sum(Return.refund_amount)).where(Return.status == "approved")
    )).scalar() or 0
    refunds_total = float(payment_refunds) + float(return_refunds)

    # Net revenue after refunds
    net_revenue = float(gross_revenue) - float(refunds_total)

    # Profit calculation based on net_revenue (from Payments — reliable source)
    # net_revenue already excludes refunds and includes VAT + shipping.
    # price_no_vat  = net_revenue / 1.18  (remove 18% VAT)
    # profit        = price_no_vat × (45 / 145)  ← 45% markup portion
    # cost          = price_no_vat - profit       ← supplier cost
    MARGIN_RATE = 0.45
    VAT_RATE = 0.18
    paid_statuses = ["paid", "processing", "supplier_ordered", "confirmed", "shipped", "delivered"]

    price_no_vat_net   = round(float(net_revenue) / (1 + VAT_RATE), 2)           # strip VAT
    profit_total      = round(price_no_vat_net * (MARGIN_RATE / (1 + MARGIN_RATE)), 2)  # 45/145
    cost_total        = round(price_no_vat_net - profit_total, 2)
    margin_pct        = round((profit_total / cost_total * 100) if cost_total > 0 else 0, 1)  # ≈ 45%
    vat_total         = round(float(net_revenue) - price_no_vat_net, 2)  # VAT portion
    net_revenue_ex_vat = price_no_vat_net  # alias for clarity

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
        "vat_total": vat_total,
        "net_revenue_ex_vat": net_revenue_ex_vat,
        "avg_order_value": round(float(avg_order), 2),
        "currency": "ILS",
    }


@app.get("/api/v1/admin/users")
async def get_admin_users(current_user: User = Depends(get_current_admin_user), limit: int = 100, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(User).order_by(User.created_at.desc()).limit(limit))
    users = result.scalars().all()
    return {"users": [{"id": str(u.id), "email": u.email, "full_name": u.full_name, "phone": u.phone, "is_verified": u.is_verified, "is_admin": u.is_admin, "is_active": u.is_active, "role": u.role, "failed_login_count": u.failed_login_count, "locked_until": u.locked_until.isoformat() if u.locked_until else None, "created_at": u.created_at} for u in users]}


@app.get("/api/v1/admin/super/settings")
async def super_admin_list_settings(
    current_user: User = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    result = await db.execute(select(SystemSetting).order_by(SystemSetting.key.asc()))
    settings = result.scalars().all()

    await _write_audit_log(
        db=db,
        current_user=current_user,
        action="super_admin.settings.list",
        entity_type="system_settings",
        old_value=None,
        new_value={"count": len(settings)},
        request=request,
    )

    return {
        "settings": [
            {
                "id": str(s.id),
                "key": s.key,
                "value": s.value,
                "value_type": s.value_type,
                "description": s.description,
                "is_public": s.is_public,
                "updated_by": str(s.updated_by) if s.updated_by else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in settings
        ]
    }


@app.put("/api/v1/admin/super/settings/{key}")
async def super_admin_update_setting(
    key: str,
    body: SuperAdminSettingUpdateBody,
    current_user: User = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    if _is_blocked_setting_key(key):
        raise HTTPException(status_code=403, detail="This setting is blocked")

    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")

    old_payload = {
        "key": setting.key,
        "value": setting.value,
        "value_type": setting.value_type,
        "description": setting.description,
        "is_public": setting.is_public,
    }

    if body.value is not None:
        setting.value = body.value
    if body.value_type is not None:
        setting.value_type = body.value_type
    if body.description is not None:
        setting.description = body.description
    if body.is_public is not None:
        setting.is_public = body.is_public

    setting.updated_by = current_user.id
    setting.updated_at = datetime.utcnow()
    await db.flush()

    new_payload = {
        "key": setting.key,
        "value": setting.value,
        "value_type": setting.value_type,
        "description": setting.description,
        "is_public": setting.is_public,
    }

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="super_admin.settings.update",
            entity_type="system_settings",
            entity_id=setting.id,
            old_value=old_payload,
            new_value=new_payload,
            ip_address=request.client.host if (request and request.client) else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )
    await db.commit()

    return {"message": "Setting updated", "setting": new_payload}


@app.post("/api/v1/admin/super/settings")
async def super_admin_create_setting(
    body: SuperAdminSettingCreateBody,
    current_user: User = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    key = body.key.strip()
    if _is_blocked_setting_key(key):
        raise HTTPException(status_code=403, detail="This setting is blocked")

    existing = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Setting already exists")

    setting = SystemSetting(
        key=key,
        value=body.value,
        value_type=body.value_type,
        description=body.description,
        is_public=body.is_public,
        updated_by=current_user.id,
        updated_at=datetime.utcnow(),
    )
    db.add(setting)
    await db.flush()

    new_payload = {
        "key": setting.key,
        "value": setting.value,
        "value_type": setting.value_type,
        "description": setting.description,
        "is_public": setting.is_public,
    }

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="super_admin.settings.create",
            entity_type="system_settings",
            entity_id=setting.id,
            old_value=None,
            new_value=new_payload,
            ip_address=request.client.host if (request and request.client) else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )
    await db.commit()

    return {
        "message": "Setting created",
        "setting": {
            "id": str(setting.id),
            **new_payload,
            "updated_by": str(setting.updated_by) if setting.updated_by else None,
            "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
        },
    }


@app.delete("/api/v1/admin/super/settings/{key}")
async def super_admin_delete_setting(
    key: str,
    current_user: User = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    if _is_blocked_setting_key(key):
        raise HTTPException(status_code=403, detail="This setting is blocked")

    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")

    old_payload = {
        "id": str(setting.id),
        "key": setting.key,
        "value": setting.value,
        "value_type": setting.value_type,
        "description": setting.description,
        "is_public": setting.is_public,
        "updated_by": str(setting.updated_by) if setting.updated_by else None,
        "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
    }

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="super_admin.settings.delete",
            entity_type="system_settings",
            entity_id=setting.id,
            old_value=old_payload,
            new_value=None,
            ip_address=request.client.host if (request and request.client) else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )
    await db.delete(setting)
    await db.commit()

    return {"message": "Setting deleted", "key": key}


@app.get("/api/v1/admin/super/users")
async def super_admin_list_users(
    current_user: User = Depends(get_current_super_admin),
    limit: int = 100,
    pii_db: AsyncSession = Depends(get_pii_db),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    result = await pii_db.execute(select(User).order_by(User.created_at.desc()).limit(limit))
    users = result.scalars().all()

    await _write_audit_log(
        db=db,
        current_user=current_user,
        action="super_admin.users.list",
        entity_type="users",
        old_value=None,
        new_value={"count": len(users), "limit": limit},
        request=request,
    )

    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "full_name": u.full_name,
                "phone": u.phone[-4:] if u.phone else None,
                "role": u.role,
                "is_admin": u.is_admin,
                "is_super_admin": u.is_super_admin,
                "is_active": u.is_active,
                "is_verified": u.is_verified,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    }


@app.put("/api/v1/admin/super/users/{user_id}/role")
async def super_admin_update_user_role(
    user_id: str,
    body: SuperAdminUserRoleUpdateBody,
    current_user: User = Depends(get_current_super_admin),
    pii_db: AsyncSession = Depends(get_pii_db),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    try:
        target_user_uuid = _UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user id")

    result = await pii_db.execute(select(User).where(User.id == target_user_uuid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id and (not body.is_admin or not body.is_super_admin):
        raise HTTPException(status_code=403, detail="Super admin cannot demote themselves")

    old_payload = {
        "role": user.role,
        "is_admin": user.is_admin,
        "is_super_admin": user.is_super_admin,
    }

    user.is_admin = body.is_admin
    user.is_super_admin = body.is_super_admin
    user.role = body.role if body.role is not None else ("admin" if body.is_admin else "customer")
    await pii_db.commit()

    new_payload = {
        "role": user.role,
        "is_admin": user.is_admin,
        "is_super_admin": user.is_super_admin,
    }

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="super_admin.users.update_role",
            entity_type="users",
            entity_id=user.id,
            old_value=old_payload,
            new_value=new_payload,
            ip_address=request.client.host if (request and request.client) else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )
    await db.commit()

    return {
        "message": "User role updated",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_admin": user.is_admin,
            "is_super_admin": user.is_super_admin,
        },
    }


class UserUpdateBody(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    is_verified: Optional[bool] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class UserCreateBody(BaseModel):
    full_name: str
    email: str
    phone: str
    password: str
    role: str = "customer"
    is_admin: bool = False
    is_verified: bool = True


@app.post("/api/v1/admin/users")
async def create_admin_user(body: UserCreateBody, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    dup_email = await db.execute(select(User).where(User.email == body.email))
    if dup_email.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="כתובת האימייל כבר קיימת במערכת")
    dup_phone = await db.execute(select(User).where(User.phone == body.phone))
    if dup_phone.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="מספר הטלפון כבר קיים במערכת")
    new_user = User(
        email=body.email,
        phone=body.phone,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role="admin" if body.is_admin else body.role,
        is_admin=body.is_admin,
        is_active=True,
        is_verified=body.is_verified,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return {"message": "User created", "user": {"id": str(new_user.id), "email": new_user.email, "full_name": new_user.full_name, "phone": new_user.phone, "role": new_user.role, "is_admin": new_user.is_admin, "is_active": new_user.is_active, "is_verified": new_user.is_verified, "failed_login_count": 0, "locked_until": None, "created_at": new_user.created_at}}

@app.put("/api/v1/admin/users/{user_id}")
async def update_admin_user(user_id: str, body: UserUpdateBody = None, is_active: Optional[bool] = None, is_admin: Optional[bool] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Handle legacy query params
    if is_active is not None:
        user.is_active = is_active
    if is_admin is not None:
        if not is_admin:
            admin_count_result = await db.execute(select(func.count()).select_from(User).where(User.is_admin == True))
            if admin_count_result.scalar() <= 1:
                raise HTTPException(status_code=400, detail="Cannot remove the last admin")
        user.is_admin = is_admin
    if body:
        if body.full_name is not None:
            user.full_name = body.full_name
        if body.email is not None:
            dup = await db.execute(select(User).where(User.email == body.email, User.id != user_id))
            if dup.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Email already in use")
            user.email = body.email
        if body.phone is not None:
            dup = await db.execute(select(User).where(User.phone == body.phone, User.id != user_id))
            if dup.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Phone already in use")
            user.phone = body.phone
        if body.role is not None:
            user.role = body.role
        if body.is_verified is not None:
            user.is_verified = body.is_verified
        if body.is_active is not None:
            user.is_active = body.is_active
        if body.is_admin is not None:
            if not body.is_admin:
                admin_count_result = await db.execute(select(func.count()).select_from(User).where(User.is_admin == True))
                if admin_count_result.scalar() <= 1:
                    raise HTTPException(status_code=400, detail="Cannot remove the last admin")
            user.is_admin = body.is_admin
            user.role = "admin" if body.is_admin else "customer"
    await db.commit()
    return {"message": "User updated", "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "phone": user.phone, "role": user.role, "is_admin": user.is_admin, "is_active": user.is_active, "is_verified": user.is_verified}}


@app.post("/api/v1/admin/users/{user_id}/reset-login")
async def reset_user_login_failures(user_id: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.failed_login_count = 0
    user.locked_until = None
    await db.commit()
    return {"message": "Login failures reset"}


@app.delete("/api/v1/admin/users/{user_id}")
async def delete_admin_user(user_id: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    if str(current_user.id) == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        admin_count_result = await db.execute(select(func.count()).select_from(User).where(User.is_admin == True))
        if admin_count_result.scalar() <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin")
    await db.delete(user)
    await db.commit()
    return {"message": "User deleted"}


@app.get("/api/v1/admin/suppliers")
async def get_admin_suppliers(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import Supplier
    result = await db.execute(select(Supplier).order_by(Supplier.priority))
    suppliers = result.scalars().all()
    def _s(s):
        creds = s.credentials or {}
        return {
            "id": str(s.id), "name": s.name, "country": s.country,
            "website": s.website, "api_endpoint": s.api_endpoint,
            "contact_email": creds.get("contact_email"), "contact_phone": creds.get("contact_phone"),
            "is_active": s.is_active, "priority": s.priority,
            "reliability_score": float(s.reliability_score),
            "supports_express": s.supports_express,
            "express_carrier": s.express_carrier,
            "express_base_cost_usd": float(s.express_base_cost_usd) if s.express_base_cost_usd else None,
            "avg_delivery_days_actual": float(s.avg_delivery_days_actual) if s.avg_delivery_days_actual else None,
            "shipping_info": s.shipping_info or {},
            "return_policy": s.return_policy or {},
            "created_at": s.created_at,
        }
    return {"suppliers": [_s(s) for s in suppliers]}


@app.post("/api/v1/admin/suppliers")
async def create_supplier(data: SupplierCreate, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import Supplier
    creds = {}
    if data.contact_email: creds["contact_email"] = data.contact_email
    if data.contact_phone: creds["contact_phone"] = data.contact_phone
    if data.api_key: creds["api_key"] = data.api_key
    supplier = Supplier(
        name=data.name, country=data.country, website=data.website,
        api_endpoint=data.api_endpoint, priority=data.priority,
        reliability_score=data.reliability_score, is_active=True,
        supports_express=data.supports_express, express_carrier=data.express_carrier,
        express_base_cost_usd=data.express_base_cost_usd,
        credentials=creds,
    )
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    creds_out = supplier.credentials or {}
    return {"id": str(supplier.id), "message": "Supplier created", "supplier": {
        "id": str(supplier.id), "name": supplier.name, "country": supplier.country,
        "website": supplier.website, "api_endpoint": supplier.api_endpoint,
        "contact_email": creds_out.get("contact_email"), "contact_phone": creds_out.get("contact_phone"),
        "is_active": supplier.is_active, "priority": supplier.priority,
        "reliability_score": float(supplier.reliability_score),
        "supports_express": supplier.supports_express, "express_carrier": supplier.express_carrier,
        "express_base_cost_usd": float(supplier.express_base_cost_usd) if supplier.express_base_cost_usd else None,
        "avg_delivery_days_actual": None, "shipping_info": {}, "return_policy": {}, "created_at": supplier.created_at,
    }}


@app.put("/api/v1/admin/suppliers/{supplier_id}")
async def update_supplier(supplier_id: str, body: SupplierUpdateBody = None, is_active: Optional[bool] = None, priority: Optional[int] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_DATABASE_MODELS import Supplier
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    # legacy query params
    if is_active is not None: supplier.is_active = is_active
    if priority is not None: supplier.priority = priority
    if body:
        if body.name is not None: supplier.name = body.name
        if body.country is not None: supplier.country = body.country
        if body.website is not None: supplier.website = body.website
        if body.api_endpoint is not None: supplier.api_endpoint = body.api_endpoint
        if body.priority is not None: supplier.priority = body.priority
        if body.reliability_score is not None: supplier.reliability_score = body.reliability_score
        if body.is_active is not None: supplier.is_active = body.is_active
        if body.supports_express is not None: supplier.supports_express = body.supports_express
        if body.express_carrier is not None: supplier.express_carrier = body.express_carrier
        if body.express_base_cost_usd is not None: supplier.express_base_cost_usd = body.express_base_cost_usd
        # credentials JSON
        if body.contact_email is not None or body.contact_phone is not None or body.api_key is not None:
            creds = dict(supplier.credentials or {})
            if body.contact_email is not None: creds["contact_email"] = body.contact_email
            if body.contact_phone is not None: creds["contact_phone"] = body.contact_phone
            if body.api_key is not None: creds["api_key"] = body.api_key
            supplier.credentials = creds
    await db.commit()
    await db.refresh(supplier)
    creds_out = supplier.credentials or {}
    return {"message": "Supplier updated", "supplier": {
        "id": str(supplier.id), "name": supplier.name, "country": supplier.country,
        "website": supplier.website, "api_endpoint": supplier.api_endpoint,
        "contact_email": creds_out.get("contact_email"), "contact_phone": creds_out.get("contact_phone"),
        "is_active": supplier.is_active, "priority": supplier.priority,
        "reliability_score": float(supplier.reliability_score),
        "supports_express": supplier.supports_express, "express_carrier": supplier.express_carrier,
        "express_base_cost_usd": float(supplier.express_base_cost_usd) if supplier.express_base_cost_usd else None,
        "avg_delivery_days_actual": float(supplier.avg_delivery_days_actual) if supplier.avg_delivery_days_actual else None,
        "shipping_info": supplier.shipping_info or {}, "return_policy": supplier.return_policy or {}, "created_at": supplier.created_at,
    }}


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
# 13c. ADMIN APPROVALS  /api/v1/admin/approvals  (2 endpoints)
# ==============================================================================

@app.get("/api/v1/admin/approvals", tags=["Admin"])
async def list_approvals(
    status: Optional[str] = "pending",
    entity_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """List approval queue items, optionally filtered by status and entity_type."""
    stmt = (
        select(ApprovalQueue, User)
        .outerjoin(User, ApprovalQueue.requested_by == User.id)
        .order_by(ApprovalQueue.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(ApprovalQueue.status == status)
    if entity_type:
        stmt = stmt.where(ApprovalQueue.entity_type == entity_type)

    result = await db.execute(stmt)
    rows = result.all()

    count_stmt = select(func.count()).select_from(ApprovalQueue)
    if status:
        count_stmt = count_stmt.where(ApprovalQueue.status == status)
    if entity_type:
        count_stmt = count_stmt.where(ApprovalQueue.entity_type == entity_type)
    total = (await db.execute(count_stmt)).scalar()

    return {
        "total": total,
        "items": [
            {
                "id": str(aq.id),
                "entity_type": aq.entity_type,
                "entity_id": str(aq.entity_id),
                "action": aq.action,
                "payload": aq.payload,
                "status": aq.status,
                "requested_by": str(aq.requested_by) if aq.requested_by else None,
                "requester_name": requester.full_name if requester else None,
                "resolved_by": str(aq.resolved_by) if aq.resolved_by else None,
                "resolution_note": aq.resolution_note,
                "created_at": aq.created_at.isoformat() if aq.created_at else None,
                "resolved_at": aq.resolved_at.isoformat() if aq.resolved_at else None,
            }
            for aq, requester in rows
        ],
    }


class ResolveApprovalBody(BaseModel):
    decision: Literal["approved", "rejected"]
    note: Optional[str] = None


@app.post("/api/v1/admin/approvals/{approval_id}/resolve", tags=["Admin"])
async def resolve_approval(
    approval_id: str,
    body: ResolveApprovalBody,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    pii_db: AsyncSession = Depends(get_pii_db),
):
    """Approve or reject a pending approval queue item."""
    result = await pii_db.execute(
        select(ApprovalQueue).where(ApprovalQueue.id == approval_id)
    )
    aq = result.scalar_one_or_none()
    if not aq:
        raise HTTPException(status_code=404, detail="Approval item not found")
    if aq.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Already resolved — current status: '{aq.status}'",
        )

    aq.status = body.decision
    aq.resolved_by = current_user.id
    aq.resolved_at = datetime.utcnow()
    aq.resolution_note = body.note
    await pii_db.commit()

    # ── Side-effect: sync social_posts.status when a social_post is approved ──
    if aq.entity_type == "social_post" and body.decision == "approved":
        sp_result = await db.execute(
            select(SocialPost).where(SocialPost.id == aq.entity_id)
        )
        post = sp_result.scalar_one_or_none()
        if post and post.status == "pending_approval":
            post.status = "approved"
            post.approved_by = current_user.id
            post.updated_at = datetime.utcnow()
            await db.commit()

    return {
        "message":     body.decision,
        "id":          str(aq.id),
        "entity_type": aq.entity_type,
        "entity_id":   str(aq.entity_id),
    }


# ==============================================================================
# 13d. ADMIN ORDERS  /api/v1/admin/orders  (2 endpoints)
# ==============================================================================

@app.get("/api/v1/admin/orders")
async def get_all_orders_admin(
    status: Optional[str] = None,
    limit: int = 200,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
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
    db: AsyncSession = Depends(get_pii_db),
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
    _status_msg = f"הזמנה {order.order_number} עודכנה: {status_labels.get(new_status, new_status)}"
    db.add(Notification(
        user_id=order.user_id,
        type="order_update",
        title="עדכון סטטוס הזמנה",
        message=_status_msg,
    ))
    asyncio.create_task(_guarded_task(publish_notification(str(order.user_id), {"type": "order_update", "title": "עדכון סטטוס הזמנה", "message": _status_msg})))
    await db.commit()
    return {"message": "Status updated", "old": old_status, "new": new_status}


@app.post("/api/v1/admin/social/posts", status_code=201)
async def create_social_post(
    data: CreateSocialPostRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    pii_db: AsyncSession = Depends(get_pii_db),
):
    post = SocialPost(
        content=data.content,
        platforms=data.platforms,
        scheduled_at=data.schedule_time,
        status="pending_approval",
        created_by=current_user.id,
    )
    db.add(post)
    await db.flush()          # get post.id before writing to pii_db

    pii_db.add(ApprovalQueue(
        entity_type="social_post",
        entity_id=post.id,
        action="review_social_post",
        payload={
            "content":      data.content,
            "platforms":    data.platforms,
            "scheduled_at": data.schedule_time.isoformat() if data.schedule_time else None,
            "created_by":   str(current_user.id),
        },
        status="pending",
        requested_by=current_user.id,
    ))
    await db.commit()
    await pii_db.commit()

    return {
        "post_id":    str(post.id),
        "status":     post.status,
        "created_at": post.created_at,
    }


@app.get("/api/v1/admin/social/posts")
async def get_scheduled_posts(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SocialPost).order_by(SocialPost.created_at.desc())
    if status:
        stmt = stmt.where(SocialPost.status == status)
    result = await db.execute(stmt)
    posts = result.scalars().all()
    return {
        "posts": [
            {
                "id":           str(p.id),
                "content":      p.content,
                "platforms":    p.platforms,
                "status":       p.status,
                "scheduled_at": p.scheduled_at,
                "published_at": p.published_at,
                "created_by":   str(p.created_by),
                "created_at":   p.created_at,
                "updated_at":   p.updated_at,
            }
            for p in posts
        ]
    }


@app.put("/api/v1/admin/social/posts/{post_id}")
async def update_social_post(
    post_id: str,
    data: UpdateSocialPostRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SocialPost).where(SocialPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status == "published":
        raise HTTPException(status_code=400, detail="Cannot edit a published post")

    if data.content is not None:
        post.content = data.content
    if data.platforms is not None:
        post.platforms = data.platforms
    if data.schedule_time is not None:
        post.scheduled_at = data.schedule_time
    post.updated_at = datetime.utcnow()

    await db.commit()
    return {"message": "Post updated", "post_id": post_id}


@app.delete("/api/v1/admin/social/posts/{post_id}")
async def delete_social_post(
    post_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SocialPost).where(SocialPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status == "published":
        raise HTTPException(status_code=400, detail="Cannot delete a published post")

    post.status = "rejected"
    post.rejection_reason = f"Deleted by admin {current_user.id}"
    post.updated_at = datetime.utcnow()

    await db.commit()
    return {"message": "Post deleted", "post_id": post_id}


@app.post("/api/v1/admin/social/publish/{post_id}")
async def publish_social_post(
    post_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from social.telegram_publisher import publish_to_telegram

    result = await db.execute(select(SocialPost).where(SocialPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Post must be 'approved' before publishing (current status: '{post.status}')",
        )

    tg_result = await publish_to_telegram(post.content)
    if not tg_result["ok"]:
        raise HTTPException(
            status_code=502,
            detail=f"Telegram publish failed: {tg_result['error']}",
        )

    post.status = "published"
    post.published_at = datetime.utcnow()
    post.external_post_ids = {"telegram": tg_result["message_id"]}
    post.updated_at = datetime.utcnow()
    await db.commit()

    return {
        "message":             "Post published",
        "post_id":             post_id,
        "telegram_message_id": tg_result["message_id"],
        "published_at":        post.published_at,
    }


@app.get("/api/v1/admin/social/analytics")
async def get_social_analytics(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import timedelta
    rows = (await db.execute(
        select(SocialPost.status, func.count(SocialPost.id).label("cnt"))
        .group_by(SocialPost.status)
    )).all()
    counts = {r.status: r.cnt for r in rows}

    now = datetime.utcnow()
    scheduled_next_7d = (await db.execute(
        select(func.count(SocialPost.id)).where(
            and_(
                SocialPost.status.in_(["approved", "pending_approval"]),
                SocialPost.scheduled_at.between(now, now + timedelta(days=7)),
            )
        )
    )).scalar() or 0

    return {
        "counts": {
            "draft":            counts.get("draft", 0),
            "pending_approval": counts.get("pending_approval", 0),
            "approved":         counts.get("approved", 0),
            "published":        counts.get("published", 0),
            "rejected":         counts.get("rejected", 0),
        },
        "scheduled_next_7d": scheduled_next_7d,
        "followers":  {"facebook": 0, "instagram": 0, "tiktok": 0},
        "engagement": {"likes": 0, "comments": 0, "shares": 0},
    }


@app.post("/api/v1/admin/social/generate-content")
async def generate_social_content(topic: str, platform: str, tone: str = "professional", current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    agent = get_agent("social_media_manager_agent")
    content = await agent.generate_post(topic, platform, tone)
    return {"content": content, "status": "pending_approval"}


@app.get("/api/v1/admin/analytics/dashboard")
async def get_analytics_dashboard(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    users_count = (await db.execute(select(func.count(User.id)))).scalar()
    orders_count = (await db.execute(select(func.count(Order.id)))).scalar()
    revenue = (await db.execute(select(func.sum(Order.total_amount)).where(Order.status.in_(["paid", "processing", "shipped", "delivered"])))).scalar() or 0
    return {"users": users_count, "orders": orders_count, "revenue": float(revenue), "period": "all_time"}


@app.get("/api/v1/admin/analytics/sales")
async def get_sales_analytics(start_date: Optional[date] = None, end_date: Optional[date] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
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
async def get_user_analytics(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    total = (await db.execute(select(func.count(User.id)))).scalar()
    verified = (await db.execute(select(func.count(User.id)).where(User.is_verified == True))).scalar()
    return {"total_users": total, "verified_users": verified}


# ==============================================================================
# 13b. AGENTS CONTROL PANEL  /api/v1/admin/agents
# ==============================================================================

AGENTS_METADATA = {
    "router_agent": {
        "display_name": "Router Agent",
        "persona": "Avi",
        "name_he": "סוכן ניתוב",
        "description": "Automatically routes messages to the appropriate specialized agent based on intent detection.",
        "description_he": "מנתב הודעות לסוכן המתאים על פי זיהוי כוונה",
        "capabilities": ["Intent detection", "Language detection", "Confidence scoring", "Context routing"],
        "model": "qwen3:8b",
        "temperature": 0.1,
        "type": "internal",
        "icon": "GitBranch",
        "color": "gray",
    },
    "parts_finder_agent": {
        "display_name": "Parts Finder Agent",
        "persona": "Nir",
        "name_he": "סוכן חיפוש חלקים",
        "description": "Finds auto parts by vehicle, category, or part number. Identifies vehicles from Israeli license plates via gov API.",
        "description_he": "מאתר חלקי רכב לפי רכב, קטגוריה או מספר חלק. זיהוי רכב ממספר לוחית ישראלי",
        "capabilities": ["Part search", "Vehicle identification (gov.il)", "Price comparison", "Image-based part ID"],
        "model": "qwen3:8b",
        "temperature": 0.3,
        "type": "customer",
        "icon": "Search",
        "color": "blue",
    },
    "sales_agent": {
        "display_name": "Sales Agent",
        "persona": "Maya",
        "name_he": "סוכן מכירות",
        "description": "Smart upselling and cross-selling. Presents Good/Better/Best options. Never reveals supplier names.",
        "description_he": "מכירה חכמה עם Good/Better/Best. לא חושף שמות ספקים",
        "capabilities": ["Product recommendations", "Upselling", "Bundle suggestions", "Price negotiation"],
        "model": "qwen3:8b",
        "temperature": 0.7,
        "type": "customer",
        "icon": "TrendingUp",
        "color": "green",
    },
    "orders_agent": {
        "display_name": "Orders Agent",
        "persona": "Lior",
        "name_he": "סוכן הזמנות",
        "description": "Manages order lifecycle from placement to delivery. Handles cancellations and returns. Dropshipping-aware.",
        "description_he": "ניהול מחזור חיי הזמנה. ביטולים וחזרות. תואם דרופשיפינג",
        "capabilities": ["Order status", "Tracking", "Cancellation", "Returns", "Dropshipping flow"],
        "model": "qwen3:8b",
        "temperature": 0.3,
        "type": "customer",
        "icon": "Package",
        "color": "orange",
    },
    "finance_agent": {
        "display_name": "Finance Agent",
        "persona": "Tal",
        "name_he": "סוכן פיננסי",
        "description": "Handles payments, invoices, and refunds. Licensed business (מס׳ עוסק: 060633880). VAT 18%, refund policy.",
        "description_he": "תשלומים, חשבוניות, החזרים. עוסק מורשה מס׳ 060633880",
        "capabilities": ["Payment questions", "Invoice generation", "Refund calculations", "VAT breakdowns"],
        "model": "qwen3:8b",
        "temperature": 0.2,
        "type": "customer",
        "icon": "DollarSign",
        "color": "yellow",
    },
    "service_agent": {
        "display_name": "Service Agent",
        "persona": "Dana",
        "name_he": "סוכן שירות לקוחות",
        "description": "Default fallback agent. Handles general questions, complaints, and technical support with empathy.",
        "description_he": "סוכן ברירת מחדל. שאלות כלליות, תלונות, תמיכה טכנית",
        "capabilities": ["General support", "Complaint handling", "Technical questions", "Escalation"],
        "model": "qwen3:8b",
        "temperature": 0.8,
        "type": "customer",
        "icon": "HeartHandshake",
        "color": "pink",
    },
    "security_agent": {
        "display_name": "Security Agent",
        "persona": "Oren",
        "name_he": "סוכן אבטחה",
        "description": "Handles login issues, 2FA, password reset, and suspicious activity. Strict identity verification.",
        "description_he": "בעיות כניסה, 2FA, איפוס סיסמה, פעילות חשודה",
        "capabilities": ["2FA support", "Password reset", "Account unlock", "Suspicious activity"],
        "model": "qwen3:8b",
        "temperature": 0.2,
        "type": "customer",
        "icon": "Shield",
        "color": "red",
    },
    "marketing_agent": {
        "display_name": "Marketing Agent",
        "persona": "Shira",
        "name_he": "סוכן שיווק",
        "description": "Manages promotions, coupons, referral program (100₪ + 10%), and loyalty points.",
        "description_he": "קופונים, תוכנית הפניות (100₪ + 10%), נקודות נאמנות",
        "capabilities": ["Coupon management", "Referral program", "Loyalty points", "Newsletter"],
        "model": "qwen3:8b",
        "temperature": 0.7,
        "type": "customer",
        "icon": "Megaphone",
        "color": "purple",
    },
    "supplier_manager_agent": {
        "display_name": "Supplier Manager Agent",
        "persona": "Boaz",
        "name_he": "סוכן ניהול ספקים",
        "description": "Background agent. Daily price sync at 02:00. Manages 3 active suppliers. Does NOT interact with customers.",
        "description_he": "סוכן רקע. סנכרון מחירים יומי 02:00. לא משוחח עם לקוחות",
        "capabilities": ["Price sync", "Catalog updates", "Availability monitoring", "Supplier performance"],
        "model": "qwen3:8b",
        "temperature": 0.1,
        "type": "admin",
        "icon": "Truck",
        "color": "indigo",
    },
    "social_media_manager_agent": {
        "display_name": "Social Media Manager Agent",
        "persona": "Noa",
        "name_he": "סוכן מנהל מדיה חברתית",
        "description": "Generates content for Facebook, Instagram, TikTok, LinkedIn, Telegram. All posts need approval before publish.",
        "description_he": "יצירת תוכן לפייסבוק, אינסטגרם, טיקטוק, לינקדאין, טלגרם",
        "capabilities": ["Content generation", "Post scheduling", "Platform-specific tone", "Hashtag generation"],
        "model": "qwen3:8b",
        "temperature": 0.9,
        "type": "admin",
        "icon": "Share2",
        "color": "teal",
    },
}

@app.get("/api/v1/admin/agents")
async def list_agents(current_user: User = Depends(get_current_admin_user)):
    from BACKEND_AI_AGENTS import AGENT_MAP
    hf_token = os.getenv("HF_TOKEN", "")
    ai_status = "active" if hf_token else "mocked"

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
        "hf_configured": bool(hf_token),
    }


@app.post("/api/v1/admin/agents/{agent_name}/test")
async def test_agent(
    agent_name: str,
    body: dict,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
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


@app.put("/api/v1/admin/agents/{agent_name}")
async def update_agent(
    agent_name: str,
    body: dict,
    current_user: User = Depends(get_current_admin_user),
):
    if agent_name not in AGENTS_METADATA:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
    allowed = {"display_name", "persona", "name_he", "description", "description_he", "model", "temperature", "capabilities", "enabled"}
    for k, v in body.items():
        if k in allowed:
            AGENTS_METADATA[agent_name][k] = v
    # Propagate model + temperature changes to live singleton (affects real agent calls)
    from BACKEND_AI_AGENTS import _agents
    if agent_name in _agents:
        if "model" in body:
            _agents[agent_name].model = body["model"]
        if "temperature" in body:
            _agents[agent_name].temperature = float(body["temperature"])
    return {"agent": agent_name, **AGENTS_METADATA[agent_name]}


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


# 13a. ADMIN JOB FAILURES  /api/v1/admin/job-failures  (2 endpoints)

@app.get("/api/v1/admin/job-failures")
async def list_job_failures(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
    status: Optional[str] = None,
    job_name: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """List dead-letter queue (job failures) with optional filtering.
    
    Query params:
      status: 'pending' | 'retrying' | 'resolved' (optional)
      job_name: filter by job name (optional)
      limit: rows to fetch (default 100, max 1000)
      offset: pagination offset (default 0)
    
    Returns: {failures: [...], total: int}
    """
    from BACKEND_DATABASE_MODELS import JobFailure
    
    limit = min(limit, 1000)
    query = select(JobFailure)
    
    if status:
        query = query.where(JobFailure.status == status)
    if job_name:
        query = query.where(JobFailure.job_name == job_name)
    
    # Get total count
    total = (await db.execute(select(func.count(JobFailure.id)).filter(query.whereclause if hasattr(query, 'whereclause') else None))).scalar() or 0
    
    # Fetch paginated results, sorted by created_at DESC
    query = query.order_by(JobFailure.created_at.desc()).limit(limit).offset(offset)
    results = (await db.execute(query)).scalars().all()
    
    failures = []
    for f in results:
        failures.append({
            "id": str(f.id),
            "job_name": f.job_name,
            "status": f.status,
            "attempts": f.attempts,
            "error": f.error[:200] if f.error else None,  # truncate for readability
            "next_retry_at": f.next_retry_at.isoformat() if f.next_retry_at else None,
            "created_at": f.created_at.isoformat(),
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
            "resolved_by": f.resolved_by,
        })
    
    return {
        "failures": failures,
        "total": total,
        "fetched": len(failures),
    }


@app.post("/api/v1/admin/job-failures/{job_id}/retry")
async def retry_job_failure(
    job_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Manually retry a failed job.
    
    Sets status='retrying' and next_retry_at=NOW (immediate retry).
    Returns the updated job failure record.
    """
    from BACKEND_DATABASE_MODELS import JobFailure
    from uuid import UUID as PyUUID
    
    try:
        job_uuid = PyUUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    result = await db.execute(select(JobFailure).where(JobFailure.id == job_uuid))
    failure = result.scalar_one_or_none()
    
    if not failure:
        raise HTTPException(status_code=404, detail="Job failure not found")
    
    # Update for immediate retry
    failure.status = "retrying"
    failure.next_retry_at = datetime.utcnow()
    failure.attempts += 1
    
    db.add(failure)
    await db.commit()
    
    return {
        "id": str(failure.id),
        "job_name": failure.job_name,
        "status": failure.status,
        "attempts": failure.attempts,
        "next_retry_at": failure.next_retry_at.isoformat(),
        "message": f"Job {failure.job_name} (ID: {job_id}) scheduled for immediate retry",
    }


# ==============================================================================
# 14. SYSTEM  /api/v1/system  (3 endpoints)
# ==============================================================================

@app.get("/api/v1/system/health")
async def health_check():
    import time as _time
    results: dict = {}

    # ── PostgreSQL catalog ────────────────────────────────────────────────────
    try:
        _t = _time.monotonic()
        async with async_session_factory() as _db:
            await _db.execute(text("SELECT 1"))
        results["postgres_catalog"] = {"status": "ok", "latency_ms": round((_time.monotonic() - _t) * 1000, 1)}
    except Exception as _e:
        results["postgres_catalog"] = {"status": "error", "error": str(_e)}

    # ── PostgreSQL PII ────────────────────────────────────────────────────────
    try:
        _t = _time.monotonic()
        async with pii_session_factory() as _db:
            await _db.execute(text("SELECT 1"))
        results["postgres_pii"] = {"status": "ok", "latency_ms": round((_time.monotonic() - _t) * 1000, 1)}
    except Exception as _e:
        results["postgres_pii"] = {"status": "error", "error": str(_e)}

    # ── Redis ─────────────────────────────────────────────────────────────────
    try:
        _r = await get_redis()
        if _r is None:
            raise RuntimeError("redis_unavailable")
        await _r.ping()
        results["redis"] = {"status": "ok"}
    except Exception as _e:
        results["redis"] = {"status": "error", "error": str(_e)}

    # ── Meilisearch ───────────────────────────────────────────────────────────
    _meili_url = os.getenv("MEILI_URL", "")
    if _meili_url:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=3) as _hc:
                _resp = await _hc.get(f"{_meili_url}/health")
            results["meilisearch"] = {"status": "ok"} if _resp.status_code == 200 else {"status": "error", "code": _resp.status_code}
        except Exception as _e:
            results["meilisearch"] = {"status": "error", "error": str(_e)}
    else:
        results["meilisearch"] = {"status": "ok", "note": "not_configured"}

    # ── Hugging Face Inference API ────────────────────────────────────────────
    _hf_token = os.getenv("HF_TOKEN", "")
    if _hf_token:
        results["huggingface"] = {"status": "ok"}
    else:
        results["huggingface"] = {"status": "error", "error": "HF_TOKEN not configured"}

    # ── ClamAV ────────────────────────────────────────────────────────────────
    try:
        _clam_ok = False
        for _make_scanner in (
            lambda: _clamd.ClamdUnixSocket(),
            lambda: _clamd.ClamdNetworkSocket(host=os.getenv("CLAMD_HOST", "clamav"), port=3310),
        ):
            try:
                _make_scanner().ping()
                _clam_ok = True
                break
            except Exception:
                continue
        results["clamav"] = {"status": "ok"} if _clam_ok else {"status": "error", "error": "daemon unreachable"}
    except Exception as _e:
        results["clamav"] = {"status": "error", "error": str(_e)}

    # ── Stripe ────────────────────────────────────────────────────────────────
    _stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if _stripe_key and not _stripe_key.startswith("sk_test_xxxxx"):
        results["stripe"] = {"status": "ok"}
    else:
        results["stripe"] = {"status": "error", "error": "key not configured"}

    # ── Aggregate ─────────────────────────────────────────────────────────────
    critical = ["postgres_catalog", "postgres_pii"]
    critical_ok = all(results.get(s, {}).get("status") == "ok" for s in critical)
    all_ok = all(v.get("status") == "ok" for v in results.values())
    if all_ok:
        overall = "healthy"
    elif critical_ok:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return {
        "status": overall,
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "services": results,
    }


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


@app.post("/api/v1/admin/orders/fulfill-stuck", tags=["Admin – Orders"])
async def admin_fulfill_stuck_orders(db: AsyncSession = Depends(get_pii_db), current_user: User = Depends(get_current_admin_user)):
    """Admin: manually re-trigger supplier fulfillment for all paid/processing orders."""
    result = await db.execute(select(Order).where(Order.status.in_(["paid", "processing"])))
    stuck = result.scalars().all()
    if not stuck:
        return {"message": "No stuck orders found", "count": 0}
    await trigger_supplier_fulfillment(stuck, db)
    await db.commit()
    return {"message": f"Fulfillment triggered for {len(stuck)} order(s)", "count": len(stuck), "orders": [o.order_number for o in stuck]}


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

    asyncio.create_task(_guarded_task(_run()))
    return {"status": "started", "message": "Price sync triggered in background"}


@app.get("/api/v1/system/settings")
async def get_public_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemSetting).where(SystemSetting.is_public == True))
    settings = result.scalars().all()
    return {s.key: s.value for s in settings}


@app.get("/api/v1/system/version")
async def get_version():
    return {"version": "1.0.0", "build": "2026.02.28", "environment": os.getenv("ENVIRONMENT", "development")}


@app.get("/api/v1/system/metrics")
async def get_system_metrics(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Real-time operational health snapshot for admins."""
    rows = (await db.execute(text("""
        SELECT
            COUNT(*)                                                          AS total_parts,
            COUNT(*) FILTER (WHERE is_available)                             AS active_parts,
            COUNT(*) FILTER (WHERE needs_oem_lookup)                         AS pending_enrichment
        FROM parts_catalog
    """))).fetchone()

    embed_pending = (await db.execute(text(
        "SELECT COUNT(*) FROM parts_images WHERE embedding IS NULL"
    ))).scalar()

    approval_pending = (await db.execute(text(
        "SELECT COUNT(*) FROM approval_queue WHERE status = 'pending'"
    ))).scalar()

    search_misses = (await db.execute(text(
        "SELECT COUNT(*) FROM search_misses WHERE triggered_scrape = FALSE"
    ))).scalar()

    bulk_deals = (await db.execute(text(
        "SELECT COUNT(*) FROM approval_queue WHERE entity_type = 'bulk_deal' AND status = 'pending'"
    ))).scalar()

    # Queue monitoring: detect stuck jobs (running > TTL without heartbeat)
    stuck_jobs = (await db.execute(text("""
        SELECT
            COUNT(*)                                       AS stuck_count,
            ARRAY_AGG(job_name)                           AS job_names,
            ARRAY_AGG(EXTRACT(EPOCH FROM (NOW() - last_heartbeat_at))::INTEGER) AS stale_seconds
        FROM job_registry
        WHERE status = 'running'
          AND ttl_seconds IS NOT NULL
          AND (NOW() - last_heartbeat_at) > (ttl_seconds * INTERVAL '1 second')
    """))).fetchone()

    stuck_details = {
        "count": stuck_jobs.stuck_count or 0,
        "jobs": [],
    }
    if stuck_jobs and stuck_jobs.stuck_count and stuck_jobs.stuck_count > 0:
        for job_name, stale_sec in zip(stuck_jobs.job_names or [], stuck_jobs.stale_seconds or []):
            stuck_details["jobs"].append({
                "name": job_name,
                "stale_seconds": stale_sec,
            })

    from db_update_agent import _last_report, _agent_running
    return {
        "catalog": {
            "total_parts":        rows.total_parts if rows else 0,
            "active_parts":       rows.active_parts if rows else 0,
            "pending_enrichment": rows.pending_enrichment if rows else 0,
            "pending_embedding":  embed_pending,
        },
        "queues": {
            "approval_pending":           approval_pending,
            "bulk_deals_pending":         bulk_deals,
            "search_misses_untriggered": search_misses,
        },
        "workers": {
            "db_agent_running":     _agent_running,
            "db_agent_last_report": _last_report,
        },
        "jobs": stuck_details,  # Queue monitoring
    }


# ==============================================================================
# EVENTS & ERROR HANDLERS
# ==============================================================================

_SEARCH_MISS_NOTIFY_INTERVAL = 3600  # seconds — 60 minutes


async def _notify_search_miss_loop() -> None:
    """Background loop: notify users when a previously-missed search now has results.
    Runs every 60 minutes. Writes Notifications to autospare_pii.
    Marks rows notified=TRUE in autospare (catalog DB).
    """
    await asyncio.sleep(30)   # brief startup delay
    while True:
        try:
            async with async_session_factory() as cat_db:
                rows = (await cat_db.execute(
                    text("""
                        SELECT id, query, user_id
                        FROM search_misses
                        WHERE triggered_scrape = TRUE
                          AND notified         = FALSE
                          AND user_id          IS NOT NULL
                        ORDER BY last_seen_at DESC
                        LIMIT 100
                    """)
                )).fetchall()

            if rows:
                notified_ids = []
                async with pii_session_factory() as pii_db:
                    for row in rows:
                        _sm_title = "🔍 מצאנו חלקים חדשים!"
                        _sm_msg = (
                            f"מצאנו חלקים חדשים התואמים לחיפוש שלך! "
                            f"חפש שוב: {row.query}"
                        )
                        pii_db.add(Notification(
                            user_id=row.user_id,
                            type="search_miss_resolved",
                            title=_sm_title,
                            message=_sm_msg,
                            data={"query": row.query, "search_miss_id": str(row.id)},
                        ))
                        asyncio.create_task(_guarded_task(publish_notification(str(row.user_id), {"type": "search_miss_resolved", "title": _sm_title, "message": _sm_msg})))
                        notified_ids.append(str(row.id))
                    await pii_db.commit()

                async with async_session_factory() as cat_db:
                    await cat_db.execute(
                        text("""
                            UPDATE search_misses
                            SET notified = TRUE
                            WHERE id = ANY(:ids::uuid[])
                        """),
                        {"ids": notified_ids},
                    )
                    await cat_db.commit()

                print(f"[search_miss_notify] notified {len(notified_ids)} users")

        except Exception as e:
            error_msg = str(e)[:500]
            print(f"[search_miss_notify] error (non-fatal): {error_msg}")
            # Log failure to DLQ (Gap 2b: Worker integration)
            try:
                async with pii_session_factory() as pii_db:
                    from resilience import log_job_failure
                    await log_job_failure(
                        pii_db,
                        job_name="notify_search_misses",
                        error=error_msg,
                        payload={},
                        attempts=1,
                    )
            except Exception as dlq_err:
                print(f"[search_miss_notify] Failed to log to DLQ: {dlq_err}")

        await asyncio.sleep(_SEARCH_MISS_NOTIFY_INTERVAL)


# How often the VIP detection + stats sync runs (default: every 24 hours)
VIP_DETECTION_INTERVAL_S = int(os.getenv("VIP_DETECTION_INTERVAL_S", "86400"))
# Thresholds for automatic VIP promotion
VIP_MIN_ORDERS = int(os.getenv("VIP_MIN_ORDERS",    "5"))
VIP_MIN_SPENT  = int(os.getenv("VIP_MIN_SPENT_ILS", "2000"))


async def _vip_detection_loop() -> None:
    """
    Background loop (every 24 h):
      1. Sync total_orders + total_spent_ils for ALL users from orders table.
      2. Promote users to VIP where (total_orders >= VIP_MIN_ORDERS OR
         total_spent_ils >= VIP_MIN_SPENT) AND is_vip = FALSE.
      3. Send Notification + SSE to newly-promoted VIP users.
    """
    await asyncio.sleep(60)  # brief startup delay
    while True:
        try:
            async with pii_session_factory() as pii_db:
                # ── 1. Sync order stats for all users ──────────────────────────────────
                await pii_db.execute(text("""
                    UPDATE user_profiles up
                    SET
                        total_orders    = agg.cnt,
                        total_spent_ils = agg.spent,
                        updated_at      = NOW()
                    FROM (
                        SELECT
                            user_id,
                            COUNT(*)                       AS cnt,
                            COALESCE(SUM(total_amount), 0) AS spent
                        FROM orders
                        WHERE status NOT IN ('cancelled', 'refunded')
                        GROUP BY user_id
                    ) agg
                    WHERE up.user_id = agg.user_id
                """))

                # ── 2. Find newly-qualifying VIP users ───────────────────────────────
                rows = (await pii_db.execute(text("""
                    SELECT up.user_id, u.full_name, u.phone,
                           up.total_orders, up.total_spent_ils
                    FROM user_profiles up
                    JOIN users u ON u.id = up.user_id
                    WHERE up.is_vip = FALSE
                      AND (
                            up.total_orders    >= :min_orders
                         OR up.total_spent_ils >= :min_spent
                      )
                """), {"min_orders": VIP_MIN_ORDERS, "min_spent": VIP_MIN_SPENT})).fetchall()

                if rows:
                    # ── 3. Promote + notify ────────────────────────────────────────────
                    new_vip_ids = [str(r.user_id) for r in rows]
                    await pii_db.execute(text("""
                        UPDATE user_profiles
                        SET is_vip     = TRUE,
                            vip_since  = NOW(),
                            updated_at = NOW()
                        WHERE user_id = ANY(:ids::uuid[])
                          AND is_vip  = FALSE
                    """), {"ids": new_vip_ids})

                    for row in rows:
                        _vip_title = "🏆 ברוך הבא למועדון הVIP של Auto Spare!"
                        _vip_msg   = (
                            f"שלום {row.full_name}! הפכת ללקוח VIP! "
                            f"קבל הנחות מיוחדות, משלוח מהיר עדיפות ושירות אישי. "
                            f"סה\"\"\u05db הזמנות: {row.total_orders} | "
                            f"סה\"\"\u05db קניות: ₪{float(row.total_spent_ils):.0f}"
                        )
                        pii_db.add(Notification(
                            user_id=row.user_id,
                            type="vip_promotion",
                            title=_vip_title,
                            message=_vip_msg,
                            data={
                                "total_orders": row.total_orders,
                                "total_spent_ils": float(row.total_spent_ils),
                                "vip_since": datetime.utcnow().isoformat(),
                            },
                        ))
                        asyncio.create_task(publish_notification(
                            str(row.user_id),
                            {"type": "vip_promotion", "title": _vip_title, "message": _vip_msg},
                        ))

                    await pii_db.commit()
                    print(f"[VIP] Promoted {len(rows)} user(s) to VIP: {new_vip_ids}")
                else:
                    await pii_db.commit()
                    print("[VIP] Stats synced, no new VIP promotions")

        except Exception as e:
            print(f"[VIP detection] error (non-fatal): {e}")

        await asyncio.sleep(VIP_DETECTION_INTERVAL_S)


@app.on_event("startup")
async def startup():
    from catalog_scraper import start_scraper_task
    from db_update_agent import start_agent_task as start_db_agent
    print("🚀 Auto Spare API starting...")
    print(f"   Environment: {os.getenv('ENVIRONMENT', 'development')}")
    # Ensure the WhatsApp sentinel user exists (anonymous conversations fallback)
    async with pii_session_factory() as _db:
        await _db.execute(text("""
            INSERT INTO users (id, email, phone, password_hash, full_name, role,
                               is_active, is_verified, is_admin, failed_login_count)
            VALUES ('00000000-0000-0000-0000-000000000001',
                    'whatsapp@autospare.internal', '+00000000000000',
                    '!disabled!', 'WhatsApp Bot', 'system', true, true, false, 0)
            ON CONFLICT (id) DO NOTHING
        """))
        await _db.commit()
    # QUEUE ARCHITECTURE: No external message broker (no Celery/RQ).
    # All async work uses asyncio.create_task() + Semaphore(50) cap.
    # ApprovalQueue table = admin approval workflow (not a message queue).
    # Upgrade to Celery/Redis Streams when scaling beyond single VPS.
    asyncio.create_task(_price_sync_loop())
    asyncio.create_task(_stuck_orders_monitor_loop())   # ← periodic stuck-order monitor (every 30 min)
    asyncio.create_task(_notify_search_miss_loop())     # ← search-miss user notifications (every 60 min)
    asyncio.create_task(_abandoned_cart_loop())         # ← abandoned-cart WhatsApp re-engagement (every 60 min)
    asyncio.create_task(_pending_payment_reminder_loop())  # ← pending-payment WhatsApp reminder (every 30 min)
    asyncio.create_task(_health_monitor_loop())            # ← service health monitoring + admin alerting (every 5 min)
    asyncio.create_task(_vip_detection_loop())             # ← VIP promotion + order stats sync (every 24 h)
    asyncio.create_task(_backup_loop())                    # ← pg_dump autospare + autospare_pii (every 24 h)
    start_scraper_task()           # ← catalog scraper background loop
    start_db_agent(get_db, 6.0)   # ← DB cleaning / normalisation agent (every 6h)
    print("✅ All systems ready — price-sync + catalog-scraper + db-agent schedulers started")


# How many hours before an order in paid/processing is considered stuck
STUCK_ORDER_HOURS = int(os.getenv("STUCK_ORDER_HOURS", "4"))
STUCK_ORDER_CHECK_INTERVAL_MIN = 30  # check every 30 minutes

# How often the abandoned-cart worker runs (default: every 60 minutes)
ABANDONED_CART_INTERVAL_S = int(os.getenv("ABANDONED_CART_INTERVAL_S", "3600"))
# How long a cart must be idle before it is considered abandoned (default: 2 hours)
ABANDONED_CART_IDLE_HOURS = int(os.getenv("ABANDONED_CART_IDLE_HOURS", "2"))

# How often the pending-payment reminder runs (default: every 30 min)
PAYMENT_REMINDER_INTERVAL_S = int(os.getenv("PAYMENT_REMINDER_INTERVAL_S", "1800"))
# Minimum age of a pending_payment order before first reminder (default: 1 hour)
PAYMENT_REMINDER_AFTER_H    = int(os.getenv("PAYMENT_REMINDER_AFTER_H", "1"))

# How often the health monitor probes all services (default: every 5 min)
HEALTH_MONITOR_INTERVAL_S = int(os.getenv("HEALTH_MONITOR_INTERVAL_S", "300"))


async def _stuck_orders_monitor_loop():
    """
    Background loop: runs every 30 minutes.

    Pass 1 — Stuck fulfillment:
      Finds orders in 'paid' or 'processing' for > STUCK_ORDER_HOURS hours
      (payment confirmed but supplier order never placed) and re-triggers the
      OrdersAgent to place the supplier order.

    Pass 2 — Shipment tracking:
      Finds orders in 'supplier_ordered' or 'shipped' and asks the OrdersAgent
      whether enough transit time has elapsed to advance the status:
        supplier_ordered → shipped  (after carrier-specific days)
        shipped          → delivered (after carrier-specific days)
      Notifies the customer on every transition.
    """
    from BACKEND_AI_AGENTS import OrdersAgent as _OrdersAgent
    await asyncio.sleep(5)  # let DB pool warm up on startup
    while True:
        now = datetime.utcnow()
        # ── Pass 1: stuck fulfillment (paid/processing > 4 h) ────────────────
        try:
            cutoff = now - timedelta(hours=STUCK_ORDER_HOURS)
            async with pii_session_factory() as db:
                result = await db.execute(
                    select(Order).where(
                        Order.status.in_(["paid", "processing"]),
                        Order.updated_at <= cutoff,
                    )
                )
                stuck = result.scalars().all()
                if stuck:
                    print(f"[OrderMonitor] Found {len(stuck)} order(s) stuck > {STUCK_ORDER_HOURS}h — triggering fulfillment...")
                    await trigger_supplier_fulfillment(stuck, db)

                    admins_res = await db.execute(select(User).where(User.is_admin == True))
                    admins = admins_res.scalars().all()
                    order_list = ", ".join(o.order_number for o in stuck)
                    _stuck_title = f"🤖 סוכן הזמנות: {len(stuck)} הזמנות תקועות טופלו אוטומטית"
                    _stuck_msg = (
                        f"הסוכן זיהה {len(stuck)} הזמנה/ות שתקועות מעל {STUCK_ORDER_HOURS} שעות "
                        f"במצב 'ממתין לספק' ופעל אוטומטית להמשך הטיפול.\n"
                        f"הזמנות: {order_list}"
                    )
                    for admin in admins:
                        db.add(Notification(
                            user_id=admin.id,
                            type="system",
                            title=_stuck_title,
                            message=_stuck_msg,
                            data={
                                "stuck_orders": [o.order_number for o in stuck],
                                "stuck_hours": STUCK_ORDER_HOURS,
                                "auto_handled": True,
                            },
                        ))
                        asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "system", "title": _stuck_title, "message": _stuck_msg})))
                    await db.commit()
                    print(f"[OrderMonitor] ✅ Auto-fulfilled: {order_list}")
                else:
                    print(f"[OrderMonitor] Pass 1: no stuck orders (threshold: {STUCK_ORDER_HOURS}h).")
        except Exception as e:
            print(f"[OrderMonitor] Pass 1 error: {e}")

        # ── Pass 2: shipment status tracking ─────────────────────────────────
        try:
            async with pii_session_factory() as db:
                result = await db.execute(
                    select(Order).where(
                        Order.status.in_(["supplier_ordered", "shipped"]),
                        Order.tracking_number.isnot(None),
                    )
                )
                in_transit = result.scalars().all()
                if not in_transit:
                    print("[OrderMonitor] Pass 2: no in-transit orders to check.")
                else:
                    agent = _OrdersAgent()
                    advanced: list[str] = []
                    for order in in_transit:
                        new_status = await agent.advance_shipment_status(order, db, now=now)
                        if new_status:
                            advanced.append(f"{order.order_number} → {new_status}")

                    if advanced:
                        # Admin notification summarising all transitions
                        admins_res = await db.execute(select(User).where(User.is_admin == True))
                        admins = admins_res.scalars().all()
                        summary = "\n".join(f"  • {a}" for a in advanced)
                        _ship_title = f"📦 עדכון משלוחים: {len(advanced)} הזמנות עודכנו"
                        _ship_msg = f"הסוכן עדכן סטטוס עבור {len(advanced)} הזמנות:\n{summary}"
                        for admin in admins:
                            db.add(Notification(
                                user_id=admin.id,
                                type="system",
                                title=_ship_title,
                                message=_ship_msg,
                                data={"advanced": advanced, "auto_tracked": True},
                            ))
                            asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "system", "title": _ship_title, "message": _ship_msg})))
                        await db.commit()
                        print(f"[OrderMonitor] Pass 2: advanced {len(advanced)} order(s): {', '.join(advanced)}")
                    else:
                        print(f"[OrderMonitor] Pass 2: {len(in_transit)} in-transit order(s), none ready to advance.")
        except Exception as e:
            print(f"[OrderMonitor] Pass 2 error: {e}")

        await asyncio.sleep(STUCK_ORDER_CHECK_INTERVAL_MIN * 60)


# ── Health monitor loop ─────────────────────────────────────────────────────
async def _health_monitor_loop():
    """
    Background loop: runs every HEALTH_MONITOR_INTERVAL_S seconds (default 5 min).

    Probes all 7 external services. Tracks previous state per service.
    On service DOWN:      notifies all admins via WhatsApp + Notification row + SSE.
    On service RESTORED:  notifies all admins the same way.
    Never sends the same alert twice in a row for the same service.
    """
    import httpx as _httpx
    from social.whatsapp_provider import get_whatsapp_provider

    await asyncio.sleep(20)  # let DB pool warm up on startup

    _prev_states: dict = {}  # service_name → "ok" | "error"

    SERVICE_LABELS = {
        "postgres_catalog": "PostgreSQL Catalog",
        "postgres_pii":     "PostgreSQL PII",
        "redis":            "Redis",
        "meilisearch":      "Meilisearch",
        "huggingface":      "Hugging Face",
        "clamav":           "ClamAV",
        "stripe":           "Stripe",
    }

    async def _probe() -> dict:
        states: dict = {}

        try:
            async with async_session_factory() as _db:
                await _db.execute(text("SELECT 1"))
            states["postgres_catalog"] = "ok"
        except Exception:
            states["postgres_catalog"] = "error"

        try:
            async with pii_session_factory() as _db:
                await _db.execute(text("SELECT 1"))
            states["postgres_pii"] = "ok"
        except Exception:
            states["postgres_pii"] = "error"

        try:
            _r = await get_redis()
            if _r is None:
                raise RuntimeError("redis_unavailable")
            await _r.ping()
            states["redis"] = "ok"
        except Exception:
            states["redis"] = "error"

        _meili_url = os.getenv("MEILI_URL", "")
        if _meili_url:
            try:
                async with _httpx.AsyncClient(timeout=3) as _hc:
                    _resp = await _hc.get(f"{_meili_url}/health")
                states["meilisearch"] = "ok" if _resp.status_code == 200 else "error"
            except Exception:
                states["meilisearch"] = "error"
        else:
            states["meilisearch"] = "ok"

        _hf_token = os.getenv("HF_TOKEN", "")
        states["huggingface"] = "ok" if _hf_token else "error"

        _clam_ok = False
        for _make_scanner in (
            lambda: _clamd.ClamdUnixSocket(),
            lambda: _clamd.ClamdNetworkSocket(host=os.getenv("CLAMD_HOST", "clamav"), port=3310),
        ):
            try:
                _make_scanner().ping()
                _clam_ok = True
                break
            except Exception:
                continue
        states["clamav"] = "ok" if _clam_ok else "error"

        _stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
        states["stripe"] = "ok" if (_stripe_key and not _stripe_key.startswith("sk_test_xxxxx")) else "error"

        return states

    while True:
        try:
            current_states = await _probe()
            provider = get_whatsapp_provider()

            for svc, state in current_states.items():
                prev = _prev_states.get(svc)
                if prev is None:
                    # First pass — record state silently, warn if already down
                    _prev_states[svc] = state
                    if state == "error":
                        print(f"[HealthMonitor] Startup: {svc} is DOWN")
                    continue

                if prev == state:
                    continue  # no change — no alert

                label = SERVICE_LABELS.get(svc, svc)
                if state == "error":
                    _title = f"\U0001f534 שירות {label} נפל!"
                    _msg   = f"שירות {label} אינו זמין. בדוק את המערכת בהקדם."
                    _notif_type = "service_down"
                    print(f"[HealthMonitor] \u26a0\ufe0f  {svc} went DOWN")
                else:
                    _title = f"\u2705 שירות {label} חזר לעבוד"
                    _msg   = f"שירות {label} חזר לפעול נורמלית."
                    _notif_type = "service_restored"
                    print(f"[HealthMonitor] \u2705  {svc} RESTORED")

                _prev_states[svc] = state

                try:
                    async with pii_session_factory() as db:
                        admins_res = await db.execute(select(User).where(User.is_admin == True))
                        admins = admins_res.scalars().all()
                        for admin in admins:
                            db.add(Notification(
                                user_id=admin.id,
                                type=_notif_type,
                                title=_title,
                                message=_msg,
                                channel="whatsapp",
                                data={"service": svc, "state": state},
                                sent_at=datetime.utcnow(),
                            ))
                            asyncio.create_task(
                                publish_notification(str(admin.id), {
                                    "type":    _notif_type,
                                    "title":   _title,
                                    "message": _msg,
                                })
                            )
                            if admin.phone and str(admin.id) != str(WHATSAPP_ANON_USER_ID):
                                wa_result = await provider.send_message(to=admin.phone, body=f"{_title}\n{_msg}")
                                if not wa_result.get("ok"):
                                    print(f"[HealthMonitor] WhatsApp failed for admin {admin.id}: {wa_result.get('error')}")
                        await db.commit()
                except Exception as _e:
                    print(f"[HealthMonitor] Notify error for {svc}: {_e}")

            down = [s for s, v in current_states.items() if v == "error"]
            if down:
                print(f"[HealthMonitor] Pass complete — DOWN: {', '.join(down)}")
            else:
                print("[HealthMonitor] Pass complete — all services OK")

            # ── Threshold checks (Gap 3 — Alerting) ────────────────────────────────
            # Check 1: parts_updated < 100 in last 6 hours (catalog stagnation)
            try:
                async with async_session_factory() as _db:
                    cutoff_6h = datetime.utcnow() - timedelta(hours=6)
                    parts_updated_6h = (await _db.execute(
                        select(func.count(SystemLog.id)).where(
                            SystemLog.logger_name == "catalog_scraper",
                            SystemLog.level.in_(["INFO", "WARNING"]),
                            SystemLog.created_at >= cutoff_6h,
                        )
                    )).scalar() or 0
                    
                    if parts_updated_6h < 100:
                        _alert_title = "⚠️  קטלוג: עדכונים נמוכים בשעות האחרונות"
                        _alert_msg = (
                            f"קטלוג עלומים {parts_updated_6h} עדכונים בשעות ה-6 האחרונות (יעד: 100+). "
                            f"בדוק את ה scraper."
                        )
                        print(f"[HealthMonitor] ALERT: parts_updated={parts_updated_6h} < 100 in 6h")
                        
                        async with pii_session_factory() as _pii_db:
                            admins_res = await _pii_db.execute(select(User).where(User.is_admin == True))
                            admins = admins_res.scalars().all()
                            for admin in admins:
                                _pii_db.add(Notification(
                                    user_id=admin.id,
                                    type="threshold_alert",
                                    title=_alert_title,
                                    message=_alert_msg,
                                    channel="whatsapp",
                                    data={"threshold_type": "catalog_stagnation", "parts_updated": parts_updated_6h},
                                ))
                                asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {
                                    "type": "threshold_alert",
                                    "title": _alert_title,
                                    "message": _alert_msg,
                                })))
                            await _pii_db.commit()
            except Exception as _e:
                print(f"[HealthMonitor] Threshold check 1 error: {_e}")

            # Check 2: error_rate > 5% in last 1 hour
            try:
                async with async_session_factory() as _db:
                    cutoff_1h = datetime.utcnow() - timedelta(hours=1)
                    log_stats = (await _db.execute(
                        select(
                            func.count(SystemLog.id).label("total"),
                            func.count(SystemLog.id).filter(SystemLog.level == "ERROR").label("errors"),
                        ).where(
                            SystemLog.created_at >= cutoff_1h,
                            SystemLog.logger_name.in_(["api_routes", "agents", "scraper"]),
                        )
                    )).fetchone()
                    
                    total = log_stats.total if log_stats else 0
                    errors = log_stats.errors if log_stats else 0
                    error_rate = (errors / total * 100) if total > 0 else 0
                    
                    if error_rate > 5.0:
                        _alert_title = f"🚨 שגיאות גבוהות: {error_rate:.1f}% בשעה האחרונה"
                        _alert_msg = (
                            f"שיעור שגיאות {error_rate:.1f}% עולה על הסף (5%). "
                            f"בדוק לוגים: {errors}/{total} שגיאות בשעה האחרונה."
                        )
                        print(f"[HealthMonitor] ALERT: error_rate={error_rate:.1f}% > 5%")
                        
                        async with pii_session_factory() as _pii_db:
                            admins_res = await _pii_db.execute(select(User).where(User.is_admin == True))
                            admins = admins_res.scalars().all()
                            for admin in admins:
                                _pii_db.add(Notification(
                                    user_id=admin.id,
                                    type="threshold_alert",
                                    title=_alert_title,
                                    message=_alert_msg,
                                    channel="whatsapp",
                                    data={"threshold_type": "error_rate", "error_rate": error_rate, "errors": errors, "total": total},
                                ))
                                asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {
                                    "type": "threshold_alert",
                                    "title": _alert_title,
                                    "message": _alert_msg,
                                })))
                            await _pii_db.commit()
            except Exception as _e:
                print(f"[HealthMonitor] Threshold check 2 error: {_e}")

            # Check 3: worker silent > 2 hours (no recent heartbeat from db_update_agent)
            try:
                from db_update_agent import _last_report
                if _last_report:
                    last_heartbeat = datetime.fromisoformat(_last_report) if isinstance(_last_report, str) else _last_report
                    silence_mins = (datetime.utcnow() - last_heartbeat).total_seconds() / 60
                    
                    if silence_mins > 120:  # 2 hours
                        _alert_title = "⏱️  Worker db_update_agent: שקט למעל 2 שעות"
                        _alert_msg = (
                            f"העובד db_update_agent לא שלח heartbeat במשך {silence_mins:.0f} דקות. "
                            f"אם הוא צריך לרוץ הוא אולי תקוע."
                        )
                        print(f"[HealthMonitor] ALERT: worker silence={silence_mins:.0f} min > 120 min")
                        
                        async with pii_session_factory() as _pii_db:
                            admins_res = await _pii_db.execute(select(User).where(User.is_admin == True))
                            admins = admins_res.scalars().all()
                            for admin in admins:
                                _pii_db.add(Notification(
                                    user_id=admin.id,
                                    type="threshold_alert",
                                    title=_alert_title,
                                    message=_alert_msg,
                                    channel="whatsapp",
                                    data={"threshold_type": "worker_silence", "silence_minutes": silence_mins},
                                ))
                                asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {
                                    "type": "threshold_alert",
                                    "title": _alert_title,
                                    "message": _alert_msg,
                                })))
                            await _pii_db.commit()
            except Exception as _e:
                print(f"[HealthMonitor] Threshold check 3 error: {_e}")

            # Check 4: unprocessed job failures > threshold (Gap 3: Alerting thresholds)
            try:
                JOB_FAILURES_ALERT_THRESHOLD = int(os.getenv("JOB_FAILURES_ALERT_THRESHOLD", "10"))
                async with pii_session_factory() as _pii_db:
                    unprocessed_count = (await _pii_db.execute(
                        select(func.count(JobFailure.id)).where(JobFailure.processed == False)
                    )).scalar() or 0
                    
                    if unprocessed_count >= JOB_FAILURES_ALERT_THRESHOLD:
                        _alert_title = f"🔴 DLQ Alert: {unprocessed_count} unprocessed failures"
                        _alert_msg = (
                            f"Dead Letter Queue has {unprocessed_count} unprocessed job failures "
                            f"(threshold: {JOB_FAILURES_ALERT_THRESHOLD}). Review failures in admin dashboard."
                        )
                        print(f"[HealthMonitor] ALERT: job_failures={unprocessed_count} >= {JOB_FAILURES_ALERT_THRESHOLD}")
                        
                        admins_res = await _pii_db.execute(select(User).where(User.is_admin == True))
                        admins = admins_res.scalars().all()
                        for admin in admins:
                            _pii_db.add(Notification(
                                user_id=admin.id,
                                type="threshold_alert",
                                title=_alert_title,
                                message=_alert_msg,
                                channel="whatsapp",
                                data={"threshold_type": "job_failures_dlq", "count": unprocessed_count, "threshold": JOB_FAILURES_ALERT_THRESHOLD},
                            ))
                            asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {
                                "type": "threshold_alert",
                                "title": _alert_title,
                                "message": _alert_msg,
                            })))
                        await _pii_db.commit()
            except Exception as _e:
                print(f"[HealthMonitor] Threshold check 4 (job_failures) error: {_e}")

        except Exception as e:
            print(f"[HealthMonitor] Outer error: {e}")

        await asyncio.sleep(HEALTH_MONITOR_INTERVAL_S)


# ── Abandoned-cart re-engagement loop ───────────────────────────────────────
async def _abandoned_cart_loop():
    """
    Background loop: runs every ABANDONED_CART_INTERVAL_S seconds (default 60 min).

    Finds carts that are:
      - idle for > ABANDONED_CART_IDLE_HOURS hours (updated_at threshold)
      - contain at least one cart_item
      - whose owner has no pending_payment order created in the last
        ABANDONED_CART_IDLE_HOURS hours (prevents double-messaging someone
        who already reached checkout)

    For each qualifying cart:
      1. Loads user (phone + full_name) and resolves part names from catalog DB
      2. Calls MAYA (SalesAgent) to generate a personalised Hebrew WhatsApp message
      3. Sends via WhatsApp (TwilioWhatsAppProvider)
      4. Persists a Notification row and pushes SSE
      5. Touches cart.updated_at = now() to suppress re-sending for another interval
    """
    from BACKEND_AI_AGENTS import SalesAgent as _SalesAgent
    from social.whatsapp_provider import get_whatsapp_provider
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel, PartsCatalog, SupplierPart

    await asyncio.sleep(10)   # let DB pool warm up on startup
    while True:
        try:
            idle_cutoff   = datetime.utcnow() - timedelta(hours=ABANDONED_CART_IDLE_HOURS)
            recent_cutoff = datetime.utcnow() - timedelta(hours=ABANDONED_CART_IDLE_HOURS)

            async with pii_session_factory() as db:
                from sqlalchemy import exists as sa_exists

                pending_order_sq = (
                    select(Order.id)
                    .where(
                        Order.user_id == Cart.user_id,
                        Order.status == "pending_payment",
                        Order.created_at > recent_cutoff,
                    )
                    .correlate(Cart)
                )
                cart_item_sq = (
                    select(CartItemModel.id)
                    .where(CartItemModel.cart_id == Cart.id)
                    .correlate(Cart)
                )

                result = await db.execute(
                    select(Cart).where(
                        Cart.updated_at < idle_cutoff,
                        sa_exists(cart_item_sq),
                        ~sa_exists(pending_order_sq),
                    )
                )
                abandoned_carts = result.scalars().all()

            if not abandoned_carts:
                print(f"[AbandonedCart] No abandoned carts found (idle > {ABANDONED_CART_IDLE_HOURS}h).")
            else:
                print(f"[AbandonedCart] Found {len(abandoned_carts)} abandoned cart(s) — processing...")
                provider  = get_whatsapp_provider()
                maya      = _SalesAgent()
                sent_count = 0
                skip_count = 0

                for cart in abandoned_carts:
                    try:
                        async with pii_session_factory() as db:
                            # Load user
                            user_res = await db.execute(
                                select(User).where(User.id == cart.user_id)
                            )
                            user = user_res.scalar_one_or_none()
                            if (
                                not user
                                or not user.phone
                                or str(user.id) == str(WHATSAPP_ANON_USER_ID)
                            ):
                                skip_count += 1
                                continue

                            # Load cart items
                            items_res = await db.execute(
                                select(CartItemModel).where(CartItemModel.cart_id == cart.id)
                            )
                            items = items_res.scalars().all()
                            if not items:
                                skip_count += 1
                                continue

                        # Resolve part names from catalog DB (cross-DB)
                        sp_ids = [i.supplier_part_id for i in items]
                        async with async_session_factory() as cat_db:
                            parts_res = await cat_db.execute(
                                select(SupplierPart, PartsCatalog)
                                .join(PartsCatalog, SupplierPart.part_id == PartsCatalog.id)
                                .where(SupplierPart.id.in_(sp_ids))
                            )
                            part_rows = {str(r.SupplierPart.id): r.PartsCatalog for r in parts_res}

                        total_value = sum(float(i.unit_price) * i.quantity for i in items)
                        item_lines  = []
                        for i in items:
                            part = part_rows.get(str(i.supplier_part_id))
                            name = part.name if part else "חלק לא ידוע"
                            item_lines.append(f"{name} (x{i.quantity})")
                        items_summary = ", ".join(item_lines)

                        # Ask MAYA to generate a personalised WhatsApp message
                        async with pii_session_factory() as db:
                            maya_prompt = (
                                f"לקוח בשם {user.full_name} השאיר {len(items)} פריטים בסל: {items_summary}. "
                                f"שווי הסל: {total_value:.0f}₪. "
                                f"צור הודעת WhatsApp קצרה ומשכנעת בעברית (עד 3 משפטים) שתחזיר אותו לסל לסיים את הרכישה."
                            )
                            wa_message = await maya.process(
                                message=maya_prompt,
                                conversation_history=[],
                                db=db,
                            )

                        # Send WhatsApp
                        wa_result = await provider.send_message(to=user.phone, body=wa_message)
                        if not wa_result.get("ok"):
                            print(f"[AbandonedCart] WhatsApp failed for user {user.id}: {wa_result.get('error')}")
                            skip_count += 1
                            continue

                        # Persist Notification + SSE push + touch cart.updated_at
                        _title = "🛒 שכחת משהו בסל?"
                        _msg   = f"יש לך {len(items)} פריטים בסל בשווי {total_value:.0f}₪ מחכים לך!"
                        async with pii_session_factory() as db:
                            db.add(Notification(
                                user_id=user.id,
                                type="abandoned_cart",
                                title=_title,
                                message=_msg,
                                channel="whatsapp",
                                data={
                                    "cart_id":     str(cart.id),
                                    "item_count":  len(items),
                                    "total_value": round(total_value, 2),
                                    "items":       item_lines,
                                    "wa_sid":      wa_result.get("sid"),
                                },
                                sent_at=datetime.utcnow(),
                            ))
                            # Touch updated_at to suppress re-sending for another interval
                            await db.execute(
                                text("UPDATE carts SET updated_at = now() WHERE id = :cid"),
                                {"cid": str(cart.id)},
                            )
                            await db.commit()

                        asyncio.create_task(
                            publish_notification(str(user.id), {
                                "type":    "abandoned_cart",
                                "title":   _title,
                                "message": _msg,
                            })
                        )
                        sent_count += 1
                        safe_phone = (user.phone or "")
                        safe_tail = safe_phone[-4:] if len(safe_phone) >= 4 else safe_phone
                        print(f"[AbandonedCart] ✅ Sent to {user.full_name} (****{safe_tail}) — cart {cart.id}")

                    except Exception as e:
                        print(f"[AbandonedCart] Error processing cart {cart.id}: {e}")
                        skip_count += 1

                print(f"[AbandonedCart] Done — sent: {sent_count}, skipped: {skip_count}")

        except Exception as e:
            print(f"[AbandonedCart] Outer error: {e}")

        await asyncio.sleep(ABANDONED_CART_INTERVAL_S)


# ── Pending-payment reminder loop ───────────────────────────────────────────
async def _pending_payment_reminder_loop():
    """
    Background loop: runs every PAYMENT_REMINDER_INTERVAL_S seconds (default 30 min).

    Finds orders that are:
      - status = 'pending_payment'
      - created more than PAYMENT_REMINDER_AFTER_H hours ago (gave them time to pay)
      - created less than 24 hours ago (not too old / auto-cancelled)
      - have no Notification with type='payment_reminder' created in the last 6 hours
        (prevents re-spamming the same order)

    For each qualifying order:
      1. Loads user (phone + full_name), skips sentinel user
      2. Calls LIOR (OrdersAgent) to generate a personalised Hebrew WhatsApp reminder
      3. Sends via WhatsApp (TwilioWhatsAppProvider)
      4. Persists a Notification row (type='payment_reminder') and pushes SSE
    """
    from BACKEND_AI_AGENTS import OrdersAgent as _OrdersAgent
    from social.whatsapp_provider import get_whatsapp_provider

    await asyncio.sleep(15)   # let DB pool warm up on startup
    while True:
        try:
            old_cutoff      = datetime.utcnow() - timedelta(hours=PAYMENT_REMINDER_AFTER_H)
            max_age_cutoff  = datetime.utcnow() - timedelta(hours=24)
            reminder_cutoff = datetime.utcnow() - timedelta(hours=6)

            async with pii_session_factory() as db:
                from sqlalchemy import exists as sa_exists, cast as sa_cast, String as sa_String

                recent_reminder_sq = (
                    select(Notification.id)
                    .where(
                        Notification.type == "payment_reminder",
                        Notification.user_id == Order.user_id,
                        Notification.data["order_id"].astext == sa_cast(Order.id, sa_String),
                        Notification.created_at > reminder_cutoff,
                    )
                    .correlate(Order)
                )

                result = await db.execute(
                    select(Order).where(
                        Order.status == "pending_payment",
                        Order.created_at < old_cutoff,
                        Order.created_at > max_age_cutoff,
                        ~sa_exists(recent_reminder_sq),
                    )
                )
                pending_orders = result.scalars().all()

        except Exception as e:
            print(f"[PaymentReminder] Outer query error: {e}")
            await asyncio.sleep(PAYMENT_REMINDER_INTERVAL_S)
            continue

        if not pending_orders:
            print("[PaymentReminder] No remindable pending_payment orders found.")
        else:
            print(f"[PaymentReminder] Found {len(pending_orders)} order(s) — sending reminders...")
            provider   = get_whatsapp_provider()
            lior       = _OrdersAgent()
            sent_count = 0
            skip_count = 0

            for order in pending_orders:
                try:
                    async with pii_session_factory() as db:
                        user_res = await db.execute(
                            select(User).where(User.id == order.user_id)
                        )
                        user = user_res.scalar_one_or_none()
                        if (
                            not user
                            or not user.phone
                            or str(user.id) == str(WHATSAPP_ANON_USER_ID)
                        ):
                            skip_count += 1
                            continue

                    # Call LIOR to generate a personalised payment reminder
                    async with pii_session_factory() as db:
                        lior_prompt = (
                            f"לקוח {user.full_name} לא השלים תשלום להזמנה {order.order_number} "
                            f"בסך {order.total_amount}₪. "
                            f"צור הודעת WhatsApp קצרה ומשכנעת בעברית (עד 3 משפטים) שתזכיר לו "
                            f"לסיים את התשלום דרך /cart"
                        )
                        wa_message = await lior.process(
                            message=lior_prompt,
                            conversation_history=[],
                            db=db,
                        )

                    # Send WhatsApp
                    wa_result = await provider.send_message(to=user.phone, body=wa_message)
                    if not wa_result.get("ok"):
                        print(f"[PaymentReminder] WhatsApp failed for user {user.id}: {wa_result.get('error')}")
                        skip_count += 1
                        continue

                    # Persist Notification + SSE push
                    _title = "⏳ הזמנה ממתינה לתשלום"
                    _msg   = f"הזמנה {order.order_number} בסך {order.total_amount}₪ מחכה לתשלום."
                    async with pii_session_factory() as db:
                        db.add(Notification(
                            user_id=user.id,
                            type="payment_reminder",
                            title=_title,
                            message=_msg,
                            channel="whatsapp",
                            data={
                                "order_id":     str(order.id),
                                "order_number": order.order_number,
                                "total_amount": float(order.total_amount),
                                "wa_sid":       wa_result.get("sid"),
                            },
                            sent_at=datetime.utcnow(),
                        ))
                        await db.commit()

                    asyncio.create_task(
                        publish_notification(str(user.id), {
                            "type":    "payment_reminder",
                            "title":   _title,
                            "message": _msg,
                        })
                    )
                    sent_count += 1
                    safe_phone = (user.phone or "")
                    safe_tail = safe_phone[-4:] if len(safe_phone) >= 4 else safe_phone
                    print(f"[PaymentReminder] ✅ Sent to {user.full_name} (****{safe_tail}) — order {order.order_number}")

                except Exception as e:
                    print(f"[PaymentReminder] Error processing order {order.order_number}: {e}")
                    skip_count += 1

            print(f"[PaymentReminder] Done — sent: {sent_count}, skipped: {skip_count}")

        await asyncio.sleep(PAYMENT_REMINDER_INTERVAL_S)


# ── Background price-sync loop ────────────────────────────────────────────────
PRICE_SYNC_INTERVAL_H = int(os.getenv("PRICE_SYNC_INTERVAL_H", "24"))  # hours


async def _price_sync_loop():
    """
    Runs the SupplierManagerAgent.sync_prices() every PRICE_SYNC_INTERVAL_H hours.
    On first start, checks the last SystemLog entry: if < interval ago, waits the
    remainder; otherwise runs immediately.
    """
    from BACKEND_AI_AGENTS import SupplierManagerAgent
    from resilience import log_job_failure, job_registry_start, job_registry_finish
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
        job_id = None
        try:
            async with async_session_factory() as db:
                try:
                    job_id = await job_registry_start(db, "sync_prices", ttl_seconds=interval_s)
                except Exception as exc:
                    print(f"[PriceSync] job_registry_start failed: {exc}")

                agent = SupplierManagerAgent()
                report = await agent.sync_prices(db)
                print(
                    f"[PriceSync] ✅ done — "
                    f"updated={report['parts_updated']:,}  "
                    f"avail_changes={report['availability_changes']}  "
                    f"errors={len(report['errors'])}"
                )
                if job_id:
                    try:
                        await job_registry_finish(db, job_id, status="completed")
                    except Exception as exc:
                        print(f"[PriceSync] job_registry_finish failed: {exc}")
        except Exception as exc:
            error_msg = str(exc)[:500]
            print(f"[PriceSync] ❌ error: {error_msg}")
            # Log failure to DLQ (Gap 2b)
            try:
                async with pii_session_factory() as pii_db:
                    await log_job_failure(
                        pii_db,
                        job_name="sync_prices",
                        error=error_msg,
                        payload={},
                        attempts=1,
                    )
            except Exception as dlq_err:
                print(f"[PriceSync] Failed to log to DLQ: {dlq_err}")

            if job_id:
                try:
                    async with async_session_factory() as db:
                        await job_registry_finish(db, job_id, status="dead", error_message=error_msg)
                except Exception:
                    pass
        
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


# ==============================================================================
# ADMIN — CATALOG SCRAPER CONTROLS  (Rex)
# GET  /api/v1/admin/scraper/status
# POST /api/v1/admin/scraper/run
# POST /api/v1/admin/scraper/run-part/{part_id}
# POST /api/v1/admin/scraper/discover           ← brand discovery, all thin brands
# POST /api/v1/admin/scraper/discover/{brand}   ← brand discovery, one brand
# ==============================================================================

@app.get("/api/v1/admin/scraper/status", tags=["Admin – Scraper"])
async def scraper_status(
    current_user=Depends(get_current_admin_user),
):
    """Return scraper config + last run summary."""
    from catalog_scraper import get_scraper_status
    return get_scraper_status()


@app.post("/api/v1/admin/scraper/run", tags=["Admin – Scraper"])
async def scraper_run_now(
    batch_size: int = 100,
    current_user=Depends(get_current_admin_user),
):
    """
    Manually trigger one scraper cycle (runs in the background, returns immediately).
    batch_size: how many supplier_parts rows to process (default 100).
    """
    from catalog_scraper import run_scraper_cycle

    async def _run():
        try:
            await run_scraper_cycle(batch_size=batch_size)
        except Exception as exc:
            print(f"[Scraper] manual run error: {exc}")

    asyncio.create_task(_guarded_task(_run()))
    return {
        "status": "started",
        "message": f"Scraper cycle started for {batch_size} parts",
        "batch_size": batch_size,
    }


@app.post("/api/v1/admin/scraper/run-part/{part_id}", tags=["Admin – Scraper"])
async def scraper_run_one_part(
    part_id: str,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Immediately scrape and update a single part plus all its supplier_parts rows.
    Returns the scraper result dict.
    """
    from catalog_scraper import (
        scrape_autodoc, scrape_ebay_motors, scrape_aliexpress,
        db_update_supplier_part, db_log, SUPPLIER_TOOL_MAP, FALLBACK_TOOLS,
        ILS_PER_USD, SCRAPE_REQUEST_DELAY,
    )

    # Load the part + its supplier rows
    rows = (await db.execute(
        text("""
            SELECT sp.id  AS sp_id,
                   sp.price_ils,
                   sp.availability,
                     s.name AS supplier_name,
                     s.rate_limit_per_minute,
                   pc.sku, pc.name AS part_name, pc.manufacturer
            FROM supplier_parts sp
            JOIN suppliers s ON s.id = sp.supplier_id
            JOIN parts_catalog pc ON pc.id = sp.part_id
            WHERE pc.id = :pid
        """),
        {"pid": part_id},
    )).fetchall()

    if not rows:
        raise HTTPException(404, detail=f"Part {part_id} not found")

    results = []
    for row in rows:
        cat_num = row.sku.split("-", 1)[-1] if "-" in row.sku else row.sku
        mfr = row.manufacturer or ""
        primary_fn = SUPPLIER_TOOL_MAP.get(row.supplier_name, scrape_ebay_motors)
        try:
            if primary_fn is scrape_aliexpress:
                data = await primary_fn(f"{mfr} {cat_num} auto part")
            else:
                data = await primary_fn(cat_num, mfr, rate_limit_per_minute=row.rate_limit_per_minute)
        except Exception as exc:
            data = {"results": []}

        prices = [r["price_ils"] for r in data.get("results", []) if r.get("price_ils", 0) > 10]
        if prices:
            prices.sort()
            median = prices[len(prices) // 2]
            derived_cost = median / 1.18 / 1.45
            old = float(row.price_ils or derived_cost)
            new_ils = round(max(old * 0.75, min(derived_cost, old * 1.25)), 2)
            await db_update_supplier_part(
                db,
                supplier_part_id=str(row.sp_id),
                price_ils=new_ils,
                price_usd=round(new_ils / ILS_PER_USD, 2),
            )
            results.append({"supplier": row.supplier_name, "old_price": old, "new_price": new_ils, "action": "updated"})
        else:
            results.append({"supplier": row.supplier_name, "old_price": float(row.price_ils or 0), "action": "no_data"})

    await db_log(db, "INFO", f"Manual scrape of part {part_id}: {len(results)} supplier rows processed")
    return {"part_id": part_id, "sku": rows[0].sku, "supplier_results": results}


@app.post("/api/v1/admin/scraper/discover", tags=["Admin – Scraper"])
async def scraper_discover_all(
    target: int = 200,
    per_run: int = 5,
    current_user=Depends(get_current_admin_user),
):
    """
    Ask Rex to discover real parts for all brands that have fewer than `target`
    parts in the catalog.  Runs in the background — returns immediately.

    - **target**: minimum parts a brand must have before Rex skips it (default 200)
    - **per_run**: max number of thin brands to process this run (default 5)
    """
    from catalog_scraper import run_brand_discovery

    async def _run():
        try:
            await run_brand_discovery(target=target, per_run=per_run)
        except Exception as exc:
            print(f"[Rex] discovery error: {exc}")

    asyncio.create_task(_guarded_task(_run()))
    return {
        "status": "started",
        "message": f"Rex brand discovery started (target={target}, per_run={per_run})",
    }


@app.post("/api/v1/admin/scraper/discover/{brand}", tags=["Admin – Scraper"])
async def scraper_discover_brand(
    brand: str,
    target: int = 200,
    current_user=Depends(get_current_admin_user),
):
    """
    Ask Rex to discover real OEM + aftermarket parts for a single **brand**.
    Sources: autodoc.eu → eBay Motors.
    Parts are classified as OEM Original / Aftermarket / OEM Equivalent.
    Runs in the background — returns immediately.
    """
    from catalog_scraper import run_brand_discovery

    async def _run():
        try:
            await run_brand_discovery(brands=[brand], target=target)
        except Exception as exc:
            print(f"[Rex] discovery error for {brand}: {exc}")

    asyncio.create_task(_guarded_task(_run()))
    return {
        "status": "started",
        "message": f"Rex discovering parts for '{brand}' (target={target} parts)",
        "brand": brand,
    }


# ==============================================================================
# ADMIN — DB UPDATE AGENT CONTROLS
# GET  /api/v1/admin/db-agent/status
# POST /api/v1/admin/db-agent/run
# POST /api/v1/admin/db-agent/run/{task}
# ==============================================================================

@app.get("/api/v1/admin/db-agent/status", tags=["Admin – DB Agent"])
async def db_agent_status(
    current_user=Depends(get_current_admin_user),
):
    """Return the last run report from the DB update agent."""
    from db_update_agent import get_last_report, is_running, TASK_REGISTRY
    return {
        "running": is_running(),
        "available_tasks": list(TASK_REGISTRY.keys()),
        "last_report": get_last_report(),
    }


@app.post("/api/v1/admin/db-agent/run", tags=["Admin – DB Agent"])
async def db_agent_run_all(
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger all DB update / cleaning tasks immediately.
    Runs in the background and returns right away.
    Poll /api/v1/admin/db-agent/status for results.
    """
    from db_update_agent import run_all_tasks, is_running

    if is_running():
        return {"status": "already_running", "message": "DB agent is already running"}

    # The task must open its own session — the context-manager session would
    # close before the background coroutine runs.
    async def _run():
        async with async_session_factory() as bg_db:
            try:
                await run_all_tasks(bg_db)
            except Exception as exc:
                print(f"[DB Agent] background run error: {exc}")

    asyncio.create_task(_guarded_task(_run()))
    return {"status": "started", "message": "All DB agent tasks triggered in the background"}


@app.post("/api/v1/admin/db-agent/run/{task_name}", tags=["Admin – DB Agent"])
async def db_agent_run_task(
    task_name: str,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Run a single named DB-agent task synchronously and return its result.

    Available tasks: clean_part_names, normalize_part_types, normalize_categories,
    normalize_availability, fix_base_prices, flag_fake_skus, fill_car_brands,
    refresh_min_max_prices, seed_system_settings
    """
    from db_update_agent import run_task, TASK_REGISTRY

    if task_name not in TASK_REGISTRY:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown task '{task_name}'. "
                   f"Valid tasks: {list(TASK_REGISTRY.keys())}",
        )

    result = await run_task(task_name, db)
    return result


# ==============================================================================
# CUSTOMERS CART  /api/v1/customers/cart  (4 endpoints)
# ==============================================================================

class CartAddRequest(BaseModel):
    part_id: str
    quantity: int = 1


async def _get_or_create_cart(user_id, db: AsyncSession):
    """Return the user's Cart row, creating one if it doesn't exist yet."""
    from BACKEND_DATABASE_MODELS import Cart
    result = await db.execute(select(Cart).where(Cart.user_id == user_id))
    cart = result.scalar_one_or_none()
    if not cart:
        cart = Cart(user_id=user_id)
        db.add(cart)
        await db.flush()
    return cart


@app.get("/api/v1/customers/cart")
async def get_cart(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel
    cart = await _get_or_create_cart(current_user.id, db)
    result = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == cart.id)
    )
    items = result.scalars().all()
    return {"items": await _cart_to_response(items, cat_db)}


@app.post("/api/v1/customers/cart/items", status_code=status.HTTP_201_CREATED)
async def add_cart_item(
    data: CartAddRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel, SupplierPart
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    # Resolve cheapest available supplier_part for the given catalog part
    sp_res = await cat_db.execute(
        select(SupplierPart)
        .where(
            and_(
                SupplierPart.part_id == data.part_id,
                SupplierPart.is_available == True,
            )
        )
        .order_by(SupplierPart.price_ils.asc().nullslast())
        .limit(1)
    )
    sp = sp_res.scalar_one_or_none()
    if not sp:
        raise HTTPException(status_code=404, detail="Part not available from any supplier")

    unit_price = float(sp.price_ils or 0) or (float(sp.price_usd or 0) * USD_TO_ILS)
    cart = await _get_or_create_cart(current_user.id, db)

    # Upsert: increment quantity if the same supplier_part is already in the cart
    stmt = (
        pg_insert(CartItemModel)
        .values(
            cart_id=cart.id,
            part_id=uuid.UUID(str(data.part_id)),
            supplier_part_id=sp.id,
            quantity=data.quantity,
            unit_price=round(unit_price, 2),
        )
        .on_conflict_do_update(
            constraint="uq_cart_item",
            set_={
                "quantity": CartItemModel.quantity + data.quantity,
                "unit_price": round(unit_price, 2),
                "updated_at": text("now()"),
            },
        )
    )
    await db.execute(stmt)
    await db.execute(
        text("UPDATE carts SET updated_at = now() WHERE id = :cid"),
        {"cid": str(cart.id)},
    )
    await db.flush()

    result = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == cart.id)
    )
    items = result.scalars().all()
    return {"items": await _cart_to_response(items, cat_db)}


@app.delete("/api/v1/customers/cart/items/{item_id}")
async def remove_cart_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel

    cart = await _get_or_create_cart(current_user.id, db)
    res = await db.execute(
        select(CartItemModel).where(
            and_(CartItemModel.id == item_id, CartItemModel.cart_id == cart.id)
        )
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found")
    await db.delete(item)
    await db.execute(
        text("UPDATE carts SET updated_at = now() WHERE id = :cid"),
        {"cid": str(cart.id)},
    )
    await db.flush()

    result = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == cart.id)
    )
    items = result.scalars().all()
    return {"items": await _cart_to_response(items, cat_db)}


@app.post("/api/v1/customers/checkout", status_code=status.HTTP_201_CREATED)
async def checkout(
    shipping_address: Dict[str, str],
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    """
    Convert the user's server-side cart into an Order, then empty the cart.
    Delegates all pricing / OrderItem creation to the existing create_order logic.
    """
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel

    cart_res = await db.execute(
        select(Cart).where(Cart.user_id == current_user.id)
    )
    cart = cart_res.scalar_one_or_none()
    if not cart:
        raise HTTPException(status_code=400, detail="Cart is empty")

    items_res = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == cart.id)
    )
    cart_items = items_res.scalars().all()
    if not cart_items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    # Build the same OrderCreate payload the existing endpoint expects
    order_payload = OrderCreate(
        items=[
            OrderItemCreate(
                part_id=str(ci.part_id),
                supplier_part_id=str(ci.supplier_part_id),
                quantity=ci.quantity,
            )
            for ci in cart_items
        ],
        shipping_address=shipping_address,
    )

    # Delegate to the existing create_order function — no logic duplication
    order_result = await create_order(
        data=order_payload,
        current_user=current_user,
        cat_db=cat_db,
        db=db,
    )

    # Clear cart on success
    await db.execute(
        text("DELETE FROM cart_items WHERE cart_id = :cid"),
        {"cid": str(cart.id)},
    )
    await db.execute(
        text("UPDATE carts SET updated_at = now() WHERE id = :cid"),
        {"cid": str(cart.id)},
    )

    return order_result


# ==============================================================================
# WISHLIST
# ==============================================================================

class WishlistAddRequest(BaseModel):
    part_id: str


async def _wishlist_item_to_response(item, cat_db: AsyncSession) -> dict:
    """Resolve part details from catalog DB for a single WishlistItem row."""
    from BACKEND_DATABASE_MODELS import PartsCatalog, PartImage
    part_res = await cat_db.execute(
        select(PartsCatalog).where(PartsCatalog.id == item.part_id)
    )
    part = part_res.scalar_one_or_none()
    if not part:
        return None

    img_res = await cat_db.execute(
        select(PartImage).where(
            and_(PartImage.part_id == part.id, PartImage.is_primary == True)
        ).limit(1)
    )
    img = img_res.scalar_one_or_none()

    return {
        "id":           str(item.id),
        "partId":       str(item.part_id),
        "name":         part.name,
        "category":     part.category,
        "manufacturer": part.manufacturer,
        "price":        float(part.min_price_ils or part.base_price or 0),
        "imageUrl":     img.url if img else None,
        "addedAt":      item.added_at.isoformat(),
    }


@app.get("/api/v1/customers/wishlist")
async def get_wishlist(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import WishlistItem
    res = await db.execute(
        select(WishlistItem)
        .where(WishlistItem.user_id == current_user.id)
        .order_by(WishlistItem.added_at.desc())
    )
    items = res.scalars().all()
    out = []
    for item in items:
        row = await _wishlist_item_to_response(item, cat_db)
        if row:
            out.append(row)
    return {"items": out, "count": len(out)}


@app.post("/api/v1/customers/wishlist", status_code=status.HTTP_201_CREATED)
async def add_to_wishlist(
    body: WishlistAddRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import WishlistItem
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    try:
        part_uuid = uuid.UUID(body.part_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid part_id")

    stmt = pg_insert(WishlistItem).values(
        user_id=current_user.id,
        part_id=part_uuid,
    ).on_conflict_do_nothing(constraint="uq_wishlist_item")
    await db.execute(stmt)
    await db.commit()

    res = await db.execute(
        select(WishlistItem).where(
            WishlistItem.user_id == current_user.id,
            WishlistItem.part_id == part_uuid,
        )
    )
    item = res.scalar_one()
    return await _wishlist_item_to_response(item, cat_db)


@app.delete("/api/v1/customers/wishlist/{part_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_wishlist(
    part_id: str,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    from BACKEND_DATABASE_MODELS import WishlistItem

    try:
        part_uuid = uuid.UUID(part_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid part_id")

    res = await db.execute(
        select(WishlistItem).where(
            WishlistItem.user_id == current_user.id,
            WishlistItem.part_id == part_uuid,
        )
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not in wishlist")
    await db.delete(item)
    await db.commit()


# ==============================================================================
# PART REVIEWS
# ==============================================================================

class ReviewCreateRequest(BaseModel):
    rating: int
    title:  Optional[str] = Field(None, max_length=255)
    body:   Optional[str] = Field(None, max_length=2000)

    @validator("rating")
    def validate_rating(cls, v):
        if not 1 <= v <= 5:
            raise ValueError("rating must be 1–5")
        return v


@app.get("/api/v1/parts/{part_id}/reviews")
async def get_part_reviews(
    part_id: str,
    db: AsyncSession = Depends(get_pii_db),
):
    from BACKEND_DATABASE_MODELS import PartReview

    try:
        part_uuid = uuid.UUID(part_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid part_id")

    res = await db.execute(
        select(PartReview, User)
        .join(User, PartReview.user_id == User.id)
        .where(PartReview.part_id == part_uuid)
        .order_by(PartReview.created_at.desc())
    )
    rows = res.all()

    reviews = []
    total_rating = 0
    for review, user in rows:
        total_rating += review.rating
        reviews.append({
            "id":                 str(review.id),
            "rating":             review.rating,
            "title":              review.title,
            "body":               review.body,
            "isVerifiedPurchase": review.is_verified_purchase,
            "createdAt":          review.created_at.isoformat(),
            "user": {
                "id":       str(user.id),
                "fullName": user.full_name,
            },
        })

    avg = round(total_rating / len(reviews), 1) if reviews else None
    return {"reviews": reviews, "count": len(reviews), "averageRating": avg}


@app.post("/api/v1/parts/{part_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_part_review(
    part_id: str,
    body: ReviewCreateRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    from BACKEND_DATABASE_MODELS import PartReview, OrderItem as _OrderItem

    try:
        part_uuid = uuid.UUID(part_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid part_id")

    existing = await db.execute(
        select(PartReview).where(
            PartReview.user_id == current_user.id,
            PartReview.part_id == part_uuid,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="You have already reviewed this part")

    # Verified purchase: user must have a delivered order containing this part
    verified_res = await db.execute(
        select(Order)
        .join(_OrderItem, _OrderItem.order_id == Order.id)
        .where(
            Order.user_id == current_user.id,
            Order.status == "delivered",
            _OrderItem.part_id == part_uuid,
        )
        .limit(1)
    )
    verified_order = verified_res.scalar_one_or_none()

    review = PartReview(
        user_id=current_user.id,
        part_id=part_uuid,
        order_id=verified_order.id if verified_order else None,
        rating=body.rating,
        title=body.title,
        body=body.body,
        is_verified_purchase=verified_order is not None,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    return {
        "id":                 str(review.id),
        "partId":             str(review.part_id),
        "rating":             review.rating,
        "title":              review.title,
        "body":               review.body,
        "isVerifiedPurchase": review.is_verified_purchase,
        "createdAt":          review.created_at.isoformat(),
    }


@app.delete("/api/v1/customers/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    review_id: str,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    from BACKEND_DATABASE_MODELS import PartReview

    try:
        review_uuid = uuid.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid review_id")

    res = await db.execute(
        select(PartReview).where(PartReview.id == review_uuid)
    )
    review = res.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if str(review.user_id) != str(current_user.id) and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not your review")
    await db.delete(review)
    await db.commit()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("BACKEND_API_ROUTES:app", host="0.0.0.0", port=8000, reload=True)
