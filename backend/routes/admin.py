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
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID as _UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, select

from BACKEND_DATABASE_MODELS import (
    AuditLog, ApprovalQueue, Notification, Order, PartsCatalog, Payment,
    Return, SocialPost, Supplier, SystemSetting, User,
    get_db, get_pii_db,
)
from BACKEND_AUTH_SECURITY import (
    get_current_admin_user, get_current_super_admin,
    hash_password, publish_notification,
)
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
)

router = APIRouter()

BLOCKED_SETTINGS = {
    "jwt_secret", "jwt_refresh_secret", "stripe_secret_key",
    "stripe_webhook_secret", "hf_token", "database_url",
    "database_pii_url", "redis_url", "encryption_key",
    "twilio_auth_token", "sendgrid_api_key",
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


@router.get("/api/v1/admin/users")
async def get_admin_users(current_user: User = Depends(get_current_admin_user), limit: int = 100, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(User).order_by(User.created_at.desc()).limit(limit))
    users = result.scalars().all()
    return {"users": [{"id": str(u.id), "email": u.email, "full_name": u.full_name, "phone": u.phone, "is_verified": u.is_verified, "is_admin": u.is_admin, "is_active": u.is_active, "role": u.role, "failed_login_count": u.failed_login_count, "locked_until": u.locked_until.isoformat() if u.locked_until else None, "created_at": u.created_at} for u in users]}


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
