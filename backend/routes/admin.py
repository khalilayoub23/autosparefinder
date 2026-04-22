"""
Admin — /api/v1/admin endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  GET    /api/v1/admin/supplier-orders
  PUT    /api/v1/admin/supplier-orders/{notification_id}/done
  GET    /api/v1/admin/stats
  GET    /api/v1/admin/users
  GET    /api/v1/admin/super/settings
  PUT    /api/v1/admin/super/settings/{key}
  POST   /api/v1/admin/super/settings
  DELETE /api/v1/admin/super/settings/{key}
  GET    /api/v1/admin/super/users
  PUT    /api/v1/admin/super/users/{user_id}/role
  POST   /api/v1/admin/users
  PUT    /api/v1/admin/users/{user_id}
  POST   /api/v1/admin/users/{user_id}/reset-login
  DELETE /api/v1/admin/users/{user_id}
  GET    /api/v1/admin/suppliers
  POST   /api/v1/admin/suppliers
  PUT    /api/v1/admin/suppliers/{supplier_id}
  DELETE /api/v1/admin/suppliers/{supplier_id}
  POST   /api/v1/admin/suppliers/{supplier_id}/sync
  GET    /api/v1/admin/approvals
  POST   /api/v1/admin/approvals/{approval_id}/resolve
  GET    /api/v1/admin/orders
  PUT    /api/v1/admin/orders/{order_id}/status
"""
import asyncio
import json
import os
import uuid
from datetime import datetime, date
from typing import Any, Dict, Optional
from uuid import UUID as _UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import String, and_, func, or_, select, text

from BACKEND_DATABASE_MODELS import (
    AuditLog, ApprovalQueue, AftermarketBrand, BrandAlias, CarBrand,
    CatalogVersion, JobRegistry, Notification, Order, PartsCatalog, PartAlias,
    PartCrossReference, PartDiagramCache, PartImage, PartMaster, PartVariant,
    Payment, PriceHistory, PurchaseOrder, Return, ScraperApiCall, SocialPost,
    Supplier, SupplierPart, SystemSetting, User, Conversation, Message,
    get_db, get_pii_db,
)
from BACKEND_AUTH_SECURITY import (
    get_current_admin_user, get_current_super_admin,
    hash_password, publish_notification,
)
from currency_rate import get_usd_to_ils_rate
from routes.utils import _guarded_task
from routes.schemas import (
    SuperAdminSettingCreateBody,
    SuperAdminSettingUpdateBody,
    SuperAdminUserRoleUpdateBody,
    SupplierCreate,
    SupplierUpdateBody,
    UserCreateBody,
    UserUpdateBody,
    ResolveApprovalBody,
    CreateSocialPostRequest,
    UpdateSocialPostRequest,
)

router = APIRouter()

BLOCKED_SETTINGS = {
    "jwt_secret", "jwt_refresh_secret", "stripe_secret_key",
    "stripe_webhook_secret", "hf_token", "database_url",
    "database_pii_url", "redis_url", "encryption_key",
    "twilio_auth_token", "sendgrid_api_key",
}

_HANDOFF_SETTINGS_KEY = "support_handoff_settings"
_DEFAULT_HANDOFF_SETTINGS: Dict[str, Any] = {
    "sla_target_seconds": 300,
    "avg_handle_minutes": 6,
    "queue_eta_floor_seconds": 60,
    "escalation_after_seconds": 420,
    "ai_lock_during_handoff": True,
    "feedback_required_on_resolve": True,
    "waiting_notice_cooldown_seconds": 120,
}


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
# SUPPLIER ORDERS
# ==============================================================================

@router.get("/api/v1/admin/supplier-orders")
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


@router.put("/api/v1/admin/supplier-orders/{notification_id}/done")
async def mark_supplier_order_done(
    notification_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
    request: Request = None,
):
    """Admin: mark a supplier purchase task as ordered, optionally recording a tracking number."""
    body: dict = {}
    if request:
        try:
            body = await request.json()
        except Exception:
            body = {}
    tracking_number: Optional[str] = body.get("tracking_number") or None
    tracking_url: Optional[str] = body.get("tracking_url") or None
    carrier: Optional[str] = body.get("carrier") or None
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
                # No tracking yet — keep processing until a real tracking number exists.
                if order.status in ("paid", "confirmed"):
                    order.status = "processing"
                _notrack_title = "🛒 ההזמנה הועברה לספק"
                _notrack_msg = f"הזמנה {order.order_number} שולמה לספק ונמצאת בעיבוד. מספר מעקב יעודכן ברגע שיתקבל."
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
# STATS & USERS
# ==============================================================================

