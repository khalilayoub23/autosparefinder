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
    CarBrand, SystemLog, USD_TO_ILS, ApprovalQueue, SocialPost, JobFailure, AuditLog, BugReport,
)
from BACKEND_AUTH_SECURITY import (
    get_current_user, get_current_active_user, get_current_verified_user,
    get_current_admin_user, get_current_super_admin, register_user, login_user, complete_2fa_login,
    refresh_access_token, logout_user, create_password_reset_token,
    use_password_reset_token, change_password, update_phone_number,
    create_2fa_code, verify_2fa_code, get_redis, hash_password, publish_notification,
    check_rate_limit
)
from BACKEND_AI_AGENTS import process_user_message, process_agent_response_for_message, get_agent, OrdersAgent, TechAgent
from auto_backup import _backup_loop

load_dotenv()

logger = logging.getLogger(__name__)

BLOCKED_SETTINGS = {
    "jwt_secret", "jwt_refresh_secret", "stripe_secret_key",
    "stripe_webhook_secret", "hf_token", "database_url",
    "database_pii_url", "redis_url", "encryption_key",
    "twilio_auth_token", "sendgrid_api_key",
}

from routes.utils import _scan_bytes_for_virus, _guarded_task, _mask_supplier, trigger_supplier_fulfillment  # shared route utilities (clamd)
from routes.schemas import (
    SuperAdminSettingCreateBody,
    SuperAdminSettingUpdateBody,
    SuperAdminUserRoleUpdateBody,
    UpdatePhoneRequest,
    PartsSearchRequest,
    OrderItemCreate,
    OrderCreate,
    OrderCancelRequest,
    ReturnRequest,
    MultiCheckoutRequest,
    NewsletterSubscribeRequest,
    CouponValidateRequest,
    SupplierCreate,
    SupplierUpdateBody,
    CreateSocialPostRequest,
    UpdateSocialPostRequest,
    UserUpdateBody,
    UserCreateBody,
    ResolveApprovalBody,
    CartAddRequest,
    WishlistAddRequest,
)

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
# DROPSHIPPING FULFILLMENT  -> routes/utils.py (trigger_supplier_fulfillment)
# ==============================================================================

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
# VIRUS SCANNING  → routes/utils.py (_scan_bytes_for_virus)
# TASK SEMAPHORE  → routes/utils.py (_guarded_task)
# SUPPLIER MASKING → routes/utils.py (_mask_supplier)
# ==============================================================================


# ==============================================================================
# 1. AUTH  /api/v1/auth  → routes/auth.py
# ==============================================================================

# POST   /api/v1/auth/register                          → routes/auth.py
# POST   /api/v1/auth/login                             → routes/auth.py
# POST   /api/v1/auth/verify-2fa                        → routes/auth.py
# POST   /api/v1/auth/refresh                           → routes/auth.py
# POST   /api/v1/auth/verify-email                      → routes/auth.py
# POST   /api/v1/auth/verify-phone                      → routes/auth.py
# POST   /api/v1/auth/send-2fa                          → routes/auth.py
# POST   /api/v1/auth/logout                            → routes/auth.py
# GET    /api/v1/auth/me                                → routes/auth.py
# POST   /api/v1/auth/accept-terms                      → routes/auth.py
# POST   /api/v1/auth/reset-password                    → routes/auth.py
# POST   /api/v1/auth/reset-password/confirm            → routes/auth.py
# POST   /api/v1/auth/change-password                   → routes/auth.py
# GET    /api/v1/auth/trusted-devices                   → routes/auth.py
# POST   /api/v1/auth/trust-device                      → routes/auth.py
# DELETE /api/v1/auth/trusted-devices/{device_id}       → routes/auth.py
# _VALID_CUSTOMER_TYPES + 7 request models              → routes/auth.py



# 2. CHAT  /api/v1/chat  (10 endpoints)  → routes/chat.py
# ==============================================================================
# POST   /api/v1/chat/message                         → routes/chat.py
# GET    /api/v1/chat/conversations                   → routes/chat.py
# GET    /api/v1/chat/conversations/{id}              → routes/chat.py
# GET    /api/v1/chat/conversations/{id}/messages     → routes/chat.py
# DELETE /api/v1/chat/conversations/{id}              → routes/chat.py
# POST   /api/v1/chat/upload-image                    → routes/chat.py
# POST   /api/v1/chat/upload-audio                    → routes/chat.py
# POST   /api/v1/chat/upload-video                    → routes/chat.py
# WS     /api/v1/chat/ws                              → routes/chat.py
# POST   /api/v1/chat/rate                            → routes/chat.py
# ChatMessageRequest model                            → routes/chat.py

