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
import watchdog_state as _wds

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

# 25s (was 3s): the heaviest warmup shape (empty query + full vehicle fitment) is
# a COLD-cache prime that legitimately takes >3s, so a 3s cap timed it out every
# boot → the wait_for cancellation left the asyncpg connection unusable and logged
# "cannot call Transaction.rollback(): the underlying connection is closed" (fixed
# 2026-07-11, together with the per-case session isolation in _warm_search_paths).
SEARCH_WARMUP_QUERY_TIMEOUT_S = float(os.getenv("SEARCH_WARMUP_QUERY_TIMEOUT_S", "25"))
SEARCH_WARMUP_CASES: List[Dict[str, Any]] = [
    # category values are English DB slugs (the Hebrew names matched 0 rows —
    # see the category root-fix 2026-07-09).
    {"query": "engine", "category": "engine", "timeout_s": 12},
    {"query": "filter", "category": "filter"},
    {"query": "mirror"},
    {"query": "battery"},
    {"query": "bosch"},
    {
        # Empty query + full vehicle fitment = "browse all parts for my car".
        # Measured 78s COLD / 0s warm (2026-07-11) — an unbounded fitment scan.
        # This warmup PRIMES that Redis cache so the first real customer gets the
        # warm path instead of eating 78s. Needs a timeout above the cold time;
        # it runs in the background at startup, so a long prime is non-blocking.
        # (Deeper perf TODO: the cold empty-query fitment scan itself is slow.)
        "query": "",
        "vehicle_manufacturer": "Toyota",
        "vehicle_model": "Corolla",
        "vehicle_year": 2018,
        "timeout_s": 120,
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
_BACKEND_START_UTC: "datetime" = datetime.now(timezone.utc)  # set at import time; used by watchdog to identify orphaned DB connections


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

        # Fresh session PER case (fixed 2026-07-11): a shared session broke here —
        # when a case hit the wait_for timeout, cancelling the query mid-flight
        # left the asyncpg connection in an aborted state, so every later case
        # failed with "cannot call Transaction.rollback(): the underlying
        # connection is closed" (17× in logs). One session per case isolates that.
        for case in SEARCH_WARMUP_CASES:
            try:
                async with async_session_factory() as db:
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

        # Pre-warm the manufacturers cache (its scan is ~68s; the parts page fetches
        # the brand dropdown on load, so a cold cache = the "pages hang after login"
        # symptom). Kick it off in the BACKGROUND — awaiting it would block startup /
        # readiness for ~68s on every restart. It fills the cache within a minute; the
        # single-flight lock covers the rare user who hits it before it finishes.
        try:
            from routes.parts import _refresh_manufacturers_cache
            asyncio.create_task(_guarded_task(_refresh_manufacturers_cache()))
            print("[SearchWarmup] manufacturers cache pre-warm scheduled (background)")
        except Exception as exc:
            logger.warning("[SearchWarmup] manufacturers pre-warm failed: %s", exc)
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
    """
    Owner WhatsApp notifications — rewritten 2026-07-05 per Khalil's feedback
    (every-30-min reports were spam). New policy:
      • Checks every 30 minutes, but SENDS only when something is wrong
        (dead supervised tasks, or failed/dead jobs in the last 2h).
      • Plus ONE full daily digest at ~09:00 Israel time (06:00 UTC).
    """
    _owner_phone = os.getenv("OWNER_WHATSAPP_PHONE", "")
    if not _owner_phone:
        return
    await asyncio.sleep(60)  # brief startup delay before first report

    _last_digest_date: str | None = None
    _last_problem_sig: str = ""  # don't repeat the same problem alert every 30min

    while True:
        try:
            _now = datetime.now(timezone.utc)
            _il_hour = (_now.hour + 3) % 24
            _today = _now.strftime("%Y-%m-%d")
            is_digest_time = _il_hour == 9 and _last_digest_date != _today

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

                # Harvest-queue progress (fix 2026-07-08): the daily report
                # looked "the same every day" because it showed only total_parts
                # (4.19M, barely moves) and never the harvesters' actual work.
                # Add live IL-queue coverage + a 24h delta so the report visibly
                # reflects what the harvesters accomplished.
                _hq = (await _db.execute(text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status IN ('done','empty')) AS done,
                        COUNT(*) AS total,
                        COUNT(DISTINCT brand_en) FILTER (WHERE status='done') AS brands_done,
                        COALESCE(SUM(parts_found),0) AS parts_found,
                        COUNT(*) FILTER (WHERE last_harvested_at > NOW() - INTERVAL '24 hours') AS done_24h
                    FROM harvest_queue
                """))).fetchone()

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

            # Harvest coverage — the moving number that shows daily progress.
            if _hq and _hq[1]:
                _hq_done, _hq_total, _hq_brands, _hq_parts, _hq_24h = _hq
                _hq_pct = round(_hq_done * 100.0 / _hq_total, 1) if _hq_total else 0
                lines.append(
                    f"\n*Harvest (IL market):* {_hq_done:,}/{_hq_total:,} models ({_hq_pct}%)"
                )
                lines.append(f"  {_hq_brands} brands · {_hq_parts:,} parts found · +{_hq_24h} models in 24h")

            # Decide whether to actually send: problems, or the daily digest.
            # A dead/failed job only counts if NOT superseded by a newer
            # running/completed cycle of the same task — deploy restarts kill
            # mid-cycle jobs that respawn healthy minutes later, and those
            # were spamming the owner with false alarms (fixed 2026-07-06).
            _healthy_names = {
                str(j.job_name).split(":")[0]
                for j in jobs if j.status in ("running", "completed")
            }

            # Restart-orphan guard (2026-07-13): a job whose last activity predates this
            # container's start died with the previous process (deploy/OOM/SIGKILL), not
            # from a real failure. Don't count it as a problem worth alerting the owner.
            _cstart_naive = _BACKEND_START_UTC.replace(tzinfo=None)

            def _job_within_container(j) -> bool:
                ts = j.last_heartbeat_at or j.started_at
                if ts is None:
                    return True
                ts = ts.replace(tzinfo=None) if ts.tzinfo else ts
                return ts >= _cstart_naive

            failed_jobs = [
                j for j in jobs
                if j.status in ("failed", "dead")
                and str(j.job_name).split(":")[0] not in _healthy_names
                and _job_within_container(j)
            ]
            problem_sig = ",".join(sorted(dead_tasks)) + "|" + ",".join(
                sorted(f"{j.job_name}:{j.status}" for j in failed_jobs)
            )
            has_problem = bool(dead_tasks or failed_jobs)

            if is_digest_time:
                lines[0] = f"\U0001f4c5 *AutoSpareFinder — דוח יומי* ({_now.strftime('%H:%M UTC')})"
                await _wa_send(to=_owner_phone, text="\n".join(lines))
                _last_digest_date = _today
                print("[StatusUpdate] Sent daily digest to owner")
            elif has_problem and problem_sig != _last_problem_sig:
                lines[0] = f"⚠️ *AutoSpareFinder — בעיה במערכת* ({_now.strftime('%H:%M UTC')})"
                await _wa_send(to=_owner_phone, text="\n".join(lines))
                _last_problem_sig = problem_sig
                print(f"[StatusUpdate] Sent PROBLEM alert to owner: {problem_sig[:120]}")
            else:
                if not has_problem:
                    _last_problem_sig = ""  # problem cleared — re-alert if it returns
                print("[StatusUpdate] Checked — all healthy, no message sent")
        except Exception as _exc:
            print(f"[StatusUpdate] loop error: {_exc}")

        await asyncio.sleep(1800)  # check every 30 minutes (send only on problems/digest)


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


async def _zombie_reaper_loop() -> None:
    """Periodically reap zombie child processes (from subprocess.Popen imports)."""
    import os as _os
    while True:
        try:
            reaped = 0
            while True:
                pid, _ = _os.waitpid(-1, _os.WNOHANG)
                if pid == 0:
                    break
                reaped += 1
            if reaped:
                print(f"[zombie_reaper] reaped {reaped} child processes", flush=True)
        except ChildProcessError:
            pass
        except Exception:
            pass
        await asyncio.sleep(60)


async def _car_parts_ie_harvester_loop() -> None:
    """Supervises car_parts_ie_flaresolverr_harvester.py — relaunches it whenever it exits or crashes."""
    import sys as _sys
    import time as _time

    await asyncio.sleep(60)  # let flaresolverr/network settle on startup
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harvesters", "car_parts_ie_flaresolverr_harvester.py")
    env = dict(os.environ)
    env.setdefault("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
    backoff = 30

    while True:
        started = _time.time()
        try:
            print(f"[car_parts_ie_harvester] launching {script}", flush=True)
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            rc = await proc.wait()
            uptime = _time.time() - started
            print(f"[car_parts_ie_harvester] exited rc={rc} after {uptime:.0f}s — restarting", flush=True)
        except Exception as exc:
            uptime = 0.0
            print(f"[car_parts_ie_harvester] failed to launch: {exc}", flush=True)

        # Ran for a while before dying → treat as a fresh start next time.
        # Died immediately (e.g. flaresolverr unreachable) → back off harder.
        backoff = 30 if uptime > 300 else min(backoff * 2, 1800)
        await asyncio.sleep(backoff)


# ── Part-thumbnail import supervisor ──────────────────────────────────────────
# Module-level status so a healthcheck / /system endpoint can read the last cycle.
_THUMBNAIL_IMPORT_STATUS: dict = {"state": "starting", "total_ok": 0, "last_batch": None, "updated_at": None}


async def _thumbnail_import_loop() -> None:
    """Supervisor for the part-thumbnail cleanup pipeline (maintenance/build_part_thumbnails.py).

    Runs the pipeline in modest batches, continuously: builds clean, deduped, no-label thumbnails
    for parts that have a source image; backs off (and periodically re-checks) when the backlog is
    empty; and NEVER starves the flaresolverr harvesters — small batches + a sleep between them +
    the child runs at low CPU priority (os.nice) because tesseract OCR is CPU-heavy on this 4-core
    box. Each batch is a SUBPROCESS (isolates the synchronous OCR/PIL work off the event loop) with
    a hard time cap so a hung fetch can't wedge the loop. Crash-restart of THIS loop is handled by
    the _supervised_task wrapper. Toggle with THUMBNAIL_IMPORT_ENABLED=0."""
    import sys as _sys
    import time as _time
    import re as _re
    from datetime import datetime as _dt

    if os.getenv("THUMBNAIL_IMPORT_ENABLED", "1") != "1":
        print("[thumbnail_import] disabled via THUMBNAIL_IMPORT_ENABLED=0", flush=True)
        _THUMBNAIL_IMPORT_STATUS["state"] = "disabled"
        return

    await asyncio.sleep(120)  # let startup + harvesters settle before adding OCR load
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maintenance", "build_part_thumbnails.py")
    # Smaller batches → status updates land sooner + gentler per-batch CPU/lock pressure
    # while OCR competes with the flaresolverr harvesters on this 4-core box.
    batch = int(os.getenv("THUMBNAIL_IMPORT_BATCH", "80"))
    between = int(os.getenv("THUMBNAIL_IMPORT_SLEEP", "90"))
    idle_backoff = 300

    while True:
        # gate — only run when the bucket is actually configured
        try:
            import s3_storage as _S
            if not _S.s3_enabled():
                _THUMBNAIL_IMPORT_STATUS["state"] = "no_s3"
                await asyncio.sleep(600); continue
        except Exception:
            await asyncio.sleep(600); continue

        started = _time.time()
        n_cand = ok = rej = nosrc = 0
        try:
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, "-u", script, "--limit", str(batch),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                preexec_fn=lambda: os.nice(15),   # low CPU priority — yield to harvesters
                env=dict(os.environ),
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=2400)  # 40-min cap
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                out = b""
                print("[thumbnail_import] batch exceeded 40min — killed (stuck fetch?)", flush=True)
            text = (out or b"").decode("utf-8", "ignore")
            cm = _re.search(r"candidates:\s*(\d+)", text)
            n_cand = int(cm.group(1)) if cm else 0
            dm = _re.search(r"DONE — ok=(\d+).*?rejected_ad=(\d+)\s+no_source=(\d+)", text)
            if dm:
                ok, rej, nosrc = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        except Exception as exc:
            print(f"[thumbnail_import] batch failed: {exc}", flush=True)

        _THUMBNAIL_IMPORT_STATUS["total_ok"] += ok
        _THUMBNAIL_IMPORT_STATUS["last_batch"] = {
            "candidates": n_cand, "ok": ok, "rejected_ad": rej, "no_source": nosrc,
            "seconds": round(_time.time() - started, 1),
        }
        _THUMBNAIL_IMPORT_STATUS["updated_at"] = _dt.utcnow().isoformat()
        _THUMBNAIL_IMPORT_STATUS["state"] = "idle" if n_cand == 0 else "importing"
        print(f"[thumbnail_import] candidates={n_cand} ok={ok} rejected={rej} no_source={nosrc} "
              f"({_THUMBNAIL_IMPORT_STATUS['last_batch']['seconds']}s) total_ok={_THUMBNAIL_IMPORT_STATUS['total_ok']}",
              flush=True)

        if n_cand == 0:
            # backlog drained — back off, re-check later for newly-imported parts / refreshes
            await asyncio.sleep(idle_backoff)
            idle_backoff = min(idle_backoff * 2, 3600)
        else:
            idle_backoff = 300
            await asyncio.sleep(between)


async def _amayama_fs_harvester_loop() -> None:
    """Supervises amayama_flaresolverr_harvester.py — the SERVER-SIDE Amayama harvester
    (FlareSolverr session+warmup bypasses Amayama's Cloudflare; injects the account
    login cookie from amayama_session.json for prices+IL shipping). Relaunches on exit;
    if amayama_session.json is missing (no login cookie), the harvester exits fast and
    this backs off — so it costs nothing until the cookie is provided."""
    import sys as _sys
    import time as _time
    await asyncio.sleep(75)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harvesters", "amayama_flaresolverr_harvester.py")
    cookie = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amayama_session.json")
    env = dict(os.environ)
    env.setdefault("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
    backoff = 60
    while True:
        started = _time.time()
        if not os.path.exists(cookie):
            # no login cookie yet — don't spin; check again in 30 min
            await asyncio.sleep(1800)
            continue
        try:
            print(f"[amayama_fs_harvester] launching {script}", flush=True)
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, script,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env)
            rc = await proc.wait()
            uptime = _time.time() - started
            print(f"[amayama_fs_harvester] exited rc={rc} after {uptime:.0f}s — restarting", flush=True)
        except Exception as exc:
            uptime = 0.0
            print(f"[amayama_fs_harvester] failed to launch: {exc}", flush=True)
        backoff = 60 if uptime > 300 else min(backoff * 2, 1800)
        await asyncio.sleep(backoff)


async def _harvest_supervisor_loop() -> None:
    """
    Smart harvest supervisor (goal 2026-07-07). The harvester itself is now
    queue-driven — it pulls the next highest-priority pending model from
    harvest_queue (seeded from vehicle_market_il, ranked by active Israeli road
    vehicles) and auto-advances. This loop is the OVERSIGHT layer:
      • Reports coverage progress toward the full IL market (all brands+models).
      • Re-seeds the queue if it's ever empty (never lets the harvester idle).
      • Sends the owner a weekly harvest digest (Sunday ~09:00 IL).
    """
    await asyncio.sleep(900)  # let startup settle
    _last_digest_date: str | None = None
    while True:
        try:
            async with async_session_factory() as db:
                row = (await db.execute(text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status IN ('done','empty')) AS done,
                        COUNT(*) FILTER (WHERE status='done') AS done_with_parts,
                        COUNT(*) AS total,
                        COUNT(DISTINCT brand_en) FILTER (WHERE status='done') AS brands_done,
                        COUNT(DISTINCT brand_en) AS brands_total,
                        COUNT(*) FILTER (WHERE status='pending') AS pending,
                        COALESCE(SUM(parts_found),0) AS parts_total
                    FROM harvest_queue
                """))).fetchone()
            if row:
                done, done_parts, total, bdone, btot, pending, parts = row
                pct = round(done * 100.0 / total, 1) if total else 0
                print(
                    f"[harvest_supervisor] IL-market coverage: {done}/{total} models ({pct}%), "
                    f"{bdone}/{btot} brands, pending={pending}, parts_found={parts}",
                    flush=True,
                )

                _now = datetime.now(timezone.utc)
                _il_hour = (_now.hour + 3) % 24
                _today = _now.strftime("%Y-%m-%d")
                if _now.weekday() == 6 and _il_hour == 9 and _last_digest_date != _today:
                    owner = os.getenv("OWNER_WHATSAPP_PHONE", "")
                    if owner:
                        # Top 3 highest-IL-priority models still pending
                        async with async_session_factory() as db2:
                            top_pending = (await db2.execute(text("""
                                SELECT brand_en, model_name, il_vehicle_count
                                FROM harvest_queue WHERE status='pending'
                                ORDER BY priority_rank ASC LIMIT 3
                            """))).fetchall()
                        nxt = "\n".join(f"• {r[0]} {r[1]} ({r[2]:,} רכבים)" for r in top_pending)
                        msg = (
                            f"🚗 *דוח קטלוג שבועי*\n"
                            f"כיסוי שוק ישראל: {done}/{total} דגמים ({pct}%)\n"
                            f"מותגים: {bdone}/{btot}\n"
                            f"סה\"כ חלקים שנאספו: {parts:,}\n\n"
                            f"הבאים בתור (עדיפות עליונה):\n{nxt}"
                        )
                        try:
                            from social.whatsapp_provider import send_message as _wa
                            await _wa(owner, msg)
                        except Exception:
                            pass
                    _last_digest_date = _today
        except Exception as exc:
            print(f"[harvest_supervisor] error: {exc}", flush=True)
        await asyncio.sleep(1800)  # every 30 min


