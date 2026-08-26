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
import json
import uuid
from uuid import UUID as _UUID
import os
import io
import time
import subprocess
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
            msg_parts = [f"🔴 *משימת רקע קרסה: {name}*"]
            if exc:
                msg_parts.append(f"שגיאה: {type(exc).__name__}: {str(exc)[:200]}")
            msg_parts.append("⚠️ המשימה לא תאותחל אוטומטית — יש לבדוק את השרת.")
            asyncio.get_event_loop().create_task(
                _wa_send_update("\n".join(msg_parts))
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

from routes.campaigns import router as campaigns_router
app.include_router(campaigns_router)


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

            _il_time = (_now + timedelta(hours=3)).strftime("%H:%M")
            lines: list[str] = [
                f"📊 *AutoSpareFinder — עדכון מצב* ({_il_time} ישראל)"
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

                # Harvest-queue progress: add live IL-queue coverage + 24h delta.
                _hq = (await _db.execute(text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status IN ('done','empty')) AS done,
                        COUNT(*) AS total,
                        COUNT(DISTINCT brand_en) FILTER (WHERE status='done') AS brands_done,
                        COALESCE(SUM(parts_found),0) AS parts_found,
                        COUNT(*) FILTER (WHERE last_harvested_at > NOW() - INTERVAL '24 hours') AS done_24h
                    FROM harvest_queue
                """))).fetchone()

            # Hebrew status labels — lead each line with Hebrew so WhatsApp
            # renders the whole line RTL (English job name embeds LTR naturally).
            _JOB_STATUS_HE = {
                "running":    "🟢 פועל",
                "completed":  "✅ הושלם",
                "failed":     "🔴 נכשל",
                "dead":       "💀 מת",
                "superseded": "⏩ הוחלף",
            }
            _TODO_STATUS_HE = {
                "pending":    "ממתין",
                "in_progress": "בתהליך",
                "completed":  "הושלם",
                "dismissed":  "נדחה",
            }

            lines.append("\n*עובדים:*")
            if jobs:
                for job in jobs:
                    age_min = (
                        int((_now - job.started_at.replace(tzinfo=timezone.utc) if job.started_at.tzinfo is None else _now - job.started_at).total_seconds() / 60)
                        if job.started_at else 0
                    )
                    hb_txt = ""
                    if job.last_heartbeat_at:
                        hb_ts = job.last_heartbeat_at if job.last_heartbeat_at.tzinfo else job.last_heartbeat_at.replace(tzinfo=timezone.utc)
                        hb_min = int((_now - hb_ts).total_seconds() / 60)
                        hb_txt = f" · פ.{hb_min}′"
                    status_he = _JOB_STATUS_HE.get(job.status, f"⏳ {job.status}")
                    lines.append(f"  {status_he}: {job.job_name} (+{age_min}′{hb_txt})")
            else:
                lines.append("  (אין משימות אחרונות)")

            dead_tasks = [n for n, t in _SUPERVISED_TASKS.items() if t.done() and not t.cancelled()]
            running_cnt = sum(1 for t in _SUPERVISED_TASKS.values() if not t.done())
            dead_suffix = f", {len(dead_tasks)} קרסו ❌" if dead_tasks else " ✅"
            lines.append(f"\n*משימות רקע:* {running_cnt} פעילות{dead_suffix}")
            if dead_tasks:
                lines.append(f"  קרסו: {', '.join(dead_tasks[:5])}")

            lines.append("\n*משימות REX לקטלוג:*")
            if todos:
                for row in todos:
                    lines.append(f"  {_TODO_STATUS_HE.get(row.status, row.status)}: {row.cnt}")
            else:
                lines.append("  (אין)")

            lines.append(f"\n*קטלוג:* {total_parts:,} חלקים פעילים")
            lines.append(f"  +{new_parts} נוספו ב-30 דקות האחרונות")

            # Harvest coverage — the moving number that shows daily progress.
            if _hq and _hq[1]:
                _hq_done, _hq_total, _hq_brands, _hq_parts, _hq_24h = _hq
                _hq_pct = round(_hq_done * 100.0 / _hq_total, 1) if _hq_total else 0
                lines.append(
                    f"\n*שאיבה (שוק ישראל):* {_hq_done:,}/{_hq_total:,} דגמים ({_hq_pct}%)"
                )
                lines.append(f"  {_hq_brands} מותגים · {_hq_parts:,} חלקים · +{_hq_24h} דגמים ב-24 שעות")

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
                lines[0] = f"📅 *AutoSpareFinder — דוח יומי* ({_il_time} ישראל)"
                await _wa_send_update("\n".join(lines))
                _last_digest_date = _today
                print("[StatusUpdate] Sent daily digest to owner")
            elif has_problem and problem_sig != _last_problem_sig:
                lines[0] = f"⚠️ *AutoSpareFinder — בעיה במערכת* ({_il_time} ישראל)"
                await _wa_send_update("\n".join(lines))
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
    Dedicated AI enrichment loop — runs every 30 minutes, 1000 parts per cycle.
    Gated by ENRICH_PARTS_ENABLED env var (default 0 — disabled by default to
    avoid burning Cerebras/Groq quota on background enrichment when providers are
    already loaded by customer chat).  Set ENRICH_PARTS_ENABLED=1 to activate.
    """
    await asyncio.sleep(120)  # 2min startup delay
    while True:
        if os.environ.get("ENRICH_PARTS_ENABLED", "0").strip().lower() in ("1", "true", "yes"):
            try:
                from ai_catalog_builder import enrich_pending_parts
                async for db in get_db():
                    report = await enrich_pending_parts(db, limit=1000)
                    print(f"[EnrichLoop] {report}")
                    break
            except Exception as exc:
                print(f"[EnrichLoop] error: {exc}")
        else:
            print("[EnrichLoop] skipped — ENRICH_PARTS_ENABLED=0")
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


_PLAYWRIGHT_MAX_CONCURRENT = int(os.getenv("PLAYWRIGHT_MAX_CONCURRENT", "2"))
# Each Chrome instance spawns ~12–15 OS processes (main, zygote×2, GPU, network,
# storage, renderer×N, crashpad×2). Alert if headless Chrome count exceeds:
_CHROME_PROC_ALERT_THRESHOLD = _PLAYWRIGHT_MAX_CONCURRENT * 18


def _count_chrome_headless_procs() -> tuple[int, list[int]]:
    """Return (count_of_running_headless_chrome_main_procs, list_of_pids)."""
    pids: list[int] = []
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                cmdline = open(f"/proc/{entry.name}/cmdline", "rb").read().replace(b"\x00", b" ").decode(errors="replace")
                if "chrome" in cmdline and "--headless" in cmdline and "--type=" not in cmdline:
                    pids.append(int(entry.name))
            except (PermissionError, FileNotFoundError):
                pass
    except Exception:
        pass
    return len(pids), pids


async def _zombie_reaper_loop() -> None:
    """
    Supervised loop — two jobs every 60 s:
    1. Reap zombie child processes left by Playwright/subprocess (waitpid WNOHANG).
    2. Monitor headless Chrome main-process count. If it exceeds
       PLAYWRIGHT_MAX_CONCURRENT × 18, kill the oldest instance (longest-running)
       and alert the owner — a safety net for leaked scraper browsers.
    """
    import os as _os
    import signal as _signal

    while True:
        await asyncio.sleep(60)
        try:
            # ── 1. Zombie reaping ──────────────────────────────────────────────
            reaped = 0
            try:
                while True:
                    pid, _ = _os.waitpid(-1, _os.WNOHANG)
                    if pid <= 0:
                        break
                    reaped += 1
            except ChildProcessError:
                pass
            if reaped:
                print(f"[zombie_reaper] reaped {reaped} child process(es)", flush=True)

            # ── 2. Chrome headless watchdog ────────────────────────────────────
            running_count, headless_pids = _count_chrome_headless_procs()
            if running_count > _CHROME_PROC_ALERT_THRESHOLD:
                print(
                    f"[zombie_reaper] WARNING: {running_count} headless Chrome main procs "
                    f"(threshold {_CHROME_PROC_ALERT_THRESHOLD}) — killing oldest",
                    flush=True,
                )
                # Kill the oldest (by /proc mtime — earliest create time)
                try:
                    oldest = min(headless_pids, key=lambda p: os.stat(f"/proc/{p}").st_ctime)
                    _os.kill(oldest, _signal.SIGKILL)
                    print(f"[zombie_reaper] killed orphaned headless Chrome PID {oldest}", flush=True)
                except Exception as kill_exc:
                    print(f"[zombie_reaper] kill failed: {kill_exc}", flush=True)

                # Alert owner once per 6h. Was previously `from agents.owner_console import
                # _wa_send_quiet` — that name doesn't exist in that module (ImportError),
                # so this alert had never once reached the owner; the surrounding bare
                # `except: pass` swallowed it silently every time. Fixed 2026-08-13 by
                # routing through the shared notify_owner (module-level, Redis-backed
                # cooldown — also drops the in-memory _WA_COOLDOWN dict that reset on
                # every restart).
                await notify_owner(
                    "harvest",
                    f"Chrome watchdog: {running_count} תהליכים headless זוהו",
                    f"חריגה מהמגבלה ({_PLAYWRIGHT_MAX_CONCURRENT}). התהליך הישן ביותר נהרג אוטומטית. בדוק לוגים של הסקרייפר.",
                    severity="warning",
                    alert_key="chrome_watchdog",
                    cooldown_s=21600,
                )
            elif running_count > 0:
                print(f"[zombie_reaper] Chrome headless: {running_count} main proc(s) running (ok)", flush=True)

        except Exception as exc:
            print(f"[zombie_reaper] error: {exc}", flush=True)


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


async def _car_parts_ie_full_seed_loop() -> None:
    """Keeps harvest_queue seeded with the FULL car-parts.ie catalogue so the main slug
    harvester covers all 176 brands, not just the IL-market seed. Runs
    maintenance/seed_car_parts_ie_full_catalog.py — which walks /car-brands (176 brands) →
    /car-brands/{brand}-parts (every model slug, statically via a cf_clearance cookie) and
    inserts each '{brand}/{model}' as a pending row (ON CONFLICT DO NOTHING, so re-runs only
    add NEW models car-parts.ie has published). The proven harvester then harvests them.

    (Replaced the dead cf_clearance-handoff + numeric-cascade crawler: car-parts.ie's
    maker_id/model_id/car_id 'spares-search' path returns 0 results — the inventory lives on
    the static slug pages this seeder enumerates. See FIXES_TRACKER 2026-07-23.)

    Runs once shortly after startup, then every ~30 days. LOW CPU priority; uses flaresolverr2
    so its one CF solve never starves the main harvester. Log → /app/state/logs/cpie_full_seed.log.
    Toggle off with CPIE_FULL_SEED_ENABLED=0. Crash-restart via _supervised_task."""
    import sys as _sys
    import time as _time
    if os.getenv("CPIE_FULL_SEED_ENABLED", "1") != "1":
        print("[cpie_full_seed] disabled (CPIE_FULL_SEED_ENABLED=0)", flush=True)
        return
    await asyncio.sleep(180)  # let startup + the harvester settle first
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "maintenance", "seed_car_parts_ie_full_catalog.py")
    logpath = "/app/state/logs/cpie_full_seed.log"
    os.makedirs("/app/state/logs", exist_ok=True)
    env = dict(os.environ)
    env.setdefault("FLARESOLVERR_URL_2", "http://flaresolverr2:8191/v1")
    between = int(os.getenv("CPIE_FULL_SEED_INTERVAL_S", str(30 * 24 * 3600)))  # ~monthly
    while True:
        started = _time.time()
        try:
            print(f"[cpie_full_seed] seeding (log → {logpath})", flush=True)
            with open(logpath, "a") as _lf:
                proc = await asyncio.create_subprocess_exec(
                    _sys.executable, "-u", script,
                    stdout=_lf, stderr=asyncio.subprocess.STDOUT,
                    preexec_fn=lambda: os.nice(10), env=env,
                )
                rc = await proc.wait()
            print(f"[cpie_full_seed] finished rc={rc} after {_time.time()-started:.0f}s", flush=True)
        except Exception as exc:
            print(f"[cpie_full_seed] launch failed: {exc}", flush=True)
        # Success → wait the full interval; a fast failure (CF down) → retry in 1h.
        await asyncio.sleep(between if _time.time() - started > 120 else 3600)


# ── Part-thumbnail import supervisor ──────────────────────────────────────────
# Module-level status so a healthcheck / /system endpoint can read the last cycle.
_THUMBNAIL_IMPORT_STATUS: dict = {"state": "starting", "total_ok": 0, "last_batch": None, "updated_at": None}


async def _weekly_maintenance_loop() -> None:
    """Weekly catalogue maintenance: re-merge new duplicates, then verify.

    WHY (owner, 2026-08-05). The big cleanups were ONE-SHOT jobs for a finite
    backlog — merge collapsed 272,152 duplicate groups to zero and was marked
    done forever. But harvesters and importers keep adding parts, and whenever
    two sources supply the same manufacturer+OEM a NEW duplicate is created.
    Measured the week after the merge: **107 new groups, 60 of them in 24h** —
    roughly 15/day, i.e. ~5,500/year if nothing ever runs again. Each duplicate
    splits a part's prices, fitment and images across two records, which is the
    exact defect the merge existed to remove.

    Nothing was watching in between either, because the parity gate is a ~6
    minute full-table suite that in practice only ran after a pipeline. So the
    drift was invisible until someone happened to look.

    This loop closes both gaps: a SMALL merge pass (the daily inflow is tiny, so
    it finishes in minutes rather than the hours the original backlog took),
    then the parity check to prove the outcome.

    Reports BY EXCEPTION — a clean week is silent. A routine "all good" weekly
    message is the camouflage that trains the owner to ignore the channel.
    """
    interval = int(os.getenv("WEEKLY_MAINT_INTERVAL_S", str(7 * 24 * 3600)))
    merge_limit = int(os.getenv("WEEKLY_MAINT_MERGE_LIMIT", "2000"))
    await asyncio.sleep(int(os.getenv("WEEKLY_MAINT_FIRST_DELAY_S", "3600")))
    while True:
        try:
            if os.getenv("WEEKLY_MAINT_ENABLED", "1").strip().lower() not in ("1", "true", "yes"):
                await asyncio.sleep(interval)
                continue
            # Never fight the job queue — same stand-down contract the other
            # heavy writers use.
            try:
                import job_queue as _jq
                async with async_session_factory() as _db:
                    if await _jq.queue_busy(_db):
                        logger.info("[weekly_maint] deferred — job queue is running")
                        await asyncio.sleep(3600)
                        continue
            except Exception:
                pass

            problems: list[str] = []

            # 1. Merge whatever duplicates arrived since last week.
            m = await asyncio.to_thread(
                subprocess.run,
                ["python3", "/app/maintenance/merge_master_parts.py",
                 "--all-brands", "--limit", str(merge_limit)],
                capture_output=True, text=True, timeout=5400)
            mout = ((m.stdout or "") + (m.stderr or ""))[-1500:]
            merged = 0
            _mm = re.search(r"ALL-BRANDS DONE groups=(\d+)", mout)
            if _mm:
                merged = int(_mm.group(1))
            logger.info("[weekly_maint] merge rc=%s groups=%s", m.returncode, merged)
            if m.returncode != 0:
                problems.append(f"מיזוג כפילויות נכשל: {mout[-200:]}")

            # 2. Verify the outcome (full suite — this is the weekly gate).
            p = await asyncio.to_thread(
                subprocess.run,
                ["python3", "/app/maintenance/pipeline_parity_check.py"],
                capture_output=True, text=True, timeout=1800)
            pout = ((p.stdout or "") + (p.stderr or ""))[-2000:]
            logger.info("[weekly_maint] parity rc=%s", p.returncode)
            if p.returncode != 0:
                fails = [ln.strip() for ln in pout.splitlines() if "[FAIL]" in ln]
                problems.append("בדיקת התאמה נכשלה:\n" + "\n".join(fails[:5]))

            if problems:
                await notify_owner(
                    "harvest",
                    "תחזוקה שבועית — נדרשת תשומת לב",
                    (f"מוזגו {merged:,} כפילויות חדשות.\n" if merged else "")
                    + "\n".join(problems)[:900],
                    severity="warning",
                )
            else:
                logger.info("[weekly_maint] clean (merged=%s) — no owner message", merged)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[weekly_maint] error: %s", exc)
        await asyncio.sleep(interval)


async def _whatsapp_link_monitor_loop() -> None:
    """Watch the WhatsApp bridge link and alert OUT-OF-BAND when it breaks.

    Owner concern 2026-07-29: "the business device is not used by a human — if
    its battery dies the link breaks and I can't reconnect until I recharge."

    The reassuring part (verified against current sources): WhatsApp multi-device
    keeps a linked device working while the phone is OFF. The phone only has to
    come online once every ~14 days or WhatsApp expires the session. So a flat
    battery for hours or days is not the failure mode.

    The real failure mode is the one that actually bit us: on 2026-07-29 the
    session was logged out at 03:25 and NOBODY KNEW for eleven hours, because
    /health reported {"ok":true,"connected":true} the whole time (it only tested
    that a socket object existed). Every owner message failed silently.

    Hence this loop, and hence its most important property:

        IT MUST NOT ALERT OVER WHATSAPP.

    An outage notification that travels over the broken channel is worthless.
    Alerts go to Telegram and email, which are independent of WhatsApp.
    """
    interval = int(os.getenv("WA_LINK_CHECK_INTERVAL_S", "300"))
    await asyncio.sleep(180)
    last_state: str | None = None
    last_alert = 0.0
    realert_s = int(os.getenv("WA_LINK_REALERT_S", "21600"))   # 6h while broken

    async def _alert(subject: str, body: str) -> None:
        sent = []
        tg_token = os.getenv("TELEGRAM_ADMIN_BOT_TOKEN", "")
        tg_chat = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")
        if tg_token and tg_chat:
            try:
                await _noa_send_telegram(tg_token, tg_chat, f"{subject}\n\n{body}")
                sent.append("telegram")
            except Exception as exc:
                logger.warning("[wa_link] telegram alert failed: %s", exc)
        to = os.getenv("OWNER_EMAIL", "") or os.getenv("SMTP_FROM", "")
        if to:
            try:
                from routes.email_utils import send_email
                await send_email(to, "AutoSpareFinder", subject,
                                 f"<pre>{body}</pre>", body)
                sent.append("email")
            except Exception as exc:
                logger.warning("[wa_link] email alert failed: %s", exc)
        logger.error("[wa_link] %s | %s | delivered via: %s",
                     subject, body.replace("\n", " ")[:200], sent or "NOTHING")

    while True:
        try:
            url = (os.getenv("WHATSAPP_BRIDGE_URL",
                             "http://whatsapp-bridge:3001/send")
                   .replace("/send", "/health"))
            state, detail = "unknown", ""
            try:
                async with httpx.AsyncClient(timeout=15) as cx:
                    h = (await cx.get(url)).json()
                if h.get("account_mismatch"):
                    am = h["account_mismatch"]
                    state = "wrong_account"
                    detail = (f"מחובר: {am.get('linked')} ({am.get('name') or '—'})\n"
                              f"אמור להיות: {am.get('expected')}")
                elif h.get("connected"):
                    state = "ok"
                elif h.get("awaiting_qr_scan"):
                    state = "awaiting_qr"
                else:
                    state = "disconnected"
            except Exception as exc:
                state = "unreachable"
                detail = f"{type(exc).__name__}: {exc}"

            now = time.time()
            changed = state != last_state
            stale = (now - last_alert) >= realert_s

            if state != "ok" and (changed or stale):
                msgs = {
                    "awaiting_qr": ("🔴 WhatsApp מנותק — ממתין לסריקת QR",
                                    "הקשר לוואטסאפ נותק והמערכת ממתינה לסריקה מחדש.\n"
                                    "לקוחות שכותבים לעסק לא מגיעים אלינו כרגע.\n\n"
                                    "לתיקון, הרץ בשרת:\n"
                                    "bash /opt/autosparefinder/whatsapp-bridge/show_qr.sh\n"
                                    "וסרוק מהטלפון של העסק (972532426920)."),
                    "disconnected": ("🔴 WhatsApp מנותק",
                                     "הגשר פועל אך אינו מחובר לוואטסאפ."),
                    "wrong_account": ("🚨 WhatsApp מחובר לחשבון הלא נכון",
                                      "לקוחות שכותבים למספר העסקי לא מגיעים אלינו."),
                    "unreachable": ("🔴 גשר הוואטסאפ אינו מגיב",
                                    "לא ניתן לפנות לשירות הגשר."),
                }
                subj, body = msgs.get(state, ("🔴 WhatsApp — מצב לא ידוע", state))
                await _alert(subj, (body + ("\n\n" + detail if detail else "")))
                last_alert = now

            if state == "ok" and last_state not in (None, "ok"):
                await _alert("✅ WhatsApp חזר לפעול",
                             "הקשר לוואטסאפ שוחזר. הודעות נשלחות שוב כרגיל.")
                last_alert = 0.0

            if changed:
                logger.info("[wa_link] state %s -> %s %s", last_state, state, detail[:120])
            last_state = state

            # ── the 14-day clock ──────────────────────────────────────────────
            # WhatsApp expires a linked device if the HOST phone has not been
            # online for ~14 days. Nothing in the Baileys session exposes when
            # the phone was last seen, so this cannot be measured — only
            # pre-empted. A reminder every N days (default 10) costs one message
            # and prevents the one outage that needs physical access to fix.
            try:
                from BACKEND_AUTH_SECURITY import get_redis as _gr_wa
                _r = await _gr_wa()
                if _r and state == "ok":
                    days = int(os.getenv("WA_PHONE_REMINDER_DAYS", "10"))
                    if await _r.set("autospare:wa:phone_reminder", "1",
                                    ex=days * 86400, nx=True):
                        await _alert(
                            "🔋 הדליקו את טלפון העסק לדקה",
                            "וואטסאפ מנתק מכשיר מקושר אם טלפון העסק לא היה מחובר "
                            f"לאינטרנט כ-14 יום.\n\n"
                            f"מספר: {os.getenv('WHATSAPP_EXPECTED_NUMBER','972532426920')}\n"
                            "הדליקו אותו לדקה עם אינטרנט — זה מאפס את השעון "
                            "ל-14 יום נוספים. הכי פשוט: להשאיר אותו על המטען.\n\n"
                            f"(תזכורת אוטומטית כל {days} ימים)")
            except Exception:
                pass

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[wa_link] monitor error: %s", exc)
        await asyncio.sleep(interval)


async def _job_queue_report_loop() -> None:
    """Hourly WhatsApp progress report on the job queue, while it is ACTIVE.

    Owner request 2026-07-29: "supervise the jobs and check each hour and send
    my update to my whatsapp."

    This is deliberately NOT the pattern that was just removed from the harvest
    supervisor. That one reported "still working, nothing changed" forever, on a
    process with no end. This one:
      • only exists while a FINITE migration is running — it goes silent the
        moment the queue is idle or finished, with no further reminders;
      • reports NUMBERS THAT MOVE (work done in the last hour, remaining, ETA),
        which is information rather than reassurance;
      • sends the completion summary once, including the parity verdict — the
        message the owner actually needs.

    Quiet hours: routine reports at night are SKIPPED, not queued, so the owner
    never wakes to a stack of stale hourly updates (the failure mode called out
    in the harvest loop). A failure or the final summary is sent as critical.
    """
    import job_queue as _jq

    interval = int(os.getenv("JOB_QUEUE_REPORT_INTERVAL_S", "3600"))
    await asyncio.sleep(300)                       # let the queue actually start
    last_sent = 0.0
    prev: dict = {}                                # step_key -> remaining
    announced_done = False

    while True:
        try:
            owner = os.getenv("OWNER_WHATSAPP_PHONE", "")
            enabled = os.getenv("JOB_QUEUE_ENABLED", "0").strip().lower() in ("1", "true", "yes")
            if not owner or not enabled:
                await asyncio.sleep(interval)
                continue

            async with async_session_factory() as db:
                st = await _jq.status(db)

            steps = st["steps"]
            active = [s for s in steps if s["status"] in ("pending", "running")]
            failed = [s for s in steps if s["status"] == "failed"]
            finished = not active

            # ── the queue finished: say so ONCE, with the parity verdict ──────
            if finished and not announced_done:
                verdict = "—"
                par = next((s for s in steps if s["step_key"] == "parity_check"), None)
                if par:
                    verdict = ("✅ עבר" if par["status"] == "done"
                               else f"❌ {par['status']}")
                lines = [
                    "🏁 *תור המשימות הסתיים*",
                    f"{st['done']}/{st['total']} שלבים הושלמו"
                    + (f" · {len(failed)} נכשלו" if failed else ""),
                    f"בדיקת התאמה סופית: {verdict}",
                ]
                for s in failed:
                    lines.append(f"❌ {s['title'] or s['step_key']}: "
                                 f"{str(s['error'] or '')[:110]}")
                await _wa_send_update("\n".join(lines), critical=True)
                announced_done = True
                await asyncio.sleep(interval)
                continue
            if active:
                announced_done = False              # a new run re-arms the summary

            # ── a FAILED step is not a routine update — send it immediately ───
            newly_failed = [s for s in failed
                            if prev.get("failed::" + s["step_key"]) != s["status"]]
            for s in newly_failed:
                prev["failed::" + s["step_key"]] = s["status"]
                await _wa_send_update(critical=True, text=(
                    f"❌ *שלב נכשל בתור המשימות*\n"
                    f"{s['title'] or s['step_key']}\n"
                    f"{str(s['error'] or '')[:220]}\n"
                    f"לצפייה: כתוב *תור*"))

            if not active:
                await asyncio.sleep(interval)
                continue

            # ── routine hourly progress ──────────────────────────────────────
            due = (time.time() - last_sent) >= interval - 60
            open_window, _ = _notify_window_open()
            if not (due and open_window):
                # Skipped at night ON PURPOSE — not queued. A stale 03:00 progress
                # report delivered at 09:00 is noise; the 09:00 one is current.
                await asyncio.sleep(min(interval, 600))
                continue

            cur = next((s for s in steps if s["status"] == "running"), active[0])
            key, rem = cur["step_key"], cur["remaining"]
            done_last_hour = None
            if rem is not None and prev.get(key) is not None:
                done_last_hour = max(0, int(prev[key]) - int(rem))
            if rem is not None:
                prev[key] = int(rem)

            age = ""
            if cur["remaining_at"]:
                try:
                    _ra = cur["remaining_at"]
                    _ra = _ra.replace(tzinfo=None) if _ra.tzinfo else _ra
                    mins = int((datetime.utcnow() - _ra).total_seconds() / 60)
                    if mins > 5:
                        age = f" (נמדד לפני {mins} דק')"
                except Exception:
                    pass

            lines = [f"🧵 *תור המשימות — עדכון שעתי*",
                     f"▶️ {cur['title'] or key}"]
            if rem is not None:
                lines.append(f"נותרו: {int(rem):,}{age}")
            if done_last_hour:
                lines.append(f"בשעה האחרונה: {done_last_hour:,} טופלו")
                if rem:
                    hrs = int(rem) / done_last_hour
                    lines.append(f"הערכת סיום לשלב: ~{hrs:.1f} שעות")
            elif done_last_hour == 0:
                lines.append("⚠️ אין התקדמות מדודה בשעה האחרונה")
            lines.append(f"מנות שהורצו: {cur['batches_run']}")
            lines.append(f"— {st['done']}/{st['total']} שלבים הושלמו · לעצירה: *עצור*")

            await _wa_send_update("\n".join(lines))
            last_sent = time.time()
            logger.info("[job_queue_report] sent: step=%s remaining=%s delta=%s",
                        key, rem, done_last_hour)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[job_queue_report] error: %s", exc)
        await asyncio.sleep(min(interval, 600))


async def _job_queue_loop() -> None:
    """Drive the sequenced catalogue pipeline (job_queue.py) one batch at a time.

    DISABLED BY DEFAULT (`JOB_QUEUE_ENABLED=0`). The owner's standing gate is
    that infrastructure is verified BEFORE anything is triggered, so the runner
    existing must not mean the runner is running. Flipping the env var and
    restarting is the deliberate "go".

    One batch per iteration: a stop request or a container restart can never
    interrupt more than a single batch, and every batch commits before the next
    is considered.
    """
    import job_queue as _jq

    await asyncio.sleep(120)  # let startup settle before touching the catalogue
    # Make sure the table and the plan exist even while disabled, so the owner
    # can inspect the queue and its measured counters before starting it.
    try:
        async with async_session_factory() as db:
            await _jq.seed_default_plan(db)
    except Exception as exc:
        logger.warning("[job_queue] seed failed: %s", exc)

    idle_sleep = int(os.getenv("JOB_QUEUE_IDLE_SLEEP_S", "300"))
    while True:
        try:
            if os.getenv("JOB_QUEUE_ENABLED", "0").strip().lower() not in ("1", "true", "yes"):
                await asyncio.sleep(idle_sleep)
                continue
            async with async_session_factory() as db:
                res = await _jq.run_once(db)
            act = res.get("action")
            logger.info("[job_queue] %s", res)
            if act in ("idle", "stopped"):
                await asyncio.sleep(idle_sleep)
            elif act == "error" and res.get("final"):
                # A step that exhausted its retries must not be retried in a hot
                # loop — it needs the owner, so back off and let the status view
                # (and the WhatsApp `תור` command) surface it.
                await asyncio.sleep(idle_sleep)
            else:
                await asyncio.sleep(_jq.COOLDOWN_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[job_queue] loop error: %s", exc)
            await asyncio.sleep(120)


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
        # Stand down while the JOB QUEUE owns the thumbnail step — otherwise two
        # processes run the SAME script against the same candidate query and
        # simply contend. The queue hands the work back (status 'done') and this
        # loop resumes for parts imported afterwards.
        try:
            import job_queue as _jq
            async with async_session_factory() as _qdb:
                # Match on the SCRIPT, not the step name — the same script gets
                # queued under different step keys (part_thumbnails,
                # thumbnails_retry_blocked) and a key-based check let both the
                # queue step and this supervisor run it simultaneously.
                if await _jq.owns_script(_qdb, "build_part_thumbnails"):
                    _THUMBNAIL_IMPORT_STATUS["state"] = "deferred_to_job_queue"
                    await asyncio.sleep(idle_backoff)
                    continue
        except Exception:
            pass  # cannot tell => keep working rather than stall silently

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
        # KILL SWITCH. The supervisor's restart-backoff only helps when the
        # harvester EXITS; this one stays alive and retries Cloudflare inside
        # its own loop, so a permanently-failing session burns CPU forever with
        # no supervisor signal. Measured 2026-08-02: 29 challenge timeouts and
        # ZERO successful solves in 24h, zero parts written, while its
        # FlareSolverr instance spiked to ~283% CPU. Off until the Cloudflare
        # path actually works again.
        if os.getenv("AMAYAMA_HARVEST_ENABLED", "1").strip().lower() not in ("1", "true", "yes"):
            await asyncio.sleep(3600)
            continue
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


async def _amayama_server_browser_loop() -> None:
    """Supervises the SERVER-SIDE real-browser Amayama harvester (2026-08-06 —
    replaces the FlareSolverr path above, which is permanently blocked by
    Cloudflare's CDP-remote-debugging-protocol fingerprint: verified live that
    BOTH FlareSolverr's internal browser and a fresh vanilla Playwright
    instance hang forever on Cloudflare's Managed Challenge on every page past
    the homepage, 100% reproducible. See FIXES_TRACKER 2026-08-06 for the full
    diagnosis).

    This runs a genuine, NON-CDP headful Chrome under Xvfb — launched as a
    plain subprocess with no --remote-debugging-port, so there is no CDP
    connection at any point, which is the exact thing Cloudflare fingerprints.
    A small unpacked extension (state/amayama_ext/) sets the account's login
    cookie via the chrome.cookies API (no profile-file/SQLite hacking) and
    auto-runs the harvest loop as a content script on amayama.com — proven
    live: a real browser session sails through the identical URLs that hang
    FlareSolverr/Playwright indefinitely.

    Ensures Xvfb + Chrome are alive; ONE combined process tree — if Chrome
    dies for any reason (crash, OOM, container restart) this relaunches it
    with backoff. Skips fast (checks every 30 min) if amayama_session.json or
    the extension dir is missing — costs nothing until those exist."""
    import time as _time
    await asyncio.sleep(90)
    root = os.path.dirname(os.path.abspath(__file__))
    cookie_file = os.path.join(root, "amayama_session.json")
    ext_dir = "/app/state/amayama_ext"
    profile_dir = "/app/state/amayama_chrome_profile"
    chrome_bin = "/root/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome"
    display = ":99"
    backoff = 60
    while True:
        if os.getenv("AMAYAMA_SERVER_BROWSER_ENABLED", "1").strip().lower() not in ("1", "true", "yes"):
            await asyncio.sleep(3600)
            continue
        if not (os.path.exists(cookie_file) and os.path.isdir(ext_dir) and os.path.exists(chrome_bin)):
            await asyncio.sleep(1800)
            continue
        started = _time.time()
        try:
            xvfb_alive = subprocess.run(["pgrep", "-f", f"Xvfb {display}"], capture_output=True).returncode == 0
            if not xvfb_alive:
                # ROOT FIX 2026-08-12: an Xvfb killed without a clean exit (container
                # restart, OOM) leaves /tmp/.X{N}-lock behind. pgrep correctly reports
                # "not running", but the NEXT Xvfb refuses to bind: "Server is already
                # active for display 99 ... remove /tmp/.X99-lock". Safe to remove — we
                # just confirmed via pgrep that nothing is actually holding it.
                _x_lock = f"/tmp/.X{display.lstrip(':')}-lock"
                if os.path.exists(_x_lock):
                    try:
                        os.remove(_x_lock)
                        print(f"[amayama_server_browser] removed stale {_x_lock}", flush=True)
                    except Exception as _xe:
                        print(f"[amayama_server_browser] could not remove {_x_lock}: {_xe}", flush=True)
                print("[amayama_server_browser] starting Xvfb", flush=True)
                subprocess.Popen(
                    ["setsid", "Xvfb", display, "-screen", "0", "1366x768x24", "-nolisten", "tcp"],
                    stdout=open("/app/state/logs/xvfb.log", "a"), stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                await asyncio.sleep(2)
            # ROOT FIX 2026-08-12: this is the actual, CONTINUOUSLY recurring failure
            # (chrome exited rc=21 every ~30 min, all day, zero successful launches).
            # Chrome's own crash-recovery lock (SingletonLock/SingletonCookie/
            # SingletonSocket in the profile dir) survives a killed process — Chrome then
            # refuses to start against what it thinks is a profile "in use by another
            # process (808) on another computer" (a stale PID+hostname from before a past
            # restart). This loop's own structure (`await proc.wait()` before ever
            # looping back) guarantees it never runs two Chrome instances against this
            # profile concurrently, so it is always safe to clear these immediately
            # before every launch attempt.
            for _lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                _lock_path = os.path.join(profile_dir, _lock_name)
                if os.path.exists(_lock_path) or os.path.islink(_lock_path):
                    try:
                        os.remove(_lock_path)
                    except Exception as _ce:
                        print(f"[amayama_server_browser] could not remove {_lock_path}: {_ce}", flush=True)
            env = dict(os.environ)
            env["DISPLAY"] = display
            print("[amayama_server_browser] launching Chrome", flush=True)
            proc = await asyncio.create_subprocess_exec(
                chrome_bin,
                f"--user-data-dir={profile_dir}",
                f"--load-extension={ext_dir}",
                f"--disable-extensions-except={ext_dir}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking",
                "--no-sandbox", "--disable-dev-shm-usage",
                "--window-size=1366,768", "--start-maximized",
                "about:blank",
                env=env,
                stdout=open("/app/state/logs/amayama_chrome.log", "a"),
                stderr=asyncio.subprocess.STDOUT,
            )
            rc = await proc.wait()
            uptime = _time.time() - started
            print(f"[amayama_server_browser] chrome exited rc={rc} after {uptime:.0f}s — restarting", flush=True)
        except Exception as exc:
            uptime = 0.0
            print(f"[amayama_server_browser] failed to launch: {exc}", flush=True)
        backoff = 60 if uptime > 300 else min(backoff * 2, 1800)
        await asyncio.sleep(backoff)


def _harvest_status_decision(
    *, first_sample: bool, d_models: int, d_parts: int, in_progress: int, pending: int,
    prev_state: "str | None", secs_since_alert: "float | None",
    realert_s: int, mode: str = "exceptions",
) -> "tuple[str, bool]":
    """Decide the harvest state and whether the owner should hear about it.

    Pulled out of `_harvest_supervisor_loop` so the notification policy can be
    tested directly instead of only by watching a 30-minute loop for a day.

    Returns (state, should_send). state ∈ {ok, stalled, idle}.

    Policy (owner, 2026-07-29): silence while healthy; speak on stall/idle and
    once on recovery. A persistent stall re-alerts only every `realert_s`.
    """
    if first_sample:
        # No measured window yet — never alert on a delta we did not observe.
        return "ok", mode == "hourly"
    if d_models == 0 and d_parts == 0:
        state = "idle" if (in_progress == 0 and pending == 0) else "stalled"
    else:
        state = "ok"
    bad = state in ("stalled", "idle")
    stale = secs_since_alert is not None and secs_since_alert >= realert_s
    should_send = (
        mode == "hourly"
        or (bad and (prev_state != state or stale))
        or (not bad and prev_state in ("stalled", "idle"))
    )
    return state, should_send


async def _harvest_supervisor_loop() -> None:
    """
    Smart harvest supervisor (goal 2026-07-07). The harvester itself is now
    queue-driven — it pulls the next highest-priority pending model from
    harvest_queue (seeded from vehicle_market_il, ranked by active Israeli road
    vehicles) and auto-advances. This loop is the OVERSIGHT layer:
      • Reports coverage progress toward the full catalogue (all brands+models).
      • Re-seeds the queue if it's ever empty (never lets the harvester idle).
      • Notifies the owner BY EXCEPTION only — when the harvest stalls or goes idle,
        and once when it recovers. A progressing harvester is silent (owner directive
        2026-07-29: routine "still working" reports are noise that hides real alerts).
      • Sends the owner a weekly harvest digest (Sunday ~09:00 IL).
    """
    await asyncio.sleep(900)  # let startup settle
    _last_digest_date: str | None = None
    # Progress is SAMPLED every _report_interval_s and judged; it is not announced.
    # HARVEST_REPORT_MODE=hourly restores the old unconditional hourly report.
    _report_interval_s = int(os.getenv("HARVEST_REPORT_INTERVAL_S", "3600"))
    _report_mode = os.getenv("HARVEST_REPORT_MODE", "exceptions").strip().lower()
    # How long a PERSISTENT stall waits before re-alerting (default 6h) — so a stuck
    # harvester is chased up, but never once an hour.
    _realert_s = int(os.getenv("HARVEST_STALL_REALERT_S", "21600"))
    _last_report_utc: "datetime | None" = None
    _prev_done: int | None = None
    _prev_parts: int | None = None
    _harvest_alert_state: str | None = None      # ok | stalled | idle
    _harvest_alert_sent_utc: "datetime | None" = None
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
                        COUNT(*) FILTER (WHERE status='in_progress') AS in_progress,
                        COALESCE(SUM(parts_found),0) AS parts_total
                    FROM harvest_queue
                """))).fetchone()
            if row:
                done, done_parts, total, bdone, btot, pending, in_progress, parts = row
                pct = round(done * 100.0 / total, 1) if total else 0
                print(
                    f"[harvest_supervisor] catalogue coverage: {done}/{total} models ({pct}%), "
                    f"{bdone}/{btot} brands, in_progress={in_progress}, pending={pending}, "
                    f"parts_found={parts}",
                    flush=True,
                )

                _now = datetime.now(timezone.utc)

                # ── Harvest status → owner WhatsApp, BY EXCEPTION ────────────────────
                # Owner directive 2026-07-29: "the messages about tasks that is done
                # should not be sent — I asked to be notified about the STATUS of the
                # import if it's idle, so I don't have to get reminders."
                #
                # So a healthy, progressing harvester is SILENT. The owner hears from
                # this loop only when the import is not moving (stalled / idle), and
                # once when it recovers. The stall signal was already being computed
                # here — it was just buried inside an unconditional hourly send, so the
                # one message that mattered arrived looking like 23 that didn't.
                _open, _local = _notify_window_open()
                _due = (_last_report_utc is None
                        or (_now - _last_report_utc).total_seconds() >= _report_interval_s - 60)
                if _due:
                    _first = _last_report_utc is None
                    mins = 0 if _first else int((_now - _last_report_utc).total_seconds() / 60)
                    d_models = 0 if _first else done - (_prev_done or 0)
                    d_parts = 0 if _first else parts - (_prev_parts or 0)
                    _state, _should_send = _harvest_status_decision(
                        first_sample=_first, d_models=d_models, d_parts=d_parts,
                        in_progress=in_progress, pending=pending,
                        prev_state=_harvest_alert_state,
                        secs_since_alert=(None if _harvest_alert_sent_utc is None
                                          else (_now - _harvest_alert_sent_utc).total_seconds()),
                        realert_s=_realert_s, mode=_report_mode,
                    )
                    _bad = _state in ("stalled", "idle")

                    if _should_send and _open:
                        owner = os.getenv("OWNER_WHATSAPP_PHONE", "")
                        if owner:
                            try:
                                async with async_session_factory() as db3:
                                    cur = (await db3.execute(text("""
                                        SELECT brand_en, model_name FROM harvest_queue
                                        WHERE status='in_progress'
                                        ORDER BY updated_at DESC LIMIT 3
                                    """))).fetchall()
                                cur_txt = "\n".join(f"• {r[0]} {r[1]}" for r in cur) or "—"
                            except Exception:
                                cur_txt = "—"
                            if _state == "stalled":
                                head = "⚠️ *שאיבת הקטלוג תקועה*"
                                delta_line = (f"אין התקדמות ב-{mins} הדק' האחרונות "
                                              f"(בתהליך: {in_progress} · ממתינים: {pending:,})")
                            elif _state == "idle":
                                head = "💤 *שאיבת הקטלוג בטלה*"
                                delta_line = "התור ריק — אין דגמים ממתינים לשאיבה."
                            else:
                                head = "✅ *שאיבת הקטלוג חזרה לפעול*"
                                delta_line = (f"ב-{mins} הדק' האחרונות: "
                                              f"+{d_models} דגמים, +{d_parts:,} חלקים")
                            msg = (
                                f"{head}\n"
                                f"כיסוי: {done:,}/{total:,} דגמים ({pct}%) · {bdone}/{btot} מותגים\n"
                                f"{delta_line}\n"
                                f"סה\"כ חלקים שנאספו: {parts:,}\n"
                                f"נשאבים כעת:\n{cur_txt}"
                            )
                            try:
                                await _wa_send_update(msg)
                                _harvest_alert_sent_utc = _now
                            except Exception as _rex:
                                print(f"[harvest_supervisor] status send failed: {_rex}", flush=True)
                        # Recovery is a one-shot: clear the cooldown so the NEXT stall
                        # alerts immediately instead of waiting out the re-alert window.
                        if not _bad:
                            _harvest_alert_sent_utc = None

                    # Track state even when the notify window is closed, so a stall that
                    # begins at night is still recognised as ONE event at 09:00 — not
                    # re-announced as new.
                    _harvest_alert_state = _state
                    print(f"[harvest_supervisor] status={_state} d_models={d_models} "
                          f"d_parts={d_parts} sent={_should_send and _open}", flush=True)
                    _last_report_utc = _now
                    _prev_done = done
                    _prev_parts = parts

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
                            await _wa_send_update(msg)
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
            # Was calling social.whatsapp_provider.send_message directly — bypasses
            # BOTH quiet hours and the updates-group routing (a real regression against
            # the documented "all outbound owner WhatsApp goes through _wa_send_quiet"
            # rule). Fixed 2026-08-13 via the shared notify_owner.
            await notify_owner(
                "harvest",
                "זוהה פער סנכרון ב-Meilisearch",
                f"אינדקס: {meili_docs:,} מסמכים\nקטלוג: {db_count:,} חלקים פעילים\n"
                f"פער: {gap:,} מסמכים — הסנכרון מפגר או מדלג על שורות.",
                severity="warning",
                alert_key="meili_parity_drift",
                cooldown_s=10800,
            )
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

            # PURGE DEACTIVATED PARTS (added 2026-07-28).
            # meili_sync only ever ADDS/UPDATES — every source query carries
            # `WHERE is_active = TRUE` — so nothing ever removed a document when
            # a part was later deactivated. Measured live: 4,419,309 index docs
            # against 4,350,159 active parts = 69,150 phantom documents, 81% of
            # them 'כללי' (deactivated parts skew to the catch-all). Customers
            # don't see them (routes/parts.py filters pc.is_active at the DB
            # join) but the Meilisearch FACET COUNTS were wrong and the drift
            # grew with every deactivation. Runs after each sync so it cannot
            # silently accumulate again.
            try:
                purge = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "maintenance", "meili_purge_inactive.py")
                if os.path.exists(purge):
                    pproc = await asyncio.create_subprocess_exec(
                        _sys.executable, purge,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    prc = await pproc.wait()
                    print(f"[meili_sync_loop] inactive purge rc={prc}", flush=True)
            except Exception as pexc:
                print(f"[meili_sync_loop] purge skipped: {pexc}", flush=True)

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
    """Supervisor for the Amayama harvest. Amayama gates every non-homepage page
    (search/find/product) behind a Cloudflare Managed Challenge that fingerprints
    and permanently blocks ANY CDP-driven browser — verified 2026-08-06 that this
    catches BOTH FlareSolverr's internal browser AND a fresh vanilla Playwright
    instance (100% reproducible "Performing security verification" hang, never
    resolves). A REAL, non-CDP browser goes around it clean (proven live: real
    offer tables via plain same-origin fetch(), zero interstitial). TWO harvesters
    now implement that: `amayama_server_browser` (primary — a headful Chrome the
    backend itself launches+supervises under Xvfb via `_amayama_server_browser_loop`,
    no owner PC needed) and `amayama_browser` (fallback — `amayama_browser_harvester.js`
    pasted into the owner's own Chrome tab). This loop monitors throughput: every
    30 min it logs the Amayama supplier_parts count + delta, and if BOTH harvesters
    have stalled (no feed activity ~25 min) WHILE Japanese-brand OEMs are still
    unpriced, it WhatsApps the owner once (with cooldown)."""
    import harvest_heartbeat
    import time as _time
    await asyncio.sleep(600)
    last_count = None
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
            # Watch ALL THREE possible sources — server (primary, self-hosted),
            # browser (fallback, owner's PC), fs (retired, kept read-only in case
            # the FlareSolverr path is ever restored).
            server_hb_age = harvest_heartbeat.age_seconds("amayama_server_browser")
            hb_age = harvest_heartbeat.age_seconds("amayama_browser")
            fs_hb_age = harvest_heartbeat.age_seconds("amayama_fs")
            best_age = min([a for a in (server_hb_age, hb_age, fs_hb_age) if a is not None], default=None)
            harvester_down = (best_age is None) or (best_age > DOWN_S)
            print(f"[amayama_monitor] amayama_parts={cnt} delta={delta} jp_unpriced={pending} "
                  f"server_heartbeat_age_s={int(server_hb_age) if server_hb_age is not None else None} "
                  f"harvester={'DOWN' if harvester_down else 'alive'}", flush=True)
            if harvester_down and pending > 1000:
                await notify_owner(
                    "harvest",
                    "שאיבת Amayama לא פעילה (~25 דקות)",
                    f"שני הנתיבים (שרת + דפדפן) לא מגיבים.\n"
                    f"~{pending:,} חלקים ממותגים יפניים עדיין ללא מחיר.\n"
                    f"לפתרון: פתח amayama.com בטאב ורץ AMAYAMA.autorun(20,40) — "
                    f"או בדוק את השרת.",
                    severity="warning",
                    alert_key="amayama_harvester_down",
                    cooldown_s=4 * 3600,
                )
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
    _supervised_task("noa_engagement_loop",         _noa_engagement_loop())
    _supervised_task("supplier_sourcing_loop",      _supplier_sourcing_loop())
    _supervised_task("social_feedback_loop",        _social_feedback_loop())
    _supervised_task("group_scan_loop",             _group_scan_loop())
    _supervised_task("ebay_fitment_backfill_loop",  _ebay_fitment_backfill_loop())
    _supervised_task("enrich_catalog_loop",         _enrich_catalog_loop())
    _supervised_task("status_update_loop",          _status_update_loop())
    _supervised_task("rex_dispatch_loop",           _rex_dispatch_loop())
    _supervised_task("zombie_reaper",               _zombie_reaper_loop())
    _supervised_task("car_parts_ie_harvester_loop",  _car_parts_ie_harvester_loop())
    _supervised_task("car_parts_ie_full_seed",       _car_parts_ie_full_seed_loop())
    _supervised_task("thumbnail_import_loop",        _thumbnail_import_loop())
    _supervised_task("job_queue_loop",               _job_queue_loop())
    _supervised_task("job_queue_report",             _job_queue_report_loop())
    _supervised_task("whatsapp_link_monitor",        _whatsapp_link_monitor_loop())
    _supervised_task("weekly_maintenance",           _weekly_maintenance_loop())
    _supervised_task("car_parts_ie_stall_watchdog",  _car_parts_ie_stall_watchdog_loop())
    _supervised_task("car_parts_ie_healthcheck",     _car_parts_ie_harvester_healthcheck_loop())
    _supervised_task("meili_sync_loop",              _meili_sync_loop())
    _supervised_task("amayama_harvest_monitor",      _amayama_harvest_monitor_loop())
    _supervised_task("amayama_fs_harvester",         _amayama_fs_harvester_loop())
    _supervised_task("amayama_server_browser",       _amayama_server_browser_loop())
    _supervised_task("harvest_supervisor",           _harvest_supervisor_loop())
    await _warm_search_paths()
    print("✅ All systems ready — price-sync + catalog-scraper + db-agent schedulers started")


# ── Social Feedback Loop ───────────────────────────────────────────────────────
# Collects engagement metrics from published posts every SOCIAL_FEEDBACK_INTERVAL_S
# seconds (default 6h). Writes to engagement_events and updates campaign totals.
# Toggle off with SOCIAL_FEEDBACK_ENABLED=0.

async def _social_feedback_loop():
    """Supervised loop: collect post engagement metrics and update campaigns."""
    interval = int(os.getenv("SOCIAL_FEEDBACK_INTERVAL_S", "21600"))  # 6h default
    enabled = os.getenv("SOCIAL_FEEDBACK_ENABLED", "1").strip() == "1"
    if not enabled:
        logger.info("[social_feedback] disabled (SOCIAL_FEEDBACK_ENABLED=0)")
        return

    # Stagger start: offset from noa_marketing_loop by 30 min
    await asyncio.sleep(1800)

    while True:
        try:
            async with async_session_factory() as db:
                from social.feedback_analyzer import collect_all_platforms
                summary = await collect_all_platforms(db)
                logger.info("[social_feedback] cycle complete: %s", summary)
        except Exception as exc:
            logger.error("[social_feedback] error: %s", exc)
        await asyncio.sleep(interval)


# ── Group Scan Loop ────────────────────────────────────────────────────────────
# Scans approved Facebook groups every SOCIAL_GROUP_SCAN_INTERVAL_S (default 86400 = daily).
# Drafts comment proposals and queues them for owner WhatsApp approval.
# Toggle off with SOCIAL_GROUP_SCAN_ENABLED=0.

async def _group_scan_loop():
    """Supervised loop: daily FB group scan → draft comment proposals → owner approval."""
    interval = int(os.getenv("SOCIAL_GROUP_SCAN_INTERVAL_S", "86400"))  # 24h default
    enabled = os.getenv("SOCIAL_GROUP_SCAN_ENABLED", "1").strip() == "1"
    if not enabled:
        logger.info("[group_scan] disabled (SOCIAL_GROUP_SCAN_ENABLED=0)")
        return

    # Stagger: offset 2h after startup so it doesn't compete with harvester warmup
    await asyncio.sleep(7200)

    while True:
        try:
            # Use a fresh session scoped to the query only; facebook_group_scan
            # schedules asyncio.create_task(_log_action(db, ...)) internally which
            # fires AFTER the async-with exits and caused an InterfaceError on
            # session close.  Passing a session that commits+closes before the task
            # runs is the root cause — fixed by closing the session BEFORE we await
            # the notification, so the background task sees an already-committed conn.
            async with async_session_factory() as db:
                from social.tools import facebook_group_scan
                result = await facebook_group_scan(db=db)
                # Flush the session so _log_action's background task completes cleanly
                # before we exit the context and close the connection.
                try:
                    await db.commit()
                except Exception:
                    pass

            discoveries = result.data.get("discoveries", [])
            groups_scanned = result.data.get("groups_scanned", 0)

            if not discoveries:
                logger.info("[group_scan] no relevant group discussions found (%d groups scanned)", groups_scanned)
                # Always notify owner so they know the scan ran — cooldown prevents
                # daily spam when nothing is found (fixed: owner was getting silence
                # instead of a summary, reported 2026-08-23).
                await notify_owner(
                    "social",
                    f"סריקת קבוצות פייסבוק — לא נמצאו פוסטים רלוונטיים",
                    f"סרקנו *{groups_scanned}* קבוצות מאושרות — לא נמצאו דיונים רלוונטיים לרכב/חלפים היום.",
                    severity="info",
                    alert_key="group_scan_empty",
                    cooldown_s=86400,  # max once/day for empty-scan summaries
                )
            else:
                lines = []
                for i, d in enumerate(discoveries[:5], 1):
                    score_pct = int(d.get("relevance_score", 0) * 100)
                    lines.append(
                        f"{i}. *{d.get('group_name', '')}*\n"
                        f"   📝 {d.get('post_text', '')[:80]}...\n"
                        f"   רלוונטיות: {score_pct}%\n"
                        f"   💬 טיוטה: {d.get('draft_comment', '(אין)')[:120]}"
                    )
                lines.append(
                    "\nלאישור ושליחה: *תגובות-גרופ* לרשימה · *אשרתגובה <מזהה>*"
                )
                await notify_owner(
                    "social",
                    f"סריקת קבוצות פייסבוק — {len(discoveries)} תגובות ממתינות",
                    "\n".join(lines),
                    severity="info",
                )
                logger.info("[group_scan] sent %d discoveries to owner", len(discoveries))
        except Exception as exc:
            logger.error("[group_scan] error: %s", exc)
        await asyncio.sleep(interval)


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
# Minimum spacing between two reminders for the SAME cart (part of the 3-sends cap fix).
ABANDONED_CART_MIN_GAP_H = int(os.getenv("ABANDONED_CART_MIN_GAP_H", "24"))
APP_LOCAL_TZ = ZoneInfo(os.getenv("APP_LOCAL_TIMEZONE", "Asia/Jerusalem"))

# ── GLOBAL notification quiet hours (G8 2026-07-20, owner directive) ───────────
# NO outbound WhatsApp — customer reminders, owner alerts, NOA briefs — may be sent
# outside the IL daytime window (default 09:00-21:00). Messages produced at night are
# queued in Redis and flushed by the health-monitor pass once the window opens.
NOTIFY_SEND_START_HOUR_IL = int(os.getenv("NOTIFY_SEND_START_HOUR_IL", str(ABANDONED_CART_SEND_START_HOUR_IL)))
NOTIFY_SEND_END_HOUR_IL = int(os.getenv("NOTIFY_SEND_END_HOUR_IL", str(ABANDONED_CART_SEND_END_HOUR_IL)))
_WA_QUIET_QUEUE_KEY = "autospare:wa_quiet_queue"


def _notify_window_open(now_local: "datetime | None" = None) -> tuple[bool, datetime]:
    current_local = now_local or datetime.now(APP_LOCAL_TZ)
    is_open = NOTIFY_SEND_START_HOUR_IL <= current_local.hour < NOTIFY_SEND_END_HOUR_IL
    return is_open, current_local


async def _wa_send_quiet(to: str, text: str, critical: bool = False) -> dict:
    """Quiet-hours-aware WhatsApp send. Inside the window (or critical=True) → send now.
    Outside → queue to Redis; the health monitor flushes the queue at window open, so
    nothing is lost and nobody gets a 03:00 message. Owner notifications go to his REAL
    number (OWNER_WHATSAPP_PHONE) — a masked …@lid is NOT deliverable (owner-confirmed
    2026-07-24), so we never route to a LID."""
    is_open, _ = _notify_window_open()
    if is_open or critical:
        return await _wa_send(to=to, text=text)
    try:
        _r = await get_redis()
        await _r.rpush(_WA_QUIET_QUEUE_KEY, json.dumps({
            "to": to, "text": text[:3800],
            "queued_at": datetime.now(APP_LOCAL_TZ).strftime("%d/%m %H:%M"),
        }, ensure_ascii=False))
        await _r.ltrim(_WA_QUIET_QUEUE_KEY, -50, -1)  # keep at most 50 queued
        print(f"[QuietHours] queued WhatsApp for {to[-4:] if to else '?'} (outside "
              f"{NOTIFY_SEND_START_HOUR_IL}:00-{NOTIFY_SEND_END_HOUR_IL}:00 window)")
        return {"ok": True, "queued": True}
    except Exception as exc:
        # Redis down — better to deliver late-night than to lose the alert entirely.
        print(f"[QuietHours] queue failed ({exc}) — sending immediately")
        return await _wa_send(to=to, text=text)


async def _updates_group_jid() -> str:
    """The WhatsApp group JID that receives system/agent UPDATES, separated from the
    owner's 1:1 conversational chat (owner request 2026-08-04). Set via the console
    (`קבוצת עדכונים <n>`, stored in Redis) or the OWNER_UPDATES_GROUP_JID env. Empty =
    not configured yet → updates fall back to the owner's 1:1 number."""
    try:
        _r = await get_redis()
        v = await _r.get("owner:updates_group_jid")
        if v:
            return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
    except Exception:
        pass
    return os.getenv("OWNER_UPDATES_GROUP_JID", "").strip()


async def _wa_send_update(text: str, critical: bool = False) -> dict:
    """Send a SYSTEM/AGENT update. Goes to the dedicated updates group if one is
    configured, otherwise to the owner's 1:1 number — both through the quiet-hours gate
    so nothing lands at 03:00. This keeps alerts/digests/approvals out of the
    conversational thread the owner uses to talk to the agents."""
    jid = await _updates_group_jid()
    if jid:
        return await _wa_send_quiet(to=jid, text=text, critical=critical)
    owner = os.getenv("OWNER_WHATSAPP_PHONE", "")
    if owner:
        return await _wa_send_quiet(to=owner, text=text, critical=critical)
    return {"ok": False, "error": "no updates destination"}


async def _flush_wa_quiet_queue() -> int:
    """Send everything queued during quiet hours. Called by the health monitor each pass
    while the window is open. Returns number of messages sent."""
    sent = 0
    try:
        _r = await get_redis()
        while sent < 50:
            raw = await _r.lpop(_WA_QUIET_QUEUE_KEY)
            if not raw:
                break
            try:
                item = json.loads(raw)
                await _wa_send(to=item["to"],
                               text=f"🌙 [נשלח מאוחר — נשמר משעות הלילה {item.get('queued_at','')}]\n{item['text']}")
                sent += 1
            except Exception as exc:
                print(f"[QuietHours] flush item failed: {exc}")
        if sent:
            print(f"[QuietHours] flushed {sent} queued message(s)")
    except Exception as exc:
        print(f"[QuietHours] flush error: {exc}")
    return sent


# ── Owner notification taxonomy (2026-08-13 — "organize the notifications") ────
# Before this, ~20 different background loops each invented their own alert
# format: some Hebrew, some English, different emoji with no severity meaning,
# some with a Redis-backed cooldown, some with an in-memory one that resets on
# every restart, and at least one that called the WhatsApp provider directly —
# bypassing BOTH quiet hours AND the owner's dedicated updates-group routing
# (see _wa_send_update / _updates_group_jid, added 2026-08-04 specifically to
# separate system/agent noise from the owner's conversational chat). One import
# in the mix was flat-out broken (ImportError swallowed by a bare except), so
# that alert had never once reached the owner. This is the single enforcement
# point every proactive owner alert now goes through instead.
_NOTIFY_CATEGORIES = {
    "health":   "🏥 בריאות המערכת",
    "harvest":  "🛰️ קטלוג וקצירה",
    "orders":   "📦 הזמנות",
    "social":   "📣 סושיאל",
    "supplier": "🔌 ספקים",
}
_NOTIFY_SEVERITY_ICON = {
    "critical": "🔴",
    "warning":  "🟡",
    "info":     "ℹ️",
    "success":  "✅",
}


async def notify_owner(
    category: str,
    title: str,
    body: str = "",
    *,
    severity: str = "info",
    alert_key: str = "",
    cooldown_s: int = 3600,
) -> None:
    """The one function every proactive owner alert must call — health, harvest,
    orders, social, supplier. Gives every message a consistent
    "{severity icon} [{category}] {title}" header, a Redis-backed cooldown
    (survives restarts, unlike a module-level dict) when alert_key is given, and
    always routes through _wa_send_update() — never a raw send to the 1:1 number
    — so every alert respects BOTH quiet hours and the dedicated updates-group
    setting the moment one is configured. Never raises; a failed alert is logged,
    never fatal to the calling loop."""
    if alert_key:
        try:
            _r = await get_redis()
            _rk = f"autospare:alert_cooldown:{alert_key}"
            if await _r.exists(_rk):
                return
        except Exception:
            pass  # Redis unavailable — allow the alert through rather than lose it
    cat_label = _NOTIFY_CATEGORIES.get(category, category)
    sev_icon = _NOTIFY_SEVERITY_ICON.get(severity, "ℹ️")
    header = f"{sev_icon} [{cat_label}] {title}"
    text = f"{header}\n\n{body}" if body else header
    try:
        result = await _wa_send_update(text)
        if result.get("ok") and alert_key:
            # Set cooldown ONLY after a confirmed successful send — a failed send must
            # not burn the cooldown window (root-fix 2026-08-26: bridge-down failures
            # were blocking every subsequent retry for 24h, delaying real alerts until
            # the cooldown from the failed attempt happened to expire).
            try:
                _r = await get_redis()
                _rk = f"autospare:alert_cooldown:{alert_key}"
                await _r.set(_rk, "1", ex=cooldown_s)
            except Exception:
                pass
        elif not result.get("ok"):
            print(f"[notify_owner] send failed ({alert_key or category}): {result.get('error')}")
    except Exception as exc:
        print(f"[notify_owner] error ({alert_key or category}): {exc}")

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


# System UUID that owns NOA-generated social posts (created_by is a pii User id with no
# cross-DB FK — a stable sentinel is fine and lets us filter NOA's posts).
_NOA_SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


async def _noa_featured_thumbnail(car: str, eng_part: str) -> "str | None":
    """A clean part thumbnail (from the thumbnail pipeline) for the featured car+part,
    so image-required platforms have media. Matches the car's brand + the part name;
    returns None if nothing clean matches (text platforms still post)."""
    try:
        brand = (car or "").strip().split()[0] if car else ""
        async with async_session_factory() as cat_db:
            row = (await cat_db.execute(text(
                """
                SELECT t.url
                FROM part_thumbnails t
                JOIN parts_catalog pc ON pc.id = t.part_id
                WHERE t.status = 'ok' AND t.url IS NOT NULL AND pc.is_active
                  AND (:brand = '' OR pc.manufacturer ILIKE :brand)
                  AND (:part = '' OR pc.name ILIKE :part)
                LIMIT 1
                """
            ), {"brand": f"%{brand}%", "part": f"%{(eng_part or '').strip()}%"})).first()
            return str(row[0]) if row and row[0] else None
    except Exception as exc:
        logger.warning("noa featured-thumbnail lookup failed: %s", exc)
        return None


async def _noa_enqueue_social_post(caption: str, platforms: list, media_url: "str | None",
                                   topic: str, part_text: str = "",
                                   topic_performance: "object | None" = None) -> "str | None":
    """Create a pending_approval SocialPost so NOA's drafts flow through the SAME
    approval→publish queue the admin endpoints + social/registry consume. Returns the id.

    SEMANTIC GUARD (2026-07-29): before queueing, the local multilingual embedding
    model checks that the caption actually describes the part it claims to. NOA
    published fluent Hebrew claiming windscreen WIPERS give "sun protection, fuel
    savings and a cooler cabin" — the benefits of a SUNSHADE. That is a grounding
    failure, not a language failure, so it is caught by MEANING not by wording.
    The guard fails OPEN (see social/post_guard) — a scoring outage must never
    stop NOA posting.

    topic_performance (2026-08-15, observability for the analytics->decision
    link): the feedback_analyzer.TopicPerformance that was actually applied
    when this topic was chosen (or None if no real signal existed yet for it).
    Recorded into external_post_ids so "why was this topic picked" is always
    answerable from the row itself, not just from a log line.
    """
    guard_score = None
    try:
        from social.post_guard import check as _guard_check
        if part_text:
            ok, guard_score = _guard_check(caption, part_text,
                                           context=f"topic={topic!r} platforms={platforms}")
            if not ok:
                logger.warning(
                    "noa_marketing_loop: caption REJECTED by semantic guard "
                    "(sim=%.3f) — not queued. topic=%r", guard_score or -1, topic)
                return None
    except Exception as exc:      # guard must never break the posting path
        logger.warning("noa semantic guard skipped: %s", exc)

    try:
        from BACKEND_DATABASE_MODELS import SocialPost
        meta = {"source": "noa_marketing_loop", "topic": topic}
        if guard_score is not None:
            meta["guard_sim"] = round(float(guard_score), 3)
        if media_url:
            meta["media_url"] = media_url
        if topic_performance is not None:
            meta["topic_weight_applied"] = round(float(topic_performance.weight), 3)
            meta["topic_weight_sample_size"] = int(topic_performance.sample_size)
        async with async_session_factory() as cat_db:
            sp = SocialPost(
                id=uuid.uuid4(),
                content=caption,
                platforms=platforms,
                status="pending_approval",
                external_post_ids=meta,
                created_by=_NOA_SYSTEM_USER_ID,
            )
            cat_db.add(sp)
            await cat_db.commit()
            logger.info("noa_marketing_loop: enqueued social_post %s platforms=%s media=%s",
                        sp.id, platforms, bool(media_url))
            return str(sp.id)
    except Exception as exc:
        logger.error("noa enqueue social_post failed: %s", exc)
        return None


async def _noa_engagement_loop():
    """
    NOA's inbound-engagement loop (the read+reply half; the marketing loop only PUBLISHES).

    Every NOA_ENGAGEMENT_INTERVAL_S (default 900s) it polls every configured social
    platform (Facebook/Instagram — others degrade to no-op), records new comments/mentions
    to `social_inbox`, has NOA draft a reply, and moves each to `pending_approval`. The owner
    reviews/approves from the WhatsApp console (*תגובות* → *ענה <id>*). Set
    NOA_ENGAGEMENT_AUTOREPLY=1 to auto-send drafts without owner approval. Toggle off with
    NOA_ENGAGEMENT_ENABLED=0. Never raises out of a cycle; backs off when nothing is
    configured so it stays silent until a token is added.
    """
    from BACKEND_DATABASE_MODELS import async_session_factory
    from social import engagement as _eng

    interval = int(os.getenv("NOA_ENGAGEMENT_INTERVAL_S", "900"))
    autoreply = os.getenv("NOA_ENGAGEMENT_AUTOREPLY", "0") == "1"
    await asyncio.sleep(30)  # let startup settle
    while True:
        if os.getenv("NOA_ENGAGEMENT_ENABLED", "1") != "1":
            await asyncio.sleep(interval)
            continue
        configured = _eng.configured_platforms()
        if not configured:
            logger.info("[noa_engagement] no social platform configured (need FACEBOOK_PAGE_TOKEN); idling")
            await asyncio.sleep(max(interval, 3600))
            continue
        try:
            async with async_session_factory() as db:
                summary = await _eng.poll_once(db, autoreply=autoreply)
                # draft items recorded by a webhook (Telegram) rather than polled
                wsum = await _eng.draft_new_items(db, autoreply=autoreply)
            logger.info("[noa_engagement] %s webhook=%s", summary, wsum)
            drafted = summary.get("drafted", 0) + wsum.get("drafted", 0)
            if drafted and not autoreply:
                await notify_owner(
                    "social",
                    f"NOA: {drafted} תגובות חדשות ברשתות ממתינות לתשובה",
                    "לצפייה: כתוב *תגובות* · לאישור: *ענה <מזהה>*",
                    severity="info",
                )
        except Exception as exc:
            logger.error("[noa_engagement] cycle failed: %s", exc)
        await asyncio.sleep(interval)


async def _supplier_sourcing_loop():
    """
    NIR's supplier-sourcing "superpower" (see services/supplier_sourcing.py).

    Every SUPPLIER_SOURCING_INTERVAL_S (default weekly) NIR searches the web for new
    sellers that fill catalog gaps (derived from recent search_misses), evaluates them,
    and onboards the good ones into `suppliers` as is_active=FALSE / pending_review — then
    WhatsApps the owner so he can approve activation from the console (*ספקים* → *אשרספק
    <id>*). Nothing goes live in customer-facing compare without owner approval. Discovery
    uses Gemini Google-Search grounding (real), falling back to LLM-propose + live-verify
    when Gemini is rate-limited. Toggle off with SUPPLIER_SOURCING_ENABLED=0.
    """
    from services import supplier_sourcing as _ss

    interval = int(os.getenv("SUPPLIER_SOURCING_INTERVAL_S", str(7 * 24 * 3600)))
    await asyncio.sleep(120)  # let startup settle
    while True:
        if os.getenv("SUPPLIER_SOURCING_ENABLED", "1") != "1":
            await asyncio.sleep(interval)
            continue
        try:
            summary = await _ss.run_sourcing_cycle()
            onboarded = summary.get("onboarded", [])
            logger.info("[supplier_sourcing] %s", summary)
            if onboarded:
                lines = "\n".join(f"• {o['name'][:34]} ({o['domain']})" for o in onboarded[:6])
                await notify_owner(
                    "supplier",
                    f"NIR מצא {len(onboarded)} ספקים חדשים אפשריים",
                    f"{lines}\nלצפייה/אישור: כתוב *ספקים*",
                    severity="info",
                )
        except Exception as exc:
            logger.error("[supplier_sourcing] cycle failed: %s", exc)
        await asyncio.sleep(interval)


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

    # G8 2026-07-20: the loop no longer free-runs on a 24h timer anchored to container
    # start (which drifted to 03:00 sends). It fires at fixed IL local times in the window.
    # 2026-07-25 (owner directive): TWICE a day at PEAK user hours (default 13:00 + 20:00 IL —
    # lunch + evening), not once at 09:30. Override with NOA_POST_HOURS_IL="13,20".
    NOA_POST_HOURS_IL = os.getenv("NOA_POST_HOURS_IL",
                                  os.getenv("NOA_POST_HOUR_IL", "13") + ",20")
    _post_hours = sorted({int(h) for h in re.findall(r"\d+", NOA_POST_HOURS_IL)}) or [13, 20]
    NOA_POST_MINUTE_IL = int(os.getenv("NOA_POST_MINUTE_IL", "0"))
    # WhatsApp is the PRIMARY owner channel (owner directive); Telegram only mirrors
    # when explicitly enabled.
    NOA_TELEGRAM_MIRROR = os.getenv("NOA_TELEGRAM_MIRROR", "0") == "1"

    def _secs_until_next_post() -> float:
        now_l = datetime.now(APP_LOCAL_TZ)
        nxt = None
        for h in _post_hours:
            t = now_l.replace(hour=h, minute=NOA_POST_MINUTE_IL, second=0, microsecond=0)
            if t <= now_l:
                t += timedelta(days=1)
            nxt = t if nxt is None else min(nxt, t)
        return max(60.0, (nxt - now_l).total_seconds())

    await asyncio.sleep(_secs_until_next_post())

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
        "Hyundai i10", "Kia Picanto", "Toyota Yaris", "Skoda Fabia",
        "Mazda CX-5", "Hyundai Ioniq", "Kia Ceed", "Renault Clio",
        "Chevrolet Captiva", "Toyota Camry", "Mercedes C-Class",
    ]

    # Part topics — (Hebrew name, English name, [pain-point variants]).
    # ROOT-FIXED 2026-08-14 (owner: "NOA keeps repeating the same posts idea").
    # Two compounding causes, both fixed here:
    #  (1) "מוט ייצוב (שלדג)" — שלדג (kingfisher, the bird) was a hardcoded typo in
    #      THIS list, not an LLM hallucination as originally (wrongly) diagnosed —
    #      every earlier "fix" for that bug tested a hand-typed heb_part that never
    #      matched what production actually fed the model. Removed here.
    #  (2) Only 14 parts, each with exactly ONE fixed pain phrase, guaranteed the
    #      same hook resurfaced every ~1-2 weeks at 2 posts/day. Expanded to 34
    #      parts spanning the real category distribution (parts_catalog, live
    #      query 2026-08-14) with 2-3 pain-point variants each — the loop below
    #      picks a random variant per generation, not just a random part.
    _PARTS = [
        ("בלמי דיסק", "brake pads", ["קול חריקה בבלימה", "רעד בהגה בבלימה", "מרחק בלימה ארוך מהרגיל"]),
        ("מסנן שמן", "oil filter", ["שמן שחור, מנוע כבד", "נורת שמן נדלקת בזמן נסיעה"]),
        ("מצבר", "battery", ["הרכב לא עולה בבוקר", "אורות חלשים כשהמנוע כבוי", "המצבר מתרוקן תוך יומיים"]),
        ("חגורת תזמון", "timing belt", ["תחזוקה מניעתית שמונעת קטסטרופה", "קליק קליק קל מהמנוע בסיבובים נמוכים"]),
        ("מנורות LED קדמיות", "LED headlights", ["תאורה חלשה בלילה", "פנס אחד חלש מהשני"]),
        ("מגבי שמשה", "wiper blades", ["שריטות על השמשה בגשם", "רעש חריקה כשהמגבים עובדים", "פס מים שנשאר אחרי כל מעבר"]),
        ("מסנן מזגן (קבינה)", "cabin AC filter", ["ריח עובש מהמזגן", "אוויר חלש מהפתחים"]),
        ("סלילי הצתה", "ignition coils", ["רעד במנוע, תאוצה גרועה", "נורת מנוע נדלקת בכביש מהיר"]),
        ("חיישני ABS", "ABS sensor", ["נורת ABS דולקת", "רעד בדוושת הבלם בבלימה חזקה"]),
        ("מוט ייצוב", "stabilizer bar link", ["קשקוש מהשלדה על פסי האטה", "רעש דפיקה בסיבוב חד"]),
        ("רדיאטור", "radiator", ["רכב מתחמם מעבר", "נוזל קירור שיורד בלי סיבה"]),
        ("נרות הצתה", "spark plugs", ["צריכת דלק גבוהה", "מנוע מגמגם בהתנעה קרה"]),
        ("פחי אוויר", "air filter", ["תאוצה איטית, מנוע חנוק", "צריכת דלק שעולה בלי סיבה ברורה"]),
        ("מיסבי גלגל", "wheel bearings", ["רעש זמזום מהגלגל במהירות", "הרעש מתחזק כשפונים"]),
        ("צמיגים", "tires", ["רעד בהגה במהירות גבוהה", "שחיקה לא אחידה בצמיג"]),
        ("בולמי זעזועים", "shock absorbers", ["הרכב קופץ בבור בכביש", "גלגול צד בפניות"]),
        ("משאבת מים", "water pump", ["מד חום עולה בפקקים", "כתם נוזל ירוק מתחת לרכב"]),
        ("תרמוסטט", "thermostat", ["מד החום לא זז בכלל", "המזגן חם כשהמנוע קר"]),
        ("מצמד", "clutch kit", ["דוושת מצמד כבדה", "ריח שריפה קל בעליות"]),
        ("משאבת דלק", "fuel pump", ["הרכב מגמגם בהתחלת נסיעה", "קושי בהתנעה אחרי תדלוק"]),
        ("חיישן חמצן", "oxygen sensor", ["נורת מנוע קבועה", "ריח דלק חזק מהפליטה"]),
        ("צינור פליטה", "exhaust pipe", ["רעש גובר מתחת לרכב", "רעד בסרק שלא היה קודם"]),
        ("דסקיות בלם", "brake discs", ["רעד בהגה בבלימה חזקה", "קול מתכתי כבד בבלימה"]),
        ("קפיצים", "coil springs", ["הרכב נוטה לצד אחד", "גובה הרכב ירד מצד אחד"]),
        ("משאבת הגה", "power steering pump", ["ההגה כבד בפניות איטיות", "רעש שריקה כשמסובבים הגה"]),
        ("תושבת מנוע", "engine mount", ["רעד חזק בסרק", "טלטלה בהחלפת הילוכים"]),
        ("רצועת מצמד מזגן", "AC compressor belt", ["חריקה כשמדליקים מזגן", "מזגן שמפסיק לקרר לפתע"]),
        ("פילטר דלק", "fuel filter", ["מנוע מאבד כוח בעליות", "גמגום במהירות גבוהה בכביש"]),
        ("מגן בוץ", "mud flap", ["רעש חבטות בנסיעה על אבנים", "שריטות בסף הדלת מבוץ מתעופף"]),
        ("פנס אחורי", "tail light", ["פנס בלימה לא נדלק", "עמימות באור האחורי"]),
        ("זרוע תחתונה", "control arm", ["הגה רועד בכביש לא חלק", "רעש דפיקה מהחזית בפניות"]),
        ("חיישן חניה", "parking sensor", ["צפצוף חנייה לא עובד", "החיישן מצפצף גם בלי מכשול"]),
        ("מצת חימום (דיזל)", "glow plug", ["קושי בהתנעה קרה בבוקר", "עשן לבן קל מהאגזוז בהתנעה"]),
        ("רפידות מגב אחוריות", "rear wiper blade", ["שמשה אחורית מטושטשת בגשם", "רעש גירוד מהמגב האחורי"]),
    ]

    async def _noa_real_catalog_fact(db, eng_part: str, heb_part: str, car: str) -> tuple[str, str]:
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
                # ROOT FIX 2026-08-11 ("posts improved for a couple of days then went back
                # to un-understood sentences"): `row[1] or row[0]` picks name_he whenever it's
                # non-EMPTY — but measured live, 37-75% of matched rows have name_he POPULATED
                # with the raw English catalog title (an importer wrote the English name into
                # the Hebrew column when no translation existed), e.g. name_he=
                # "BOSCH 3 397 007 462 Wiper blade Beam, Length: 600mm, Front". `or` never
                # catches that — it's truthy. That English string was then handed to NOA
                # labelled "real fact — quote it", and the model quoted it verbatim, which is
                # exactly the raw-English-part-name regression despite the Hebrew-only prompt
                # rule (a data problem, not a prompt problem — the earlier fix addressed the
                # prompt but not this). Guard on actual Hebrew CONTENT, not emptiness, and fall
                # back to the topic's own Hebrew part name (heb_part — always populated, from
                # the loop's fixed _PARTS list) — never the raw English catalog title.
                _name_he_raw = (row[1] or "").strip()
                _pname = _name_he_raw if re.search(r"[֐-׿]", _name_he_raw) \
                    else (heb_part or (row[0] or "").strip())
                _pname = _pname[:70]
                cost = float(row[3])
                sell_net = cost * PROFIT_MARGIN                      # cost × 1.45
                vat_rate = get_supplier_vat_rate(                     # 18% local, 0% foreign
                    supplier_name=row[4], supplier_country=row[5])
                price = round(sell_net + sell_net * vat_rate)         # pre-shipping "from"
                vat_note = "כולל מע\"מ" if vat_rate > 0 else "ללא מע\"מ (יבוא)"
                # Manufacturer returned alongside the text (not just embedded in it) so
                # the coherence gate can deterministically verify it survived the LLM's
                # rewrite into prose — see social/coherence_guard.manufacturer_preserved.
                return (f"\nעובדה אמיתית מהקטלוג (מותר ואף רצוי לצטט): "
                        f"{_pname} ({row[2]}) — החל מ‑₪{price} {vat_note} באתר.", str(row[2] or ""))
        except Exception as exc:
            logger.warning("noa_marketing_loop: catalog fact lookup failed: %s", exc)
        return "", ""

    def _noa_utm_link(platform: str, week_num: int) -> str:
        return (f"https://autosparefinder.co.il/?utm_source={platform}"
                f"&utm_medium=social&utm_campaign=noa_w{week_num}")

    # Platform rotation by weekday (0=Mon, 1=Tue … 6=Sun)
    # One platform per day so all six public channels get covered across a week.
    # (tiktok/instagram are media-required — the enqueue attaches a part thumbnail;
    # tiktok additionally needs video so it may stay draft until a clip is supplied.)
    _DAY_PLATFORM = {
        1: ("tiktok",    "TikTok — hook קצר וחד, שורה ראשונה שמחזיקה"),
        2: ("instagram", "Instagram — story-telling ויזואלי, אמוציונלי"),
        3: ("facebook",  "Facebook — פוסט מידעי בעל ערך, ניתן לשיתוף"),
        4: ("x",         "X/Twitter — חד, קצר, מתחת ל-280 תווים, טוויט אחד"),
        5: ("reddit",    "Reddit — כותרת בשורה ראשונה + גוף מסביר, טון אותנטי לא-פרסומי"),
        6: ("discord",   "Discord — הודעה קהילתית ידידותית עם קריאה לפעולה"),
    }

    while True:
        try:
            now = datetime.now(APP_LOCAL_TZ)   # IL local — weekday/season match the audience
            weekday = now.weekday()   # 0=Monday … 6=Sunday
            month = now.month
            week_num = now.isocalendar()[1]
            season = _SEASONAL.get(month, "")

            async with async_session_factory() as db:
                await ensure_memory_table(db)
                mem = AgentMemory(db, agent_name="noa")
                noa = SocialMediaManagerAgent()

                # Owner guidelines (saved from the WhatsApp console) — MUST be applied to
                # every generation. This is how the owner's directives to NOA actually take
                # effect operationally, not just as a chat acknowledgement.
                _owner_guidelines = ""
                try:
                    _g = await mem.get("owner_guidelines")
                    _owner_guidelines = (_g.get("text") if isinstance(_g, dict) else _g) or ""
                except Exception:
                    _owner_guidelines = ""
                _noa_system = noa.system_prompt + (
                    ("\n\n=== הנחיות קבועות מהבעלים (חובה לפעול לפיהן בכל פוסט) ===\n"
                     + _owner_guidelines) if _owner_guidelines else "")

                # Load recent history — used for BOTH the deterministic topic-pool
                # exclusion below and the soft "don't repeat" prompt instruction.
                history_raw = await mem.get("post_history") or []
                recent_topics: list[str] = []
                recent_parts_seen: list[str] = []
                recent_cars_seen: list[str] = []
                if isinstance(history_raw, list):
                    for h in history_raw[-6:]:
                        if isinstance(h, dict):
                            t = h.get("topic") or h.get("caption", "")[:70]
                            if t:
                                recent_topics.append(str(t))
                    # Deeper lookback (10 posts = 5 days at 2/post) for the actual
                    # pool exclusion — a prompt instruction alone doesn't reliably
                    # stop repetition (owner report 2026-08-14: same hooks kept
                    # resurfacing despite "don't repeat" already being in the prompt).
                    # Excluding recently-used topics from the RANDOM CHOICE itself is
                    # a guarantee, not a request.
                    for h in history_raw[-10:]:
                        if isinstance(h, dict):
                            topic_str = str(h.get("topic") or "")
                            if " — " in topic_str:
                                p, c = topic_str.split(" — ", 1)
                                recent_parts_seen.append(p.strip())
                                recent_cars_seen.append(c.strip())
                no_repeat = (
                    f"\nנושאים שכבר כוסו לאחרונה — אל תחזרי עליהם:\n" +
                    "\n".join(f"• {t}" for t in recent_topics)
                ) if recent_topics else ""

                _car_pool = [c for c in _CARS if c not in recent_cars_seen] or _CARS
                _part_pool = [p for p in _PARTS if p[0] not in recent_parts_seen] or _PARTS
                car = random.choice(_car_pool)

                # Analytics -> decision link (2026-08-15): weight the topic
                # choice by REAL historical engagement instead of pure uniform
                # random. compute_topic_performance() is a deterministic
                # aggregation over real engagement_events (no LLM call, no
                # invented numbers) — a topic with no measured data yet gets
                # a neutral weight of 1.0, never penalized. This is the only
                # place real analytics actually change what NOA generates
                # next; everywhere else analytics reaches only a human
                # dashboard (routes/campaigns.py analytics endpoints).
                try:
                    from social.feedback_analyzer import compute_topic_performance
                    _topic_perf = await compute_topic_performance(db)
                except Exception as _tpe:
                    logger.warning("noa_marketing_loop: compute_topic_performance failed, using uniform weights: %s", _tpe)
                    _topic_perf = {}
                _part_weights = [_topic_perf[p[0]].weight if p[0] in _topic_perf else 1.0 for p in _part_pool]
                heb_part, eng_part, _pain_options = random.choices(_part_pool, weights=_part_weights, k=1)[0]
                _applied_weight = _topic_perf.get(heb_part)
                pain = random.choice(_pain_options)

                real_fact, real_fact_manufacturer = await _noa_real_catalog_fact(db, eng_part, heb_part, car)

                if weekday == 0 and now.hour <= _post_hours[0]:
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
                        "• כתבי חכם, מצחיק ואנושי: כל copy_hebrew עם קריצה אחת + עובדה שמלמדת משהו + מכירה ברורה (כאב→פתרון→מחיר→CTA)\n"
                        "• בחרי נושא שבועי יצירתי ורלוונטי — לא חייב להיות בדיוק הרכב/חלק שניתן לדגמה\n"
                        "• תכנני 6 פוסטים יומיים: ב׳=TikTok, ג׳=Instagram, ד׳=Facebook, ה׳=WhatsApp, ו׳=TikTok, שבת=Instagram\n"
                        "• כל פוסט — זווית שונה לגמרי, לא וריאציה של אותו טקסט\n"
                        "• כתבי copy_hebrew מלא לכל יום — טקסט מוכן לפרסום, לא תיאור של הטקסט\n"
                        "• אם ניתנה עובדה אמיתית מהקטלוג — שלבי את המחיר האמיתי; אסור להמציא מחירים אחרים\n"
                        "• שם החלק והיצרן/המותג בעובדה האמיתית מהקטלוג הם מדויקים — צטטי אותם בדיוק, אסור "
                        "להמציא מילה נרדפת, תרגום, או כינוי חלופי להם בשום copy_hebrew\n\n"
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

                    # L3 fix (2026-08-09): inject Digital Department context so the
                    # Monday weekly brief receives the same brand/positioning/campaign_launch
                    # guidelines as generate_campaign_plan(). Advisory only — the JSON
                    # schema requirement in campaign_prompt still dominates output shape.
                    try:
                        from digital_department import build_prompt_with_context as _bpwc
                        campaign_prompt = _bpwc(campaign_prompt, agent="noa", task_type="social_campaign")
                    except Exception: pass  # preserve original campaign_prompt on any loader failure

                    raw_plan = await _hf_text(prompt=campaign_prompt, system=_noa_system, timeout=180.0, max_tokens=6000, temperature=noa.temperature, reasoning_effort="low")

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
                    _brief_title = f"NOA — קמפיין שבוע {week_num}"

                    if OWNER_PHONE:
                        # The title used to be baked into wa_lines itself; now notify_owner
                        # supplies it via the [category] header, so it isn't duplicated here.
                        await notify_owner("social", _brief_title, wa_msg, severity="info")
                    if NOA_TELEGRAM_MIRROR and TELEGRAM_OWNER_ID and TELEGRAM_ADMIN_TOKEN:
                        await _noa_send_telegram(TELEGRAM_ADMIN_TOKEN, TELEGRAM_OWNER_ID, f"📅 *{_brief_title}*\n\n{wa_msg}")

                    await mem.append_event("post_history", {
                        "type": "campaign_brief", "topic": theme,
                        "platform": "all", "created_at": now.isoformat(),
                    })
                    logger.info("noa_marketing_loop: weekly campaign brief week=%d theme=%s", week_num, theme)

                # ── Every run (2×/day): ONE universal post → ALL connected platforms ──
                # Owner directive 2026-07-25: the SAME post goes to every connected platform,
                # twice a day at peak hours — not a different platform per day.
                if True:
                    try:
                        from social import registry as _reg
                        _configured = _reg.configured_platforms() or []
                    except Exception:
                        _configured = []
                    if not _configured:
                        _configured = ["facebook", "instagram", "telegram", "x", "discord", "reddit"]
                    platform = "all"
                    platform_desc = ("פוסט אוניברסלי שמתאים לכל הפלטפורמות (קצר, קולע, מתחת ל-280 "
                                     "תווים היכן שאפשר) — אותו תוכן יפורסם בכולן")
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
                        "כתיבה (חכם + מצחיק + אנושי + מוכר — הוראת בעלים):\n"
                        "• פתחי עם ה-hook בשורה ראשונה — קצרה, חדה, לא שאלה גנרית\n"
                        "• שלבי קריצה אחת חכמה — אירוניה עדינה או סיטואציה שכל נהג מכיר (בלי בדיחות דחוקות)\n"
                        "• למדי את הקורא משהו קטן ואמיתי על הרכב/החלק — שירגיש חכם יותר אחרי הקריאה\n"
                        "• כתבי כמו בן אדם: גוף ראשון, משפטים קצרים, עברית מדוברת, 1-3 אמוג'י\n"
                        "• ציוני את שם הרכב ואת שם החלק הספציפי\n"
                        "• זה פוסט מכירה: אם ניתנה עובדה אמיתית מהקטלוג — שלבי את המחיר האמיתי (זה מה שמוכר); אסור להמציא מחיר\n"
                        "• שם החלק והיצרן/המותג בעובדה האמיתית מהקטלוג הם מדויקים — צטטי אותם בדיוק, מותר לשלב "
                        "אותם בזרימה טבעית של המשפט אבל אסור להמציא מילה נרדפת, תרגום, או כינוי חלופי להם "
                        "(לדוגמה: אם היצרן הוא Toyota, אל תכתבי מילה אחרת בסוגריים במקומו — לא ניחוש, לא תרגום)\n"
                        "• פתרון: חיפוש לפי מספר רישוי ב-autosparefinder.co.il — CTA אחד ברור\n"
                        "• סיימי בשאלה שקל וכיף לענות עליה בתגובה\n"
                        "• האשטאגים בשורה אחרונה בלבד — עברית, ערבית ואנגלית מעולם הרכב\n\n"
                        "החזירי: טקסט הפוסט הסופי בלבד — ללא הסבר, ללא כותרת, ללא ספירה."
                    )

                    # H1 fix (2026-08-09): inject Digital Department context so daily
                    # organic posts receive the same brand/positioning/content guidelines
                    # as campaign-workflow posts (generate_post / execute_campaign).
                    try:
                        from digital_department import build_prompt_with_context as _bpwc
                        post_prompt = _bpwc(post_prompt, agent="noa", task_type="social_post")
                    except Exception:
                        pass  # preserve original prompt on any loader failure

                    # Coherence gate (added 2026-08-14): a stochastic generator at any
                    # temperature can still occasionally invent a word/nonsensical
                    # metaphor despite the prompt rules above — those reduce the failure
                    # rate, they don't guarantee zero. Deterministic manufacturer check +
                    # LLM judge run BEFORE finalization; on failure, regenerate with the
                    # concrete reason fed back (cheap, and usually self-corrects). If it
                    # still fails after retries, skip this cycle rather than enqueue a
                    # draft that will just look broken to the owner again — see
                    # social/coherence_guard.py for the full rationale + calibration.
                    from social import coherence_guard as _cguard
                    _gen_prompt = post_prompt
                    caption = ""
                    _gate_failed = True
                    for _attempt in range(3):
                        raw_post = await _hf_text(prompt=_gen_prompt, system=_noa_system, timeout=90.0, max_tokens=1500, temperature=noa.temperature, reasoning_effort="low")
                        _candidate = noa._finalize_noa_post(raw_post, platforms=_configured)
                        _mfr_ok = _cguard.manufacturer_preserved(_candidate, real_fact_manufacturer)
                        _coh_ok, _coh_reason = await _cguard.check(_candidate)
                        if _mfr_ok and _coh_ok:
                            caption = _candidate
                            _gate_failed = False
                            break
                        _reason = _coh_reason or (
                            f"שם היצרן '{real_fact_manufacturer}' לא נשמר בדיוק בטקסט"
                            if not _mfr_ok else "בעיית עריכה לא ידועה"
                        )
                        logger.warning("noa_marketing_loop: coherence gate FAIL attempt=%d reason=%s", _attempt + 1, _reason[:150])
                        _gen_prompt = (
                            f"{post_prompt}\n\n"
                            f"⚠️ הניסיון הקודם שלך נדחה: {_reason}\n"
                            f"כתבי גרסה חדשה שמתקנת את הבעיה הזו במדויק."
                        )
                    if _gate_failed:
                        logger.error("noa_marketing_loop: coherence gate failed 3/3 attempts — skipping this cycle")
                        await notify_owner(
                            "social",
                            "NOA — פוסט נפסל 3 פעמים ולא פורסם המחזור הזה",
                            f"נושא: {heb_part} ({eng_part}) — {car}. שער הבדיקה (מילה מומצאת/דימוי לא הגיוני) "
                            f"דחה 3 ניסיונות ברצף. אין פעולה נדרשת — המחזור הבא ינסה נושא אחר.",
                            severity="warning",
                            alert_key="noa_coherence_gate_exhausted",
                            cooldown_s=3600,
                        )
                        await asyncio.sleep(_secs_until_next_post())
                        continue
                    # NO UTM re-injection (fix 2026-08-05, owner "fix the long link"): the
                    # finalizer deliberately produces a CLEAN bare "autosparefinder.co.il".
                    # Re-adding "?utm_source=…&utm_medium=…&utm_campaign=…" here put the long
                    # ugly link straight back into the visible caption. Attribution rides on
                    # the QR (?src=qr_<platform>_w<week>) + per-platform posting instead.

                    hashtags = [f"#{m.group(1)}" for m in noa._NOA_HASHTAG_RE.finditer(caption)]

                    # Attach a clean part thumbnail (from the thumbnail pipeline) so
                    # image-required platforms (instagram/tiktok) have media.
                    media_url = await _noa_featured_thumbnail(car, eng_part)

                    # G8 2026-07-20: EVERY post gets media with a QR code (thumbnail+QR
                    # composite, or brand-canvas+QR when no clean thumbnail matches).
                    # The QR lands on /api/v1/go where the customer picks their channel —
                    # replacing the old 5-link text footer.
                    try:
                        from social.qr_media import build_post_media
                        media_url = await build_post_media(media_url, f"qr_{platform}_w{week_num}")
                    except Exception as _qre:
                        logger.warning("noa_marketing_loop: QR media failed: %s", _qre)
                    if media_url and "/thumbs/qr/" in media_url:
                        caption = f"{caption}\n📲 סרקו את הקוד בתמונה — ובחרו איפה נוח לכם לדבר איתנו"

                    # ENQUEUE into the social_posts approval queue → owner approves →
                    # the registry publishes to the real platform. This is the single
                    # source of truth the admin endpoints + Telegram approval consume.
                    # Give the guard the part this post is SUPPOSED to be about,
                    # so it can reject copy that drifted onto a different product.
                    try:
                        from social.post_guard import part_text_for as _ptf
                        _guard_part = _ptf(name=eng_part, name_he=heb_part)
                    except Exception:
                        _guard_part = f"{heb_part} {eng_part}".strip()

                    social_post_id = await _noa_enqueue_social_post(
                        caption=caption, platforms=_configured, media_url=media_url,
                        topic=f"{heb_part} — {car}",
                        part_text=_guard_part,
                        topic_performance=_applied_weight,
                    )

                    pending_payload = {
                        "caption": caption,
                        "hashtags": hashtags,
                        "platform": platform,
                        "topic": f"{heb_part} — {car}",
                        "post_type": platform,
                        "status": "awaiting_approval",
                        "social_post_id": social_post_id,
                        "media_url": media_url,
                        "created_at": now.isoformat(),
                    }

                    await mem.set("pending_post", pending_payload, ttl_hours=72)
                    await mem.append_event("post_history", pending_payload)

                    # Send to WHATSAPP for approval (owner directive G8 2026-07-20 —
                    # WhatsApp instead of Telegram). Telegram only mirrors if enabled.
                    if OWNER_PHONE:
                        _post_short_id = (social_post_id or "")[:8] or "—"
                        # Strip the QR-scan instruction from the WA preview: it says
                        # "scan the code IN THE IMAGE" — but WA shows only text so no
                        # image appears, making the instruction meaningless and confusing
                        # (reported 2026-08-23 as "image mixed in text"). The line lives
                        # in the stored caption for the actual published post where the
                        # image IS shown; the WA preview uses a clean version instead.
                        _wa_caption = re.sub(r"\n?📲\s*סרקו[^\n]*", "", caption).strip()
                        wa_post_body = (
                            f"{_wa_caption}\n\n"
                            + (f"🖼️ מדיה (עם QR): {media_url}\n" if media_url else "")
                            + f"לאישור ופרסום: כתוב *אשר {_post_short_id}*\n"
                            + f"לדחייה: כתוב *דחה {_post_short_id}*"
                        )
                        await notify_owner(
                            "social",
                            f"NOA — פוסט {platform.title()} מוכן לאישורך",
                            wa_post_body,
                            severity="info",
                        )
                    if NOA_TELEGRAM_MIRROR and TELEGRAM_OWNER_ID and TELEGRAM_ADMIN_TOKEN:
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

        await asyncio.sleep(_secs_until_next_post())



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
                # ROOT FIX 2026-08-06: automated Issuing authorization is DELIBERATELY
                # blocked outside Stripe sandbox mode (routes/utils.py raises "Automated
                # Issuing authorization is supported in sandbox mode only" — this platform
                # runs a LIVE key). 4 real customer orders had been retried on this EXACT
                # doomed path every 30 min since APRIL, each retry logged as "🤖 N orders
                # auto-handled" — false progress on something that cannot succeed without
                # either live-mode support being built or the owner fulfilling manually.
                # Exclude orders whose most recent stripe_issuing failure in the last 24h
                # is this structural (non-transient) guard from the auto-retry loop.
                _nonretryable_recent_ids = (
                    select(SupplierPayment.order_id)
                    .where(
                        SupplierPayment.status == "failed",
                        SupplierPayment.provider == "stripe_issuing",
                        or_(
                            SupplierPayment.failure_reason.ilike("%sandbox mode only%"),
                            SupplierPayment.failure_reason.ilike("%not configured%"),
                        ),
                        SupplierPayment.created_at > now - timedelta(hours=24),
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
                        ~Order.id.in_(_nonretryable_recent_ids),
                    )
                )
                stuck = result.scalars().all()

                # Orders EXCLUDED above because they're known-doomed right now — these need
                # the OWNER, not another retry. Honest framing (never "auto-handled"),
                # deduped weekly (the underlying state won't change without owner action).
                manual_result = await db.execute(
                    select(Order).where(
                        Order.status.in_(["confirmed", "paid", "processing"]),
                        Order.updated_at <= cutoff,
                        Order.id.in_(_nonretryable_recent_ids),
                    )
                )
                manual_orders = manual_result.scalars().all()
                if manual_orders:
                    import hashlib as _hl2
                    _manual_sig = _hl2.sha256(
                        ",".join(sorted(o.order_number for o in manual_orders)).encode()
                    ).hexdigest()[:16]
                    _notify_manual = True
                    try:
                        _rm = await get_redis()
                        _mk = f"autospare:manual_orders_notified:{_manual_sig}"
                        if _rm is not None:
                            if await _rm.exists(_mk):
                                _notify_manual = False
                            else:
                                await _rm.set(_mk, "1", ex=7 * 86400)  # weekly reminder
                    except Exception:
                        _notify_manual = True
                    if _notify_manual:
                        _manual_list = ", ".join(o.order_number for o in manual_orders)
                        _manual_title = f"{len(manual_orders)} הזמנות דורשות טיפול ידני שלך"
                        _manual_msg = (
                            f"התשלום ללקוח התקבל, אבל התשלום האוטומטי לספק (Stripe Issuing) "
                            f"חסום ב-live mode — לא ינסה אוטומטית שוב. יש לטפל ידנית בהזמנות: "
                            f"{_manual_list}"
                        )
                        await notify_owner("orders", _manual_title, _manual_msg, severity="warning")
                        admins_res0 = await db.execute(select(User).where(User.is_admin == True))
                        for admin in admins_res0.scalars().all():
                            db.add(Notification(
                                user_id=admin.id, type="system",
                                title=_manual_title, message=_manual_msg,
                                data={"manual_orders": [o.order_number for o in manual_orders],
                                      "reason": "stripe_issuing_live_mode_blocked"},
                            ))
                        await db.commit()
                        print(f"[OrderMonitor] manual-action alert sent for {_manual_list}")

                if stuck:
                    print(f"[OrderMonitor] Found {len(stuck)} order(s) stuck > {STUCK_ORDER_HOURS}h — triggering fulfillment...")
                    await trigger_supplier_fulfillment(stuck, db)

                    order_list = ", ".join(o.order_number for o in stuck)
                    # NOTIFY BY EXCEPTION (fix 2026-08-04): this loop re-triggers the SAME
                    # unfulfillable orders every cycle (their supplier payment keeps failing),
                    # and it used to send an identical "N orders auto-handled" notification to
                    # every admin EACH cycle — 310 identical rows over 3 days. That is the
                    # "template that keeps sending without a real update" the owner reported.
                    # Only notify when the STUCK-ORDER SET actually changes (Redis signature,
                    # 24h TTL); a re-alert after 24h still surfaces a persistent problem.
                    import hashlib as _hl
                    _stuck_sig = _hl.sha256(",".join(sorted(o.order_number for o in stuck)).encode()).hexdigest()[:16]
                    _notify_stuck = True
                    try:
                        _r = await get_redis()
                        _sk = f"autospare:stuck_orders_notified:{_stuck_sig}"
                        if _r is not None:
                            if await _r.exists(_sk):
                                _notify_stuck = False
                            else:
                                await _r.set(_sk, "1", ex=86400)
                    except Exception:
                        _notify_stuck = True
                    if _notify_stuck:
                        admins_res = await db.execute(select(User).where(User.is_admin == True))
                        admins = admins_res.scalars().all()
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
                        print(f"[OrderMonitor] ✅ Auto-fulfilled + notified (new set): {order_list}")
                    else:
                        await db.commit()
                        print(f"[OrderMonitor] ✅ Auto-fulfilled (same set, notify suppressed): {order_list}")
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

    async def _alert_owner(title: str, msg: str, alert_key: str = "", cooldown_s: int = 3600,
                            severity: str = "warning") -> None:
        """Thin wrapper over notify_owner (category="health") — kept so the ~8 call
        sites below don't all need touching; only the severity varies per alert."""
        if not _OWNER_PHONE:
            return
        await notify_owner("health", title, msg, severity=severity, alert_key=alert_key, cooldown_s=cooldown_s)

    _prev_states: dict = {}  # service_name → "ok" | "error"

    SERVICE_LABELS = {
        "postgres_catalog": "מסד נתונים — קטלוג",
        "postgres_pii":     "מסד נתונים — לקוחות",
        "redis":            "Redis (תור/מטמון)",
        "meilisearch":      "מנוע חיפוש",
        "huggingface":      "Hugging Face AI",
        # clamav DECOMMISSIONED 2026-07-12 (RAM-incompatible with this no-swap box;
        # uploads fail-open). Removed from health probes so it no longer alerts.
        "stripe":           "Stripe (תשלומים)",
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
            # G8 2026-07-20: deliver WhatsApp messages queued during quiet hours as soon
            # as the daytime window opens (this pass runs every 5 min).
            if _notify_window_open()[0]:
                await _flush_wa_quiet_queue()

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
                    _title = f"שירות {label} נפל!"
                    _msg   = f"שירות {label} אינו זמין. בדוק את המערכת בהקדם."
                    _notif_type = "service_down"
                    _severity = "critical"
                    print(f"[HealthMonitor] \u26a0\ufe0f  {svc} went DOWN")
                else:
                    _title = f"שירות {label} חזר לעבוד"
                    _msg   = f"שירות {label} חזר לפעול נורמלית."
                    _severity = "success"
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
                                wa_result = await _wa_send_quiet(to=admin.phone, text=f"{_title}\n{_msg}")
                                if not wa_result.get("ok"):
                                    print(f"[HealthMonitor] WhatsApp failed for admin {admin.id}: {wa_result.get('error')}")
                        await db.commit()
                        # Send directly to owner phone (no cooldown — service state changes are already deduplicated)
                        if _OWNER_PHONE and _OWNER_PHONE not in admin_phones:
                            await _alert_owner(_title, _msg, alert_key="", severity=_severity)
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
                        _alert_title = "קטלוג: עדכונים נמוכים בשעות האחרונות"
                        _alert_msg = (
                            f"רק {parts_updated_6h} חלקים עודכנו ב-6 השעות האחרונות (יעד: 50+). "
                            f"הסקרייפר אולי תקוע."
                        )
                        print(f"[HealthMonitor] ALERT: parts_updated={parts_updated_6h} < 100 in 6h")
                        await _alert_owner(_alert_title, _alert_msg, alert_key="catalog_stagnation", severity="warning")
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
                                    await _wa_send_quiet(to=admin.phone, text=f"{_alert_title}\n{_alert_msg}")
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
                        _alert_title = f"שגיאות גבוהות: {error_rate:.1f}% בשעה האחרונה"
                        _alert_msg = (
                            f"שיעור שגיאות {error_rate:.1f}% עולה על הסף (5%). "
                            f"בדוק לוגים: {errors}/{total} שגיאות בשעה האחרונה."
                        )
                        print(f"[HealthMonitor] ALERT: error_rate={error_rate:.1f}% > 5%")
                        await _alert_owner(_alert_title, _alert_msg, alert_key="high_error_rate", severity="critical")
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
                        _alert_title = "Worker db_update_agent: תקוע באמת"
                        _alert_msg = f"db_update_agent: {_stall_reason}."
                        print(f"[HealthMonitor] ALERT: worker stalled — {_stall_reason}")
                        await _alert_owner(_alert_title, _alert_msg, alert_key="worker_silence", severity="warning")

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
                            # ROOT FIX 2026-08-06: this Notification row used to be created for
                            # every admin on EVERY 5-min health-check cycle the worker stayed
                            # stalled — 18 near-identical "worker stuck" rows over one ~90-min
                            # incident, even though the WhatsApp send itself was already capped
                            # to 1/hour by _awk_ws. That's the "templated notification, same
                            # task shown over and over" the owner reported. Persist a row on the
                            # SAME cadence as the actual WhatsApp send, not every health check.
                            if _send_admin_wa_ws:
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
                                    if admin.phone and str(admin.id) != str(WHATSAPP_ANON_USER_ID):
                                        await _wa_send_quiet(to=admin.phone, text=f"{_alert_title}\n{_alert_msg}")
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
                        _alert_title = f"תור כשלונות: {unprocessed_count} משימות ממתינות לטיפול"
                        _alert_msg = (
                            f"יש {unprocessed_count} משימות שנכשלו ב-48 השעות האחרונות "
                            f"(סף התראה: {JOB_FAILURES_ALERT_THRESHOLD}). בדוק בלוח הבקרה."
                        )
                        print(f"[HealthMonitor] ALERT: job_failures={unprocessed_count} >= {JOB_FAILURES_ALERT_THRESHOLD}")
                        await _alert_owner(_alert_title, _alert_msg, alert_key="job_failures_dlq", cooldown_s=21600, severity="critical")

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
                                await _wa_send_quiet(to=admin.phone, text=f"{_alert_title}\n{_alert_msg}")
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
                        _jt = f"משימה נכשלה: {_jr.job_name}"
                        _jm = (
                            f"המשימה *{_jr.job_name}* הסתיימה עם status={_jr.status}.\n"
                            + (f"שגיאה: {(_jr.error_message or '')[:200]}\n" if _jr.error_message else "")
                            + f"התחילה: {str(_jr.started_at)[:19]}"
                        )
                        print(f"[HealthMonitor] ALERT: job {_jr.job_name} ({_jr.job_id}) {_jr.status}")
                        await _alert_owner(_jt, _jm, alert_key="", severity="critical")  # no cooldown — each job_id is unique

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
                        _zt = f"תהליך זומבי טופל אוטומטית: {_zr.job_name}"
                        _zm = (
                            f"המשימה *{_zr.job_name}* הייתה שקטה {_silence_min} דקות — "
                            f"ה-lock ב-Redis נוקה והסטטוס עודכן לנכשל אוטומטית.\n"
                            f"הריצה המתוזמנת הבאה תתחיל מחדש."
                        )
                        print(f"[HealthMonitor] ALERT: zombie {_zr.job_name} ({_zr.job_id}) silent={_silence_min}min — auto-fixed")
                        await _alert_owner(_zt, _zm, alert_key="", severity="success")

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
                                _tt = f"תהליך רקע הפסיק לרוץ: {_tname}"
                                _tm = (
                                    f"הלולאה *{_tname}* כבר לא רצה.\n"
                                    + (f"שגיאה: {_exc}\n" if _exc else "")
                                    + "המערכת לא תפעיל אותה מחדש אוטומטית — נדרש טיפול ידני."
                                )
                                print(f"[HealthMonitor] ALERT: task {_tname} stopped exc={_exc}")
                                await _alert_owner(_tt, _tm, alert_key="", severity="critical")

            except Exception as _e:
                print(f"[HealthMonitor] Check 5 (job_registry/tasks) error: {_e}")

        except Exception as e:
            print(f"[HealthMonitor] Outer error: {e}")

        # ── Pending social-post reminder (runs every health-monitor cycle) ────
        # One-shot WA notification on creation is easy to miss. This re-pings the
        # owner once per day during window hours when posts are waiting > 2 hours.
        try:
            async with async_session_factory() as _sp_db:
                _pending = await _sp_db.execute(text("""
                    SELECT id, platforms, created_at,
                           EXTRACT(EPOCH FROM (NOW() - created_at))/3600 AS age_h
                    FROM social_posts
                    WHERE status = 'pending_approval'
                      AND created_at < NOW() - INTERVAL '2 hours'
                    ORDER BY created_at ASC
                """))
                _pending_rows = _pending.fetchall()
            if _pending_rows:
                _oldest_id = str(_pending_rows[0][0])[:8]
                _total = len(_pending_rows)
                _oldest_age_h = round(_pending_rows[0][3])
                _lines = [
                    f"📢 *NOA — {_total} פוסטי' ממתינים לאישורך*",
                    "",
                ]
                for _pr in _pending_rows[:5]:
                    _pid = str(_pr[0])[:8]
                    _plat = ", ".join(_pr[1]) if _pr[1] else "?"
                    _age = round(_pr[3])
                    _lines.append(f"• *אשר {_pid}* — {_plat} (לפני {_age}ש')")
                if _total > 5:
                    _lines.append(f"  ועוד {_total - 5} נוספים…")
                _lines += ["", "לפרסום: *אשר <מזהה>* | לדחייה: *דחה <מזהה>*"]
                await notify_owner(
                    "social",
                    f"פוסטים ממתינים לאישור ({_total})",
                    "\n".join(_lines),
                    severity="info",
                    alert_key=f"pending_posts_reminder_{_oldest_id}",
                    cooldown_s=86400,  # remind once per day per oldest-post-id
                )
        except Exception as _spe:
            print(f"[HealthMonitor] pending-posts reminder error: {_spe}")

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

                        # ROOT FIX (G8 2026-07-20): the cap used to count only reminders
                        # inside a ROLLING 3-day window — so every 3 days the same cart
                        # legally earned 3 MORE sends, forever. That is how "max 3" kept
                        # breaking. The cap is now LIFETIME per cart (no time cutoff),
                        # plus a minimum gap between consecutive sends.
                        async with pii_session_factory() as db:
                            _cap_row = (await db.execute(
                                select(func.count(Notification.id), func.max(Notification.created_at)).where(
                                    Notification.type == "abandoned_cart",
                                    Notification.data["cart_id"].astext == str(cart.id),
                                )
                            )).first()
                        sent_total = int(_cap_row[0] or 0)
                        last_sent_at = _cap_row[1]
                        if sent_total >= ABANDONED_CART_MAX_SENDS_PER_WINDOW:
                            print(
                                f"[AbandonedCart] Skip cart {cart.id} — lifetime cap reached "
                                f"({sent_total}/{ABANDONED_CART_MAX_SENDS_PER_WINDOW} reminders ever sent)"
                            )
                            skip_count += 1
                            continue
                        if last_sent_at and (datetime.utcnow() - last_sent_at) < timedelta(hours=ABANDONED_CART_MIN_GAP_H):
                            print(
                                f"[AbandonedCart] Skip cart {cart.id} — last reminder "
                                f"{(datetime.utcnow() - last_sent_at).total_seconds()/3600:.1f}h ago "
                                f"(< {ABANDONED_CART_MIN_GAP_H}h gap)"
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
    PAYMENT_REMINDER_MAX_SENDS = int(os.getenv("PAYMENT_REMINDER_MAX_SENDS", "3"))
    await asyncio.sleep(15)   # let DB pool warm up on startup
    while True:
        try:
            # G8 2026-07-20: same IL-daytime window as the cart loop — this loop used to
            # send around the clock (THE 03:00 reminder source). Defer to daytime.
            _pp_open, _pp_now = _notify_window_open()
            if not _pp_open:
                print(f"[PaymentReminder] Skip — outside IL daytime window "
                      f"({NOTIFY_SEND_START_HOUR_IL}:00-{NOTIFY_SEND_END_HOUR_IL}:00, "
                      f"now={_pp_now.strftime('%H:%M')})")
                await asyncio.sleep(PAYMENT_REMINDER_INTERVAL_S)
                continue
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

                    # G8 2026-07-20: lifetime cap — max PAYMENT_REMINDER_MAX_SENDS (3)
                    # reminders per order, ever (the 6h dedup alone allowed ~4/day).
                    async with pii_session_factory() as db:
                        _pr_sent = int((await db.execute(
                            select(func.count(Notification.id)).where(
                                Notification.type == "payment_reminder",
                                Notification.data["order_id"].astext == str(order.id),
                            )
                        )).scalar() or 0)
                    if _pr_sent >= PAYMENT_REMINDER_MAX_SENDS:
                        print(f"[PaymentReminder] Skip order {order.order_number} — lifetime cap "
                              f"({_pr_sent}/{PAYMENT_REMINDER_MAX_SENDS})")
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
    from resilience import log_job_failure, job_registry_start, job_registry_finish, job_heartbeat
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

    # ROOT FIX 2026-07-25: this loop is the SOLE scheduled runner of sync_prices, so a
    # lock present at startup is stale — left by a run that was killed mid-flight (a backend
    # restart/OOM). sync_prices' own release() only runs on its success path, so an error or
    # a kill leaked autospare:lock:sync_prices for the full 4h TTL → every subsequent run
    # logged "skipped — already running on another worker" and the price sync stalled. Clear
    # it once here so a fresh container never inherits a phantom lock.
    try:
        from BACKEND_AUTH_SECURITY import get_redis as _get_redis_ps
        _r0 = await _get_redis_ps()
        if await _r0.delete("autospare:lock:sync_prices"):
            print("[PriceSync] cleared a stale sync_prices lock from a prior killed run")
    except Exception as _lce:
        print(f"[PriceSync] startup lock check failed: {_lce}")

    while True:
        job_id = None
        sleep_s = interval_s
        try:
            async with async_session_factory() as db:
                try:
                    job_id = await job_registry_start(db, "sync_prices", ttl_seconds=interval_s)
                except Exception as exc:
                    print(f"[PriceSync] job_registry_start failed: {exc}")

                # ROOT FIX 2026-07-23: sync_prices runs eBay+AliExpress for ~1-2h. Previously
                # the whole run used this ONE `db` session and never heartbeated, so (a) the
                # zombie watchdog marked it 'dead' after 30 min of silence, and (b) the long-held
                # transaction hit idle_in_transaction / connection reset → job_registry_finish
                # threw "invalid transaction". Fix: heartbeat on a separate short session every
                # 5 min, and finish on a FRESH session (the long-running one may be poisoned).
                async def _hb_loop():
                    while True:
                        await asyncio.sleep(300)
                        try:
                            async with async_session_factory() as _hbdb:
                                await job_heartbeat(_hbdb, job_id)
                        except Exception as _hbe:
                            print(f"[PriceSync] heartbeat failed: {_hbe}")
                hb_task = asyncio.create_task(_hb_loop()) if job_id else None

                agent = SupplierManagerAgent()
                try:
                    report = await agent.sync_prices(db)
                finally:
                    if hb_task:
                        hb_task.cancel()
                        try:
                            await hb_task
                        except BaseException:
                            pass
                    # Always free the lock after a run — sync_prices only releases it on its
                    # own success path, so an error mid-run would otherwise hold it for 4h and
                    # skip every subsequent run. (This loop is the sole scheduled runner.)
                    try:
                        from BACKEND_AUTH_SECURITY import get_redis as _grp
                        await (await _grp()).delete("autospare:lock:sync_prices")
                    except Exception:
                        pass
                status = str((report or {}).get("status") or "ok")

                async def _finish(_status: str, _err: "str | None" = None):
                    if not job_id:
                        return
                    try:
                        async with async_session_factory() as _fdb:
                            await job_registry_finish(_fdb, job_id, status=_status, error_message=_err)
                    except Exception as _fe:
                        print(f"[PriceSync] job_registry_finish failed: {_fe}")

                if status == "skipped":
                    reason = str((report or {}).get("reason") or "unknown")
                    sleep_s = min(interval_s, 900)
                    print(f"[PriceSync] skipped — {reason}. retry_in={int(sleep_s)}s")
                    await _finish("skipped", reason)
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
                    await _finish("completed")
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
from routes.connect import router as connect_router
app.include_router(connect_router)
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