# ==============================================================================
# 3. PARTS  /api/v1/parts
#    GET  /api/v1/parts/search            → routes/parts.py
#    GET  /api/v1/parts/categories        → routes/parts.py
#    GET  /api/v1/parts/autocomplete      → routes/parts.py
#    POST /api/v1/parts/search-by-vehicle → routes/parts.py
#    GET  /api/v1/parts/manufacturers     → routes/parts.py
#    GET  /api/v1/parts/models            → routes/parts.py
#    GET  /api/v1/parts/search-by-vin     → routes/parts.py
#    GET  /api/v1/parts/{part_id}         → routes/parts.py
#    POST /api/v1/parts/compare           → routes/parts.py
#    POST /api/v1/parts/identify-from-image → routes/parts.py
#    GET  /api/v1/parts/{part_id}/reviews → routes/parts.py
#    POST /api/v1/parts/{part_id}/reviews → routes/parts.py
# ==============================================================================


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


# GET  /api/v1/parts/search-by-vin        → routes/parts.py
# GET  /api/v1/parts/{part_id}             → routes/parts.py
# POST /api/v1/parts/compare               → routes/parts.py
# POST /api/v1/parts/identify-from-image   → routes/parts.py


# ==============================================================================
# 4. VEHICLES  /api/v1/vehicles  → routes/vehicles.py
# ==============================================================================

# POST   /api/v1/vehicles/identify                          → routes/vehicles.py
# POST   /api/v1/vehicles/identify-from-image               → routes/vehicles.py
# GET    /api/v1/vehicles/my-vehicles                       → routes/vehicles.py
# POST   /api/v1/vehicles/my-vehicles                       → routes/vehicles.py
# PUT    /api/v1/vehicles/my-vehicles/{vehicle_id}          → routes/vehicles.py
# DELETE /api/v1/vehicles/my-vehicles/{vehicle_id}          → routes/vehicles.py
# POST   /api/v1/vehicles/my-vehicles/set-primary           → routes/vehicles.py
# GET    /api/v1/vehicles/{vehicle_id}/compatible-parts     → routes/vehicles.py
# VehicleIdentifyRequest model                              → routes/vehicles.py


# ==============================================================================
# 5. ORDERS  /api/v1/orders  → routes/orders.py
# ==============================================================================

# POST   /api/v1/orders                           → routes/orders.py
# GET    /api/v1/orders                           → routes/orders.py
# GET    /api/v1/orders/{order_id}                → routes/orders.py
# GET    /api/v1/orders/{order_id}/track          → routes/orders.py
# PUT    /api/v1/orders/{order_id}/cancel         → routes/orders.py
# POST   /api/v1/orders/{order_id}/return         → routes/orders.py
# DELETE /api/v1/orders/{order_id}                → routes/orders.py
# GET    /api/v1/orders/{order_id}/invoice        → routes/orders.py


# ==============================================================================
# 6. PAYMENTS  /api/v1/payments  -> routes/payments.py
# ==============================================================================

# POST /api/v1/payments/create-checkout           -> routes/payments.py
# POST /api/v1/payments/create-multi-checkout     -> routes/payments.py
# GET  /api/v1/payments/verify-session            -> routes/payments.py
# POST /api/v1/payments/create-intent             -> routes/payments.py
# POST /api/v1/payments/confirm                   -> routes/payments.py
# GET  /api/v1/payments/refunds/list              -> routes/payments.py
# GET  /api/v1/payments/{payment_id}              -> routes/payments.py
# POST /api/v1/payments/webhook                   -> routes/payments.py

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