@router.get("/api/v1/admin/stats")
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

    paid_statuses = ["paid", "processing", "supplier_ordered", "confirmed", "shipped", "delivered"]

    # Gross revenue (primary source): successful payment rows.
    gross_revenue = (await db.execute(
        select(func.sum(Payment.amount)).where(
            Payment.status.in_(["paid", "refunded"])
        )
    )).scalar() or 0

    # Compatibility fallback for environments with legacy/missing payment rows:
    # derive gross from paid-like orders so dashboard cards don't show false zeros.
    if float(gross_revenue or 0) <= 0:
        gross_revenue = (await db.execute(
            select(func.sum(Order.total_amount)).where(Order.status.in_(paid_statuses))
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


@router.get("/api/v1/admin/users")
async def get_admin_users(current_user: User = Depends(get_current_admin_user), limit: int = 100, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(User).order_by(User.created_at.desc()).limit(limit))
    users = result.scalars().all()
    return {"users": [{"id": str(u.id), "email": u.email, "full_name": u.full_name, "phone": u.phone, "is_verified": u.is_verified, "is_admin": u.is_admin, "is_active": u.is_active, "role": u.role, "failed_login_count": u.failed_login_count, "locked_until": u.locked_until.isoformat() if u.locked_until else None, "created_at": u.created_at} for u in users]}


@router.get("/api/v1/admin/db-view")
async def admin_db_view(
    dataset: str = "parts_catalog",
    search: str = "",
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_admin_user),
    cat_db: AsyncSession = Depends(get_db),
):
    dataset = (dataset or "parts_catalog").strip().lower()
    search = (search or "").strip()
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    all_datasets = [
        {"id": "parts_catalog", "label": "Parts Catalog", "scope": "catalog", "group": "Core Parts", "table": "parts_catalog"},
        {"id": "part_master", "label": "Part Master", "scope": "catalog", "group": "Core Parts", "table": "parts_master"},
        {"id": "part_variants", "label": "Part Variants", "scope": "catalog", "group": "Core Parts", "table": "part_variants"},
        {"id": "part_cross_reference", "label": "Part Cross Reference", "scope": "catalog", "group": "Core Parts", "table": "part_cross_reference"},
        {"id": "part_aliases", "label": "Part Aliases", "scope": "catalog", "group": "Core Parts", "table": "part_aliases"},
        {"id": "part_images", "label": "Part Images", "scope": "catalog", "group": "Core Parts", "table": "parts_images"},
        {"id": "aftermarket_brands", "label": "Aftermarket Brands", "scope": "catalog", "group": "Core Parts", "table": "aftermarket_brands"},
        {"id": "car_brands", "label": "Car Brands", "scope": "catalog", "group": "Core Parts", "table": "car_brands"},
        {"id": "brand_aliases", "label": "Brand Aliases", "scope": "catalog", "group": "Core Parts", "table": "brand_aliases"},
        {"id": "catalog_versions", "label": "Catalog Versions", "scope": "catalog", "group": "Pricing", "table": "catalog_versions"},
        {"id": "price_history", "label": "Price History", "scope": "catalog", "group": "Pricing", "table": "price_history"},
        {"id": "suppliers", "label": "Suppliers", "scope": "catalog", "group": "Supplier Ops", "table": "suppliers"},
        {"id": "supplier_parts", "label": "Supplier Parts", "scope": "catalog", "group": "Supplier Ops", "table": "supplier_parts"},
        {"id": "purchase_orders", "label": "Purchase Orders", "scope": "catalog", "group": "Supplier Ops", "table": "purchase_orders"},
        {"id": "part_diagram_cache", "label": "Part Diagram Cache", "scope": "catalog", "group": "AI Cache", "table": "part_diagram_cache"},
        {"id": "scraper_api_calls", "label": "Scraper API Calls", "scope": "catalog", "group": "AI Cache", "table": "scraper_api_calls"},
        {"id": "job_registry", "label": "Job Registry", "scope": "catalog", "group": "System", "table": "job_registry"},
        {"id": "system_settings", "label": "System Settings", "scope": "catalog", "group": "System", "table": "system_settings"},
    ]

    existing_tables = set()
    try:
        table_rows = (await cat_db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))).all()
        existing_tables = {str(r[0]) for r in table_rows}
    except Exception:
        # If metadata lookup fails, keep original behavior and expose all datasets.
        existing_tables = {str(d["table"]) for d in all_datasets}

    datasets = [
        {k: v for k, v in d.items() if k != "table"}
        for d in all_datasets
        if d["table"] in existing_tables
    ]
    if not datasets:
        return {
            "dataset": "",
            "datasets": [],
            "columns": [],
            "rows": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
        }

    allowed_ids = {d["id"] for d in datasets}
    if dataset not in allowed_ids:
        dataset = datasets[0]["id"]

    def _iso(value: Any) -> Optional[str]:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def _preview(text_value: Any, max_len: int = 240) -> Optional[str]:
        if text_value is None:
            return None
        text = str(text_value)
        if len(text) <= max_len:
            return text
        return f"{text[:max_len - 3]}..."

    rows: list[Dict[str, Any]] = []
    columns: list[Dict[str, str]] = []
    total: int = 0

    if dataset == "parts_catalog":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    PartsCatalog.id.cast(String).ilike(q),
                    PartsCatalog.sku.ilike(q),
                    PartsCatalog.name.ilike(q),
                    PartsCatalog.name_he.ilike(q),
                    PartsCatalog.manufacturer.ilike(q),
                    PartsCatalog.oem_number.ilike(q),
                    PartsCatalog.category.ilike(q),
                    PartsCatalog.part_type.ilike(q),
                )
            )

        count_stmt = select(func.count(PartsCatalog.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PartsCatalog).order_by(PartsCatalog.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        parts = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(p.id),
                "sku": p.sku,
                "name": p.name,
                "name_he": p.name_he,
                "category": p.category,
                "manufacturer": p.manufacturer,
                "part_type": p.part_type,
                "oem_number": p.oem_number,
                "base_price": float(p.base_price or 0),
                "is_active": bool(p.is_active),
                "updated_at": _iso(p.updated_at),
            }
            for p in parts
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "sku", "label": "SKU"},
            {"key": "name", "label": "Name"},
            {"key": "name_he", "label": "Name (HE)"},
            {"key": "category", "label": "Category"},
            {"key": "manufacturer", "label": "Manufacturer"},
            {"key": "part_type", "label": "Part Type"},
            {"key": "oem_number", "label": "OEM"},
            {"key": "base_price", "label": "Base Price"},
            {"key": "is_active", "label": "Active"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    elif dataset == "part_master":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    PartMaster.id.cast(String).ilike(q),
                    PartMaster.canonical_name.ilike(q),
                    PartMaster.canonical_name_he.ilike(q),
                    PartMaster.category.ilike(q),
                    PartMaster.part_type.ilike(q),
                )
            )

        count_stmt = select(func.count(PartMaster.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PartMaster).order_by(PartMaster.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        masters = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(m.id),
                "canonical_name": m.canonical_name,
                "canonical_name_he": m.canonical_name_he,
                "category": m.category,
                "part_type": m.part_type,
                "is_safety_critical": bool(m.is_safety_critical),
                "updated_at": _iso(m.updated_at),
            }
            for m in masters
        ]

        columns = [
            {"key": "id", "label": "ID"},
            {"key": "canonical_name", "label": "Canonical Name"},
            {"key": "canonical_name_he", "label": "Canonical Name (HE)"},
            {"key": "category", "label": "Category"},
            {"key": "part_type", "label": "Part Type"},
            {"key": "is_safety_critical", "label": "Safety Critical"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    elif dataset == "part_variants":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    PartVariant.id.cast(String).ilike(q),
                    PartVariant.master_part_id.cast(String).ilike(q),
                    PartVariant.catalog_part_id.cast(String).ilike(q),
                    PartVariant.quality_level.ilike(q),
                    PartVariant.manufacturer.ilike(q),
                    PartVariant.sku.ilike(q),
                )
            )

        count_stmt = select(func.count(PartVariant.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PartVariant).order_by(PartVariant.created_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        variants = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(v.id),
                "master_part_id": str(v.master_part_id),
                "catalog_part_id": str(v.catalog_part_id),
                "quality_level": v.quality_level,
                "manufacturer": v.manufacturer,
                "sku": v.sku,
                "created_at": _iso(v.created_at),
            }
            for v in variants
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "master_part_id", "label": "Master Part ID"},
            {"key": "catalog_part_id", "label": "Catalog Part ID"},
            {"key": "quality_level", "label": "Quality Level"},
            {"key": "manufacturer", "label": "Manufacturer"},
            {"key": "sku", "label": "SKU"},
            {"key": "created_at", "label": "Created At"},
        ]

    elif dataset == "supplier_parts":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    SupplierPart.id.cast(String).ilike(q),
                    SupplierPart.supplier_id.cast(String).ilike(q),
                    SupplierPart.part_id.cast(String).ilike(q),
                    SupplierPart.supplier_sku.ilike(q),
                    SupplierPart.availability.ilike(q),
                    SupplierPart.part_type.ilike(q),
                )
            )

        count_stmt = select(func.count(SupplierPart.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(SupplierPart).order_by(SupplierPart.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        supplier_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(sp.id),
                "supplier_id": str(sp.supplier_id),
                "part_id": str(sp.part_id),
                "supplier_sku": sp.supplier_sku,
                "price_ils": float(sp.price_ils or 0),
                "price_usd": float(sp.price_usd or 0),
                "availability": sp.availability,
                "is_available": bool(sp.is_available),
                "part_type": sp.part_type,
                "last_checked_at": _iso(sp.last_checked_at),
                "updated_at": _iso(sp.updated_at),
            }
            for sp in supplier_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "supplier_id", "label": "Supplier ID"},
            {"key": "part_id", "label": "Part ID"},
            {"key": "supplier_sku", "label": "Supplier SKU"},
            {"key": "price_ils", "label": "Price ILS"},
            {"key": "price_usd", "label": "Price USD"},
            {"key": "availability", "label": "Availability"},
            {"key": "is_available", "label": "In Stock"},
            {"key": "part_type", "label": "Part Type"},
            {"key": "last_checked_at", "label": "Last Checked"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    elif dataset == "part_cross_reference":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    PartCrossReference.id.cast(String).ilike(q),
                    PartCrossReference.part_id.cast(String).ilike(q),
                    PartCrossReference.ref_number.ilike(q),
                    PartCrossReference.manufacturer.ilike(q),
                    PartCrossReference.ref_type.ilike(q),
                )
            )

        count_stmt = select(func.count(PartCrossReference.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PartCrossReference).order_by(PartCrossReference.created_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        refs = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(r.id),
                "part_id": str(r.part_id),
                "ref_number": r.ref_number,
                "manufacturer": r.manufacturer,
                "ref_type": r.ref_type,
                "is_superseded": bool(r.is_superseded),
                "superseded_by": r.superseded_by,
                "created_at": _iso(r.created_at),
            }
            for r in refs
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "part_id", "label": "Part ID"},
            {"key": "ref_number", "label": "Reference"},
            {"key": "manufacturer", "label": "Manufacturer"},
            {"key": "ref_type", "label": "Ref Type"},
            {"key": "is_superseded", "label": "Superseded"},
            {"key": "superseded_by", "label": "Superseded By"},
            {"key": "created_at", "label": "Created At"},
        ]

    elif dataset == "part_aliases":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    PartAlias.id.cast(String).ilike(q),
                    PartAlias.part_id.cast(String).ilike(q),
                    PartAlias.alias.ilike(q),
                    PartAlias.language.ilike(q),
                )
            )

        count_stmt = select(func.count(PartAlias.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PartAlias).order_by(PartAlias.created_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        alias_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(a.id),
                "part_id": str(a.part_id),
                "alias": a.alias,
                "language": a.language,
                "created_at": _iso(a.created_at),
            }
            for a in alias_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "part_id", "label": "Part ID"},
            {"key": "alias", "label": "Alias"},
            {"key": "language", "label": "Language"},
            {"key": "created_at", "label": "Created At"},
        ]

    elif dataset == "part_images":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    PartImage.id.cast(String).ilike(q),
                    PartImage.part_id.cast(String).ilike(q),
                    PartImage.url.ilike(q),
                )
            )

        count_stmt = select(func.count(PartImage.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PartImage).order_by(PartImage.created_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        img_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(i.id),
                "part_id": str(i.part_id),
                "url": _preview(i.url, 140),
                "is_primary": bool(i.is_primary),
                "sort_order": i.sort_order,
                "embedding_generated": bool(i.embedding_generated),
                "created_at": _iso(i.created_at),
            }
            for i in img_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "part_id", "label": "Part ID"},
            {"key": "url", "label": "URL"},
            {"key": "is_primary", "label": "Primary"},
            {"key": "sort_order", "label": "Sort"},
            {"key": "embedding_generated", "label": "Embedded"},
            {"key": "created_at", "label": "Created At"},
        ]

    elif dataset == "suppliers":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    Supplier.id.cast(String).ilike(q),
                    Supplier.name.ilike(q),
                    Supplier.country.ilike(q),
                    Supplier.manufacturer_name.ilike(q),
                    Supplier.website.ilike(q),
                )
            )

        count_stmt = select(func.count(Supplier.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(Supplier).order_by(Supplier.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        sup_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(s.id),
                "name": s.name,
                "country": s.country,
                "manufacturer_name": s.manufacturer_name,
                "priority": s.priority,
                "reliability_score": float(s.reliability_score or 0),
                "supports_express": bool(s.supports_express),
                "is_active": bool(s.is_active),
                "updated_at": _iso(s.updated_at),
            }
            for s in sup_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "name", "label": "Name"},
            {"key": "country", "label": "Country"},
            {"key": "manufacturer_name", "label": "Manufacturer"},
            {"key": "priority", "label": "Priority"},
            {"key": "reliability_score", "label": "Reliability"},
            {"key": "supports_express", "label": "Express"},
            {"key": "is_active", "label": "Active"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    elif dataset == "aftermarket_brands":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(AftermarketBrand.id.cast(String).ilike(q), AftermarketBrand.name.ilike(q), AftermarketBrand.tier.ilike(q), AftermarketBrand.country.ilike(q)))

        count_stmt = select(func.count(AftermarketBrand.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(AftermarketBrand).order_by(AftermarketBrand.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        brand_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(b.id),
                "name": b.name,
                "tier": b.tier,
                "country": b.country,
                "website": b.website,
                "is_active": bool(b.is_active),
                "updated_at": _iso(b.updated_at),
            }
            for b in brand_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "name", "label": "Name"},
            {"key": "tier", "label": "Tier"},
            {"key": "country", "label": "Country"},
            {"key": "website", "label": "Website"},
            {"key": "is_active", "label": "Active"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    elif dataset == "car_brands":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(CarBrand.id.cast(String).ilike(q), CarBrand.name.ilike(q), CarBrand.name_he.ilike(q), CarBrand.group_name.ilike(q), CarBrand.country.ilike(q)))

        count_stmt = select(func.count(CarBrand.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(CarBrand).order_by(CarBrand.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        car_brand_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(b.id),
                "name": b.name,
                "name_he": b.name_he,
                "group_name": b.group_name,
                "country": b.country,
                "is_luxury": bool(b.is_luxury),
                "is_active": bool(b.is_active),
                "updated_at": _iso(b.updated_at),
            }
            for b in car_brand_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "name", "label": "Name"},
            {"key": "name_he", "label": "Name (HE)"},
            {"key": "group_name", "label": "Group"},
            {"key": "country", "label": "Country"},
            {"key": "is_luxury", "label": "Luxury"},
            {"key": "is_active", "label": "Active"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    elif dataset == "brand_aliases":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(BrandAlias.id.cast(String).ilike(q), BrandAlias.brand_id.cast(String).ilike(q), BrandAlias.alias.ilike(q), BrandAlias.normalized.ilike(q), BrandAlias.source.ilike(q)))

        count_stmt = select(func.count(BrandAlias.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(BrandAlias).order_by(BrandAlias.created_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        alias_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(a.id),
                "brand_id": str(a.brand_id),
                "alias": a.alias,
                "normalized": a.normalized,
                "source": a.source,
                "created_at": _iso(a.created_at),
            }
            for a in alias_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "brand_id", "label": "Brand ID"},
            {"key": "alias", "label": "Alias"},
            {"key": "normalized", "label": "Normalized"},
            {"key": "source", "label": "Source"},
            {"key": "created_at", "label": "Created At"},
        ]

    elif dataset == "catalog_versions":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(
                or_(
                    CatalogVersion.id.cast(String).ilike(q),
                    CatalogVersion.version_tag.ilike(q),
                    CatalogVersion.status.ilike(q),
                    CatalogVersion.source.ilike(q),
                    CatalogVersion.description.ilike(q),
                    CatalogVersion.triggered_by.cast(String).ilike(q),
                )
            )

        count_stmt = select(func.count(CatalogVersion.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(CatalogVersion).order_by(CatalogVersion.created_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        ver_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(v.id),
                "version_tag": v.version_tag,
                "source": v.source,
                "status": v.status,
                "parts_added": v.parts_added,
                "parts_updated": v.parts_updated,
                "parts_total": v.parts_total,
                "triggered_by": str(v.triggered_by) if v.triggered_by else None,
                "started_at": _iso(v.started_at),
                "completed_at": _iso(v.completed_at),
                "created_at": _iso(v.created_at),
            }
            for v in ver_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "version_tag", "label": "Version"},
            {"key": "source", "label": "Source"},
            {"key": "status", "label": "Status"},
            {"key": "parts_added", "label": "Parts Added"},
            {"key": "parts_updated", "label": "Parts Updated"},
            {"key": "parts_total", "label": "Parts Total"},
            {"key": "triggered_by", "label": "Triggered By"},
            {"key": "started_at", "label": "Started At"},
            {"key": "completed_at", "label": "Completed At"},
            {"key": "created_at", "label": "Created At"},
        ]

    elif dataset == "price_history":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(PriceHistory.id.cast(String).ilike(q), PriceHistory.supplier_part_id.cast(String).ilike(q), PriceHistory.source.ilike(q)))

        count_stmt = select(func.count(PriceHistory.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PriceHistory).order_by(PriceHistory.created_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        hist_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(h.id),
                "supplier_part_id": str(h.supplier_part_id),
                "old_price_ils": float(h.old_price_ils or 0),
                "new_price_ils": float(h.new_price_ils or 0),
                "old_price_usd": float(h.old_price_usd or 0),
                "new_price_usd": float(h.new_price_usd or 0),
                "change_pct": float(h.change_pct or 0),
                "source": h.source,
                "created_at": _iso(h.created_at),
            }
            for h in hist_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "supplier_part_id", "label": "Supplier Part ID"},
            {"key": "old_price_ils", "label": "Old ILS"},
            {"key": "new_price_ils", "label": "New ILS"},
            {"key": "old_price_usd", "label": "Old USD"},
            {"key": "new_price_usd", "label": "New USD"},
            {"key": "change_pct", "label": "Change %"},
            {"key": "source", "label": "Source"},
            {"key": "created_at", "label": "Created At"},
        ]

    elif dataset == "purchase_orders":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(PurchaseOrder.id.cast(String).ilike(q), PurchaseOrder.po_number.ilike(q), PurchaseOrder.order_id.cast(String).ilike(q), PurchaseOrder.supplier_id.cast(String).ilike(q), PurchaseOrder.status.ilike(q), PurchaseOrder.tracking_number.ilike(q)))

        count_stmt = select(func.count(PurchaseOrder.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PurchaseOrder).order_by(PurchaseOrder.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        po_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(po.id),
                "po_number": po.po_number,
                "order_id": str(po.order_id) if po.order_id else None,
                "supplier_id": str(po.supplier_id),
                "status": po.status,
                "total_ils": float(po.total_ils or 0),
                "shipping_type": po.shipping_type,
                "tracking_number": po.tracking_number,
                "updated_at": _iso(po.updated_at),
            }
            for po in po_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "po_number", "label": "PO #"},
            {"key": "order_id", "label": "Order ID"},
            {"key": "supplier_id", "label": "Supplier ID"},
            {"key": "status", "label": "Status"},
            {"key": "total_ils", "label": "Total ILS"},
            {"key": "shipping_type", "label": "Shipping"},
            {"key": "tracking_number", "label": "Tracking"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    elif dataset == "part_diagram_cache":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(PartDiagramCache.id.cast(String).ilike(q), PartDiagramCache.image_hash.ilike(q), PartDiagramCache.vehicle_make.ilike(q), PartDiagramCache.vehicle_model.ilike(q), PartDiagramCache.part_name_he.ilike(q), PartDiagramCache.part_name_en.ilike(q), PartDiagramCache.catalog_part_id.cast(String).ilike(q)))

        count_stmt = select(func.count(PartDiagramCache.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(PartDiagramCache).order_by(PartDiagramCache.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        cache_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(c.id),
                "image_hash": c.image_hash,
                "vehicle_make": c.vehicle_make,
                "vehicle_model": c.vehicle_model,
                "vehicle_year": c.vehicle_year,
                "part_name_he": c.part_name_he,
                "part_name_en": c.part_name_en,
                "times_seen": c.times_seen,
                "updated_at": _iso(c.updated_at),
            }
            for c in cache_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "image_hash", "label": "Image Hash"},
            {"key": "vehicle_make", "label": "Vehicle Make"},
            {"key": "vehicle_model", "label": "Vehicle Model"},
            {"key": "vehicle_year", "label": "Year"},
            {"key": "part_name_he", "label": "Part Name (HE)"},
            {"key": "part_name_en", "label": "Part Name"},
            {"key": "times_seen", "label": "Times Seen"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    elif dataset == "scraper_api_calls":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(ScraperApiCall.id.cast(String).ilike(q), ScraperApiCall.source.ilike(q), ScraperApiCall.query.ilike(q), ScraperApiCall.part_number.ilike(q), ScraperApiCall.error_message.ilike(q)))

        count_stmt = select(func.count(ScraperApiCall.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(ScraperApiCall).order_by(ScraperApiCall.created_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        call_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(c.id),
                "source": c.source,
                "query": c.query,
                "part_number": c.part_number,
                "http_status": c.http_status,
                "success": bool(c.success),
                "results_count": c.results_count,
                "response_ms": c.response_ms,
                "created_at": _iso(c.created_at),
            }
            for c in call_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "source", "label": "Source"},
            {"key": "query", "label": "Query"},
            {"key": "part_number", "label": "Part #"},
            {"key": "http_status", "label": "HTTP"},
            {"key": "success", "label": "Success"},
            {"key": "results_count", "label": "Results"},
            {"key": "response_ms", "label": "Response ms"},
            {"key": "created_at", "label": "Created At"},
        ]

    elif dataset == "job_registry":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(JobRegistry.id.cast(String).ilike(q), JobRegistry.job_id.ilike(q), JobRegistry.job_name.ilike(q), JobRegistry.worker_host.ilike(q), JobRegistry.status.ilike(q), JobRegistry.error_message.ilike(q)))

        count_stmt = select(func.count(JobRegistry.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(JobRegistry).order_by(JobRegistry.started_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        job_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(j.id),
                "job_id": j.job_id,
                "job_name": j.job_name,
                "worker_host": j.worker_host,
                "status": j.status,
                "started_at": _iso(j.started_at),
                "completed_at": _iso(j.completed_at),
                "last_heartbeat_at": _iso(j.last_heartbeat_at),
            }
            for j in job_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "job_id", "label": "Job ID"},
            {"key": "job_name", "label": "Job Name"},
            {"key": "worker_host", "label": "Worker"},
            {"key": "status", "label": "Status"},
            {"key": "started_at", "label": "Started At"},
            {"key": "completed_at", "label": "Completed At"},
            {"key": "last_heartbeat_at", "label": "Heartbeat"},
        ]

    elif dataset == "system_settings":
        filters = []
        if search:
            q = f"%{search}%"
            filters.append(or_(SystemSetting.id.cast(String).ilike(q), SystemSetting.key.ilike(q), SystemSetting.value.ilike(q), SystemSetting.value_type.ilike(q), SystemSetting.description.ilike(q)))

        count_stmt = select(func.count(SystemSetting.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = (await cat_db.execute(count_stmt)).scalar() or 0

        stmt = select(SystemSetting).order_by(SystemSetting.updated_at.desc()).offset(offset).limit(limit)
        if filters:
            stmt = stmt.where(and_(*filters))
        setting_rows = (await cat_db.execute(stmt)).scalars().all()
        rows = [
            {
                "id": str(s.id),
                "key": s.key,
                "value_preview": _preview(s.value, 220),
                "value_type": s.value_type,
                "is_public": bool(s.is_public),
                "updated_at": _iso(s.updated_at),
            }
            for s in setting_rows
        ]
        columns = [
            {"key": "id", "label": "ID"},
            {"key": "key", "label": "Key"},
            {"key": "value_preview", "label": "Value"},
            {"key": "value_type", "label": "Type"},
            {"key": "is_public", "label": "Public"},
            {"key": "updated_at", "label": "Updated At"},
        ]

    return {
        "dataset": dataset,
        "datasets": datasets,
        "columns": columns,
        "rows": rows,
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


# ==============================================================================
# ADMIN — CHAT MANAGEMENT
# ==============================================================================

def _conversation_channel(conv: Conversation) -> str:
    ctx = conv.context if isinstance(conv.context, dict) else {}
    if str(ctx.get("telegram_chat_id") or "").strip():
        return "telegram"
    if str(ctx.get("whatsapp_phone") or "").strip():
        return "whatsapp"
    return "web"


def _conversation_external_id(conv: Conversation) -> str | None:
    ctx = conv.context if isinstance(conv.context, dict) else {}
    ch = _conversation_channel(conv)
    if ch == "telegram":
        return str(ctx.get("telegram_chat_id") or "").strip() or None
    if ch == "whatsapp":
        return str(ctx.get("whatsapp_phone") or "").strip() or None
    return None


def _safe_channel_text(msg: str) -> str:
    # Telegram sender uses parse_mode=HTML; escape angle brackets to prevent rendering issues.
    return (msg or "").replace("<", "&lt;").replace(">", "&gt;").strip()


def _is_internal_bot_user(user: User) -> bool:
    email = (user.email or "").strip().lower()
    return email.endswith("@autospare.internal")


def _conversation_takeover_active(conv: Conversation) -> bool:
    ctx = conv.context if isinstance(conv.context, dict) else {}
    return bool(ctx.get("admin_takeover_active"))


def _conversation_handoff_meta(conv: Conversation) -> Dict[str, Any]:
    ctx = conv.context if isinstance(conv.context, dict) else {}
    requested = bool(ctx.get("human_handoff_requested"))
    status = str(ctx.get("human_handoff_status") or "none")
    requested_at = str(ctx.get("human_handoff_requested_at") or "").strip() or None
    priority = int(ctx.get("human_handoff_priority") or 1)
    wait_seconds = None
    if status == "requested" and requested_at:
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

    return {
        "human_handoff_requested": requested,
        "human_handoff_status": status,
        "human_handoff_requested_at": requested_at,
        "human_handoff_reason": str(ctx.get("human_handoff_reason") or "").strip() or None,
        "human_handoff_priority": priority,
        "human_handoff_wait_seconds": wait_seconds,
        "human_handoff_assigned_admin_id": str(ctx.get("human_handoff_assigned_admin_id") or "").strip() or None,
        "human_handoff_assigned_name": str(ctx.get("human_handoff_assigned_name") or "").strip() or None,
        "human_handoff_assigned_role": str(ctx.get("human_handoff_assigned_role") or "").strip() or None,
        "human_handoff_lock_active": bool(ctx.get("human_handoff_lock_active", True)),
        "human_handoff_feedback_required": bool(ctx.get("human_handoff_feedback_required")),
        "human_handoff_feedback_submitted": bool(ctx.get("human_handoff_feedback_submitted")),
        "human_handoff_feedback_rating": feedback_rating,
        "human_handoff_feedback_at": str(ctx.get("human_handoff_feedback_at") or "").strip() or None,
        "human_handoff_feedback_text": str(ctx.get("human_handoff_feedback_text") or "").strip() or None,
        "human_handoff_resolved_at": str(ctx.get("human_handoff_resolved_at") or "").strip() or None,
    }


def _conversation_handoff_timeline(conv: Conversation) -> list[Dict[str, Any]]:
    ctx = conv.context if isinstance(conv.context, dict) else {}
    events: list[Dict[str, Any]] = []

    def _iso(raw: Any) -> Optional[str]:
        value = str(raw or "").strip()
        if not value:
            return None
        try:
            datetime.fromisoformat(value)
            return value
        except Exception:
            return None

    requested_at = _iso(ctx.get("human_handoff_requested_at"))
    if requested_at:
        reason = str(ctx.get("human_handoff_reason") or "").strip()
        events.append({
            "type": "requested",
            "title": "הלקוח ביקש נציג אנושי",
            "detail": f"סיבה: {reason}" if reason else None,
            "at": requested_at,
        })

    assigned_at = _iso(ctx.get("human_handoff_assigned_at"))
    if assigned_at:
        assigned_name = str(ctx.get("human_handoff_assigned_name") or "").strip()
        assigned_role = str(ctx.get("human_handoff_assigned_role") or "").strip()
        assigned_text = " ".join(p for p in [assigned_name, assigned_role] if p).strip() or None
        events.append({
            "type": "accepted",
            "title": "נציג אנושי קיבל את השיחה",
            "detail": assigned_text,
            "at": assigned_at,
        })

    resolved_at = _iso(ctx.get("human_handoff_resolved_at"))
    if resolved_at:
        status = str(ctx.get("human_handoff_status") or "").strip() or "resolved"
        detail = "הועבר להמתנה למשוב לקוח" if status == "awaiting_feedback" else "השיחה שוחררה לבוט/סגורה"
        events.append({
            "type": "released",
            "title": "הטיפול הידני הסתיים",
            "detail": detail,
            "at": resolved_at,
        })

    feedback_at = _iso(ctx.get("human_handoff_feedback_at"))
    if feedback_at:
        rating_raw = ctx.get("human_handoff_feedback_rating")
        rating = None
        if rating_raw is not None:
            try:
                rating = int(rating_raw)
            except Exception:
                rating = None
        detail = f"דירוג: {rating}/5" if rating else "התקבל משוב לקוח"
        events.append({
            "type": "feedback_received",
            "title": "התקבל משוב מהלקוח",
            "detail": detail,
            "at": feedback_at,
        })

    events.sort(key=lambda item: str(item.get("at") or ""))
    return events


def _handoff_priority_label(priority: int) -> str:
    if priority >= 4:
        return "critical"
    if priority >= 3:
        return "urgent"
    if priority == 2:
        return "high"
    return "normal"


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
    settings["waiting_notice_cooldown_seconds"] = _to_int(raw.get("waiting_notice_cooldown_seconds"), settings["waiting_notice_cooldown_seconds"], 30, 900)
    settings["ai_lock_during_handoff"] = _to_bool(raw.get("ai_lock_during_handoff"), settings["ai_lock_during_handoff"])
    settings["feedback_required_on_resolve"] = _to_bool(raw.get("feedback_required_on_resolve"), settings["feedback_required_on_resolve"])
    return settings


async def _load_handoff_settings(cat_db: AsyncSession) -> Dict[str, Any]:
    row = (
        await cat_db.execute(select(SystemSetting).where(SystemSetting.key == _HANDOFF_SETTINGS_KEY))
    ).scalar_one_or_none()
    if not row or not row.value:
        return dict(_DEFAULT_HANDOFF_SETTINGS)

    parsed: Any = row.value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except Exception:
            return dict(_DEFAULT_HANDOFF_SETTINGS)
    return _normalize_handoff_settings(parsed)


def _handoff_priority_effective(priority: int, wait_seconds: Optional[int], settings: Dict[str, Any]) -> tuple[int, bool]:
    escalation_after = int(settings.get("escalation_after_seconds") or 420)
    escalated = wait_seconds is not None and wait_seconds >= escalation_after
    return (max(priority, 4) if escalated else priority, escalated)


def _build_takeover_closure_prompt(user: User, ask_feedback: bool) -> str:
    name = (user.full_name or "").strip() or "נציג/ה השירות"
    if ask_feedback:
        return (
            f"כאן {name}. סיימנו לטפל בפנייה שלך ונשמח למשוב קצר על הטיפול.\n"
            "אם אפשר, דרג/י את החוויה (1-5) וכתוב/כתבי בקצרה מה עבד טוב ומה נוכל לשפר."
        )
    return f"כאן {name}. סיימנו לטפל בפנייה שלך. אם תצטרך/י משהו נוסף, אנחנו כאן."


def _human_role_title(user: User) -> str:
    role = (user.role or "").strip().lower()
    if user.is_admin or role in {"admin", "super_admin"}:
        return "מנהל/ת שירות לקוחות"
    if role in {"support", "service", "customer_support"}:
        return "נציג/ת שירות לקוחות"
    return "נציג/ת שירות"


def _build_takeover_intro(user: User) -> str:
    name = (user.full_name or "").strip() or "נציג/ה אנושי/ת"
    role_title = _human_role_title(user)
    return (
        f"שלום, כאן {name}, {role_title} של Auto Spare.\n"
        "אני מצטרף/ת עכשיו לשיחה כדי לטפל בבקשה שלך אישית.\n"
        "אפשר להמשיך מאיפה שעצרת, ואני איתך עד לפתרון."
    )


async def _send_channel_message(channel_name: str, context: Dict[str, Any], text: str) -> Dict[str, Any]:
    if channel_name == "telegram":
        chat_id = str(context.get("telegram_chat_id") or "").strip()
        user_id = str(context.get("telegram_user_id") or "").strip()
        username = str(context.get("telegram_username") or "").strip().lstrip("@")

        def _is_int_like(value: str) -> bool:
            v = value[1:] if value.startswith("-") else value
            return bool(v) and v.isdigit()

        targets: list[str] = []

        def _push(value: str) -> None:
            if value and value not in targets:
                targets.append(value)

        if _is_int_like(chat_id):
            _push(chat_id)
        if _is_int_like(user_id):
            _push(user_id)
        _push(chat_id)
        _push(user_id)
        if username:
            _push(f"@{username}")

        if not targets:
            return {"ok": False, "error": "telegram_chat_id/telegram_user_id missing in conversation context"}

        from social.telegram_publisher import send_telegram_message
        safe_text = _safe_channel_text(text)
        last_error = "Unknown Telegram API error"
        for target in targets:
            result = await send_telegram_message(target, safe_text)
            if result.get("ok"):
                return {
                    "ok": True,
                    "message_id": result.get("message_id"),
                    "telegram_target": target,
                }
            last_error = str(result.get("error") or last_error)

        return {
            "ok": False,
            "error": last_error,
            "telegram_targets_tried": targets,
        }

    if channel_name == "whatsapp":
        phone = str(context.get("whatsapp_phone") or "").strip()
        if not phone:
            return {"ok": False, "error": "whatsapp_phone missing in conversation context"}
        from social.whatsapp_provider import get_whatsapp_provider
        provider = get_whatsapp_provider()
        return await provider.send_message(phone, text)

    if channel_name == "web":
        return {"ok": True, "channel": "web", "mode": "db_only"}

    return {"ok": False, "error": "Unsupported channel"}


def _conversation_display_identity(conv: Conversation, usr: User) -> Dict[str, Optional[str]]:
    ctx = conv.context if isinstance(conv.context, dict) else {}
    channel_name = _conversation_channel(conv)
    ext_id = _conversation_external_id(conv)
    is_internal = _is_internal_bot_user(usr)

    if channel_name == "telegram":
        tg_username = (ctx.get("telegram_username") or "").strip()
        tg_user_id = str(ctx.get("telegram_user_id") or "").strip() or None
        display_name = f"Telegram @{tg_username}" if tg_username else "Telegram User"
        display_contact = f"@{tg_username}" if tg_username else (ext_id or tg_user_id)
        return {
            "display_name": display_name,
            "display_contact": display_contact,
            "display_user_email": None if is_internal else usr.email,
            "display_user_phone": None if is_internal else usr.phone,
        }

    if channel_name == "whatsapp":
        profile_name = (ctx.get("profile_name") or "").strip()
        display_name = profile_name or "WhatsApp User"
        display_contact = ext_id or (None if is_internal else usr.phone)
        return {
            "display_name": display_name,
            "display_contact": display_contact,
            "display_user_email": None if is_internal else usr.email,
            "display_user_phone": ext_id or (None if is_internal else usr.phone),
        }

    # Web chat: keep account identity as primary.
    return {
        "display_name": usr.full_name or usr.email or "לקוח",
        "display_contact": usr.email or usr.phone,
        "display_user_email": usr.email,
        "display_user_phone": usr.phone,
    }


@router.get("/api/v1/admin/chats")
async def admin_list_chats(
    channel: str = "all",
    search: str = "",
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    channel = (channel or "all").strip().lower()

    filters = [Conversation.deleted_at.is_(None)]
    if channel == "telegram":
        filters.append(Conversation.context["telegram_chat_id"].astext.isnot(None))
    elif channel == "whatsapp":
        filters.append(Conversation.context["whatsapp_phone"].astext.isnot(None))
    elif channel == "web":
        filters.append(Conversation.context["telegram_chat_id"].astext.is_(None))
        filters.append(Conversation.context["whatsapp_phone"].astext.is_(None))

    search_value = (search or "").strip()
    if search_value:
        q = f"%{search_value}%"
        filters.append(or_(
            User.full_name.ilike(q),
            User.email.ilike(q),
            User.phone.ilike(q),
            Conversation.title.ilike(q),
            Conversation.context["telegram_chat_id"].astext.ilike(q),
            Conversation.context["telegram_username"].astext.ilike(q),
            Conversation.context["whatsapp_phone"].astext.ilike(q),
            Conversation.context["profile_name"].astext.ilike(q),
        ))

    total_stmt = (
        select(func.count(Conversation.id))
        .select_from(Conversation)
        .join(User, User.id == Conversation.user_id)
        .where(and_(*filters))
    )
    total = (await db.execute(total_stmt)).scalar() or 0

    rows_stmt = (
        select(Conversation, User)
        .join(User, User.id == Conversation.user_id)
        .where(and_(*filters))
        .order_by(Conversation.last_message_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(rows_stmt)).all()

    if not rows:
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "conversations": [],
        }

    conv_ids = [row[0].id for row in rows]
    msg_count_rows = (await db.execute(
        select(Message.conversation_id, func.count(Message.id))
        .where(and_(Message.conversation_id.in_(conv_ids), Message.deleted_at.is_(None)))
        .group_by(Message.conversation_id)
    )).all()
    msg_counts = {str(conversation_id): int(count) for conversation_id, count in msg_count_rows}

    last_msgs = (await db.execute(
        select(Message)
        .where(and_(Message.conversation_id.in_(conv_ids), Message.deleted_at.is_(None)))
        .order_by(Message.conversation_id.asc(), Message.created_at.desc())
    )).scalars().all()
    last_msg_by_conv: Dict[str, Message] = {}
    for msg in last_msgs:
        conv_id = str(msg.conversation_id)
        if conv_id not in last_msg_by_conv:
            last_msg_by_conv[conv_id] = msg

    out = []
    for conv, usr in rows:
        conv_id = str(conv.id)
        channel_name = _conversation_channel(conv)
        ext_id = _conversation_external_id(conv)
        ctx = conv.context if isinstance(conv.context, dict) else {}
        display = _conversation_display_identity(conv, usr)
        handoff = _conversation_handoff_meta(conv)
        last_msg = last_msg_by_conv.get(conv_id)
        preview = (last_msg.content or "") if last_msg else ""
        if len(preview) > 160:
            preview = f"{preview[:157]}..."

        out.append({
            "id": conv_id,
            "title": conv.title,
            "channel": channel_name,
            "external_id": ext_id,
            "telegram_username": ctx.get("telegram_username"),
            "profile_name": ctx.get("profile_name"),
            "current_agent": conv.current_agent,
            "is_active": conv.is_active,
            "admin_takeover_active": _conversation_takeover_active(conv),
            "started_at": conv.started_at,
            "last_message_at": conv.last_message_at,
            "message_count": msg_counts.get(conv_id, 0),
            "preview": preview,
            "last_message_role": last_msg.role if last_msg else None,
            "last_message_created_at": last_msg.created_at if last_msg else None,
            **handoff,
            "human_handoff_timeline": _conversation_handoff_timeline(conv),
            **display,
            "user": {
                "id": str(usr.id),
                "full_name": usr.full_name,
                "email": usr.email,
                "phone": usr.phone,
            },
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "conversations": out,
    }


@router.get("/api/v1/admin/chats/handoff-queue")
async def admin_handoff_queue(
    limit: int = 50,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    handoff_settings = await _load_handoff_settings(cat_db)
    rows = (await db.execute(
        select(Conversation, User)
        .join(User, User.id == Conversation.user_id)
        .where(Conversation.deleted_at.is_(None))
        .order_by(Conversation.last_message_at.desc())
        .limit(500)
    )).all()

    queue_items = []
    for conv, usr in rows:
        handoff = _conversation_handoff_meta(conv)
        if handoff.get("human_handoff_status") != "requested":
            continue
        if _conversation_takeover_active(conv):
            continue

        display = _conversation_display_identity(conv, usr)
        wait_seconds = handoff.get("human_handoff_wait_seconds")
        priority = int(handoff.get("human_handoff_priority") or 1)
        effective_priority, escalated = _handoff_priority_effective(priority, wait_seconds, handoff_settings)

        queue_items.append({
            "conversation_id": str(conv.id),
            "channel": _conversation_channel(conv),
            "external_id": _conversation_external_id(conv),
            "requested_at": handoff.get("human_handoff_requested_at"),
            "wait_seconds": wait_seconds,
            "priority": priority,
            "effective_priority": effective_priority,
            "priority_label": _handoff_priority_label(effective_priority),
            "escalated": escalated,
            "reason": handoff.get("human_handoff_reason"),
            **display,
        })

    queue_items.sort(
        key=lambda item: (
            -int(item.get("effective_priority") or item.get("priority") or 1),
            -(int(item.get("wait_seconds") or 0)),
        )
    )

    avg_handle = int(handoff_settings.get("avg_handle_minutes") or 6)
    eta_floor = int(handoff_settings.get("queue_eta_floor_seconds") or 60)
    for idx, item in enumerate(queue_items):
        position = idx + 1
        eta_seconds = max(eta_floor, (position - 1) * avg_handle * 60)
        item["queue_position"] = position
        item["queue_size"] = len(queue_items)
        item["eta_seconds"] = eta_seconds
        item["eta_minutes"] = max(1, int(round(eta_seconds / 60)))

    queue_items = queue_items[:limit]

    waits = [int(i.get("wait_seconds") or 0) for i in queue_items]
    pending_count = len(queue_items)
    sla_target = int(handoff_settings.get("sla_target_seconds") or 300)
    overdue_count = sum(1 for w in waits if w >= sla_target)
    urgent_count = sum(1 for i in queue_items if int(i.get("effective_priority") or i.get("priority") or 1) >= 3)
    escalated_count = sum(1 for i in queue_items if bool(i.get("escalated")))
    avg_wait = int(sum(waits) / len(waits)) if waits else 0
    eta_values = [int(i.get("eta_seconds") or 0) for i in queue_items if i.get("eta_seconds") is not None]
    avg_eta = int(sum(eta_values) / len(eta_values)) if eta_values else 0

    return {
        "summary": {
            "pending_count": pending_count,
            "urgent_count": urgent_count,
            "overdue_count": overdue_count,
            "escalated_count": escalated_count,
            "avg_wait_seconds": avg_wait,
            "max_wait_seconds": max(waits) if waits else 0,
            "avg_eta_seconds": avg_eta,
            "sla_target_seconds": sla_target,
        },
        "settings": handoff_settings,
        "items": queue_items,
    }


@router.get("/api/v1/admin/chats/handoff-settings")
async def admin_get_handoff_settings(
    current_user: User = Depends(get_current_admin_user),
    cat_db: AsyncSession = Depends(get_db),
):
    return {"settings": await _load_handoff_settings(cat_db)}


@router.put("/api/v1/admin/chats/handoff-settings")
async def admin_update_handoff_settings(
    body: dict,
    current_user: User = Depends(get_current_admin_user),
    cat_db: AsyncSession = Depends(get_db),
):
    incoming = body.get("settings") if isinstance(body, dict) and isinstance(body.get("settings"), dict) else body
    settings = _normalize_handoff_settings(incoming if isinstance(incoming, dict) else {})
    payload = json.dumps(settings, ensure_ascii=False)

    row = (
        await cat_db.execute(select(SystemSetting).where(SystemSetting.key == _HANDOFF_SETTINGS_KEY))
    ).scalar_one_or_none()
    if row:
        row.value = payload
        row.value_type = "json"
        row.is_public = False
        row.updated_by = current_user.id
        row.updated_at = datetime.utcnow()
    else:
        cat_db.add(
            SystemSetting(
                key=_HANDOFF_SETTINGS_KEY,
                value=payload,
                value_type="json",
                description="Team human handoff operations settings",
                is_public=False,
                updated_by=current_user.id,
                updated_at=datetime.utcnow(),
            )
        )

    await cat_db.commit()
    return {
        "ok": True,
        "settings": settings,
        "updated_at": datetime.utcnow().isoformat(),
    }


@router.get("/api/v1/admin/chats/usage")
async def admin_chat_usage(
    days: int = 30,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    from datetime import timedelta

    days = max(1, min(days, 365))
    cutoff = datetime.utcnow() - timedelta(days=days)

    conv_rows = (await db.execute(
        select(Conversation).where(and_(Conversation.deleted_at.is_(None), Conversation.last_message_at >= cutoff))
    )).scalars().all()

    if not conv_rows:
        return {
            "range_days": days,
            "summary": {
                "total_conversations": 0,
                "active_conversations": 0,
                "unique_clients": 0,
                "total_messages": 0,
                "by_channel": {"telegram": 0, "whatsapp": 0, "web": 0},
            },
            "daily": [],
        }

    conv_ids = [c.id for c in conv_rows]
    msg_rows = (await db.execute(
        select(Message).where(and_(Message.conversation_id.in_(conv_ids), Message.deleted_at.is_(None), Message.created_at >= cutoff))
    )).scalars().all()

    by_channel = {"telegram": 0, "whatsapp": 0, "web": 0}
    active_conversations = 0
    unique_clients: set[str] = set()
    for conv in conv_rows:
        ch = _conversation_channel(conv)
        by_channel[ch] = by_channel.get(ch, 0) + 1
        ext_id = _conversation_external_id(conv)
        if ch in ("telegram", "whatsapp"):
            unique_clients.add(f"{ch}:{ext_id or str(conv.user_id)}")
        else:
            unique_clients.add(str(conv.user_id))
        if conv.is_active:
            active_conversations += 1

    daily_map: Dict[str, Dict[str, Any]] = {}
    for msg in msg_rows:
        day = msg.created_at.strftime("%Y-%m-%d")
        if day not in daily_map:
            daily_map[day] = {
                "date": day,
                "messages": 0,
                "user_messages": 0,
                "assistant_messages": 0,
            }
        daily_map[day]["messages"] += 1
        if msg.role == "user":
            daily_map[day]["user_messages"] += 1
        elif msg.role == "assistant":
            daily_map[day]["assistant_messages"] += 1

    daily = [daily_map[k] for k in sorted(daily_map.keys())]
    return {
        "range_days": days,
        "summary": {
            "total_conversations": len(conv_rows),
            "active_conversations": active_conversations,
            "unique_clients": len(unique_clients),
            "total_messages": len(msg_rows),
            "by_channel": by_channel,
        },
        "daily": daily,
    }


@router.get("/api/v1/admin/chats/{conversation_id}/messages")
async def admin_get_chat_messages(
    conversation_id: str,
    limit: int = 200,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    limit = max(1, min(limit, 500))
    row = (await db.execute(
        select(Conversation, User)
        .join(User, User.id == Conversation.user_id)
        .where(and_(Conversation.id == conversation_id, Conversation.deleted_at.is_(None)))
    )).first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv, usr = row
    display = _conversation_display_identity(conv, usr)
    handoff = _conversation_handoff_meta(conv)
    msgs = (await db.execute(
        select(Message)
        .where(and_(Message.conversation_id == conversation_id, Message.deleted_at.is_(None)))
        .order_by(Message.created_at.asc())
        .limit(limit)
    )).scalars().all()

    return {
        "conversation": {
            "id": str(conv.id),
            "title": conv.title,
            "channel": _conversation_channel(conv),
            "external_id": _conversation_external_id(conv),
            "current_agent": conv.current_agent,
            "is_active": conv.is_active,
            "admin_takeover_active": _conversation_takeover_active(conv),
            "started_at": conv.started_at,
            "last_message_at": conv.last_message_at,
            **handoff,
            "human_handoff_timeline": _conversation_handoff_timeline(conv),
            **display,
            "user": {
                "id": str(usr.id),
                "full_name": usr.full_name,
                "email": usr.email,
                "phone": usr.phone,
            },
        },
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "agent_name": m.agent_name,
                "content": m.content,
                "content_type": m.content_type,
                "model_used": m.model_used,
                "tokens_used": m.tokens_used,
                "created_at": m.created_at,
            }
            for m in msgs
        ],
        "count": len(msgs),
    }


@router.put("/api/v1/admin/chats/{conversation_id}/status")
async def admin_set_chat_status(
    conversation_id: str,
    body: dict,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    row = (await db.execute(
        select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.deleted_at.is_(None)))
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_active = bool(body.get("is_active", True))
    row.is_active = is_active
    if not is_active:
        row.ended_at = datetime.utcnow()
    await db.commit()
    return {
        "id": str(row.id),
        "is_active": row.is_active,
        "ended_at": row.ended_at,
    }


@router.put("/api/v1/admin/chats/{conversation_id}/takeover")
async def admin_set_chat_takeover(
    conversation_id: str,
    body: dict,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    conv = (await db.execute(
        select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.deleted_at.is_(None)))
    )).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    was_active = _conversation_takeover_active(conv)
    active = bool(body.get("active", True))
    channel_name = _conversation_channel(conv)
    handoff_settings = await _load_handoff_settings(cat_db)
    ctx = dict(conv.context or {})
    ctx["admin_takeover_active"] = active
    ctx["admin_takeover_by"] = str(current_user.id) if active else None
    ctx["admin_takeover_at"] = datetime.utcnow().isoformat() if active else None

    if active:
        ctx["human_handoff_requested"] = False
        ctx["human_handoff_status"] = "active"
        ctx["human_handoff_lock_active"] = bool(handoff_settings.get("ai_lock_during_handoff", True))
        ctx["human_handoff_assigned_admin_id"] = str(current_user.id)
        ctx["human_handoff_assigned_name"] = (current_user.full_name or "").strip() or "נציג/ה אנושי/ת"
        ctx["human_handoff_assigned_role"] = _human_role_title(current_user)
        ctx["human_handoff_assigned_at"] = datetime.utcnow().isoformat()
        ctx["human_handoff_feedback_required"] = False
        ctx["human_handoff_feedback_submitted"] = False
    else:
        ask_feedback = bool(handoff_settings.get("feedback_required_on_resolve", True))
        if str(ctx.get("human_handoff_status") or "") == "active":
            ctx["human_handoff_status"] = "awaiting_feedback" if ask_feedback else "resolved"
            ctx["human_handoff_resolved_at"] = datetime.utcnow().isoformat()
            ctx["human_handoff_feedback_required"] = ask_feedback
            if ask_feedback:
                ctx["human_handoff_feedback_requested_at"] = datetime.utcnow().isoformat()
        ctx["human_handoff_requested"] = False
        ctx["human_handoff_lock_active"] = False
        ctx["human_handoff_assigned_admin_id"] = None
        ctx["human_handoff_assigned_name"] = None
        ctx["human_handoff_assigned_role"] = None

    conv.context = ctx

    intro_message = None
    intro_delivery: Dict[str, Any] | None = None
    closure_message = None
    closure_delivery: Dict[str, Any] | None = None
    if active and not was_active:
        intro_message = _build_takeover_intro(current_user)
        intro_delivery = await _send_channel_message(channel_name, ctx, intro_message)
        if intro_delivery.get("ok"):
            db.add(Message(
                conversation_id=conv.id,
                role="assistant",
                agent_name="human_takeover_intro",
                content=intro_message,
                content_type="text",
                model_used="handoff_intro",
                tokens_used=0,
                created_at=datetime.utcnow(),
            ))
            conv.last_message_at = datetime.utcnow()

    if (not active) and was_active:
        closure_message = _build_takeover_closure_prompt(
            current_user,
            bool(ctx.get("human_handoff_feedback_required")),
        )
        closure_delivery = await _send_channel_message(channel_name, ctx, closure_message)
        if closure_delivery.get("ok"):
            db.add(Message(
                conversation_id=conv.id,
                role="assistant",
                agent_name="human_takeover_closure",
                content=closure_message,
                content_type="text",
                model_used="handoff_closure",
                tokens_used=0,
                created_at=datetime.utcnow(),
            ))
            conv.last_message_at = datetime.utcnow()

    await db.commit()
    return {
        "conversation_id": str(conv.id),
        "admin_takeover_active": active,
        "channel": channel_name,
        "human_handoff_status": str(ctx.get("human_handoff_status") or "none"),
        "intro_message_sent": bool(intro_delivery and intro_delivery.get("ok")),
        "intro_delivery": intro_delivery,
        "closure_message_sent": bool(closure_delivery and closure_delivery.get("ok")),
        "closure_delivery": closure_delivery,
    }


@router.put("/api/v1/admin/chats/{conversation_id}")
async def admin_update_chat(
    conversation_id: str,
    body: dict,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    conv = (await db.execute(
        select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.deleted_at.is_(None)))
    )).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    title = body.get("title")
    if title is not None:
        cleaned = str(title).strip()
        conv.title = cleaned[:255] if cleaned else conv.title

    await db.commit()
    return {
        "id": str(conv.id),
        "title": conv.title,
        "updated": True,
    }


@router.delete("/api/v1/admin/chats/{conversation_id}")
async def admin_delete_chat(
    conversation_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    conv = (await db.execute(
        select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.deleted_at.is_(None)))
    )).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    now = datetime.utcnow()
    conv.deleted_at = now
    conv.is_active = False
    conv.ended_at = now
    await db.commit()
    return {
        "id": str(conv.id),
        "deleted": True,
    }


@router.post("/api/v1/admin/chats/{conversation_id}/reply")
async def admin_reply_chat(
    conversation_id: str,
    body: dict,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    conv = (await db.execute(
        select(Conversation).where(and_(Conversation.id == conversation_id, Conversation.deleted_at.is_(None)))
    )).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    channel_name = _conversation_channel(conv)
    context = conv.context if isinstance(conv.context, dict) else {}

    if not _conversation_takeover_active(conv):
        raise HTTPException(status_code=409, detail="Enable admin takeover before sending manual replies")

    if channel_name not in {"telegram", "whatsapp", "web"}:
        raise HTTPException(status_code=400, detail="Manual outbound is supported for telegram/whatsapp/web only")

    outbound_result = await _send_channel_message(channel_name, context, message)

    if not outbound_result.get("ok"):
        err = outbound_result.get("error") or "Failed to send message"
        raise HTTPException(status_code=502, detail=err)

    db.add(Message(
        conversation_id=conv.id,
        role="assistant",
        agent_name="admin_manual",
        content=message,
        content_type="text",
        model_used="admin_manual_reply",
        tokens_used=0,
        created_at=datetime.utcnow(),
    ))
    conv.last_message_at = datetime.utcnow()
    await db.commit()

    return {
        "ok": True,
        "conversation_id": str(conv.id),
        "channel": channel_name,
        "delivery": outbound_result,
    }


# ==============================================================================
# SUPER ADMIN — SETTINGS
# ==============================================================================

@router.get("/api/v1/admin/super/settings")
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


@router.put("/api/v1/admin/super/settings/{key}")
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


@router.post("/api/v1/admin/super/settings")
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


@router.delete("/api/v1/admin/super/settings/{key}")
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


# ==============================================================================
# SUPER ADMIN — USERS
# ==============================================================================

@router.get("/api/v1/admin/super/users")
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


@router.put("/api/v1/admin/super/users/{user_id}/role")
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

    # Enforce single super admin: block promoting a second user
    if body.is_super_admin and user.id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only one super admin is allowed. Cannot grant super admin to another user.",
        )

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


# ==============================================================================
# ADMIN — USERS CRUD
# ==============================================================================

@router.post("/api/v1/admin/users")
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

@router.put("/api/v1/admin/users/{user_id}")
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


@router.post("/api/v1/admin/users/{user_id}/reset-login")
async def reset_user_login_failures(user_id: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.failed_login_count = 0
    user.locked_until = None
    await db.commit()
    return {"message": "Login failures reset"}


@router.delete("/api/v1/admin/users/{user_id}")
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


# ==============================================================================
# ADMIN — SUPPLIERS CRUD
# ==============================================================================

@router.get("/api/v1/admin/suppliers")
async def get_admin_suppliers(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
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


@router.post("/api/v1/admin/suppliers")
async def create_supplier(data: SupplierCreate, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
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


@router.put("/api/v1/admin/suppliers/{supplier_id}")
async def update_supplier(supplier_id: str, body: SupplierUpdateBody = None, is_active: Optional[bool] = None, priority: Optional[int] = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
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


@router.delete("/api/v1/admin/suppliers/{supplier_id}")
async def delete_supplier(supplier_id: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    await db.delete(supplier)
    await db.commit()
    return {"message": "Supplier deleted"}


@router.post("/api/v1/admin/suppliers/{supplier_id}/sync")
async def sync_supplier_catalog(supplier_id: str, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    return {"message": "Sync started", "job_id": str(uuid.uuid4())}


# ==============================================================================
# ADMIN — APPROVALS
# ==============================================================================

@router.get("/api/v1/admin/approvals", tags=["Admin"])
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


@router.post("/api/v1/admin/approvals/{approval_id}/resolve", tags=["Admin"])
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
# ADMIN — ORDERS
# ==============================================================================

@router.get("/api/v1/admin/orders")
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


@router.put("/api/v1/admin/orders/{order_id}/status")
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


# ==============================================================================
# SOCIAL POSTS  /api/v1/admin/social/*
# ==============================================================================

@router.post("/api/v1/admin/social/posts", status_code=201)
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


@router.get("/api/v1/admin/social/posts")
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


@router.put("/api/v1/admin/social/posts/{post_id}")
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


@router.delete("/api/v1/admin/social/posts/{post_id}")
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


@router.post("/api/v1/admin/social/publish/{post_id}")
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


@router.post("/api/v1/admin/social/telegram/webhook")
async def configure_telegram_webhook(
    webhook_url: str,
    current_user: User = Depends(get_current_admin_user),
):
    from social.telegram_publisher import set_telegram_webhook

    if not webhook_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="webhook_url must be HTTPS")

    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip() or None
    result = await set_telegram_webhook(webhook_url, secret_token=secret)
    if not result.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=f"Telegram webhook setup failed: {result.get('error', 'unknown error')}",
        )

    return {
        "message": "Telegram webhook configured",
        "webhook_url": webhook_url,
        "secret_configured": bool(secret),
        "telegram": result,
    }


@router.get("/api/v1/admin/social/analytics")
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


@router.post("/api/v1/admin/social/generate-content")
async def generate_social_content(topic: str, platform: str, tone: str = "professional", current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    from BACKEND_AI_AGENTS import get_agent
    agent = get_agent("social_media_manager_agent")
    content = await agent.generate_post(topic, platform, tone)
    return {"content": content, "status": "pending_approval"}


# ==============================================================================
# ANALYTICS  /api/v1/admin/analytics/*
# ==============================================================================

@router.get("/api/v1/admin/analytics/dashboard")
async def get_analytics_dashboard(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    users_count = (await db.execute(select(func.count(User.id)))).scalar()
    orders_count = (await db.execute(select(func.count(Order.id)))).scalar()
    revenue = (await db.execute(select(func.sum(Order.total_amount)).where(Order.status.in_(["paid", "processing", "shipped", "delivered"])))).scalar() or 0
    return {"users": users_count, "orders": orders_count, "revenue": float(revenue), "period": "all_time"}


@router.get("/api/v1/admin/analytics/sales")
async def get_sales_analytics(start_date=None, end_date=None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    from datetime import date, timedelta
    from typing import Optional as _Opt
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

    data = []
    current = d_start
    while current <= d_end:
        ds = str(current)
        data.append({"date": ds, "orders": rows.get(ds, {}).get("orders", 0), "revenue": rows.get(ds, {}).get("revenue", 0.0)})
        current += timedelta(days=1)

    return {"data": data}


@router.get("/api/v1/admin/analytics/users")
async def get_user_analytics(current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    total = (await db.execute(select(func.count(User.id)))).scalar()
    verified = (await db.execute(select(func.count(User.id)).where(User.is_verified == True))).scalar()
    return {"total_users": total, "verified_users": verified}


# ==============================================================================
# AGENTS CONTROL PANEL  /api/v1/admin/agents
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

RUNTIME_TOKEN_PROVIDERS = {
    "huggingface": {
        "provider": "huggingface",
        "label": "Hugging Face",
        "env_key": "HF_TOKEN",
        "setting_key": "runtime_hf_token",
        "description": "Runtime HuggingFace token override for AI agents",
        "module_attr": "HF_TOKEN",
    },
    "cerebras": {
        "provider": "cerebras",
        "label": "Cerebras",
        "env_key": "CEREBRAS_API_KEY",
        "setting_key": "runtime_cerebras_api_key",
        "description": "Runtime Cerebras API key override for AI text generation",
        "module_attr": "CEREBRAS_API_KEY",
    },
    "gemini": {
        "provider": "gemini",
        "label": "Google Gemini",
        "env_key": "GEMINI_API_KEY",
        "setting_key": "runtime_gemini_api_key",
        "description": "Runtime Gemini API key override for vision analysis",
        "module_attr": "GEMINI_API_KEY",
    },
    "groq": {
        "provider": "groq",
        "label": "Groq",
        "env_key": "GROQ_API_KEY",
        "setting_key": "runtime_groq_api_key",
        "description": "Runtime Groq API key override for audio transcription",
        "module_attr": "GROQ_API_KEY",
    },
}


def _normalize_provider_name(provider: Optional[str]) -> str:
    return (provider or "huggingface").strip().lower()


def _provider_config(provider: Optional[str]) -> Dict[str, str]:
    normalized = _normalize_provider_name(provider)
    cfg = RUNTIME_TOKEN_PROVIDERS.get(normalized)
    if not cfg:
        supported = ", ".join(sorted(RUNTIME_TOKEN_PROVIDERS.keys()))
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{normalized}'. Supported: {supported}")
    return cfg


def _mask_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    if len(token) > 8:
        return f"{token[:4]}...{token[-4:]}"
    return "****"


def _apply_runtime_provider_token(cfg: Dict[str, str], token: str) -> None:
    env_key = cfg["env_key"]
    module_attr = cfg.get("module_attr")
    clean_token = (token or "").strip()

    if clean_token:
        os.environ[env_key] = clean_token
    else:
        os.environ.pop(env_key, None)

    try:
        import hf_client
        if module_attr:
            setattr(hf_client, module_attr, clean_token)
    except Exception:
        # Non-fatal: env var is still updated and will be reloaded on process restart.
        pass


async def _runtime_provider_statuses(db: AsyncSession) -> Dict[str, Dict[str, Any]]:
    setting_keys = [cfg["setting_key"] for cfg in RUNTIME_TOKEN_PROVIDERS.values()]
    rows = (await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(setting_keys))
    )).scalars().all()
    row_by_key = {row.key: row for row in rows}

    statuses: Dict[str, Dict[str, Any]] = {}
    for provider, cfg in RUNTIME_TOKEN_PROVIDERS.items():
        token = (os.getenv(cfg["env_key"], "") or "").strip()
        setting_row = row_by_key.get(cfg["setting_key"])
        persisted_token = ((setting_row.value if setting_row else "") or "").strip()
        persisted_updated_at = setting_row.updated_at.isoformat() if (setting_row and setting_row.updated_at) else None
        statuses[provider] = {
            "provider": provider,
            "label": cfg["label"],
            "configured": bool(token),
            "persisted": bool(persisted_token),
            "persisted_updated_at": persisted_updated_at,
            "token_preview": _mask_token(token),
            "scope": "runtime+db",
            "env_key": cfg["env_key"],
        }
    return statuses

@router.get("/api/v1/admin/agents")
async def list_agents(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from BACKEND_AI_AGENTS import AGENT_MAP
    import os as _os
    cerebras_token = _os.getenv("CEREBRAS_API_KEY", "")
    ai_status = "active" if cerebras_token else "mocked"
    actual_model = _os.getenv("CEREBRAS_TEXT_MODEL", _os.getenv("AGENTS_DEFAULT_MODEL", "unknown"))

    channel_models = {
        "web": _os.getenv("WEB_AI_MODEL", _os.getenv("AGENTS_DEFAULT_MODEL", actual_model)),
        "whatsapp": _os.getenv("WHATSAPP_AI_MODEL", _os.getenv("AGENTS_DEFAULT_MODEL", actual_model)),
        "telegram": _os.getenv("TELEGRAM_AI_MODEL", _os.getenv("AGENTS_DEFAULT_MODEL", actual_model)),
    }

    agents = []
    for name, meta in AGENTS_METADATA.items():
        # Use live model from running agent if loaded, else env model
        live_model = actual_model
        live_prompt = meta.get("system_prompt", "")
        live_agent_name = meta.get("persona", "")
        if name in AGENT_MAP:
            raw = getattr(AGENT_MAP[name], "model", actual_model)
            live_model = raw
            live_prompt = getattr(AGENT_MAP[name], "system_prompt", live_prompt)
            live_agent_name = getattr(AGENT_MAP[name], "agent_name", live_agent_name)
        assigned_tasks = meta.get("assigned_tasks") or meta.get("capabilities") or []
        agents.append({
            "name": name,
            **meta,
            "persona": live_agent_name or meta.get("persona"),
            "model": live_model,
            "system_prompt": live_prompt,
            "assigned_tasks": assigned_tasks,
            "ai_status": ai_status,
            "is_loaded": name in AGENT_MAP,
        })

    models_in_use = sorted({a.get("model") for a in agents if a.get("model")})
    for ch_model in channel_models.values():
        if ch_model:
            models_in_use.append(ch_model)
    models_in_use = sorted(set(models_in_use))

    try:
        provider_statuses = list((await _runtime_provider_statuses(db)).values())
    except Exception:
        provider_statuses = []

    return {
        "agents": agents,
        "total": len(agents),
        "ai_status": ai_status,
        "hf_configured": bool(_os.getenv("HF_TOKEN", "")),
        "cerebras_configured": bool(cerebras_token),
        "models_in_use": models_in_use,
        "channel_models": channel_models,
        "runtime_providers": provider_statuses,
    }


@router.get("/api/v1/admin/agents/runtime/token")
async def get_agents_runtime_token_status(
    provider: str = Query("huggingface"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = _provider_config(provider)
    statuses = await _runtime_provider_statuses(db)
    return statuses[cfg["provider"]]


@router.get("/api/v1/admin/agents/runtime/tokens")
async def get_agents_runtime_tokens_status(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    statuses = await _runtime_provider_statuses(db)
    providers = [statuses[k] for k in sorted(statuses.keys())]
    return {
        "providers": providers,
        "total": len(providers),
    }


@router.put("/api/v1/admin/agents/runtime/token")
async def update_agents_runtime_token(
    body: dict,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = _provider_config(body.get("provider"))
    token = (body.get("token") or "").strip()
    persist = bool(body.get("persist", True))
    clear = bool(body.get("clear", False))
    if not token and not clear:
        raise HTTPException(status_code=400, detail="token is required")

    _apply_runtime_provider_token(cfg, "" if clear else token)

    if persist:
        row = (await db.execute(select(SystemSetting).where(SystemSetting.key == cfg["setting_key"]))).scalar_one_or_none()
        if row:
            row.value = "" if clear else token
            row.value_type = "string"
            row.description = cfg["description"]
            row.updated_at = datetime.utcnow()
        else:
            db.add(SystemSetting(
                key=cfg["setting_key"],
                value="" if clear else token,
                value_type="string",
                description=cfg["description"],
                is_public=False,
            ))
        await db.commit()

    statuses = await _runtime_provider_statuses(db)
    selected = statuses[cfg["provider"]]
    return {
        "message": f"{cfg['label']} token updated",
        **selected,
    }


@router.post("/api/v1/admin/agents/{agent_name}/test")
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


@router.put("/api/v1/admin/agents/{agent_name}")
async def update_agent(
    agent_name: str,
    body: dict,
    current_user: User = Depends(get_current_admin_user),
):
    if agent_name not in AGENTS_METADATA:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
    allowed = {
        "display_name", "persona", "name_he", "description", "description_he",
        "model", "temperature", "capabilities", "enabled", "system_prompt",
        "agent_name", "assigned_tasks",
    }
    for k, v in body.items():
        if k in allowed:
            if k in {"capabilities", "assigned_tasks"} and isinstance(v, list):
                v = [str(x).strip() for x in v if str(x).strip()]
            AGENTS_METADATA[agent_name][k] = v
    from BACKEND_AI_AGENTS import _agents
    if agent_name in _agents:
        runtime_agent = _agents[agent_name]
        if "model" in body:
            runtime_agent.model = body["model"]
        if "temperature" in body:
            runtime_agent.temperature = float(body["temperature"])
        if "system_prompt" in body:
            runtime_agent.system_prompt = body["system_prompt"]
        if "agent_name" in body:
            runtime_agent.agent_name = body["agent_name"]
        elif "persona" in body:
            runtime_agent.agent_name = body["persona"]

    assigned_tasks = AGENTS_METADATA[agent_name].get("assigned_tasks") or AGENTS_METADATA[agent_name].get("capabilities") or []
    return {
        "agent": agent_name,
        **AGENTS_METADATA[agent_name],
        "assigned_tasks": assigned_tasks,
    }


# ==============================================================================
# PARTS IMPORT  /api/v1/admin/parts/import
# ==============================================================================

@router.post("/api/v1/admin/parts/import")
async def import_parts_excel(
    file,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Import parts from Excel (.xlsx / .xls) file."""
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
# JOB FAILURES  /api/v1/admin/job-failures
# ==============================================================================

@router.get("/api/v1/admin/job-failures")
async def list_job_failures(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
    status: Optional[str] = None,
    job_name: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    from BACKEND_DATABASE_MODELS import JobFailure

    limit = min(limit, 1000)
    query = select(JobFailure)

    if status:
        query = query.where(JobFailure.status == status)
    if job_name:
        query = query.where(JobFailure.job_name == job_name)

    total = (await db.execute(select(func.count(JobFailure.id)).filter(query.whereclause if hasattr(query, 'whereclause') else None))).scalar() or 0

    query = query.order_by(JobFailure.created_at.desc()).limit(limit).offset(offset)
    results = (await db.execute(query)).scalars().all()

    failures = []
    for f in results:
        failures.append({
            "id": str(f.id),
            "job_name": f.job_name,
            "status": f.status,
            "attempts": f.attempts,
            "error": f.error[:200] if f.error else None,
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


@router.post("/api/v1/admin/job-failures/{job_id}/retry")
async def retry_job_failure(
    job_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
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
# PRICE SYNC + FULFILL-STUCK  /api/v1/admin/price-sync  /api/v1/admin/orders/fulfill-stuck
# ==============================================================================

PRICE_SYNC_INTERVAL_H = int(__import__('os').getenv("PRICE_SYNC_INTERVAL_H", "24"))


@router.get("/api/v1/admin/price-sync/status")
async def price_sync_status(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import SystemLog
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


@router.post("/api/v1/admin/orders/fulfill-stuck", tags=["Admin – Orders"])
async def admin_fulfill_stuck_orders(db: AsyncSession = Depends(get_pii_db), current_user: User = Depends(get_current_admin_user)):
    from routes.utils import trigger_supplier_fulfillment as _tsf
    result = await db.execute(select(Order).where(Order.status.in_(["paid", "processing"])))
    stuck = result.scalars().all()
    if not stuck:
        return {"message": "No stuck orders found", "count": 0}
    await _tsf(stuck, db)
    await db.commit()
    return {"message": f"Fulfillment triggered for {len(stuck)} order(s)", "count": len(stuck), "orders": [o.order_number for o in stuck]}


@router.post("/api/v1/admin/price-sync/run")
async def trigger_price_sync(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from BACKEND_AI_AGENTS import SupplierManagerAgent
    from BACKEND_DATABASE_MODELS import async_session_factory as _asf

    async def _run():
        async with _asf() as session:
            try:
                agent = SupplierManagerAgent()
                await agent.sync_prices(session)
            except Exception as e:
                print(f"[PriceSync manual] error: {e}")

    asyncio.create_task(_guarded_task(_run()))
    return {"status": "started", "message": "Price sync triggered in background"}


# ==============================================================================
# SCRAPER CONTROLS  /api/v1/admin/scraper/*
# ==============================================================================

@router.get("/api/v1/admin/scraper/status", tags=["Admin – Scraper"])
async def scraper_status(
    current_user=Depends(get_current_admin_user),
):
    from catalog_scraper import get_scraper_status
    return get_scraper_status()


@router.post("/api/v1/admin/scraper/run", tags=["Admin – Scraper"])
async def scraper_run_now(
    batch_size: int = 100,
    current_user=Depends(get_current_admin_user),
):
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


@router.post("/api/v1/admin/scraper/run-part/{part_id}", tags=["Admin – Scraper"])
async def scraper_run_one_part(
    part_id: str,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text as _text
    from catalog_scraper import (
        scrape_autodoc, scrape_ebay_motors, scrape_aliexpress,
        db_update_supplier_part, db_log, SUPPLIER_TOOL_MAP, FALLBACK_TOOLS,
        SCRAPE_REQUEST_DELAY,
    )

    usd_to_ils_rate = await get_usd_to_ils_rate(db)

    rows = (await db.execute(
        _text("""
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
                price_usd=round(new_ils / usd_to_ils_rate, 2),
            )
            results.append({"supplier": row.supplier_name, "old_price": old, "new_price": new_ils, "action": "updated"})
        else:
            results.append({"supplier": row.supplier_name, "old_price": float(row.price_ils or 0), "action": "no_data"})

    await db_log(db, "INFO", f"Manual scrape of part {part_id}: {len(results)} supplier rows processed")
    return {"part_id": part_id, "sku": rows[0].sku, "supplier_results": results}


@router.post("/api/v1/admin/scraper/discover", tags=["Admin – Scraper"])
async def scraper_discover_all(
    target: int = 200,
    per_run: int = 5,
    current_user=Depends(get_current_admin_user),
):
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


@router.post("/api/v1/admin/scraper/discover/{brand}", tags=["Admin – Scraper"])
async def scraper_discover_brand(
    brand: str,
    target: int = 200,
    current_user=Depends(get_current_admin_user),
):
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
# DB UPDATE AGENT  /api/v1/admin/db-agent/*
# ==============================================================================

@router.get("/api/v1/admin/db-agent/status", tags=["Admin – DB Agent"])
async def db_agent_status(
    current_user=Depends(get_current_admin_user),
):
    from db_update_agent import get_last_report, is_running, TASK_REGISTRY
    return {
        "running": is_running(),
        "available_tasks": list(TASK_REGISTRY.keys()),
        "last_report": get_last_report(),
    }


@router.post("/api/v1/admin/db-agent/run", tags=["Admin – DB Agent"])
async def db_agent_run_all(
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from db_update_agent import run_all_tasks, is_running
    from BACKEND_DATABASE_MODELS import async_session_factory as _asf

    if is_running():
        return {"status": "already_running", "message": "DB agent is already running"}

    async def _run():
        async with _asf() as bg_db:
            try:
                await run_all_tasks(bg_db)
            except Exception as exc:
                print(f"[DB Agent] background run error: {exc}")

    asyncio.create_task(_guarded_task(_run()))
    return {"status": "started", "message": "All DB agent tasks triggered in the background"}


@router.post("/api/v1/admin/db-agent/run/{task_name}", tags=["Admin – DB Agent"])
async def db_agent_run_task(
    task_name: str,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from db_update_agent import run_task, TASK_REGISTRY

    if task_name not in TASK_REGISTRY:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown task '{task_name}'. "
                   f"Valid tasks: {list(TASK_REGISTRY.keys())}",
        )

    result = await run_task(task_name, db)
    return result
