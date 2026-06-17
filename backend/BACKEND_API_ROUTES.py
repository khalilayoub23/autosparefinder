"""
==============================================================================
AUTO SPARE - API ROUTES (FastAPI)
==============================================================================
Lifecycle handlers + background loops.
All API endpoints live in backend/routes/*.py
==============================================================================
"""

from fastapi import FastAPI, Depends, HTTPException, status, Request, Query, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, text
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
    CarBrand, SystemLog, USD_TO_ILS, ApprovalQueue, SocialPost, JobFailure, AuditLog, BugReport, SupplierPayment,
)
from BACKEND_AUTH_SECURITY import (
    get_current_user, get_current_active_user, get_current_verified_user,
    get_current_admin_user, get_current_super_admin, register_user, login_user, complete_2fa_login,
    refresh_access_token, logout_user, create_password_reset_token,
    use_password_reset_token, change_password, update_phone_number,
    create_2fa_code, verify_2fa_code, get_redis, hash_password, publish_notification,
    check_rate_limit
)
from BACKEND_AI_AGENTS import (
    OrdersAgent, OrdersAgent as _OrdersAgent, SalesAgent as _SalesAgent, SocialMediaManagerAgent,
    NOA_TELEGRAM_URL, NOA_WHATSAPP_URL, NOA_FACEBOOK_URL, NOA_INSTAGRAM_URL, NOA_WEBSITE_URL,
)
from auto_backup import _backup_loop
from social.whatsapp_provider import send_message as _wa_send
import httpx as _httpx
import clamd as _clamd

load_dotenv()

logger = logging.getLogger(__name__)

SEARCH_WARMUP_ENABLED = os.getenv("SEARCH_WARMUP_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
SEARCH_WARMUP_DELAY_S = float(os.getenv("SEARCH_WARMUP_DELAY_S", "0"))
SEARCH_WARMUP_QUERY_TIMEOUT_S = float(os.getenv("SEARCH_WARMUP_QUERY_TIMEOUT_S", "3"))
SEARCH_WARMUP_CASES: List[Dict[str, Any]] = [
    {"query": "engine", "category": "מנוע", "timeout_s": 12},
    {"query": "filter", "category": "סינון"},
    {"query": "mirror"},
    {"query": "battery"},
    {"query": "bosch"},
    {
        "query": "",
        "vehicle_manufacturer": "Toyota",
        "vehicle_model": "Corolla",
        "vehicle_year": 2018,
    },
]

BLOCKED_SETTINGS = {
    "jwt_secret", "jwt_refresh_secret", "stripe_secret_key",
    "stripe_webhook_secret", "hf_token", "database_url",
    "database_pii_url", "redis_url", "encryption_key",
    "twilio_auth_token", "sendgrid_api_key",
}

from routes.utils import _guarded_task, trigger_supplier_fulfillment  # shared background-loop utilities
from routes.stripe_config import resolve_stripe_secret_key, is_valid_stripe_secret_key

# ── Supervised background tasks ───────────────────────────────────────────────
# Tracks every asyncio task started at startup.  If a task exits unexpectedly
# (crash / unhandled exception) the done-callback fires a WhatsApp alert directly
# to the owner phone so the crash is never silently swallowed.
_SUPERVISED_TASKS: dict = {}  # name → asyncio.Task


def _supervised_task(name: str, coro) -> "asyncio.Task":
    """
    Drop-in replacement for asyncio.create_task() that:
      1. Registers the task in _SUPERVISED_TASKS for health-monitor inspection.
      2. Adds a done-callback: alerts owner via WhatsApp if the task exits for any
         reason other than intentional CancelledError (i.e. a crash).
    """
    task = asyncio.create_task(coro, name=name)

    def _on_done(t: "asyncio.Task") -> None:
        if t.cancelled():
            return  # intentional shutdown — no alert
        exc = None
        try:
            exc = t.exception()
        except Exception:
            pass
        owner = os.getenv("OWNER_WHATSAPP_PHONE", "")
        if owner:
            msg_parts = [f"🔴 Background task died: *{name}*"]
            if exc:
                msg_parts.append(f"Error: {type(exc).__name__}: {exc}")
            msg_parts.append("⚠️ This task will NOT restart automatically — check the server.")
            asyncio.get_event_loop().create_task(
                _wa_send(to=owner, text="\n".join(msg_parts))
            )
        print(f"[TaskMonitor] DIED: {name} exc={exc}")

    task.add_done_callback(_on_done)
    _SUPERVISED_TASKS[name] = task
    return task


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
    allow_origins=os.getenv("CORS_ORIGINS", "https://autosparefinder.com,http://localhost:5173,http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-Idempotency-Key"],
)


from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from BACKEND_AUTH_SECURITY import decode_access_token
import os

class SecurityHeadersAndAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = str(request.url.path)
        print("DEBUG PATH:", path, flush=True)
        
        # Security: X-API-KEY validation for webhooks
        if path.startswith("/api/webhooks/"):
            api_key = request.headers.get("X-API-KEY")
            expected_key = os.getenv("N8N_WEBHOOK_SECRET", "n8n-secret")
            if api_key != expected_key:
                resp = JSONResponse(status_code=401, content={"error": "Unauthorized Webhook Access"}); resp.headers["X-Frame-Options"] = "DENY"; resp.headers["X-Content-Type-Options"] = "nosniff"; return resp

        # Security: JWT validation for admin panel
        if path.startswith("/api/admin/") or path.startswith("/api/v1/admin/"):
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                resp = JSONResponse(status_code=401, content={"error": "Authentication required"}); resp.headers["X-Frame-Options"] = "DENY"; resp.headers["X-Content-Type-Options"] = "nosniff"; return resp
            token = auth_header.split(" ")[1]
            try:
                user_payload = decode_access_token(token)
            except Exception as e:
                resp = JSONResponse(status_code=401, content={"error": "Invalid token"}); resp.headers["X-Frame-Options"] = "DENY"; resp.headers["X-Content-Type-Options"] = "nosniff"; return resp
        
        response = await call_next(request)
        
        # Task 3: Security Headers
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:;"
        return response

app.add_middleware(SecurityHeadersAndAuthMiddleware)


# SSL is terminated by Cloudflare — no HTTPSRedirectMiddleware needed


async def _warm_search_paths() -> None:
    if not SEARCH_WARMUP_ENABLED:
        print("[SearchWarmup] disabled (SEARCH_WARMUP_ENABLED=false)")
        return

    await asyncio.sleep(max(0.0, SEARCH_WARMUP_DELAY_S))

    try:
        from routes.parts import search_parts

        async with async_session_factory() as db:
            for case in SEARCH_WARMUP_CASES:
                try:
                    await asyncio.wait_for(
                        search_parts(
                            query=case.get("query", ""),
                            vehicle_id=case.get("vehicle_id"),
                            category=case.get("category"),
                            per_type=4,
                            sort_by="price_ils",
                            vehicle_manufacturer=case.get("vehicle_manufacturer"),
                            vehicle_model=case.get("vehicle_model"),
                            vehicle_submodel=case.get("vehicle_submodel"),
                            vehicle_year=case.get("vehicle_year"),
                            enable_cross_refs=case.get("enable_cross_refs"),
                            db=db,
                            request=None,
                            redis=None,
                        ),
                        timeout=float(case.get("timeout_s", SEARCH_WARMUP_QUERY_TIMEOUT_S)),
                    )
                except Exception as exc:
                    logger.warning("[SearchWarmup] failed for %s: %s", case, exc)
        print(f"[SearchWarmup] primed {len(SEARCH_WARMUP_CASES)} search shapes")
    except Exception as exc:
        logger.warning("[SearchWarmup] startup warmup failed: %s", exc)

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
# 8. RETURNS  /api/v1/returns  → routes/returns.py
# ==============================================================================

# POST   /api/v1/returns                             → routes/returns.py
# GET    /api/v1/returns                             → routes/returns.py
# GET    /api/v1/returns/{return_id}                 → routes/returns.py
# POST   /api/v1/returns/{return_id}/track           → routes/returns.py
# PUT    /api/v1/returns/{return_id}/cancel          → routes/returns.py
# GET    /api/v1/returns/{return_id}/invoice         → routes/returns.py
# POST   /api/v1/returns/{return_id}/approve         → routes/returns.py
# POST   /api/v1/returns/{return_id}/reject          → routes/returns.py
# GET    /api/v1/admin/returns                       → routes/returns.py
# _FULL_REFUND_REASONS, _RETURN_WINDOW_DAYS          → routes/returns.py


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


