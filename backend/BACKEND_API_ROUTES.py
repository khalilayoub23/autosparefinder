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

# _cart_to_response helper → routes/cart.py
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
# BRANDS REFERENCE  /api/v1/brands  → routes/brands.py
# ==============================================================================

# brands endpoints moved to routes/brands.py

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
# WHATSAPP WEBHOOK  /api/v1/webhooks/whatsapp  → routes/webhooks.py
# ==============================================================================

# whatsapp_webhook endpoint moved to routes/webhooks.py
# ==============================================================================
# 6b. ADMIN SUPPLIER ORDERS  → routes/admin.py
# ==============================================================================

# GET  /api/v1/admin/supplier-orders                      → routes/admin.py
# PUT  /api/v1/admin/supplier-orders/{notification_id}/done → routes/admin.py


# ==============================================================================
# 7. INVOICES  /api/v1/invoices  -> routes/invoices.py
# ==============================================================================

# GET  /api/v1/invoices                          -> routes/invoices.py
# GET  /api/v1/invoices/{invoice_id}             -> routes/invoices.py
# GET  /api/v1/invoices/{invoice_id}/download    -> routes/invoices.py
# POST /api/v1/invoices/{invoice_id}/resend      -> routes/invoices.py


# ==============================================================================
# 9. FILES  /api/v1/files  (4 endpoints)
# ==============================================================================


# /api/v1/files/upload — see routes/files.py



# /api/v1/files/{file_id} — see routes/files.py



# DELETE /api/v1/files/{file_id} — see routes/files.py
# Add files_router to include_router block
from routes.files import router as files_router
app.include_router(files_router)


# ==============================================================================
# 10. PROFILE  /api/v1/profile  (7 endpoints)
# ==============================================================================

# /api/v1/profile/* endpoints → routes/profile.py
# (get-profile, update-profile, avatar, update-phone, marketing-preferences, order-history)

from routes.profile import router as profile_router
app.include_router(profile_router)


# ==============================================================================
# 11. MARKETING  /api/v1/marketing  (7 endpoints)
# ==============================================================================


# /api/v1/marketing/* endpoints → routes/marketing.py
# (subscribe, validate-coupon, coupons, apply-coupon, promotions, referral, loyalty-points)

# Add marketing_router to include_router block
from routes.marketing import router as marketing_router
app.include_router(marketing_router)


# ==============================================================================
# 12. NOTIFICATIONS  /api/v1/notifications  (6 endpoints)
# ==============================================================================
#
# /api/v1/notifications/* endpoints → routes/notifications.py
# (stream, list, unread-count, read, read-all, delete)
#
# See: backend/routes/notifications.py

# Add notifications_router to include_router block
from routes.notifications import router as notifications_router
app.include_router(notifications_router)


# ==============================================================================
# 13. ADMIN  /api/v1/admin  → routes/admin.py
# ==============================================================================

# GET    /api/v1/admin/stats                              → routes/admin.py
# GET    /api/v1/admin/users                              → routes/admin.py
# GET/PUT/POST/DELETE /api/v1/admin/super/settings        → routes/admin.py
# GET/PUT /api/v1/admin/super/users                       → routes/admin.py
# POST/PUT/POST/DELETE /api/v1/admin/users                → routes/admin.py
# GET/POST/PUT/DELETE/POST /api/v1/admin/suppliers        → routes/admin.py
# GET/POST /api/v1/admin/approvals                        → routes/admin.py
# GET/PUT /api/v1/admin/orders                            → routes/admin.py



# ==============================================================================
# 14. SYSTEM  /api/v1/system  → routes/system.py
# ==============================================================================

# health, settings, version, metrics endpoints moved to routes/system.py






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
# CUSTOMERS CART + WISHLIST  /api/v1/customers/*  → routes/cart.py
# ==============================================================================

# cart, wishlist, checkout endpoints moved to routes/cart.py

# ==============================================================================
# PART REVIEWS  → routes/reviews.py
# ==============================================================================

# @router.get("/api/v1/parts/{part_id}/reviews")    → routes/reviews.py
# @router.get/post/delete /api/v1/parts/{part_id}/reviews   → routes/reviews.py
# @router.delete /api/v1/customers/reviews/{review_id}      → routes/reviews.py


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
from routes.returns import router as returns_router
app.include_router(returns_router)
from routes.invoices import router as invoices_router
app.include_router(invoices_router)
from routes.system import router as system_router
app.include_router(system_router)
from routes.cart import router as cart_router
app.include_router(cart_router)
from routes.brands import router as brands_router
app.include_router(brands_router, tags=["Brands"])
from routes.webhooks import router as webhooks_router
app.include_router(webhooks_router, tags=["Webhooks"])
from routes.support import router as support_router
app.include_router(support_router, tags=["Support"])
from routes.admin import router as admin_router
app.include_router(admin_router, tags=["Admin"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("BACKEND_API_ROUTES:app", host="0.0.0.0", port=8000, reload=True)
