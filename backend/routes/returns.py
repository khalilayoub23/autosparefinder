"""Returns — all /api/v1/returns* endpoints extracted from BACKEND_API_ROUTES.py."""

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import (
    get_pii_db, Order, OrderItem, Return, ApprovalQueue, Notification, User
)
from BACKEND_AUTH_SECURITY import (
    get_current_user, get_current_admin_user, publish_notification
)
from routes.schemas import ReturnRequest
from invoice_generator import generate_credit_note_pdf
import asyncio
from routes.utils import _guarded_task

router = APIRouter()

_FULL_REFUND_REASONS = {"defective", "wrong_part", "damaged_in_transit"}
_RETURN_WINDOW_DAYS = int(os.getenv("RETURN_WINDOW_DAYS", "14"))

@router.post("/api/v1/returns", status_code=status.HTTP_201_CREATED)
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


@router.get("/api/v1/returns")
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
            "item_shipped_at": r.item_shipped_at,
            "supplier_confirmed_at": r.supplier_confirmed_at,
            "refund_issued_at": r.refund_issued_at,
        }
        for r in returns
    ]}


@router.get("/api/v1/returns/{return_id}")
async def get_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    return {
        "id": str(ret.id),
        "return_number": ret.return_number,
        "status": ret.status,
        "reason": ret.reason,
        "description": ret.description,
        "original_amount": float(ret.original_amount),
        "refund_amount": float(ret.refund_amount) if ret.refund_amount else None,
        "refund_percentage": float(ret.refund_percentage) if ret.refund_percentage else None,
        "handling_fee": float(ret.handling_fee) if ret.handling_fee else None,
        "tracking_number": ret.tracking_number,
        "requested_at": ret.requested_at,
        "approved_at": ret.approved_at,
        "item_shipped_at": ret.item_shipped_at,
        "supplier_confirmed_at": ret.supplier_confirmed_at,
        "refund_issued_at": ret.refund_issued_at,
        "supplier_notes": ret.supplier_notes,
    }


@router.post("/api/v1/returns/{return_id}/track")
async def track_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    return {"return_number": ret.return_number, "status": ret.status, "tracking_number": ret.tracking_number}


@router.put("/api/v1/returns/{return_id}/cancel")
async def cancel_return(return_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id)))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status not in ["pending", "pending_review", "approved"]:
        raise HTTPException(status_code=400, detail="Cannot cancel return in current status")
    ret.status = "cancelled"
    await db.commit()
    return {"message": "Return cancelled"}