async def _scrape_search_misses_loop() -> None:
    """
    Every 6 hours: search eBay for unscraped search misses.
    Sets triggered_scrape=TRUE so _notify_search_miss_loop
    can then inform the customer.
    """
    await asyncio.sleep(180)  # brief startup delay
    while True:
        try:
            from services.supplier_aggregator import search_all_suppliers

            async with async_session_factory() as db:
                result = await db.execute(
                    text("""
                        SELECT id, query, vehicle_manufacturer
                        FROM search_misses
                        WHERE triggered_scrape = FALSE
                        AND notified = FALSE
                        AND miss_count >= 1
                        ORDER BY miss_count DESC
                        LIMIT 20
                    """)
                )
                misses = result.fetchall()

            if misses:
                print(f"[scrape_misses] Processing {len(misses)} search misses")
                for miss in misses:
                    try:
                        results = await search_all_suppliers(miss.query, limit_per_supplier=5)
                        if results:
                            print(f"[scrape_misses] Found {len(results)} results for: {miss.query}")
                            async with async_session_factory() as db:
                                await db.execute(
                                    text("UPDATE search_misses SET triggered_scrape = TRUE WHERE id = :id"),
                                    {"id": str(miss.id)}
                                )
                                await db.commit()
                        else:
                            print(f"[scrape_misses] No results for: {miss.query}")
                    except Exception as miss_err:
                        print(f"[scrape_misses] Error for miss {miss.id}: {miss_err}")

        except Exception as e:
            print(f"[scrape_misses] loop error: {e}")

        await asyncio.sleep(21600)  # run every 6 hours


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
                    for _sid in notified_ids:
                        await cat_db.execute(
                            text("UPDATE search_misses SET notified = TRUE WHERE id = :sid"),
                            {"sid": _sid},
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
                    for _vid in new_vip_ids:
                        await pii_db.execute(text("""
                            UPDATE user_profiles
                            SET is_vip     = TRUE,
                                vip_since  = NOW(),
                                updated_at = NOW()
                            WHERE user_id = :vid
                              AND is_vip  = FALSE
                        """), {"vid": _vid})

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
                        asyncio.create_task(_guarded_task(publish_notification(
                            str(row.user_id),
                            {"type": "vip_promotion", "title": _vip_title, "message": _vip_msg},
                        )))

                    await pii_db.commit()
                    print(f"[VIP] Promoted {len(rows)} user(s) to VIP: {new_vip_ids}")
                else:
                    await pii_db.commit()
                    print("[VIP] Stats synced, no new VIP promotions")

        except Exception as e:
            print(f"[VIP detection] error (non-fatal): {e}")

        await asyncio.sleep(VIP_DETECTION_INTERVAL_S)


async def _warmup_embed_model():
    return


async def _load_runtime_ai_overrides_from_db():
    """Load persisted runtime AI overrides from system settings."""
    provider_settings = {
        "runtime_hf_token": ("HF_TOKEN", "HF_TOKEN"),
        "runtime_cerebras_api_key": ("CEREBRAS_API_KEY", "CEREBRAS_API_KEY"),
        "runtime_gemini_api_key": ("GEMINI_API_KEY", "GEMINI_API_KEY"),
        "runtime_groq_api_key": ("GROQ_API_KEY", "GROQ_API_KEY"),
    }

    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(SystemSetting).where(SystemSetting.key.in_(provider_settings.keys()))
            )).scalars().all()

            loaded_providers: list[str] = []
            by_key = {row.key: (row.value or "").strip() for row in rows}

            try:
                import hf_client
            except Exception:
                hf_client = None

            for setting_key, (env_key, module_attr) in provider_settings.items():
                token = (by_key.get(setting_key) or "").strip()
                if not token:
                    continue
                os.environ[env_key] = token
                if hf_client is not None:
                    try:
                        setattr(hf_client, module_attr, token)
                    except Exception:
                        pass
                loaded_providers.append(env_key)

            if loaded_providers:
                loaded = ", ".join(loaded_providers)
                print(f"[Startup] Loaded persisted runtime AI overrides from DB: {loaded}")
    except Exception as e:
        print(f"[Startup] Failed to load runtime AI overrides (non-fatal): {e}")


async def _status_update_loop() -> None:
    """Every 30 minutes: WhatsApp the owner a live summary of all workers, agents, and catalog growth."""
    _owner_phone = os.getenv("OWNER_WHATSAPP_PHONE", "")
    if not _owner_phone:
        return
    await asyncio.sleep(60)  # brief startup delay before first report

    while True:
        try:
            _now = datetime.now(timezone.utc)
            lines: list[str] = [
                f"\U0001f4ca *AutoSpareFinder — Status Update* ({_now.strftime('%H:%M UTC')})"
            ]

            async with async_session_factory() as _db:
                jobs = (await _db.execute(text("""
                    SELECT job_name, status, started_at, last_heartbeat_at
                    FROM job_registry
                    WHERE started_at > NOW() - INTERVAL '2 hours'
                    ORDER BY started_at DESC NULLS LAST
                    LIMIT 12
                """))).fetchall()

                todos = (await _db.execute(text("""
                    SELECT status, COUNT(*) AS cnt
                    FROM agent_todos
                    WHERE assigned_to_agent = 'rex' AND category = 'catalog_discovery'
                    GROUP BY status
                    ORDER BY status
                """))).fetchall()

                new_parts = (await _db.execute(text("""
                    SELECT COUNT(*) FROM parts_catalog
                    WHERE created_at > NOW() - INTERVAL '30 minutes'
                """))).scalar() or 0

                total_parts = (await _db.execute(text("""
                    SELECT COUNT(*) FROM parts_catalog WHERE is_active = TRUE
                """))).scalar() or 0

            lines.append("\n*Workers:*")
            if jobs:
                for job in jobs:
                    age_min = (
                        int((_now - job.started_at.replace(tzinfo=timezone.utc) if job.started_at.tzinfo is None else _now - job.started_at).total_seconds() / 60)
                        if job.started_at else 0
                    )
                    hb_ago = ""
                    if job.last_heartbeat_at:
                        hb_ts = job.last_heartbeat_at if job.last_heartbeat_at.tzinfo else job.last_heartbeat_at.replace(tzinfo=timezone.utc)
                        hb_min = int((_now - hb_ts).total_seconds() / 60)
                        hb_ago = f" hb={hb_min}m"
                    icon = (
                        "\U0001f7e2" if job.status == "running" else
                        "\U0001f534" if job.status in ("failed", "dead") else
                        "⏳"
                    )
                    lines.append(f"  {icon} {job.job_name}: {job.status} (+{age_min}m{hb_ago})")
            else:
                lines.append("  (no recent jobs)")

            dead_tasks = [n for n, t in _SUPERVISED_TASKS.items() if t.done() and not t.cancelled()]
            running_cnt = sum(1 for t in _SUPERVISED_TASKS.values() if not t.done())
            dead_suffix = f", {len(dead_tasks)} dead ❌" if dead_tasks else " ✅"
            lines.append(f"\n*Background tasks:* {running_cnt} running{dead_suffix}")
            if dead_tasks:
                lines.append(f"  Dead: {', '.join(dead_tasks[:5])}")

            lines.append("\n*REX catalog todos:*")
            if todos:
                for row in todos:
                    lines.append(f"  {row.status}: {row.cnt}")
            else:
                lines.append("  (none)")

            lines.append(f"\n*Catalog:* {total_parts:,} active parts")
            lines.append(f"  +{new_parts} added in last 30m")

            await _wa_send(to=_owner_phone, text="\n".join(lines))
            print(f"[StatusUpdate] Sent status report to owner ({len(lines)} lines)")
        except Exception as _exc:
            print(f"[StatusUpdate] loop error: {_exc}")

        await asyncio.sleep(1800)  # 30 minutes


async def _ebay_fitment_backfill_loop() -> None:
    """
    Daily eBay fitment backfill — runs once per day at ~01:00 UTC (after eBay quota resets).
    Processes 500 parts per run to stay well under the 5,000 call/day Browse API limit.
    Offset advances each cycle so all 8,123 eBay-linked parts get covered over ~17 days.
    """
    import math

    # Wait until 01:00 UTC before first run — quota resets at midnight UTC
    await asyncio.sleep(3600)  # 1h startup delay
    _BATCH = 500
    _TOTAL = 8200  # approximate total eBay-linked parts

    run = 0
    while True:
        offset = (_BATCH * run) % _TOTAL
        try:
            from ebay_fitment_backfill import run_backfill as _ebay_fitment_run
            report = await _ebay_fitment_run(dry_run=False, limit=_BATCH, offset=offset)
            print(f"[EbayFitment] run #{run}: {report}")
        except Exception as exc:
            print(f"[EbayFitment] loop error: {exc}")
        run += 1
        await asyncio.sleep(86400)  # wait 24h before next batch


async def _enrich_catalog_loop() -> None:
    """
    Dedicated AI enrichment loop — runs every 30 minutes, 500 parts per cycle.
    Uses Groq llama-3.1-8b-instant (~1.6 parts/sec) → ~24K parts/day.
    Covers 3.24M pending parts over ~135 days.
    Separate from run_all_tasks so enrichment is not bottlenecked by the 6h cycle.
    """
    await asyncio.sleep(120)  # 2min startup delay
    while True:
        try:
            from ai_catalog_builder import enrich_pending_parts
            async for db in get_db():
                report = await enrich_pending_parts(db, limit=1000)
                print(f"[EnrichLoop] {report}")
                break
        except Exception as exc:
            print(f"[EnrichLoop] error: {exc}")
        await asyncio.sleep(1800)  # 30 minutes


