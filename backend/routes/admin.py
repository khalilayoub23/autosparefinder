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
from datetime import datetime, date
from typing import Any, Dict, Optional
from uuid import UUID as _UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
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

@router.get("/api/v1/admin/agents")
async def list_agents(current_user: User = Depends(get_current_admin_user)):
    from BACKEND_AI_AGENTS import AGENT_MAP
    import os as _os
    hf_token = _os.getenv("HF_TOKEN", "")
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
    allowed = {"display_name", "persona", "name_he", "description", "description_he", "model", "temperature", "capabilities", "enabled"}
    for k, v in body.items():
        if k in allowed:
            AGENTS_METADATA[agent_name][k] = v
    from BACKEND_AI_AGENTS import _agents
    if agent_name in _agents:
        if "model" in body:
            _agents[agent_name].model = body["model"]
        if "temperature" in body:
            _agents[agent_name].temperature = float(body["temperature"])
    return {"agent": agent_name, **AGENTS_METADATA[agent_name]}


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
        ILS_PER_USD, SCRAPE_REQUEST_DELAY,
    )

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
                price_usd=round(new_ils / ILS_PER_USD, 2),
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