@router.get("/api/v1/returns/{return_id}/invoice")
async def get_return_invoice(
    return_id: str,
    inline: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Generate and stream a Hebrew PDF credit note for an approved return."""
    ret_res = await db.execute(
        select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id))
    )
    ret = ret_res.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status not in ("approved", "item_in_transit", "supplier_confirmed", "refund_issued", "completed"):
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


@router.post("/api/v1/returns/{return_id}/approve")
async def approve_return(return_id: str, refund_percentage: int = None, current_user: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Return).where(Return.id == return_id))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status not in ["pending", "pending_review"]:
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
        f"הזיכוי של ₪{float(ret.refund_amount):.2f} יועבר לכרטיס האשראי שלך לאחר שהספק יאשר קבלת החלק בחזרה."
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


@router.post("/api/v1/returns/{return_id}/reject", tags=["Returns"])
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
    if ret.status not in ["pending", "pending_review", "approved"]:
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


@router.post("/api/v1/returns/{return_id}/ship", tags=["Returns"])
async def customer_ship_return(
    return_id: str,
    tracking_number: Optional[str] = None,
    tracking_url: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Customer confirms they have shipped the item back to the supplier."""
    result = await db.execute(
        select(Return).where(and_(Return.id == return_id, Return.user_id == current_user.id))
    )
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status != "approved":
        raise HTTPException(status_code=400, detail="Return must be in 'approved' status before marking as shipped")

    ret.status = "item_in_transit"
    ret.item_shipped_at = datetime.utcnow()
    if tracking_number:
        ret.tracking_number = tracking_number
    if tracking_url:
        ret.tracking_url = tracking_url

    _title = f"📬 פריט נשלח בחזרה — {ret.return_number}"
    _msg = (
        f"אישרת שהפריט נשלח בחזרה לספק עבור בקשה {ret.return_number}.\n"
        "הזיכוי יועבר אחרי שהספק יאשר קבלת הפריט."
    )
    db.add(Notification(
        user_id=ret.user_id, type="return_update", title=_title, message=_msg,
        data={"return_number": ret.return_number, "tracking_number": tracking_number},
    ))
    asyncio.create_task(_guarded_task(publish_notification(str(ret.user_id), {"type": "return_update", "title": _title, "message": _msg})))

    await db.commit()
    return {"message": "Shipment confirmed", "status": ret.status, "return_number": ret.return_number}


@router.post("/api/v1/returns/{return_id}/supplier-confirm", tags=["Returns"])
async def supplier_confirm_return(
    return_id: str,
    supplier_notes: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Admin: mark that the supplier confirmed receipt of the returned part.
    Unlocks the issue-refund step — refund is NOT sent yet."""
    result = await db.execute(select(Return).where(Return.id == return_id))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status != "item_in_transit":
        raise HTTPException(
            status_code=400,
            detail="Return must be 'item_in_transit' before supplier can confirm",
        )

    ret.status = "supplier_confirmed"
    ret.supplier_confirmed_at = datetime.utcnow()
    if supplier_notes:
        ret.supplier_notes = supplier_notes

    _title = f"✅ הספק אישר קבלת הפריט — {ret.return_number}"
    _msg = (
        f"הספק אישר שקיבל את הפריט בחזרה עבור בקשה {ret.return_number}.\n"
        f"הזיכוי של ₪{float(ret.refund_amount):.2f} יועבר לכרטיס האשראי שלך תוך 3-5 ימי עסקים."
    )
    db.add(Notification(
        user_id=ret.user_id, type="return_update", title=_title, message=_msg,
        data={"return_number": ret.return_number, "refund_amount": float(ret.refund_amount)},
    ))
    asyncio.create_task(_guarded_task(publish_notification(str(ret.user_id), {"type": "return_update", "title": _title, "message": _msg})))

    await db.commit()
    return {"message": "Supplier confirmation recorded", "status": ret.status, "return_number": ret.return_number}


@router.post("/api/v1/returns/{return_id}/issue-refund", tags=["Returns"])
async def issue_refund(
    return_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Admin: issue the actual payment refund to the customer.
    Only allowed after supplier_confirmed. Marks Payment as refunded and return as refund_issued."""
    from BACKEND_DATABASE_MODELS import Payment
    result = await db.execute(select(Return).where(Return.id == return_id))
    ret = result.scalar_one_or_none()
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.status != "supplier_confirmed":
        raise HTTPException(
            status_code=400,
            detail="Refund can only be issued after the supplier has confirmed receipt of the returned part",
        )
    if not ret.refund_amount or float(ret.refund_amount) <= 0:
        raise HTTPException(status_code=400, detail="No refund amount calculated — approve the return first")

    # Mark the Payment as refunded
    pay_result = await db.execute(
        select(Payment).where(Payment.order_id == ret.order_id)
        .order_by(Payment.id.desc()).limit(1)
    )
    payment = pay_result.scalar_one_or_none()
    if payment and payment.status == "paid":
        payment.status = "refunded"
        payment.refunded_at = datetime.utcnow()
        payment.refund_amount = ret.refund_amount
        payment.refund_reason = ret.reason

    ret.status = "refund_issued"
    ret.refund_issued_at = datetime.utcnow()
    ret.completed_at = datetime.utcnow()

    _title = f"💳 הזיכוי הועבר — {ret.return_number}"
    _msg = (
        f"זיכוי של ₪{float(ret.refund_amount):.2f} הועבר לכרטיס האשראי שלך עבור בקשה {ret.return_number}.\n"
        "הזיכוי יופיע בחשבונך תוך 3-5 ימי עסקים בהתאם לחברת האשראי."
    )
    db.add(Notification(
        user_id=ret.user_id, type="return_update", title=_title, message=_msg,
        data={"return_number": ret.return_number, "refund_amount": float(ret.refund_amount)},
    ))
    asyncio.create_task(_guarded_task(publish_notification(str(ret.user_id), {"type": "return_update", "title": _title, "message": _msg})))

    await db.commit()
    return {
        "message": "Refund issued successfully",
        "return_number": ret.return_number,
        "refund_amount": float(ret.refund_amount),
        "status": ret.status,
    }


@router.get("/api/v1/admin/returns", tags=["Returns"])
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
            "refund_percentage": float(r.refund_percentage) if r.refund_percentage else None,
            "requested_at": r.requested_at,
            "approved_at": r.approved_at,
            "item_shipped_at": r.item_shipped_at,
            "supplier_confirmed_at": r.supplier_confirmed_at,
            "refund_issued_at": r.refund_issued_at,
            "supplier_notes": r.supplier_notes,
        }
        for r in returns
    ]}