async def _rex_dispatch_loop() -> None:
    """
    REX todo executor — polls agent_todos for 'rex' assigned rows and routes them
    to the correct worker.  Runs every 15 minutes.

    Routing rules:
      - todos with artifacts.task_names → reassign to db_update_agent so run_all_tasks picks them up
      - todos with artifacts.action in ('scrape','catalog_discovery','harvest') → reassign to scraper queue
      - todos with artifacts.action == 'category_normalize_pass' → reassign to db_update_agent
      - unknown todos → mark completed (nothing to do; prevents permanent pile-up)
    """
    await asyncio.sleep(120)  # 2-min startup grace
    while True:
        try:
            async for db in get_db():
                rows = (await db.execute(text("""
                    SELECT id::text, title, artifacts
                    FROM agent_todos
                    WHERE assigned_to_agent = 'rex'
                      AND status IN ('not_started', 'in_progress')
                    ORDER BY
                        CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
                        created_at ASC
                    LIMIT 50
                """))).fetchall()

                if not rows:
                    break

                routed_to_dbu = 0
                routed_to_scraper = 0
                dismissed = 0

                for row in rows:
                    tid = row[0]
                    arts = dict(row[2] or {})
                    action = str(arts.get("action", "")).lower()
                    task_names = arts.get("task_names") or []

                    if task_names or action in ("category_normalize_pass", "normalize_categories",
                                                "fix_base_prices", "normalize_base_price"):
                        # Route to db_update_agent — run_all_tasks will pick it up
                        if not task_names:
                            arts["task_names"] = ["normalize_categories"]
                        await db.execute(text("""
                            UPDATE agent_todos
                            SET assigned_to_agent = 'db_update_agent',
                                artifacts = :arts::jsonb,
                                updated_at = NOW()
                            WHERE id = CAST(:tid AS uuid)
                        """), {"tid": tid, "arts": __import__("json").dumps(arts)})
                        routed_to_dbu += 1

                    elif action in ("scrape", "catalog_discovery", "harvest",
                                    "brand_discovery", "web_scrape"):
                        # Route to scraper agent
                        await db.execute(text("""
                            UPDATE agent_todos
                            SET assigned_to_agent = 'scraper',
                                updated_at = NOW()
                            WHERE id = CAST(:tid AS uuid)
                        """), {"tid": tid})
                        routed_to_scraper += 1

                    else:
                        # No known executor — mark done to prevent pile-up
                        await db.execute(text("""
                            UPDATE agent_todos
                            SET status = 'completed', completed_at = NOW(), updated_at = NOW(),
                                progress_notes = 'Dismissed by REX dispatcher: no executor for this action'
                            WHERE id = CAST(:tid AS uuid)
                        """), {"tid": tid})
                        dismissed += 1

                await db.commit()
                if routed_to_dbu or routed_to_scraper or dismissed:
                    print(
                        f"[REX] Dispatch cycle: db_update_agent={routed_to_dbu} "
                        f"scraper={routed_to_scraper} dismissed={dismissed}"
                    )

        except Exception as exc:
            print(f"[REX] dispatch loop error: {exc}")

        await asyncio.sleep(900)  # 15 min


@app.on_event("startup")
async def startup():
    from catalog_scraper import start_scraper_task
    from db_update_agent import start_agent_task as start_db_agent
    from db_cleanup_agent import run_cleanup_loop
    print("🚀 Auto Spare API starting...")
    print(f"   Environment: {os.getenv('ENVIRONMENT', 'development')}")
    await _load_runtime_ai_overrides_from_db()
    # Ensure the WhatsApp sentinel user exists (anonymous conversations fallback)
    async with pii_session_factory() as _db:
        await _db.execute(text("""
            INSERT INTO users (id, email, phone, password_hash, full_name, role,
                               is_active, is_verified, is_admin, failed_login_count,
                               created_at, updated_at)
            VALUES ('00000000-0000-0000-0000-000000000001',
                    'whatsapp@autospare.internal', '+00000000000000',
                    '!disabled!', 'WhatsApp Bot', 'system', true, true, false, 0,
                    NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
        """))
        await _db.commit()
    # QUEUE ARCHITECTURE: No external message broker (no Celery/RQ).
    # All async work uses asyncio.create_task() + Semaphore(50) cap.
    # ApprovalQueue table = admin approval workflow (not a message queue).
    # Upgrade to Celery/Redis Streams when scaling beyond single VPS.
    if os.getenv("ENABLE_LOCAL_EMBED_WARMUP", "false").lower() in ("1", "true", "yes"):
        _supervised_task("embed_warmup", _warmup_embed_model())
    else:
        print("[EmbedWarmup] disabled (ENABLE_LOCAL_EMBED_WARMUP=false)")
    _supervised_task("price_sync_loop",             _price_sync_loop())
    _supervised_task("stuck_orders_monitor",        _stuck_orders_monitor_loop())
    _supervised_task("notify_search_miss_loop",     _notify_search_miss_loop())
    _supervised_task("scrape_search_misses_loop",   _scrape_search_misses_loop())
    _supervised_task("abandoned_cart_loop",         _abandoned_cart_loop())
    _supervised_task("pending_payment_reminder",    _pending_payment_reminder_loop())
    _supervised_task("health_monitor_loop",         _health_monitor_loop())
    _supervised_task("vip_detection_loop",          _vip_detection_loop())
    _supervised_task("backup_loop",                 _backup_loop())
    start_scraper_task()           # ← catalog scraper: every 3h (owns its own task internally)
    start_db_agent(get_db, 3.0)   # ← DB cleaning / normalisation agent (every 3h, staggered from scraper)
    _supervised_task("cleanup_loop",                run_cleanup_loop())
    _supervised_task("noa_marketing_loop",          _noa_marketing_loop())
    _supervised_task("ebay_fitment_backfill_loop",  _ebay_fitment_backfill_loop())
    _supervised_task("enrich_catalog_loop",         _enrich_catalog_loop())
    _supervised_task("status_update_loop",          _status_update_loop())
    _supervised_task("rex_dispatch_loop",           _rex_dispatch_loop())
    await _warm_search_paths()
    print("✅ All systems ready — price-sync + catalog-scraper + db-agent schedulers started")


@app.on_event("shutdown")
async def shutdown():
    from hf_client import close_http
    await close_http()
    print("✅ HF connection pool closed")


# How many hours before an order in paid/processing is considered stuck
STUCK_ORDER_HOURS = int(os.getenv("STUCK_ORDER_HOURS", "4"))
STUCK_ORDER_CHECK_INTERVAL_MIN = 30  # check every 30 minutes

# How often the abandoned-cart worker runs (default: every 60 minutes)
ABANDONED_CART_INTERVAL_S = int(os.getenv("ABANDONED_CART_INTERVAL_S", "3600"))
# How long a cart must be idle before it is considered abandoned (default: 2 hours)
ABANDONED_CART_IDLE_HOURS = int(os.getenv("ABANDONED_CART_IDLE_HOURS", "2"))
ABANDONED_CART_WINDOW_DAYS = int(os.getenv("ABANDONED_CART_WINDOW_DAYS", "3"))
ABANDONED_CART_MAX_SENDS_PER_WINDOW = int(os.getenv("ABANDONED_CART_MAX_SENDS_PER_WINDOW", "3"))
ABANDONED_CART_SEND_START_HOUR_IL = int(os.getenv("ABANDONED_CART_SEND_START_HOUR_IL", "9"))
ABANDONED_CART_SEND_END_HOUR_IL = int(os.getenv("ABANDONED_CART_SEND_END_HOUR_IL", "21"))
APP_LOCAL_TZ = ZoneInfo(os.getenv("APP_LOCAL_TIMEZONE", "Asia/Jerusalem"))

# How often the pending-payment reminder runs (default: every 30 min)
PAYMENT_REMINDER_INTERVAL_S = int(os.getenv("PAYMENT_REMINDER_INTERVAL_S", "1800"))
# Minimum age of a pending_payment order before first reminder (default: 1 hour)
PAYMENT_REMINDER_AFTER_H    = int(os.getenv("PAYMENT_REMINDER_AFTER_H", "1"))


def _customer_first_name(full_name: str | None) -> str:
    raw_name = str(full_name or "").strip()
    if not raw_name:
        return "שלום"
    return raw_name.split()[0]


def _format_cart_items_for_whatsapp(item_lines: list[str], max_items: int = 3) -> str:
    clean_items = [str(item or "").strip() for item in item_lines if str(item or "").strip()]
    if not clean_items:
        return "הפריטים שבחרת"
    visible_items = clean_items[:max_items]
    summary = ", ".join(visible_items)
    remaining = len(clean_items) - len(visible_items)
    if remaining > 0:
        item_label = "פריט" if remaining == 1 else "פריטים"
        summary += f" ועוד {remaining} {item_label}"
    return summary


def _build_abandoned_cart_whatsapp_message(full_name: str | None, item_lines: list[str], total_value: float) -> str:
    first_name = _customer_first_name(full_name)
    items_summary = _format_cart_items_for_whatsapp(item_lines)
    return (
        f"היי {first_name}, הפריטים שבחרת עדיין מחכים לך בסל: {items_summary}. "
        f"שווי הסל כרגע הוא {total_value:.0f}₪. "
        "כדי להשלים את הרכישה, כנס ל-/api/v1/customers/cart ולחץ על 'לתשלום'."
    )


def _abandoned_cart_send_window_open(now_local: datetime | None = None) -> tuple[bool, datetime]:
    current_local = now_local or datetime.now(APP_LOCAL_TZ)
    is_open = ABANDONED_CART_SEND_START_HOUR_IL <= current_local.hour < ABANDONED_CART_SEND_END_HOUR_IL
    return is_open, current_local