async def _force_kill_pid(pid: int, label: str) -> None:
    """SIGTERM a pid, escalate to SIGKILL if it's still alive 5s later."""
    import signal as _signal
    try:
        os.kill(pid, _signal.SIGTERM)
    except ProcessLookupError:
        return
    await asyncio.sleep(5)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    try:
        os.kill(pid, _signal.SIGKILL)
        print(f"[car_parts_ie_watchdog] SIGKILL escalation for {label} pid={pid}", flush=True)
    except ProcessLookupError:
        pass


async def _car_parts_ie_stall_watchdog_loop() -> None:
    """
    Two-tier DB connection supervisor — context-aware, not threshold-based:

    TIER 1 — Orphaned connections (backend_start < this container's start):
      The connection belongs to a dead process (previous container instance that
      didn't close its sockets cleanly). No legitimate work is in progress on it.
      Kill after just 60s of blocking — these should never persist at all.

    TIER 2 — Active backend connections (backend_start >= container start):
      The connection belongs to live backend work (e.g. db_update_agent running
      normalize_part_types across 4M rows, legitimately taking 20+ min). Never
      kill these — just log a warning so very-long blockers are visible.

    Also kills car_parts_ie_import_generic.py subprocesses stuck past their
    expected 1-2 min runtime ceiling.
    """
    import subprocess as _sp

    STUCK_IMPORT_S = 600     # importer subprocesses: normal <2 min, stuck >10 min = kill
    ORPHAN_BLOCKER_S = 60    # connections from dead prev-container: kill after 60s
    LIVE_WARN_S = 1800       # active-backend connections: warn if blocking >30 min
    # Zombie query threshold: a same-container connection running the SAME query for
    # this long AND actively blocking other queries is almost certainly from a dead
    # process (e.g. a killed docker exec job whose asyncpg connection outlived it).
    # With delta processing, no legitimate task ever runs longer than a few minutes.
    # Set conservatively at 45 min — well above any real task, well below the 60+ min
    # zombie queries we observed today.
    ZOMBIE_QUERY_S = 2700

    await asyncio.sleep(180)
    while True:
        # ── Stuck importer subprocesses ──────────────────────────────────────
        try:
            ps_out = _sp.run(
                ["ps", "-eo", "pid,etimes,args"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in ps_out.splitlines():
                if "car_parts_ie_import_generic.py" not in line:
                    continue
                parts = line.split(None, 2)
                if len(parts) < 3:
                    continue
                try:
                    pid, etimes = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                if etimes > STUCK_IMPORT_S:
                    print(f"[car_parts_ie_watchdog] importer pid={pid} stuck for {etimes}s — killing", flush=True)
                    _wds.record("kill_stuck_importer", pid, etimes, f"exceeded {STUCK_IMPORT_S}s threshold")
                    await _force_kill_pid(pid, "stuck importer")
        except Exception as exc:
            print(f"[car_parts_ie_watchdog] process scan error: {exc}", flush=True)

        # ── Blocking DB connections — orphan vs active ────────────────────────
        try:
            async with async_session_factory() as db:
                rows = (await db.execute(text("""
                    SELECT DISTINCT
                        blocking.pid,
                        EXTRACT(EPOCH FROM (now() - blocking.query_start))::int AS dur_s,
                        blocking.backend_start < :container_start AS is_orphan
                    FROM pg_locks bl
                    JOIN pg_stat_activity blocked  ON bl.pid = blocked.pid
                    JOIN pg_locks kl
                        ON  kl.locktype          = bl.locktype
                        AND kl.database          IS NOT DISTINCT FROM bl.database
                        AND kl.relation          IS NOT DISTINCT FROM bl.relation
                        AND kl.page              IS NOT DISTINCT FROM bl.page
                        AND kl.tuple             IS NOT DISTINCT FROM bl.tuple
                        AND kl.transactionid     IS NOT DISTINCT FROM bl.transactionid
                        AND kl.classid           IS NOT DISTINCT FROM bl.classid
                        AND kl.objid             IS NOT DISTINCT FROM bl.objid
                        AND kl.objsubid          IS NOT DISTINCT FROM bl.objsubid
                        AND kl.pid != bl.pid
                    JOIN pg_stat_activity blocking ON kl.pid = blocking.pid
                    WHERE NOT bl.granted
                      AND blocking.pid != pg_backend_pid()
                      AND now() - blocking.query_start > make_interval(secs => :min_dur)
                """), {
                    "container_start": _BACKEND_START_UTC,
                    "min_dur": ORPHAN_BLOCKER_S,
                })).fetchall()

                for pid, dur_s, is_orphan in rows:
                    if is_orphan:
                        # Tier 1 — External orphan (previous container): kill immediately
                        await db.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
                        _wds.record("kill_orphan", pid, dur_s, "backend_start predates this container")
                        print(
                            f"[car_parts_ie_watchdog] ORPHAN connection pid={pid} "
                            f"blocking_for={dur_s}s (pre-dates this container) — terminated",
                            flush=True,
                        )
                    elif dur_s >= ZOMBIE_QUERY_S:
                        # Tier 2 — Zombie query: same container but running 45+ min AND blocking.
                        # Most likely a killed docker exec / manual run whose asyncpg connection
                        # outlived the process. With delta processing, no real task runs this long.
                        await db.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
                        # Distinct action from kill_orphan: a same-container zombie is a
                        # LEGITIMATE kill (not an orphan pre-dating the container), so it must
                        # NOT be graded against the orphan's "predates" rule — doing so made
                        # validate_watchdog_actions flag every zombie kill as a false anomaly
                        # (root-fixed 2026-07-13). The validator recognises kill_zombie on its
                        # own terms (dur_s must exceed the zombie threshold).
                        _wds.record("kill_zombie", pid, dur_s,
                                    f"zombie: same-container query running {dur_s}s still blocking")
                        print(
                            f"[car_parts_ie_watchdog] ZOMBIE query pid={pid} "
                            f"running {dur_s}s AND blocking — terminated (same-container but no live task runs this long)",
                            flush=True,
                        )
                    elif dur_s >= LIVE_WARN_S:
                        # Tier 3 — Active backend work: warn only, never kill
                        _wds.record("warn_live_long", pid, dur_s, f"active backend query blocking for {dur_s}s — not killed")
                        print(
                            f"[car_parts_ie_watchdog] WARNING: active backend connection pid={pid} "
                            f"blocking for {dur_s}s — monitoring only (within zombie threshold)",
                            flush=True,
                        )
                if rows:
                    await db.commit()
        except Exception as exc:
            print(f"[car_parts_ie_watchdog] lock scan error: {exc}", flush=True)

        await asyncio.sleep(180)  # every 3 min


async def _car_parts_ie_harvester_healthcheck_loop() -> None:
    """
    Every 30 min: confirm the harvester process is alive and has logged recent
    progress. If it's alive but stalled (no log activity), kill it so
    _car_parts_ie_harvester_loop's crash-relaunch picks it back up.
    """
    import subprocess as _sp
    import time as _time

    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "logs", "flaresolverr_harvester.log")
    STALE_LOG_S = 900  # no log line in 15 min while the process is alive = stuck

    await asyncio.sleep(300)
    while True:
        try:
            ps_out = _sp.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            pid = None
            for line in ps_out.splitlines():
                if "car_parts_ie_flaresolverr_harvester.py" in line:
                    pid = int(line.split(None, 1)[0])
                    break

            log_age_s = None
            if os.path.exists(log_path):
                log_age_s = _time.time() - os.path.getmtime(log_path)

            stalled = pid is not None and log_age_s is not None and log_age_s > STALE_LOG_S
            status = "STALLED" if stalled else ("MISSING" if pid is None else "ok")
            print(
                f"[car_parts_ie_healthcheck] alive={pid is not None} pid={pid} "
                f"log_age_s={int(log_age_s) if log_age_s is not None else None} status={status}",
                flush=True,
            )

            if stalled:
                await _force_kill_pid(pid, "stalled harvester")

            # FlareSolverr session-leak guard (added 2026-07-07). Second layer
            # behind the harvester's own per-cycle cleanup: if sessions ever
            # exceed a hard cap the box is being starved (each session = a
            # headless Chrome). 33 leaked sessions once drove host load to 90
            # and took search down. We can't restart the flaresolverr container
            # from here (no docker socket in the backend), so destroy the
            # leaked sessions directly via the API, then kill the harvester so
            # its supervisor relaunches it with a clean 3-session baseline.
            try:
                import httpx as _hx
                _fs_url = os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
                async with _hx.AsyncClient(timeout=20) as _hc:
                    _sr = await _hc.post(_fs_url, json={"cmd": "sessions.list"})
                    _sessions = _sr.json().get("sessions", []) if _sr.status_code == 200 else []
                    _SESSION_CAP = 8  # 3 expected × safety margin
                    if len(_sessions) > _SESSION_CAP:
                        print(f"[car_parts_ie_healthcheck] FlareSolverr session leak: {len(_sessions)} > {_SESSION_CAP} — destroying all sessions", flush=True)
                        for _sid in _sessions:
                            try:
                                await _hc.post(_fs_url, json={"cmd": "sessions.destroy", "session": _sid})
                            except Exception:
                                pass
                        if pid is not None:
                            await _force_kill_pid(pid, "harvester after session-leak cleanup")
            except Exception as _fexc:
                print(f"[car_parts_ie_healthcheck] session-leak check skipped: {_fexc}", flush=True)
        except Exception as exc:
            print(f"[car_parts_ie_healthcheck] error: {exc}", flush=True)

        await asyncio.sleep(1800)  # 30 min


async def _meili_verify_parity() -> None:
    """
    Destination verification — added 2026-07-02 after the index silently
    drifted 620K docs behind while the sync's own checkpoint claimed 100%
    complete. Lesson: a pipeline's self-report ("I sent everything") is not
    verification; only comparing the actual destination against the actual
    source is. Runs after every sync cycle: counts docs in Meilisearch vs
    active rows in parts_catalog, logs the gap every time, and WhatsApp-alerts
    the owner when the gap exceeds 100K docs so drift can never again
    accumulate unnoticed.
    """
    import httpx as _httpx

    try:
        meili_url = os.getenv("MEILI_URL", "http://meilisearch:7700")
        meili_key = os.getenv("MEILI_MASTER_KEY", "")
        async with _httpx.AsyncClient(timeout=15) as hc:
            r = await hc.get(
                f"{meili_url}/indexes/parts/stats",
                headers={"Authorization": f"Bearer {meili_key}"} if meili_key else {},
            )
            meili_docs = r.json().get("numberOfDocuments", 0)

        import asyncpg as _apg
        db_url = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
        conn = await _apg.connect(db_url)
        try:
            await conn.execute("SET statement_timeout = '120s'")
            db_count = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE is_active")
        finally:
            await conn.close()

        gap = db_count - meili_docs
        print(f"[meili_parity] index={meili_docs:,} catalog={db_count:,} gap={gap:,}", flush=True)

        if gap > 100_000:
            owner = os.getenv("OWNER_WHATSAPP_PHONE", "")
            if owner:
                msg = (
                    "⚠️ *Meilisearch drift detected*\n"
                    f"Index: {meili_docs:,} docs\nCatalog: {db_count:,} active parts\n"
                    f"Gap: {gap:,} docs — sync is falling behind or skipping rows."
                )
                try:
                    from social.whatsapp_provider import send_message as _wa_alert
                    await _wa_alert(owner, msg)
                except Exception:
                    pass
    except Exception as exc:
        print(f"[meili_parity] check failed (non-fatal): {exc}", flush=True)


async def _meili_sync_loop() -> None:
    """
    Keeps the Meilisearch index in sync with parts_catalog. Found 2026-06-30:
    this had no scheduling whatsoever (no cron, not in run_all_tasks) — it ran
    once manually on 2026-06-24 and then drifted for 6 days, ending up ~580K
    documents behind the catalog (everything harvested since wasn't searchable).
    Runs incremental (MEILI_REBUILD=0 in env, not a full rebuild) every 2h,
    then verifies the destination actually matches the source (parity check).
    """
    import sys as _sys

    await asyncio.sleep(600)  # let startup settle
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meili_sync.py")
    while True:
        try:
            print("[meili_sync_loop] starting incremental sync", flush=True)
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await proc.wait()
            print(f"[meili_sync_loop] sync finished rc={rc}", flush=True)
            await _meili_verify_parity()
        except Exception as exc:
            print(f"[meili_sync_loop] error: {exc}", flush=True)
        await asyncio.sleep(7200)  # every 2h


async def _reconcile_orphaned_jobs() -> None:
    """Reconcile jobs left 'running' by a PREVIOUS backend container.

    Root cause of the recurring "🔴 Worker failed: run_all_tasks / run_brand_discovery
    — no heartbeat within TTL" owner alerts (root-fixed 2026-07-10): a backend
    restart (deploy / `compose up` recreate / OOM / crash) kills in-flight
    run_all_tasks and run_brand_discovery cycles mid-run. Their job_registry rows
    stay status='running' with a frozen last_heartbeat_at. Nothing cleans them up
    until the db_cleanup zombie watchdog reaps them ~2h later as 'failed', which
    the HealthMonitor then reports to the owner as a worker failure — even though
    nothing actually failed, the process was just restarted.

    This runs ONCE at startup, BEFORE any scheduler starts a new cycle, so every
    status='running' row with a heartbeat older than this container's start is
    provably an orphan from the previous process. Mark them 'superseded' (a
    terminal, NON-alerting status the HealthMonitor ignores) and free their Redis
    locks so the fresh cycles can acquire them immediately instead of waiting out
    the 2h zombie window. Genuine mid-run stalls (process alive but stuck >2h)
    are unaffected — those still get reaped and alerted by the watchdog.
    """
    cutoff = _BACKEND_START_UTC.replace(tzinfo=None)  # job_registry timestamps are naive UTC
    _JOB_LOCK_MAP = {
        "run_scraper_cycle":   "scraper_cycle",
        "run_brand_discovery": "brand_discovery",
        "run_all_tasks":       "db_update_agent",
        "category_discovery":  "category_discovery",
        "sync_prices":         "price_sync",
    }
    try:
        async with async_session_factory() as _db:
            rows = (await _db.execute(text("""
                UPDATE job_registry
                SET status        = 'superseded',
                    completed_at  = NOW(),
                    error_message = 'Superseded: backend restarted mid-run (orphaned by previous container)'
                WHERE status = 'running'
                  AND COALESCE(last_heartbeat_at, started_at) < :cutoff
                RETURNING job_id, job_name
            """), {"cutoff": cutoff})).fetchall()
            await _db.commit()
        if rows:
            names = [str(r.job_name).split(":")[0] for r in rows]
            print(f"[Startup] reconciled {len(rows)} orphaned running job(s) → superseded: {names}")
            try:
                from BACKEND_AUTH_SECURITY import get_redis
                _r = await get_redis()
                if _r:
                    for base in {n for n in names}:
                        lock = _JOB_LOCK_MAP.get(base, base)
                        await _r.delete(f"autospare:lock:{lock}")
                    try:
                        await _r.aclose()
                    except Exception:
                        pass
            except Exception as _le:
                print(f"[Startup] orphan lock clear failed: {_le}")
        else:
            print("[Startup] no orphaned running jobs to reconcile")
    except Exception as e:
        print(f"[Startup] orphan job reconciliation failed: {e}")


async def _amayama_harvest_monitor_loop() -> None:
    """Supervisor for the Amayama harvest. Amayama is browser-driven (its Cloudflare
    blocks FlareSolverr AND it needs the owner's login for IL shipping), so unlike
    car-parts.ie we CANNOT auto-restart it server-side. Instead this monitors
    throughput: every 30 min it logs the Amayama supplier_parts count + delta, and
    if the harvest has stalled (0 growth for ~1h) WHILE Japanese-brand OEMs are still
    unpriced, it WhatsApps the owner once (with cooldown) to reopen the Amayama tab."""
    import harvest_heartbeat
    import time as _time
    await asyncio.sleep(600)
    last_count = None
    last_alert_ts = 0.0
    ALERT_COOLDOWN_S = 4 * 3600  # nudge at most every 4h while down
    DOWN_S = 1500  # ~25 min with no feed activity = harvester genuinely stopped
    JP_BRANDS = ("toyota", "lexus", "honda", "nissan", "mazda", "subaru",
                 "mitsubishi", "infiniti", "acura", "suzuki", "daihatsu")
    while True:
        try:
            async with async_session_factory() as _db:
                cnt = (await _db.execute(text(
                    "SELECT COUNT(*) FROM supplier_parts sp JOIN suppliers s ON s.id=sp.supplier_id "
                    "WHERE s.name='Amayama'"
                ))).scalar() or 0
                pending = (await _db.execute(text(
                    "SELECT COUNT(*) FROM parts_catalog "
                    "WHERE is_active AND (base_price IS NULL OR base_price=0) "
                    "AND LOWER(manufacturer) = ANY(:b)"
                ), {"b": list(JP_BRANDS)})).scalar() or 0
            delta = None if last_count is None else cnt - last_count
            # Watch the SERVER-SIDE harvester (source 'amayama_fs' — feed heartbeat).
            # It's supervised (auto-restarts), so a stale heartbeat means it can't run —
            # almost always the login cookie (ama_ssid_s) expired, sometimes FlareSolverr.
            hb_age = harvest_heartbeat.age_seconds("amayama_fs")
            harvester_down = (hb_age is None) or (hb_age > DOWN_S)
            print(f"[amayama_monitor] amayama_parts={cnt} delta={delta} jp_unpriced={pending} "
                  f"fs_heartbeat_age_s={int(hb_age) if hb_age is not None else None} "
                  f"server_harvester={'DOWN' if harvester_down else 'alive'}", flush=True)
            if harvester_down and pending > 1000 and (_time.time() - last_alert_ts) > ALERT_COOLDOWN_S:
                last_alert_ts = _time.time()
                owner = os.getenv("OWNER_WHATSAPP_PHONE", "")
                if owner:
                    try:
                        await _wa_send(to=owner, text=(
                            "🈁 SERVER Amayama harvester (FlareSolverr) is DOWN — no feed "
                            "activity ~25 min. ~{:,} Japanese-brand parts still unpriced. "
                            "Most likely the Amayama login cookie expired: refresh "
                            "backend/amayama_session.json (ama_ssid_s). It auto-restarts "
                            "once fixed.".format(pending)))
                    except Exception:
                        pass
            last_count = cnt
        except Exception as e:
            print(f"[amayama_monitor] error: {e}", flush=True)
        await asyncio.sleep(1800)  # 30 min


@app.on_event("startup")
async def startup():
    from catalog_scraper import start_scraper_task
    from db_update_agent import start_agent_task as start_db_agent
    from db_cleanup_agent import run_cleanup_loop
    print("🚀 Auto Spare API starting...")
    print(f"   Environment: {os.getenv('ENVIRONMENT', 'development')}")
    # Reconcile jobs orphaned by the previous container BEFORE any scheduler
    # starts a new cycle — prevents the false "Worker failed: no heartbeat" alert
    # that a restart used to trigger 2h later, and frees stale locks immediately.
    await _reconcile_orphaned_jobs()
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
    _supervised_task("price_watch_loop",            _price_watch_loop())
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
    _supervised_task("zombie_reaper",               _zombie_reaper_loop())
    _supervised_task("car_parts_ie_harvester_loop",  _car_parts_ie_harvester_loop())
    _supervised_task("thumbnail_import_loop",        _thumbnail_import_loop())
    _supervised_task("car_parts_ie_stall_watchdog",  _car_parts_ie_stall_watchdog_loop())
    _supervised_task("car_parts_ie_healthcheck",     _car_parts_ie_harvester_healthcheck_loop())
    _supervised_task("meili_sync_loop",              _meili_sync_loop())
    _supervised_task("amayama_harvest_monitor",      _amayama_harvest_monitor_loop())
    _supervised_task("amayama_fs_harvester",         _amayama_fs_harvester_loop())
    _supervised_task("harvest_supervisor",           _harvest_supervisor_loop())
    await _warm_search_paths()
    print("✅ All systems ready — price-sync + catalog-scraper + db-agent schedulers started")


@app.on_event("shutdown")
async def shutdown():
    # Close out in-flight jobs BEFORE the process dies (added 2026-07-11).
    # `docker restart` and `compose up` recreate both send SIGTERM, which uvicorn
    # turns into this graceful shutdown. run_all_tasks / run_brand_discovery run
    # as asyncio tasks INSIDE this process, so a restart kills them instantly and
    # their job_registry rows would be left status='running' → orphaned → reaped
    # by the 2h zombie watchdog as 'failed' → false "Worker failed" owner alert.
    # (pre_restart.sh only SIGTERMs separate importer SUBPROCESSES; it never
    # touched these in-process asyncio jobs — which is why the pre-restart layer
    # didn't stop the orphan/anomaly alerts.) Marking them 'superseded' here
    # closes the orphan window on every GRACEFUL restart; _reconcile_orphaned_jobs()
    # at startup is the safety net for UNGRACEFUL deaths (OOM / SIGKILL / crash),
    # where this handler never gets to run.
    try:
        _JOB_LOCK_MAP = {
            "run_scraper_cycle": "scraper_cycle", "run_brand_discovery": "brand_discovery",
            "run_all_tasks": "db_update_agent", "category_discovery": "category_discovery",
            "sync_prices": "price_sync",
        }
        async with async_session_factory() as _db:
            _rows = (await _db.execute(text("""
                UPDATE job_registry
                SET status='superseded', completed_at=NOW(),
                    error_message='Superseded: backend graceful shutdown (restart)'
                WHERE status='running'
                RETURNING job_name
            """))).fetchall()
            await _db.commit()
        if _rows:
            _names = {str(r.job_name).split(":")[0] for r in _rows}
            print(f"[Shutdown] closed {len(_rows)} in-flight job(s) → superseded: {sorted(_names)}")
            try:
                from BACKEND_AUTH_SECURITY import get_redis
                _r = await get_redis()
                if _r:
                    for _base in _names:
                        await _r.delete(f"autospare:lock:{_JOB_LOCK_MAP.get(_base, _base)}")
                    try:
                        await _r.aclose()
                    except Exception:
                        pass
            except Exception as _le:
                print(f"[Shutdown] lock clear failed: {_le}")
    except Exception as _e:
        print(f"[Shutdown] job reconciliation failed: {_e}")

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


def _cart_recovery_url(user_id) -> str:
    """A one-tap link that logs the RECIPIENT into their own account and lands on their
    cart (see /api/v1/customers/cart/recover). Used instead of a bare /cart URL, which
    would show whoever is logged into the device — not the person the reminder is for."""
    from BACKEND_AUTH_SECURITY import create_cart_recovery_token
    base = os.getenv("FRONTEND_URL", "https://autosparefinder.co.il").rstrip("/")
    return f"{base}/api/v1/customers/cart/recover?token={create_cart_recovery_token(str(user_id))}"


def _build_abandoned_cart_whatsapp_message(
    full_name: str | None,
    item_lines: list[str],
    total_value: float,
    pay_link: str | None = None,
) -> str:
    first_name = _customer_first_name(full_name)
    items_summary = _format_cart_items_for_whatsapp(item_lines)
    # pay_link is a full https URL (a one-tap /pay/ checkout link when we could build one,
    # otherwise the cart page) so WhatsApp auto-linkifies it into a pressable link. The old
    # message pointed at the bare API path "/api/v1/customers/cart", which is NOT a URL, is
    # not tappable, and isn't even a customer-facing page — customers had nothing to click.
    link = (pay_link or "").strip() or (
        os.getenv("FRONTEND_URL", "https://autosparefinder.co.il").rstrip("/") + "/cart"
    )
    return (
        f"היי {first_name}, הפריטים שבחרת עדיין מחכים לך בסל: {items_summary}. "
        f"שווי הסל כרגע הוא {total_value:.0f}₪.\n"
        f"להשלמת הרכישה ותשלום מאובטח: {link}"
    )


def _abandoned_cart_send_window_open(now_local: datetime | None = None) -> tuple[bool, datetime]:
    current_local = now_local or datetime.now(APP_LOCAL_TZ)
    is_open = ABANDONED_CART_SEND_START_HOUR_IL <= current_local.hour < ABANDONED_CART_SEND_END_HOUR_IL
    return is_open, current_local


def _build_pending_payment_whatsapp_message(
    full_name: str | None,
    order_number: str | None,
    total_amount: float,
    pay_link: str | None = None,
) -> str:
    first_name = _customer_first_name(full_name)
    safe_order_number = str(order_number or "").strip() or "שלך"
    # Full https URL so WhatsApp linkifies it (was a bare, un-tappable API path).
    link = (pay_link or "").strip() or (
        os.getenv("FRONTEND_URL", "https://autosparefinder.co.il").rstrip("/") + "/cart"
    )
    return (
        f"היי {first_name}, ההזמנה {safe_order_number} בסך {total_amount:.0f}₪ עדיין ממתינה לתשלום.\n"
        f"להשלמת התשלום המאובטח: {link}\n"
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
        ("מיסבי גלגל", "wheel bearings", "רעש זמזום מהגלגל במהירות"),
    ]

    async def _noa_real_catalog_fact(db, eng_part: str, heb_part: str, car: str) -> str:
        """Marketing grounding (added 2026-07-05): pull a REAL priced part from
        the catalog matching today's topic so NOA advertises true facts —
        real part, real price, real fit — never invented claims.

        Price MUST match what the customer actually pays (2026-07-14 fix): use the
        cheapest AVAILABLE supplier's real cost and the CANONICAL formula — margin ×1.45
        plus VAT computed by get_supplier_vat_rate, which applies 18% ONLY to local (IL)
        suppliers and 0% to foreign-sourced parts. The old code did a flat base_price×1.18
        which (a) ignored that VAT condition (overstating every foreign part by 18%) and
        (b) trusted base_price, which is unreliable for some rows (landing near raw cost).
        """
        try:
            from BACKEND_AI_AGENTS import get_supplier_vat_rate, PROFIT_MARGIN
            car_make = car.split()[0]
            # Cheapest available supplier per matching part = the same cost the website
            # search/checkout price off of. Carry the supplier country for the VAT rule.
            _sql = """
                SELECT pc.name, pc.name_he, pc.manufacturer,
                       mp.cost, mp.supplier_name, mp.country
                FROM parts_catalog pc
                JOIN LATERAL (
                    SELECT sp.price_ils AS cost, s.name AS supplier_name, s.country
                    FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
                    WHERE sp.part_id = pc.id AND sp.is_available AND sp.price_ils > 0
                    ORDER BY sp.price_ils ASC LIMIT 1
                ) mp ON TRUE
                WHERE pc.is_active
                  AND (pc.name_he ILIKE :hq OR pc.name ILIKE :eq)
                  {mfr_clause}
                  AND mp.cost BETWEEN 15 AND 4000
                ORDER BY random() LIMIT 1
            """
            params = {"hq": f"%{heb_part.split()[0]}%", "eq": f"%{eng_part.split()[0]}%"}
            row = (await db.execute(text(_sql.format(mfr_clause="AND pc.manufacturer ILIKE :mfr")),
                                    {**params, "mfr": f"%{car_make}%"})).fetchone()
            if not row:
                row = (await db.execute(text(_sql.format(mfr_clause="")), params)).fetchone()
            if row:
                _pname = (row[1] or row[0] or "").strip()[:70]
                cost = float(row[3])
                sell_net = cost * PROFIT_MARGIN                      # cost × 1.45
                vat_rate = get_supplier_vat_rate(                     # 18% local, 0% foreign
                    supplier_name=row[4], supplier_country=row[5])
                price = round(sell_net + sell_net * vat_rate)         # pre-shipping "from"
                vat_note = "כולל מע\"מ" if vat_rate > 0 else "ללא מע\"מ (יבוא)"
                return (f"\nעובדה אמיתית מהקטלוג (מותר ואף רצוי לצטט): "
                        f"{_pname} ({row[2]}) — החל מ‑₪{price} {vat_note} באתר.")
        except Exception as exc:
            logger.warning("noa_marketing_loop: catalog fact lookup failed: %s", exc)
        return ""

    def _noa_utm_link(platform: str, week_num: int) -> str:
        return (f"https://autosparefinder.co.il/?utm_source={platform}"
                f"&utm_medium=social&utm_campaign=noa_w{week_num}")

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

                real_fact = await _noa_real_catalog_fact(db, eng_part, heb_part, car)

                if weekday == 0:
                    # ── Monday: generate weekly campaign brief + ad pack ─────────
                    campaign_prompt = (
                        "את נועה, מנהלת המדיה החברתית של AutoSpareFinder.\n"
                        "היום יום שני — תכנני קמפיין שיווקי שבועי מלא.\n\n"
                        f"הקשר השבוע:\n"
                        f"• עונה/ביקוש: {season}\n"
                        f"• רכב לדגמה (שים לב, אפשר לבחור אחר): {car}\n"
                        f"• חלק לדגמה: {heb_part} ({eng_part}) — כאב שכיח: {pain}\n"
                        f"• שבוע {week_num} בשנה {now.year}\n"
                        f"{real_fact}\n"
                        f"{no_repeat}\n\n"
                        "הנחיות:\n"
                        "• בחרי נושא שבועי יצירתי ורלוונטי — לא חייב להיות בדיוק הרכב/חלק שניתן לדגמה\n"
                        "• תכנני 6 פוסטים יומיים: ב׳=TikTok, ג׳=Instagram, ד׳=Facebook, ה׳=WhatsApp, ו׳=TikTok, שבת=Instagram\n"
                        "• כל פוסט — זווית שונה לגמרי, לא וריאציה של אותו טקסט\n"
                        "• כתבי copy_hebrew מלא לכל יום — טקסט מוכן לפרסום, לא תיאור של הטקסט\n"
                        "• אם ניתנה עובדה אמיתית מהקטלוג — שלבי את המחיר האמיתי; אסור להמציא מחירים אחרים\n\n"
                        "בנוסף — חבילת מודעות ממומנות (Facebook/Instagram Ads):\n"
                        "• 3 וריאציות מודעה לבדיקת A/B — כל אחת בזווית אחרת (כאב / מחיר / קלות שימוש)\n"
                        "• headline עד 40 תווים; primary_text עד 125 תווים; cta קצר\n"
                        "• רק טענות אמיתיות: חיפוש לפי לוחית, השוואת ספקים, המחיר האמיתי מהקטלוג אם ניתן\n\n"
                        "בנוסף — קמפיין Google Ads (חיפוש) לפי המתודולוגיה של Google:\n"
                        "• קבוצת מודעות אחת ממוקדת לנושא השבוע (שלב Do במשפך)\n"
                        "• keywords_exact: 5-8 ביטויי כוונה חמה בעברית [חלק+דגם/מחיר]\n"
                        "• keywords_phrase: 4-6 ביטויי השוואה (שלב Think)\n"
                        "• negatives: שלילות חובה (יד שניה, משומש, מוסך, תיקון וכו')\n"
                        "• headlines: 8-10 כותרות RSA שונות באמת, כל אחת עד 30 תווים\n"
                        "• descriptions: 4 תיאורים עד 90 תווים\n"
                        "• רק טענות אמיתיות; אם ניתן מחיר אמיתי מהקטלוג — שלבי אותו בכותרת אחת לפחות\n\n"
                        "החזירי JSON בלבד (ללא markdown) עם השדות:\n"
                        "week_theme, core_message, target_persona, hashtag_strategy, success_metrics,\n"
                        "daily_plan: [{day, platform, content_angle, visual_concept, copy_hebrew, cta}],\n"
                        "ad_pack: [{variant, headline, primary_text, cta}],\n"
                        "google_ads: {ad_group, keywords_exact, keywords_phrase, negatives, headlines, descriptions}\n"
                    )

                    raw_plan = await _hf_text(prompt=campaign_prompt, system=noa.system_prompt, timeout=180.0, max_tokens=6000)

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
                    # Ad pack — ready-to-run paid ad variants for A/B testing
                    _ads = [a for a in (plan.get("ad_pack") or []) if isinstance(a, dict)][:3]
                    if _ads:
                        wa_lines += ["", "🎯 *חבילת מודעות ממומנות (A/B):*"]
                        for ai, ad in enumerate(_ads, 1):
                            wa_lines.append(
                                f"{ai}. *{ad.get('headline','')}*\n"
                                f"   {ad.get('primary_text','')}\n"
                                f"   CTA: {ad.get('cta','')}"
                            )
                        wa_lines.append(f"🔗 קישור למודעות: {_noa_utm_link('paid_ads', week_num)}")
                    # Google Ads search campaign pack (See-Think-Do-Care / RSA specs)
                    _gads = plan.get("google_ads") or {}
                    if isinstance(_gads, dict) and _gads.get("headlines"):
                        wa_lines += ["", f"🔎 *Google Ads — {_gads.get('ad_group','קבוצת מודעות')}*"]
                        _kw_e = ", ".join(map(str, (_gads.get("keywords_exact") or [])[:8]))
                        _kw_p = ", ".join(map(str, (_gads.get("keywords_phrase") or [])[:6]))
                        _neg = ", ".join(map(str, (_gads.get("negatives") or [])[:8]))
                        if _kw_e: wa_lines.append(f"🎯 Exact: {_kw_e}")
                        if _kw_p: wa_lines.append(f"💭 Phrase: {_kw_p}")
                        if _neg:  wa_lines.append(f"🚫 שלילות: {_neg}")
                        wa_lines.append("📰 כותרות RSA:")
                        for h in (_gads.get("headlines") or [])[:10]:
                            wa_lines.append(f"  • {str(h)[:30]}")
                        wa_lines.append("📄 תיאורים:")
                        for d in (_gads.get("descriptions") or [])[:4]:
                            wa_lines.append(f"  • {str(d)[:90]}")
                        wa_lines.append(f"🔗 Final URL: {_noa_utm_link('google_ads', week_num)}")
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
                        f"{real_fact}\n"
                        f"{theme_hint}{plan_hint}\n"
                        f"{no_repeat}\n\n"
                        "כתיבה:\n"
                        "• פתחי עם ה-hook בשורה ראשונה — קצרה, חדה, לא שאלה גנרית\n"
                        "• ציוני את שם הרכב ואת שם החלק הספציפי\n"
                        "• אם ניתנה עובדה אמיתית מהקטלוג — שלבי את המחיר האמיתי (זה מה שמוכר); אסור להמציא מחיר\n"
                        "• פתרון: חיפוש לפי מספר רישוי ב-autosparefinder.co.il\n"
                        "• סיימי בשאלה אחת מעוררת שיח\n"
                        "• האשטאגים בשורה אחרונה בלבד\n\n"
                        "החזירי: טקסט הפוסט הסופי בלבד — ללא הסבר, ללא כותרת, ללא ספירה."
                    )

                    raw_post = await _hf_text(prompt=post_prompt, system=noa.system_prompt, timeout=90.0, max_tokens=1500)
                    caption = noa._finalize_noa_post(raw_post, platforms=[platform])
                    # UTM attribution (added 2026-07-05): every post link carries
                    # utm_source=<platform> so clicks are measurable per channel —
                    # "success_metrics" mean nothing without attribution.
                    caption = re.sub(
                        r"(?<![/\w.])autosparefinder\.co\.il(?![/\w])",
                        _noa_utm_link(platform, week_num).replace("https://", ""),
                        caption,
                    )

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
        # clamav DECOMMISSIONED 2026-07-12 (RAM-incompatible with this no-swap box;
        # uploads fail-open). Removed from health probes so it no longer alerts.
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

        # clamav probe removed 2026-07-12 — service decommissioned (see SERVICE_LABELS).

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
                # ROOT FIX 2026-07-06: this check used to read the in-memory
                # `db_update_agent._last_report`, which only updates when a FULL
                # cycle completes. Cycles legitimately run 1-3h and start 3h
                # apart, so "silence" exceeded the 120-min threshold during
                # EVERY normal cycle — 75 false WhatsApp alarms/day to the
                # owner. Real liveness lives in job_registry heartbeats (ticked
                # every few seconds while a cycle runs):
                #   • cycle RUNNING + heartbeat >30 min old  → genuinely stuck
                #   • no cycle at all for >5h (normal gap ≤ ~3h) → scheduler dead
                _stall_reason = None
                silence_mins = 0.0
                async with async_session_factory() as _cdb:
                    _hb_row = (await _cdb.execute(text("""
                        SELECT status, last_heartbeat_at FROM job_registry
                        WHERE job_id LIKE 'run_all_tasks%'
                        ORDER BY started_at DESC LIMIT 1
                    """))).fetchone()
                if _hb_row and _hb_row[1]:
                    _hb_ts = _hb_row[1]
                    if _hb_ts.tzinfo is not None:
                        _hb_ts = _hb_ts.astimezone(timezone.utc).replace(tzinfo=None)
                    silence_mins = (datetime.utcnow() - _hb_ts).total_seconds() / 60
                    if str(_hb_row[0]) == "running" and silence_mins > 30:
                        _stall_reason = f"מחזור רץ אבל ה-heartbeat קפוא כבר {silence_mins:.0f} דקות — כנראה תקוע"
                    elif str(_hb_row[0]) != "running" and silence_mins > 300:
                        _stall_reason = f"לא התחיל מחזור חדש כבר {silence_mins:.0f} דקות (רגיל: עד ~180)"

                if _stall_reason:
                    if True:
                        _alert_title = "⏱️  Worker db_update_agent: תקוע באמת"
                        _alert_msg = f"db_update_agent: {_stall_reason}."
                        print(f"[HealthMonitor] ALERT: worker stalled — {_stall_reason}")
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
                    # ROOT FIX 2026-07-08: only count RECENT failures (last 48h).
                    # The DLQ had 61 stale pending failures from transient infra
                    # errors months ago (old pool config, DB restarts) that were
                    # never retried/resolved. Counting ALL of them kept the count
                    # ≥ threshold forever, and the Redis dedup key's 24h TTL meant
                    # the "prev count" reset daily → re-alerted on ancient stale
                    # failures every day. A failure nobody acted on for months is
                    # not a live problem; only failures in the last 48h are.
                    _dlq_cutoff = datetime.utcnow() - timedelta(hours=48)
                    unprocessed_count = (await _pii_db.execute(
                        select(func.count(JobFailure.id)).where(
                            JobFailure.status.in_(["pending", "retrying"]),
                            JobFailure.created_at > _dlq_cutoff,
                        )
                    )).scalar() or 0
                    # Auto-resolve stale entries (>7d) so the DLQ self-cleans and
                    # never accumulates a permanent backlog of dead transient errors.
                    await _pii_db.execute(text("""
                        UPDATE job_failures
                        SET status='resolved', resolved_at=NOW(), resolved_by='auto_aged_7d'
                        WHERE status IN ('pending','retrying') AND created_at < NOW() - INTERVAL '7 days'
                    """))
                    await _pii_db.commit()

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

                    # 5a: Jobs that transitioned to failed or dead — but ONLY if
                    # not already superseded by a newer healthy run of the same
                    # task. A backend restart orphans mid-run jobs; the next cycle
                    # respawns healthy minutes later. Alerting on the orphan is a
                    # false alarm (root-fixed 2026-07-10; same filter the status
                    # digest already applies). Orphans are now marked 'superseded'
                    # at startup so most never reach here; this NOT EXISTS guard
                    # covers any the 2h watchdog reaps as 'failed' after recovery.
                    # Second guard (root-fixed 2026-07-13): only alert on jobs whose last
                    # activity falls WITHIN this container's lifetime. A restart (deploy /
                    # OOM / SIGKILL) orphans in-flight jobs whose heartbeat froze under the
                    # PREVIOUS container; the 2h zombie watchdog later flips them to 'failed'.
                    # Those are not real failures — the process was just replaced. Comparing
                    # COALESCE(last_heartbeat, started_at) to this container's start cleanly
                    # separates "died in the previous container" (skip) from "genuinely failed
                    # while we were running" (alert), independent of whether a newer run exists.
                    _cstart = _BACKEND_START_UTC.replace(tzinfo=None)
                    _failed_rows = (await _db.execute(text("""
                        SELECT job_id, job_name, status, error_message, started_at
                        FROM job_registry jr
                        WHERE status IN ('failed', 'dead')
                          AND started_at > NOW() - INTERVAL '12 hours'
                          AND COALESCE(jr.last_heartbeat_at, jr.started_at) >= :cstart
                          AND NOT EXISTS (
                              SELECT 1 FROM job_registry j2
                              WHERE split_part(j2.job_name, ':', 1) = split_part(jr.job_name, ':', 1)
                                AND j2.status IN ('running', 'completed', 'superseded')
                                AND j2.started_at > jr.started_at
                          )
                        ORDER BY started_at DESC
                    """), {"cstart": _cstart})).fetchall()

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
                    # Same container-lifetime guard as 5a: a 'running' row whose heartbeat
                    # froze before this container started is a restart orphan (handled by
                    # _reconcile_orphaned_jobs → 'superseded'), NOT a genuine stall. Only
                    # alert on jobs that were heartbeating within THIS container's lifetime.
                    _zombie_rows = (await _db.execute(text("""
                        SELECT job_id, job_name, last_heartbeat_at,
                               EXTRACT(EPOCH FROM (NOW() - last_heartbeat_at)) AS silence_s
                        FROM job_registry
                        WHERE status = 'running'
                          AND last_heartbeat_at >= :cstart
                          AND last_heartbeat_at < NOW() - (
                              COALESCE(ttl_seconds, 7200) * INTERVAL '1 second'
                          )
                    """), {"cstart": _cstart})).fetchall()

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
async def _price_watch_loop():
    """Every 6h: for each price watch, compare the current cheapest customer price to the
    watched price; if it dropped >=5% (and below the last-notified price), email price_drop.
    Cross-DB: watches live in PII, prices in catalog. Best-effort per watch."""
    from routes.parts import _current_part_price
    import email_templates as _ET
    from routes.email_utils import send_template
    _site = os.getenv("FRONTEND_URL", "https://autosparefinder.co.il").rstrip("/")
    await asyncio.sleep(300)
    while True:
        try:
            async with pii_session_factory() as pdb:
                watches = (await pdb.execute(text(
                    "SELECT id, user_id, part_id, part_name, watch_price_ils, last_notified_price_ils "
                    "FROM part_price_watches"))).fetchall()
            notified = 0
            for w in watches:
                try:
                    async with async_session_factory() as cat:
                        cur = await _current_part_price(cat, str(w.part_id))
                    if not cur:
                        continue
                    price, name = cur
                    threshold = float(w.watch_price_ils) * 0.95
                    already = w.last_notified_price_ils
                    if price <= threshold and (already is None or price < float(already)):
                        async with pii_session_factory() as pdb:
                            user = (await pdb.execute(select(User).where(User.id == w.user_id))).scalar_one_or_none()
                            if user and user.email:
                                # No /parts/:id detail route exists in the SPA — deep-link into
                                # the parts SEARCH route (which reads ?search=) so the watched
                                # part actually opens instead of redirecting to the homepage.
                                from urllib.parse import quote as _quote
                                _pname = w.part_name or name
                                _purl = f"{_site}/parts?search={_quote(_pname)}"
                                await send_template(user.email, user.full_name or "",
                                    _ET.price_drop(user.full_name or "", _pname, price, _purl))
                                notified += 1
                            await pdb.execute(text(
                                "UPDATE part_price_watches SET last_notified_price_ils=:p, last_notified_at=NOW() WHERE id=:id"),
                                {"p": price, "id": w.id})
                            await pdb.commit()
                except Exception as _we:
                    print(f"[PriceWatch] watch {getattr(w,'id','?')} error: {_we}")
                await asyncio.sleep(0.2)
            if watches:
                print(f"[PriceWatch] checked {len(watches)} watches, notified {notified}")
        except Exception as e:
            print(f"[PriceWatch] loop error: {e}")
        await asyncio.sleep(6 * 3600)  # every 6h


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

                        # Build a REAL, pressable payment link. For a single-item cart we can
                        # mint a one-tap canonical /pay/ checkout link (create_checkout_link runs
                        # the same server-side pricing as Stripe/website — never the raw cart
                        # unit_price, which is supplier COST). For multi-item carts the message
                        # falls back to the full cart URL (correct multi-item pricing + checkout
                        # button live there); we don't reprice a basket in a background loop.
                        pay_link = None
                        try:
                            if len(items) == 1:
                                from BACKEND_AI_AGENTS import create_checkout_link
                                _it = items[0]
                                _link = await create_checkout_link(
                                    part_id=str(_it.part_id),
                                    quantity=int(_it.quantity or 1),
                                    user_id=str(user.id),
                                    shipping_address={},
                                    source="whatsapp",
                                )
                                if _link and not _link.startswith("ERROR:"):
                                    pay_link = _link
                                else:
                                    print(f"[AbandonedCart] pay-link build failed for cart {cart.id}: {_link}")
                        except Exception as _ple:
                            print(f"[AbandonedCart] pay-link exception for cart {cart.id}: {_ple}")

                        # For a single-item cart we have a direct /pay/ link; otherwise fall
                        # back to a recipient-scoped cart-recovery link (NOT a bare /cart, which
                        # would open whoever is logged into the device — the "shows my cart" bug).
                        recovery_url = _cart_recovery_url(user.id)
                        wa_message = _build_abandoned_cart_whatsapp_message(
                            full_name=user.full_name,
                            item_lines=item_lines,
                            total_value=total_value,
                            pay_link=pay_link or recovery_url,
                        )

                        # Send WhatsApp
                        wa_result = await _wa_send(to=user.phone, text=wa_message)
                        if not wa_result.get("ok"):
                            print(f"[AbandonedCart] WhatsApp failed for user {user.id}: {wa_result.get('error')}")
                            skip_count += 1
                            continue

                        # Also nudge by email (best-effort — same cart, branded template).
                        if getattr(user, "email", ""):
                            try:
                                from routes.email_utils import send_template
                                import email_templates as _ET
                                _site = os.getenv("FRONTEND_URL", "https://autosparefinder.co.il").rstrip("/")
                                await send_template(user.email, user.full_name or "",
                                    _ET.abandoned_cart(user.full_name or "", items_summary,
                                                       total_value, pay_link or recovery_url))
                            except Exception as _ace:
                                print(f"[AbandonedCart] email failed for {user.id}: {_ace}")

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

                    # Regenerate a fresh, pressable /pay/ link for this existing order
                    # (canonical pricing from its OrderItems; Stripe URLs expire in 24h so
                    # we mint a new one each reminder). Falls back to the cart URL on failure.
                    _order_pay_link = None
                    try:
                        from routes.payments import regenerate_order_pay_link
                        _order_pay_link = await regenerate_order_pay_link(str(order.id))
                    except Exception as _ple:
                        print(f"[PaymentReminder] pay-link exception for order {order.id}: {_ple}")

                    wa_message = _build_pending_payment_whatsapp_message(
                        full_name=user.full_name,
                        order_number=order.order_number,
                        total_amount=float(order.total_amount),
                        pay_link=_order_pay_link,
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
from routes.public_api import router as public_api_router
app.include_router(public_api_router)
from routes.thumbnails import router as thumbnails_router
app.include_router(thumbnails_router)
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

@app.get("/api/v1/system/thumbnail-import")
async def thumbnail_import_status(cat_db: AsyncSession = Depends(get_db)):
    """Observability for the thumbnail-import supervisor (last cycle + live catalog coverage)."""
    out = dict(_THUMBNAIL_IMPORT_STATUS)
    try:
        row = (await cat_db.execute(text(
            "SELECT COUNT(*) FILTER (WHERE status='ok') AS ok, "
            "COUNT(*) FILTER (WHERE status='rejected_ad') AS rejected_ad, "
            "COUNT(*) FILTER (WHERE status='no_source') AS no_source, "
            "COUNT(DISTINCT url) FILTER (WHERE status='ok') AS distinct_images FROM part_thumbnails"
        ))).first()
        out["catalog"] = {"ok": row[0], "rejected_ad": row[1], "no_source": row[2],
                          "distinct_images": row[3], "dedup_saved": (row[0] or 0) - (row[3] or 0)}
    except Exception:
        out["catalog"] = None
    return out


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
        script = _Path("/app/importers/supplier_pdf_import.py")
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