@app.post("/api/v1/support/report")
async def submit_bug_report(
    request: Request,
    db: AsyncSession = Depends(get_db),
    pii_db: AsyncSession = Depends(get_pii_db),
):
    body = await request.json()
    user = None
    try:
        user = await get_current_user(request, pii_db)
    except Exception:
        pass

    lang = request.headers.get("accept-language", "he")[:2]
    device_info = {
        "user_agent": request.headers.get("user-agent", ""),
        "platform": request.headers.get("x-platform", body.get("platform", "web")),
        "app_version": request.headers.get("x-app-version", body.get("app_version", "")),
        "language": lang,
    }

    report_data = {
        "title": body.get("title", "Bug Report"),
        "description": body.get("description", ""),
        "platform": device_info["platform"],
        "app_version": device_info["app_version"],
        "screen_name": body.get("screen_name"),
        "endpoint_url": body.get("endpoint_url"),
        "http_method": body.get("http_method"),
        "http_status_code": body.get("http_status_code"),
        "error_trace": body.get("error_trace"),
        "last_api_calls": body.get("last_api_calls", []),
    }

    tech_agent = TechAgent()
    analysis = await tech_agent.process({"report": report_data})

    report = BugReport(
        id=uuid.uuid4(),
        user_id=user.id if user else None,
        user_role=getattr(user, "role", None),
        tech_analysis=analysis,
        device_info=device_info,
        severity=analysis.get("severity", "medium"),
        **{k: v for k, v in report_data.items()},
    )
    db.add(report)
    await db.flush()

    if analysis.get("severity") in ("critical", "high"):
        approval = ApprovalQueue(
            id=uuid.uuid4(),
            entity_type="bug_report",
            entity_id=report.id,
            action="review_bug_report",
            payload={
                "bug_report_id": str(report.id),
                "title": report_data["title"],
                "severity": analysis.get("severity"),
                "affected_component": analysis.get("affected_component", ""),
                "suggested_fix": analysis.get("suggested_fix", ""),
            },
            status="pending",
            requested_by=user.id if user else None,
        )
        pii_db.add(approval)

    await db.commit()
    await pii_db.commit()

    msg_key = f"customer_message_{lang}" if lang in ("he", "ar", "en") else "customer_message_he"
    message = analysis.get(msg_key, analysis.get("customer_message_he", "קיבלנו את הדיווח"))

    return {
        "success": True,
        "report_id": str(report.id),
        "message": message,
        "severity": analysis.get("severity", "medium"),
    }


@app.get("/api/v1/admin/bug-reports")
async def list_bug_reports(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    query = select(BugReport).order_by(BugReport.created_at.desc())
    if status:
        query = query.where(BugReport.status == status)
    if severity:
        query = query.where(BugReport.severity == severity)
    result = await db.execute(query.limit(limit).offset(offset))
    reports = result.scalars().all()
    return {
        "reports": [
            {
                "id": str(r.id),
                "title": r.title,
                "severity": r.severity,
                "status": r.status,
                "platform": r.platform,
                "screen_name": r.screen_name,
                "endpoint_url": r.endpoint_url,
                "tech_analysis": r.tech_analysis,
                "admin_notes": r.admin_notes,
                "created_at": str(r.created_at),
                "resolved_at": str(r.resolved_at) if r.resolved_at else None,
            }
            for r in reports
        ],
        "total": len(reports),
    }


@app.put("/api/v1/admin/bug-reports/{report_id}")
async def update_bug_report(
    report_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    body = await request.json()
    try:
        report_uuid = uuid.UUID(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid report_id") from exc

    result = await db.execute(select(BugReport).where(BugReport.id == report_uuid))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Bug report not found")
    if "status" in body:
        report.status = body["status"]
        if body["status"] == "resolved":
            report.resolved_at = datetime.utcnow()
    if "admin_notes" in body:
        report.admin_notes = body["admin_notes"]
    report.updated_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "report_id": report_id, "status": report.status}


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
    from routes.orders import create_order

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
# PART REVIEWS  → routes/reviews.py
# ==============================================================================

# @router.get("/api/v1/parts/{part_id}/reviews")    → routes/reviews.py
# @router.post("/api/v1/parts/{part_id}/reviews")   → routes/reviews.py
# ReviewCreateRequest model                          → routes/reviews.py


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


# ── Route modules extracted from this file (loaded after all symbols are defined)
# NOTE: The circular import between routes/* and BACKEND_API_ROUTES is intentional
#       and safe ONLY because this block runs after all function/helper definitions.
from routes.parts import router as parts_router
app.include_router(parts_router)
from routes.reviews import router as reviews_router
app.include_router(reviews_router)
from routes.vehicles import router as vehicles_router
app.include_router(vehicles_router)
from routes.auth import router as auth_router
app.include_router(auth_router)
from routes.chat import router as chat_router
app.include_router(chat_router)
from routes.orders import router as orders_router
app.include_router(orders_router)
from routes.payments import router as payments_router
app.include_router(payments_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("BACKEND_API_ROUTES:app", host="0.0.0.0", port=8000, reload=True)