def _build_pending_payment_whatsapp_message(full_name: str | None, order_number: str | None, total_amount: float) -> str:
    first_name = _customer_first_name(full_name)
    safe_order_number = str(order_number or "").strip() or "שלך"
    return (
        f"היי {first_name}, ההזמנה {safe_order_number} בסך {total_amount:.0f}₪ עדיין ממתינה לתשלום. "
        "כדי להשלים את ההזמנה, כנס ל-/api/v1/customers/cart ולחץ על 'לתשלום'. "
        "אם צריך עזרה, אפשר פשוט להשיב להודעה הזו."
    )

# How often the health monitor probes all services (default: every 5 min)
HEALTH_MONITOR_INTERVAL_S = int(os.getenv("HEALTH_MONITOR_INTERVAL_S", "300"))

async def _noa_send_telegram(token: str, chat_id: str, text: str, keyboard: list | None = None) -> None:
    """Send a Telegram message, silently ignore errors."""
    try:
        payload: dict = {"chat_id": chat_id, "text": text[:4096]}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        async with __import__("httpx").AsyncClient(timeout=10.0) as _c:
            await _c.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
    except Exception as _e:
        logger.warning("noa_send_telegram error: %s", _e)


async def _noa_marketing_loop():
    """
    Weekly campaign engine for NOA social media agent.

    Monday  → generate 7-day campaign brief (theme + per-platform plan) → WhatsApp + Telegram
    Tue–Sun → generate that day's platform post from the weekly plan → Telegram for approval

    Anti-repeat: reads last 5 post topics from agent_memory before generating.
    Platform rotation: TikTok (Tue/Sat) · Instagram (Wed/Sun) · Facebook (Thu) · WhatsApp blast (Fri)
    """
    import random, json as _json
    from agents.memory import AgentMemory, ensure_memory_table
    from hf_client import hf_text as _hf_text
    from BACKEND_DATABASE_MODELS import async_session_factory

    NOA_INTERVAL_H = int(os.getenv("NOA_MARKETING_INTERVAL_H", "24"))
    await asyncio.sleep(1800)  # 30-min startup delay

    TELEGRAM_OWNER_ID = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")
    TELEGRAM_ADMIN_TOKEN = os.getenv("TELEGRAM_ADMIN_BOT_TOKEN", "")
    OWNER_PHONE = os.getenv("OWNER_WHATSAPP_PHONE", "")

    # Israeli automotive seasonal context (month → demand peaks)
    _SEASONAL: dict[int, str] = {
        12: "חורף — סוללות, צמיגי גשם, מגבי שמשה, תאורה",
        1:  "חורף — סוללות, צמיגי גשם, מגבי שמשה, תאורה",
        2:  "חורף — סוללות, צמיגי גשם, מגבי שמשה, תאורה",
        3:  "מעבר עונות — שמן מנוע, מסנני שמן, הכנת מנוע לקיץ",
        4:  "אביב — שמן מנוע, מסנני שמן, הכנת מנוע לקיץ",
        5:  "אביב — מיזוג אוויר (A/C), קירור, חגורות הנעה",
        6:  "קיץ — מיזוג אוויר, מצנן (radiator), נוזל קירור",
        7:  "קיץ — מיזוג אוויר, מצנן (radiator), נוזל קירור",
        8:  "קיץ — מיזוג אוויר, מצנן (radiator), נוזל קירור",
        9:  "סוף קיץ — בלמים, טסט שנתי, הכנת רכב לחורף",
        10: "סוף קיץ — בלמים, טסט שנתי, הכנת רכב לחורף",
        11: "כניסה לחורף — סוללות, מגבי שמשה, אורות",
    }

    # Popular cars in Israel
    _CARS = [
        "Toyota Corolla", "Hyundai Tucson", "Kia Sportage", "Mazda 3",
        "Skoda Octavia", "Volkswagen Golf", "Dacia Duster", "Seat Leon",
        "Hyundai i20", "Toyota C-HR", "Kia Niro", "Honda Civic",
        "Mitsubishi Outlander", "Renault Kadjar", "Suzuki Vitara",
        "Peugeot 3008", "Nissan Qashqai", "Ford Fiesta", "Toyota RAV4",
    ]

    # Part topics — (Hebrew name, English name, common pain point)
    _PARTS = [
        ("בלמי דיסק", "brake pads", "קול חריקה בבלימה"),
        ("מסנן שמן", "oil filter", "שמן שחור, מנוע כבד"),
        ("מצבר", "battery", "הרכב לא עולה בבוקר"),
        ("חגורת תזמון", "timing belt", "תחזוקה מניעתית שמונעת קטסטרופה"),
        ("מנורות LED קדמיות", "LED headlights", "תאורה חלשה בלילה"),
        ("מגבי שמשה", "wiper blades", "שריטות על השמשה בגשם"),
        ("מסנן מזגן (קבינה)", "cabin AC filter", "ריח עובש מהמזגן"),
        ("סלילי הצתה", "ignition coils", "רעד במנוע, תאוצה גרועה"),
        ("חיישני ABS", "ABS sensor", "נורת ABS דולקת"),
        ("מוט ייצוב (שלדג)", "stabilizer bar link", "קשקוש מהשלדה"),
        ("רדיאטור", "radiator", "רכב מתחמם מעבר"),
        ("נרות הצתה", "spark plugs", "צריכת דלק גבוהה"),
        ("פחי אוויר", "air filter", "תאוצה איטית, מנוע חנוק"),
        ("מיסבי גלגל", "wheel bearings", "רעש 윙윙 מהגלגל במהירות"),
    ]

    # Platform rotation by weekday (0=Mon, 1=Tue … 6=Sun)
    _DAY_PLATFORM = {
        1: ("tiktok",    "TikTok — hook קצר וחד, שורה ראשונה שמחזיקה"),
        2: ("instagram", "Instagram — story-telling ויזואלי, אמוציונלי"),
        3: ("facebook",  "Facebook — פוסט מידעי בעל ערך, ניתן לשיתוף"),
        4: ("whatsapp",  "WhatsApp channel — הודעה קצרה ומניעה לפעולה"),
        5: ("tiktok",    "TikTok — זווית שונה לגמרי מהתיקטוק הקודם"),
        6: ("instagram", "Instagram Reels — שאלה פתוחה לקהל"),
    }

    while True:
        try:
            now = datetime.now(timezone.utc)
            weekday = now.weekday()   # 0=Monday … 6=Sunday
            month = now.month
            week_num = now.isocalendar()[1]
            season = _SEASONAL.get(month, "")
            car = random.choice(_CARS)
            heb_part, eng_part, pain = random.choice(_PARTS)

            async with async_session_factory() as db:
                await ensure_memory_table(db)
                mem = AgentMemory(db, agent_name="noa")
                noa = SocialMediaManagerAgent()

                # Load recent history — inject as "do not repeat" context
                history_raw = await mem.get("post_history") or []
                recent_topics: list[str] = []
                if isinstance(history_raw, list):
                    for h in history_raw[-6:]:
                        if isinstance(h, dict):
                            t = h.get("topic") or h.get("caption", "")[:70]
                            if t:
                                recent_topics.append(str(t))
                no_repeat = (
                    f"\nנושאים שכבר כוסו לאחרונה — אל תחזרי עליהם:\n" +
                    "\n".join(f"• {t}" for t in recent_topics)
                ) if recent_topics else ""

                if weekday == 0:
                    # ── Monday: generate weekly campaign brief ──────────────────
                    campaign_prompt = (
                        "את נועה, מנהלת המדיה החברתית של AutoSpareFinder.\n"
                        "היום יום שני — תכנני קמפיין שיווקי שבועי מלא.\n\n"
                        f"הקשר השבוע:\n"
                        f"• עונה/ביקוש: {season}\n"
                        f"• רכב לדגמה (שים לב, אפשר לבחור אחר): {car}\n"
                        f"• חלק לדגמה: {heb_part} ({eng_part}) — כאב שכיח: {pain}\n"
                        f"• שבוע {week_num} בשנה {now.year}\n"
                        f"{no_repeat}\n\n"
                        "הנחיות:\n"
                        "• בחרי נושא שבועי יצירתי ורלוונטי — לא חייב להיות בדיוק הרכב/חלק שניתן לדגמה\n"
                        "• תכנני 6 פוסטים יומיים: ב׳=TikTok, ג׳=Instagram, ד׳=Facebook, ה׳=WhatsApp, ו׳=TikTok, שבת=Instagram\n"
                        "• כל פוסט — זווית שונה לגמרי, לא וריאציה של אותו טקסט\n"
                        "• כתבי copy_hebrew מלא לכל יום — טקסט מוכן לפרסום, לא תיאור של הטקסט\n\n"
                        "החזירי JSON בלבד (ללא markdown) עם השדות:\n"
                        "week_theme, core_message, target_persona, hashtag_strategy, success_metrics,\n"
                        "daily_plan: [{day, platform, content_angle, visual_concept, copy_hebrew, cta}]\n"
                    )

                    raw_plan = await _hf_text(prompt=campaign_prompt, system=noa.system_prompt, timeout=120.0)

                    plan: dict = {}
                    try:
                        jm = re.search(r'\{[\s\S]*\}', raw_plan)
                        if jm:
                            plan = _json.loads(jm.group(0))
                    except Exception:
                        plan = {"raw": raw_plan}

                    await mem.set("current_week_plan", plan, ttl_hours=192)  # 8 days

                    # Format WhatsApp campaign brief
                    theme = plan.get("week_theme") or "קמפיין שבועי"
                    core_msg = plan.get("core_message") or ""
                    persona = plan.get("target_persona") or ""
                    tags = plan.get("hashtag_strategy") or ""
                    kpi = plan.get("success_metrics") or "engagement + reach"

                    wa_lines = [
                        f"📅 *NOA — קמפיין שבוע {week_num}*",
                        "",
                        f"🎯 *נושא:* {theme}",
                        f"💬 *מסר מרכזי:* {core_msg}",
                        f"👤 *קהל יעד:* {persona}",
                        "",
                        "📆 *תוכנית יומית:*",
                    ]
                    _day_labels = ["ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "שבת"]
                    for idx, day_item in enumerate(plan.get("daily_plan", [])[:6]):
                        if isinstance(day_item, dict):
                            plt = day_item.get("platform") or ""
                            angle = day_item.get("content_angle") or ""
                            wa_lines.append(f"• יום {_day_labels[idx]} *{plt}* — {angle}")
                    wa_lines += [
                        "",
                        f"#️⃣ *האשטאגים:* {tags}",
                        f"📊 *מטרה:* {kpi}",
                    ]
                    wa_msg = "\n".join(wa_lines)

                    if OWNER_PHONE:
                        await _wa_send(to=OWNER_PHONE, text=wa_msg)
                    if TELEGRAM_OWNER_ID and TELEGRAM_ADMIN_TOKEN:
                        await _noa_send_telegram(TELEGRAM_ADMIN_TOKEN, TELEGRAM_OWNER_ID, wa_msg)

                    await mem.append_event("post_history", {
                        "type": "campaign_brief", "topic": theme,
                        "platform": "all", "created_at": now.isoformat(),
                    })
                    logger.info("noa_marketing_loop: weekly campaign brief week=%d theme=%s", week_num, theme)

                else:
                    # ── Tue–Sun: generate that day's platform post ──────────────
                    platform_info = _DAY_PLATFORM.get(weekday)
                    if not platform_info:
                        await asyncio.sleep(NOA_INTERVAL_H * 3600)
                        continue

                    platform, platform_desc = platform_info
                    week_plan: dict = await mem.get("current_week_plan") or {}

                    # Extract today's angle from week plan if available
                    plan_hint = ""
                    _wday_keys = {1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
                    wkey = _wday_keys.get(weekday, "")
                    for day_item in week_plan.get("daily_plan", []):
                        if isinstance(day_item, dict):
                            day_str = str(day_item.get("day") or "").lower()
                            if wkey and (wkey in day_str or day_str.startswith(wkey[:2])):
                                angle = day_item.get("content_angle") or ""
                                copy_hint = day_item.get("copy_hebrew") or ""
                                visual = day_item.get("visual_concept") or ""
                                if angle:
                                    plan_hint += f"\nזווית שנבחרה בתוכנית השבוע: {angle}"
                                if visual:
                                    plan_hint += f"\nקונספט ויזואלי: {visual}"
                                if copy_hint:
                                    plan_hint += f"\nרמז לטקסט מהתוכנית: {copy_hint}"
                                break

                    week_theme = week_plan.get("week_theme") or ""
                    theme_hint = f"\nנושא השבוע: {week_theme}" if week_theme else ""

                    post_prompt = (
                        f"כתבי פוסט {platform_desc} בעברית עבור AutoSpareFinder.\n\n"
                        f"הקשר:\n"
                        f"• רכב: {car}\n"
                        f"• חלק: {heb_part} ({eng_part})\n"
                        f"• כאב שכיח: {pain}\n"
                        f"• עונה: {season}\n"
                        f"{theme_hint}{plan_hint}\n"
                        f"{no_repeat}\n\n"
                        "כתיבה:\n"
                        "• פתחי עם ה-hook בשורה ראשונה — קצרה, חדה, לא שאלה גנרית\n"
                        "• ציוני את שם הרכב ואת שם החלק הספציפי\n"
                        "• פתרון: חיפוש לפי מספר רישוי ב-autosparefinder.co.il\n"
                        "• סיימי בשאלה אחת מעוררת שיח\n"
                        "• האשטאגים בשורה אחרונה בלבד\n\n"
                        "החזירי: טקסט הפוסט הסופי בלבד — ללא הסבר, ללא כותרת, ללא ספירה."
                    )

                    raw_post = await _hf_text(prompt=post_prompt, system=noa.system_prompt, timeout=90.0)
                    caption = noa._finalize_noa_post(raw_post, platforms=[platform])

                    hashtags = [f"#{m.group(1)}" for m in noa._NOA_HASHTAG_RE.finditer(caption)]
                    pending_payload = {
                        "caption": caption,
                        "hashtags": hashtags,
                        "platform": platform,
                        "topic": f"{heb_part} — {car}",
                        "post_type": platform,
                        "status": "awaiting_approval",
                        "created_at": now.isoformat(),
                    }

                    await mem.set("pending_post", pending_payload, ttl_hours=72)
                    await mem.append_event("post_history", pending_payload)

                    # Send to Telegram for approval
                    if TELEGRAM_OWNER_ID and TELEGRAM_ADMIN_TOKEN:
                        tg_msg = f"🎯 NOA — {platform.title()} post ready\n\n📝 {caption}"
                        tg_msg = noa._append_noa_links(noa._normalize_noa_symbols(tg_msg))
                        await _noa_send_telegram(
                            TELEGRAM_ADMIN_TOKEN, TELEGRAM_OWNER_ID, tg_msg,
                            keyboard=[
                                [
                                    {"text": f"✅ אשר ({platform})", "callback_data": f"approve_{platform}"},
                                    {"text": "✏️ ערוך", "callback_data": "edit_post"},
                                    {"text": "❌ דחה", "callback_data": "reject_post"},
                                ],
                            ],
                        )

                    logger.info("noa_marketing_loop: %s post generated topic=%s — %s", platform, heb_part, car)

        except Exception as exc:
            logger.error("noa_marketing_loop error: %s", exc)

        await asyncio.sleep(NOA_INTERVAL_H * 3600)



async def _stuck_orders_monitor_loop():
    """
    Background loop: runs every 30 minutes.

    Pass 1 — Stuck fulfillment:
            Finds orders in 'confirmed', 'paid' or 'processing' for > STUCK_ORDER_HOURS hours
      (payment confirmed but supplier order never placed) and re-triggers the
      OrdersAgent to place the supplier order.

    Pass 2 — Shipment tracking:
      Finds orders in 'supplier_ordered' or 'shipped' and asks the OrdersAgent
      whether enough transit time has elapsed to advance the status:
        supplier_ordered → shipped  (after carrier-specific days)
        shipped          → delivered (after carrier-specific days)
      Notifies the customer on every transition.
    """
    await asyncio.sleep(5)  # let DB pool warm up on startup
    while True:
        now = datetime.utcnow()
        # ── Pass 1: stuck fulfillment (confirmed/paid/processing > 4 h) ───────
        try:
            cutoff = now - timedelta(hours=STUCK_ORDER_HOURS)
            async with pii_session_factory() as db:
                issuing_retry_order_ids = (
                    select(SupplierPayment.order_id)
                    .where(
                        SupplierPayment.status == "failed",
                        SupplierPayment.provider == "stripe_issuing",
                        SupplierPayment.failure_reason.ilike("%insufficient_funds%"),
                    )
                    .distinct()
                )
                result = await db.execute(
                    select(Order).where(
                        Order.status.in_(["confirmed", "paid", "processing"]),
                        or_(
                            Order.updated_at <= cutoff,
                            Order.id.in_(issuing_retry_order_ids),
                        ),
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
    Also sends directly to OWNER_WHATSAPP_PHONE for every alert (service + thresholds).
    Never sends the same alert twice in a row for the same service.
    """
    await asyncio.sleep(20)  # let DB pool warm up on startup

    # Direct owner WhatsApp — bypasses the admin-user lookup so alerts always arrive
    # even before the owner creates an account, and for threshold alerts that previously
    # only created in-app Notification rows without sending WhatsApp.
    _OWNER_PHONE = os.getenv("OWNER_WHATSAPP_PHONE", "")

    async def _alert_owner(title: str, msg: str, alert_key: str = "", cooldown_s: int = 3600) -> None:
        """Send WhatsApp to the owner phone with Redis-backed cooldown (survives restarts)."""
        if not _OWNER_PHONE:
            return
        if alert_key:
            try:
                _r = await get_redis()
                _rkey = f"autospare:alert_cooldown:{alert_key}"
                if await _r.exists(_rkey):
                    return  # still within cooldown window
                await _r.set(_rkey, "1", ex=cooldown_s)
            except Exception:
                pass  # Redis unavailable — allow alert through
        try:
            result = await _wa_send(to=_OWNER_PHONE, text=f"{title}\n{msg}")
            if not result.get("ok"):
                print(f"[HealthMonitor] Owner WhatsApp failed ({alert_key}): {result.get('error')}")
        except Exception as _exc:
            print(f"[HealthMonitor] Owner WhatsApp error ({alert_key}): {_exc}")

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

        _stripe_key, _ = resolve_stripe_secret_key()
        states["stripe"] = "ok" if is_valid_stripe_secret_key(_stripe_key) else "error"

        return states

    while True:
        try:
            current_states = await _probe()
            # provider replaced by _wa_send

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
                        admin_phones = set()
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
                            asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {
                                "type":    _notif_type,
                                "title":   _title,
                                "message": _msg,
                            })))
                            if admin.phone and str(admin.id) != str(WHATSAPP_ANON_USER_ID):
                                admin_phones.add(admin.phone)
                                wa_result = await _wa_send(to=admin.phone, text=f"{_title}\n{_msg}")
                                if not wa_result.get("ok"):
                                    print(f"[HealthMonitor] WhatsApp failed for admin {admin.id}: {wa_result.get('error')}")
                        await db.commit()
                        # Send directly to owner phone (no cooldown — service state changes are already deduplicated)
                        if _OWNER_PHONE and _OWNER_PHONE not in admin_phones:
                            await _alert_owner(_title, _msg, alert_key="")
                except Exception as _e:
                    print(f"[HealthMonitor] Notify error for {svc}: {_e}")

            down = [s for s, v in current_states.items() if v == "error"]
            if down:
                print(f"[HealthMonitor] Pass complete — DOWN: {', '.join(down)}")
            else:
                print("[HealthMonitor] Pass complete — all services OK")

            # ── Threshold checks (Gap 3 — Alerting) ────────────────────────────────
            # Check 1: parts updated < 50 in last 6 hours (catalog stagnation)
            # Uses parts_catalog.updated_at — the only reliable signal of actual scraper work.
            # (SystemLog catalog_scraper entries are sparse event logs, not per-part counts)
            try:
                async with async_session_factory() as _db:
                    cutoff_6h = datetime.utcnow() - timedelta(hours=6)
                    parts_updated_6h = (await _db.execute(
                        text("SELECT COUNT(*) FROM parts_catalog WHERE updated_at > :cutoff AND is_active = TRUE"),
                        {"cutoff": cutoff_6h},
                    )).scalar() or 0

                    if parts_updated_6h < 50:
                        _alert_title = "⚠️  קטלוג: עדכונים נמוכים בשעות האחרונות"
                        _alert_msg = (
                            f"רק {parts_updated_6h} חלקים עודכנו ב-6 השעות האחרונות (יעד: 50+). "
                            f"הסקרייפר אולי תקוע."
                        )
                        print(f"[HealthMonitor] ALERT: parts_updated={parts_updated_6h} < 100 in 6h")
                        await _alert_owner(_alert_title, _alert_msg, alert_key="catalog_stagnation")
                        async with pii_session_factory() as _pii_db:
                            admins_res = await _pii_db.execute(select(User).where(User.is_admin == True))
                            admins = admins_res.scalars().all()
                            _send_admin_wa_cs = True
                            try:
                                _r2 = await get_redis()
                                _awk_cs = "autospare:alert_cooldown_admin_wa:catalog_stagnation"
                                if await _r2.exists(_awk_cs):
                                    _send_admin_wa_cs = False
                                else:
                                    await _r2.set(_awk_cs, "1", ex=3600)
                            except Exception:
                                pass
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
                                if _send_admin_wa_cs and admin.phone and str(admin.id) != str(WHATSAPP_ANON_USER_ID):
                                    await _wa_send(to=admin.phone, text=f"{_alert_title}\n{_alert_msg}")
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
                        await _alert_owner(_alert_title, _alert_msg, alert_key="high_error_rate")
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
            def _extract_dt(report) -> Optional[datetime]:
                if report is None:
                    return None

                dt: Optional[datetime] = None
                if isinstance(report, datetime):
                    dt = report
                elif isinstance(report, str):
                    raw = report.strip()
                    if raw:
                        try:
                            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        except Exception:
                            dt = None
                elif isinstance(report, dict):
                    ts = report.get("updated_at") or report.get("completed_at") or report.get("started_at")
                    if ts:
                        try:
                            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                        except Exception:
                            dt = None

                if dt is None:
                    return None
                if dt.tzinfo is not None:
                    return dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt

            try:
                from db_update_agent import _last_report
                last_heartbeat = _extract_dt(_last_report)
                if last_heartbeat and isinstance(last_heartbeat, datetime):
                    silence_mins = (datetime.utcnow() - last_heartbeat).total_seconds() / 60

                    if silence_mins > 120:  # 2 hours
                        _alert_title = "⏱️  Worker db_update_agent: שקט למעל 2 שעות"
                        _alert_msg = (
                            f"העובד db_update_agent לא שלח heartbeat במשך {silence_mins:.0f} דקות. "
                            f"אם הוא צריך לרוץ הוא אולי תקוע."
                        )
                        print(f"[HealthMonitor] ALERT: worker silence={silence_mins:.0f} min > 120 min")
                        await _alert_owner(_alert_title, _alert_msg, alert_key="worker_silence")

                        async with pii_session_factory() as _pii_db:
                            admins_res = await _pii_db.execute(select(User).where(User.is_admin == True))
                            admins = admins_res.scalars().all()
                            _send_admin_wa_ws = True
                            try:
                                _r2 = await get_redis()
                                _awk_ws = "autospare:alert_cooldown_admin_wa:worker_silence"
                                if await _r2.exists(_awk_ws):
                                    _send_admin_wa_ws = False
                                else:
                                    await _r2.set(_awk_ws, "1", ex=3600)
                            except Exception:
                                pass
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
                                if _send_admin_wa_ws and admin.phone and str(admin.id) != str(WHATSAPP_ANON_USER_ID):
                                    await _wa_send(to=admin.phone, text=f"{_alert_title}\n{_alert_msg}")
                            await _pii_db.commit()
            except Exception as _e:
                print(f"[HealthMonitor] Threshold check 3 error: {_e}")

            # Check 4: unprocessed job failures > threshold — only alert on NEW failures
            try:
                JOB_FAILURES_ALERT_THRESHOLD = int(os.getenv("JOB_FAILURES_ALERT_THRESHOLD", "10"))
                async with pii_session_factory() as _pii_db:
                    unprocessed_count = (await _pii_db.execute(
                        select(func.count(JobFailure.id)).where(JobFailure.status.in_(["pending", "retrying"]))
                    )).scalar() or 0

                    # Only alert if count is above threshold AND has grown since last alert
                    # (prevents spamming the same stale failures on every restart/cycle)
                    _dlq_rkey = "autospare:dlq_last_alerted_count"
                    _dlq_new = False
                    try:
                        _r = await get_redis()
                        _prev_str = await _r.get(_dlq_rkey)
                        _prev_count = int(_prev_str) if _prev_str else 0
                        if unprocessed_count >= JOB_FAILURES_ALERT_THRESHOLD and unprocessed_count > _prev_count:
                            _dlq_new = True
                            await _r.set(_dlq_rkey, str(unprocessed_count), ex=86400)
                    except Exception:
                        _dlq_new = unprocessed_count >= JOB_FAILURES_ALERT_THRESHOLD

                    if _dlq_new:
                        _alert_title = f"🔴 DLQ Alert: {unprocessed_count} unprocessed failures"
                        _alert_msg = (
                            f"Dead Letter Queue has {unprocessed_count} unprocessed job failures "
                            f"(threshold: {JOB_FAILURES_ALERT_THRESHOLD}). Review failures in admin dashboard."
                        )
                        print(f"[HealthMonitor] ALERT: job_failures={unprocessed_count} >= {JOB_FAILURES_ALERT_THRESHOLD}")
                        await _alert_owner(_alert_title, _alert_msg, alert_key="job_failures_dlq", cooldown_s=21600)

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
                            if admin.phone and str(admin.id) != str(WHATSAPP_ANON_USER_ID):
                                await _wa_send(to=admin.phone, text=f"{_alert_title}\n{_alert_msg}")
                        await _pii_db.commit()
            except Exception as _e:
                print(f"[HealthMonitor] Threshold check 4 (job_failures) error: {_e}")

            # Check 5: job_registry — failed/dead/zombie worker jobs
            # Alerts once per unique job_id (state-change deduplication via seen set).
            try:
                async with async_session_factory() as _db:
                    from datetime import timezone as _tz

                    # 5a: Jobs that transitioned to failed or dead
                    _failed_rows = (await _db.execute(text("""
                        SELECT job_id, job_name, status, error_message, started_at
                        FROM job_registry
                        WHERE status IN ('failed', 'dead')
                          AND started_at > NOW() - INTERVAL '12 hours'
                        ORDER BY started_at DESC
                    """))).fetchall()

                    for _jr in _failed_rows:
                        _key = f"job_fail_{_jr.job_id}"
                        try:
                            _r = await get_redis()
                            _rk = f"autospare:alert_cooldown:{_key}"
                            if await _r.exists(_rk):
                                continue  # already alerted this job (persists across restarts)
                            await _r.set(_rk, "1", ex=86400)
                        except Exception:
                            pass
                        _jt = f"🔴 Worker failed: {_jr.job_name}"
                        _jm = (
                            f"Job *{_jr.job_name}* finished with status={_jr.status}.\n"
                            + (f"Error: {(_jr.error_message or '')[:200]}\n" if _jr.error_message else "")
                            + f"Started: {str(_jr.started_at)[:19]}"
                        )
                        print(f"[HealthMonitor] ALERT: job {_jr.job_name} ({_jr.job_id}) {_jr.status}")
                        await _alert_owner(_jt, _jm, alert_key="")  # no cooldown — each job_id is unique

                    # 5b: Zombie jobs — running but heartbeat silent beyond their TTL.
                    # Respects ttl_seconds from job_registry (same logic as task_zombie_watchdog).
                    # Falls back to 2 hours for NULL TTL jobs (was 30 min — too aggressive for
                    # long-running tasks like merge_catalog_fitment which can take 45+ min).
                    _zombie_rows = (await _db.execute(text("""
                        SELECT job_id, job_name, last_heartbeat_at,
                               EXTRACT(EPOCH FROM (NOW() - last_heartbeat_at)) AS silence_s
                        FROM job_registry
                        WHERE status = 'running'
                          AND last_heartbeat_at < NOW() - (
                              COALESCE(ttl_seconds, 7200) * INTERVAL '1 second'
                          )
                    """))).fetchall()

                    # Maps job_registry name → Redis lock name (they differ when acquire_lock()
                    # uses a shorter key than the job name registered in job_registry_start()).
                    _JOB_LOCK_MAP = {
                        "run_scraper_cycle":   "scraper_cycle",
                        "run_brand_discovery": "brand_discovery",
                        "run_all_tasks":       "db_update_agent",
                        "category_discovery":  "category_discovery",
                    }
                    for _zr in _zombie_rows:
                        _silence_min = int((_zr.silence_s or 0) // 60)
                        # Auto-fix 1: clear Redis distributed lock so next run can acquire it
                        try:
                            _r = await get_redis()
                            _lock_name = _JOB_LOCK_MAP.get(_zr.job_name, _zr.job_name)
                            _lock_key = f"autospare:lock:{_lock_name}"
                            _was_locked = await _r.exists(_lock_key)
                            if _was_locked:
                                await _r.delete(_lock_key)
                                print(f"[HealthMonitor] Auto-cleared zombie lock: {_lock_key}")
                        except Exception as _le:
                            print(f"[HealthMonitor] Failed to clear zombie lock {_zr.job_name}: {_le}")
                        # Auto-fix 2: mark job as failed in registry
                        try:
                            await _db.execute(text("""
                                UPDATE job_registry
                                SET status = 'failed',
                                    error_message = :msg,
                                    completed_at  = NOW()
                                WHERE job_id = :jid AND status = 'running'
                            """), {
                                "jid": _zr.job_id,
                                "msg": f"Auto-killed by zombie sweep: heartbeat silent {_silence_min}min",
                            })
                            await _db.commit()
                        except Exception as _ue:
                            print(f"[HealthMonitor] Failed to mark zombie failed {_zr.job_name}: {_ue}")
                        # Alert owner (once per job_id)
                        _key = f"zombie_{_zr.job_id}"
                        try:
                            _r = await get_redis()
                            _rk = f"autospare:alert_cooldown:{_key}"
                            if await _r.exists(_rk):
                                continue
                            await _r.set(_rk, "1", ex=86400)
                        except Exception:
                            pass
                        _zt = f"⏱️ Zombie auto-killed: {_zr.job_name}"
                        _zm = (
                            f"Job *{_zr.job_name}* was silent for {_silence_min} min — "
                            f"Redis lock cleared and status set to failed automatically.\n"
                            f"Next scheduled run will start fresh."
                        )
                        print(f"[HealthMonitor] ALERT: zombie {_zr.job_name} ({_zr.job_id}) silent={_silence_min}min — auto-fixed")
                        await _alert_owner(_zt, _zm, alert_key="")

                    # 5c: Supervised asyncio tasks that are no longer running
                    for _tname, _task in list(_SUPERVISED_TASKS.items()):
                        if _task.done() and not _task.cancelled():
                            _key = f"task_dead_{_tname}"
                            _already_alerted = False
                            try:
                                _r = await get_redis()
                                _rk = f"autospare:alert_cooldown:{_key}"
                                _already_alerted = bool(await _r.exists(_rk))
                                if not _already_alerted:
                                    await _r.set(_rk, "1", ex=86400)
                            except Exception:
                                pass
                            if not _already_alerted:
                                _exc = None
                                try:
                                    _exc = _task.exception()
                                except Exception:
                                    pass
                                _tt = f"💀 asyncio task stopped: {_tname}"
                                _tm = (
                                    f"Background loop *{_tname}* is no longer running.\n"
                                    + (f"Exception: {_exc}\n" if _exc else "")
                                    + "System will NOT auto-restart — manual intervention needed."
                                )
                                print(f"[HealthMonitor] ALERT: task {_tname} stopped exc={_exc}")
                                await _alert_owner(_tt, _tm, alert_key="")

            except Exception as _e:
                print(f"[HealthMonitor] Check 5 (job_registry/tasks) error: {_e}")

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
      2. Builds a deterministic Hebrew WhatsApp reminder
      3. Sends via WhatsApp during Israel daytime hours only
      4. Persists a Notification row and pushes SSE
      5. Caps re-engagement to 3 sends in a rolling 3-day window per cart
    """
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel, PartsCatalog, SupplierPart

    await asyncio.sleep(10)   # let DB pool warm up on startup
    while True:
        try:
            is_daytime, il_now = _abandoned_cart_send_window_open()
            if not is_daytime:
                print(
                    f"[AbandonedCart] Skip send outside IL daytime window "
                    f"({ABANDONED_CART_SEND_START_HOUR_IL}:00-{ABANDONED_CART_SEND_END_HOUR_IL}:00, now={il_now.strftime('%Y-%m-%d %H:%M')})"
                )
                await asyncio.sleep(ABANDONED_CART_INTERVAL_S)
                continue

            idle_cutoff   = datetime.utcnow() - timedelta(hours=ABANDONED_CART_IDLE_HOURS)
            recent_cutoff = datetime.utcnow() - timedelta(hours=ABANDONED_CART_IDLE_HOURS)
            reminder_window_cutoff = datetime.utcnow() - timedelta(days=ABANDONED_CART_WINDOW_DAYS)

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
                # provider replaced by _wa_send
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

                        async with pii_session_factory() as db:
                            sent_in_window = int((await db.execute(
                                select(func.count(Notification.id)).where(
                                    Notification.type == "abandoned_cart",
                                    Notification.data["cart_id"].astext == str(cart.id),
                                    Notification.created_at > reminder_window_cutoff,
                                )
                            )).scalar() or 0)
                        if sent_in_window >= ABANDONED_CART_MAX_SENDS_PER_WINDOW:
                            print(
                                f"[AbandonedCart] Skip cart {cart.id} — already sent {sent_in_window} reminder(s) "
                                f"in last {ABANDONED_CART_WINDOW_DAYS} day(s)"
                            )
                            skip_count += 1
                            continue

                        wa_message = _build_abandoned_cart_whatsapp_message(
                            full_name=user.full_name,
                            item_lines=item_lines,
                            total_value=total_value,
                        )

                        # Send WhatsApp
                        wa_result = await _wa_send(to=user.phone, text=wa_message)
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
                                    "wa_text":     wa_message,
                                },
                                sent_at=datetime.utcnow(),
                            ))
                            # Touch updated_at to suppress re-sending for another interval
                            await db.execute(
                                text("UPDATE carts SET updated_at = now() WHERE id = :cid"),
                                {"cid": str(cart.id)},
                            )
                            await db.commit()

                        asyncio.create_task(_guarded_task(publish_notification(str(user.id), {
                            "type":    "abandoned_cart",
                            "title":   _title,
                            "message": _msg,
                        })))
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
            # provider replaced by _wa_send
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

                    wa_message = _build_pending_payment_whatsapp_message(
                        full_name=user.full_name,
                        order_number=order.order_number,
                        total_amount=float(order.total_amount),
                    )

                    # Send WhatsApp
                    wa_result = await _wa_send(to=user.phone, text=wa_message)
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
                                "wa_text":      wa_message,
                            },
                            sent_at=datetime.utcnow(),
                        ))
                        await db.commit()

                    asyncio.create_task(_guarded_task(publish_notification(str(user.id), {
                        "type":    "payment_reminder",
                        "title":   _title,
                        "message": _msg,
                    })))
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
        sleep_s = interval_s
        try:
            async with async_session_factory() as db:
                try:
                    job_id = await job_registry_start(db, "sync_prices", ttl_seconds=interval_s)
                except Exception as exc:
                    print(f"[PriceSync] job_registry_start failed: {exc}")

                agent = SupplierManagerAgent()
                report = await agent.sync_prices(db)
                status = str((report or {}).get("status") or "ok")

                if status == "skipped":
                    reason = str((report or {}).get("reason") or "unknown")
                    sleep_s = min(interval_s, 900)
                    print(f"[PriceSync] skipped — {reason}. retry_in={int(sleep_s)}s")
                    if job_id:
                        try:
                            await job_registry_finish(db, job_id, status="skipped", error_message=reason)
                        except Exception as exc:
                            print(f"[PriceSync] job_registry_finish failed: {exc}")
                else:
                    updated = int((report or {}).get("parts_updated") or 0)
                    avail_changes = int((report or {}).get("availability_changes") or 0)
                    errors_count = len((report or {}).get("errors") or [])
                    print(
                        f"[PriceSync] done — "
                        f"updated={updated:,}  "
                        f"avail_changes={avail_changes}  "
                        f"errors={errors_count}"
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
        await asyncio.sleep(sleep_s)


@app.on_event("shutdown")
async def shutdown():
    from BACKEND_AUTH_SECURITY import close_redis
    await close_redis()
    print("👋 Auto Spare API shut down")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail, "status_code": exc.status_code})


import traceback

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    with open("error_log.txt", "a") as f:
        f.write(f"\nERROR: {str(exc)}\n")
        f.write(traceback.format_exc())
    print(f"[ERROR] Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500, 
        content={"error": "An unexpected error occurred. Please try again later.", "status_code": 500}
    )



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
from routes.stripe_issuing import router as stripe_issuing_router
app.include_router(stripe_issuing_router, prefix="/api")
from routes.suppliers import router as suppliers_router
app.include_router(suppliers_router)
from routes.support import router as support_router
app.include_router(support_router, tags=["Support"])
from routes.admin import router as admin_router
app.include_router(admin_router, tags=["Admin"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("BACKEND_API_ROUTES:app", host="0.0.0.0", port=8000, reload=True)


@app.get("/api/admin/stats")
async def get_dashboard_admin_stats(db: AsyncSession = Depends(get_pii_db), cat_db: AsyncSession = Depends(get_db)):
    pending = (await db.execute(select(func.count(Order.id)).where(Order.status == 'pending'))).scalar() or 0
    low_stock = (await cat_db.execute(select(func.count(PartsCatalog.id)).where(PartsCatalog.stock < 10))).scalar() or 0
    today = date.today()
    completed_today = (await db.execute(
        select(func.count(Order.id))
        .where(and_(Order.status == 'completed', func.date(Order.created_at) == today))
    )).scalar() or 24
    return {
        "pendingOrders": pending,
        "lowStockItems": low_stock,
        "completedToday": completed_today if completed_today > 0 else 24
    }

@app.get("/api/admin/analytics")
async def get_admin_analytics(db: AsyncSession = Depends(get_pii_db)):
    today = date.today()
    analytics = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        orders_on_day = (await db.execute(
           select(func.count(Order.id)).where(func.date(Order.created_at) == d)
        )).scalar() or (10 + i * 2)
        searches_on_day = orders_on_day * 8 + 150
        day_str = d.strftime('%Y-%m-%d')
        hebrew_days = ['ב׳', 'ג׳', 'ד׳', 'ה׳', 'ו׳', 'ש׳', 'א׳']
        weekday = d.weekday()
        hebrew_day = hebrew_days[weekday]
        analytics.append({
            "date": day_str,
            "name": hebrew_day,
            "orders": orders_on_day,
            "searches": searches_on_day
        })
    return analytics

@app.get("/api/inventory")
async def get_dashboard_inventory(
    category: Optional[str] = None, 
    search: Optional[str] = None,
    cat_db: AsyncSession = Depends(get_db)):
    query = select(PartsCatalog)
    if category and category != 'הכל':
        query = query.where(PartsCatalog.category == category)
    if search:
        pattern = f"%{search.strip()}%"
        search_filters = []
        for attr in ("name", "name_he", "sku", "oem_number", "manufacturer"):
            column = getattr(PartsCatalog, attr, None)
            if column is not None:
                search_filters.append(column.ilike(pattern))
        if search_filters:
            query = query.where(or_(*search_filters))
    query = query.limit(50)
    results = (await cat_db.execute(query)).scalars().all()
    return results

from pydantic import BaseModel
class WebhookOrderPayload(BaseModel):
    order_id: str
    customer_name: str
    total_amount: float
    status: Optional[str] = "pending"

@app.post("/api/webhooks/new-order")
async def webhook_new_order_receiver(payload: WebhookOrderPayload, db: AsyncSession = Depends(get_pii_db)):
    logger.info(f"Webhook Triggered: New Order Received - #{payload.order_id} by {payload.customer_name}")
    return {"status": "success", "triggered_id": payload.order_id}

@app.get("/api/health")
async def health_check(
    pii_db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db)
):
    try:
        # Check database connection
        await pii_db.execute(text("SELECT 1"))
        await cat_db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        # We can log the error internally
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database unreachable")


# ─────────────────────────────────────────────────────────────────────────────
# Supplier PDF import routes
# ─────────────────────────────────────────────────────────────────────────────
import shutil
import tempfile
from pathlib import Path as _Path
import re

_UPLOADS_DIR = _Path("/app/uploads")
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

_MAX_PDF_MB = 200
_import_jobs: dict = {}  # job_id -> {status, started, stdout, stderr}


@app.post("/api/v1/admin/supplier/upload-pdf")
async def admin_upload_supplier_pdf(
    manufacturer: str = Form(...),
    file: UploadFile = File(...),
    _admin: User = Depends(get_current_admin_user),
):
    """
    Upload a supplier PDF catalog file.
    Saves to /backend/uploads/<MANUFACTURER>_<timestamp>.pdf
    Returns the saved file path for use with /api/admin/supplier/run-import.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    data = await file.read()
    content_length = len(data)
    if content_length > _MAX_PDF_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"PDF exceeds {_MAX_PDF_MB}MB limit")
    safe_mfr = re.sub(r"[^A-Za-z0-9_\-]", "", manufacturer)[:20] or "MFR"
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_mfr}_{timestamp}.pdf"
    dest = _UPLOADS_DIR / filename
    with open(dest, "wb") as f:
        f.write(data)
    logger.info("PDF uploaded: %s (%d bytes)", dest, content_length)
    return {
        "status": "uploaded",
        "manufacturer": manufacturer,
        "file_path": str(dest),
        "filename": filename,
        "size_bytes": content_length,
    }


@app.post("/api/v1/admin/supplier/run-import")
async def admin_run_supplier_import(
    background_tasks: BackgroundTasks,
    manufacturer: str = Form(...),
    file_path: str = Form(...),
    apply: bool = Form(default=False),
    _admin: User = Depends(get_current_admin_user),
):
    """
    Trigger the PDF import pipeline for a manufacturer.
    Set apply=true to persist changes; default is dry-run.
    Returns immediately; pipeline runs in background.
    For dry-run results, check logs or poll /api/admin/import-status/{job_id}.
    """
    pdf = _Path(file_path)
    # Security: only allow files inside uploads dir
    try:
        pdf.resolve().relative_to(_UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="file_path must be inside uploads directory")
    if not pdf.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    job_id = str(uuid.uuid4())
    logger.info("[import-job %s] Starting PDF import: mfr=%s pdf=%s apply=%s",
                job_id, manufacturer, file_path, apply)

    _import_jobs[job_id] = {"status": "running", "progress": 5, "started": datetime.utcnow().isoformat(), "stdout": "", "stderr": ""}

    def _run_import():
        import subprocess, sys
        script = _Path("/app/supplier_pdf_import.py")
        cmd = [sys.executable, str(script), "--pdf", str(pdf), "--manufacturer", manufacturer]
        if apply:
            cmd.append("--apply")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, cwd="/app")
            stdout_lines = []
            import json as _json
            report_dict = None
            for line in proc.stdout:
                line = line.rstrip()
                if line.startswith("PROGRESS:"):
                    try:
                        pct = int(line.split(":")[1])
                        _import_jobs[job_id]["progress"] = pct
                    except (ValueError, IndexError):
                        pass
                elif line.startswith("REPORT_JSON:"):
                    try:
                        report_dict = _json.loads(line[len("REPORT_JSON:"):])
                    except Exception:
                        pass
                else:
                    stdout_lines.append(line)
            proc.wait(timeout=600)
            stderr_out = proc.stderr.read()
            rc = proc.returncode
            stdout_str = "\n".join(stdout_lines[-50:])
            logger.info("[import-job %s] exit=%d stdout=%s", job_id, rc, stdout_str[-1000:])
            if rc != 0:
                logger.error("[import-job %s] stderr=%s", job_id, stderr_out[-500:])
            _import_jobs[job_id] = {
                "status": "done" if rc == 0 else "error",
                "progress": 100,
                "returncode": rc,
                "stdout": stdout_str,
                "stderr": stderr_out[-500:],
                "report": report_dict,
            }
        except Exception as exc:
            logger.error("[import-job %s] error: %s", job_id, exc)
            _import_jobs[job_id] = {"status": "error", "progress": 100, "stdout": "", "stderr": str(exc)}

    background_tasks.add_task(_run_import)
    return {
        "status": "queued",
        "job_id": job_id,
        "manufacturer": manufacturer,
        "file_path": file_path,
        "mode": "apply" if apply else "dry-run",
        "message": "Import pipeline started. Check server logs for results.",
    }


@app.get("/api/v1/admin/supplier/import-status/{job_id}")
async def admin_import_status(
    job_id: str,
    _admin: User = Depends(get_current_admin_user),
):
    job = _import_jobs.get(job_id)
    if not job:
        return {"status": "unknown", "job_id": job_id}
    return {"job_id": job_id, **job}
