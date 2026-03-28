"""Orders — all /api/v1/orders/* endpoints extracted from BACKEND_API_ROUTES.py."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime
import uuid
import os
import asyncio

from BACKEND_DATABASE_MODELS import (
    get_db,
    get_pii_db,
    User,
    Order,
    OrderItem,
    Return,
    Payment,
    Invoice,
    Notification,
    PartsCatalog,
    SupplierPart,
    Supplier as SupplierModel,
    USD_TO_ILS,
)
from BACKEND_AUTH_SECURITY import get_current_user, get_current_verified_user
from routes.schemas import OrderCreate, OrderCancelRequest, ReturnRequest
from routes.utils import _mask_supplier, _guarded_task

router = APIRouter()


@router.post("/api/v1/orders", status_code=status.HTTP_201_CREATED)
async def create_order(data: OrderCreate, current_user: User = Depends(get_current_verified_user), cat_db: AsyncSession = Depends(get_db), db: AsyncSession = Depends(get_pii_db)):
    from BACKEND_AI_AGENTS import get_supplier_shipping as _get_ship2

    subtotal = 0.0
    items_data = []
    # USD_TO_ILS is imported from BACKEND_DATABASE_MODELS (single source of truth)
    # Track unique suppliers in this order -> charge delivery fee once per supplier origin
    supplier_delivery_fees: dict[str, float] = {}

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
        total_cost_ils = cost_ils + ship_ils
        delivery_fee = _get_ship2(supplier_rec.name or "")
        supplier_delivery_fees[str(supplier_rec.id)] = delivery_fee
        unit_price = round(total_cost_ils * 1.45, 2)
        vat = round(unit_price * 0.18, 2)
        subtotal += unit_price * item.quantity
        items_data.append(
            {
                "part_id": item.part_id or str(part.id),
                "supplier_part_id": item.supplier_part_id,
                "quantity": item.quantity,
                "unit_price": unit_price,
                "vat": vat,
                "part": part,
                "sp": sp,
                "supplier_name": _mask_supplier(supplier_rec.name),
            }
        )

    vat_total = round(subtotal * 0.18, 2)
    shipping = round(sum(supplier_delivery_fees.values()), 2)
    total = round(subtotal + vat_total + shipping, 2)
    order_number = f"AUTO-2026-{str(uuid.uuid4())[:8].upper()}"

    order = Order(
        order_number=order_number,
        user_id=current_user.id,
        status="pending_payment",
        subtotal=subtotal,
        vat_amount=vat_total,
        shipping_cost=shipping,
        total_amount=total,
        shipping_address=data.shipping_address,
    )
    db.add(order)
    await db.flush()

    for d in items_data:
        try:
            _part_id = uuid.UUID(str(d["part_id"])) if d["part_id"] else None
            _sp_id = uuid.UUID(str(d["supplier_part_id"]))
        except (ValueError, AttributeError):
            _part_id = None
            _sp_id = None

        db.add(
            OrderItem(
                order_id=order.id,
                part_id=_part_id,
                supplier_part_id=_sp_id,
                part_name=d["part"].name,
                part_sku=d["part"].sku,
                manufacturer=d["part"].manufacturer,
                part_type=d["part"].part_type,
                supplier_name=d["supplier_name"],
                quantity=d["quantity"],
                unit_price=d["unit_price"],
                vat_amount=d["vat"],
                total_price=(d["unit_price"] + d["vat"]) * d["quantity"],
                warranty_months=d["sp"].warranty_months,
            )
        )

    await db.commit()
    await db.refresh(order)
    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "status": order.status,
        "subtotal": float(order.subtotal),
        "vat": float(order.vat_amount),
        "shipping": float(order.shipping_cost),
        "total": float(order.total_amount),
    }


@router.get("/api/v1/orders")
async def get_orders(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Order).where(Order.user_id == current_user.id).order_by(Order.created_at.desc()).limit(limit))
    orders = result.scalars().all()
    return {
        "orders": [
            {
                "id": str(o.id),
                "order_number": o.order_number,
                "status": o.status,
                "total": float(o.total_amount),
                "created_at": o.created_at,
                "tracking_number": o.tracking_number,
                "tracking_url": o.tracking_url,
                "estimated_delivery": o.estimated_delivery,
            }
            for o in orders
        ]
    }


@router.get("/api/v1/orders/{order_id}")
async def get_order(order_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    result = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))
    items = result.scalars().all()
    return {
        "id": str(order.id),
        "order_number": order.order_number,
        "status": order.status,
        "subtotal": float(order.subtotal),
        "vat": float(order.vat_amount),
        "shipping": float(order.shipping_cost),
        "total": float(order.total_amount),
        "tracking_number": order.tracking_number,
        "tracking_url": order.tracking_url,
        "estimated_delivery": order.estimated_delivery,
        "items": [
            {
                "part_id": str(i.part_id) if i.part_id else None,
                "supplier_part_id": str(i.supplier_part_id) if i.supplier_part_id else None,
                "part_name": i.part_name,
                "manufacturer": i.manufacturer,
                "quantity": i.quantity,
                "unit_price": float(i.unit_price),
                "total": float(i.total_price),
            }
            for i in items
        ],
    }


@router.get("/api/v1/orders/{order_id}/track")
async def track_order(order_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "order_number": order.order_number,
        "status": order.status,
        "tracking_number": order.tracking_number,
        "tracking_url": order.tracking_url,
        "estimated_delivery": order.estimated_delivery,
    }


@router.put("/api/v1/orders/{order_id}/cancel")
async def cancel_order(order_id: uuid.UUID, data: OrderCancelRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
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

    if was_paid:
        pay_res = await db.execute(select(Payment).where(and_(Payment.order_id == order.id, Payment.status == "paid")))
        payment = pay_res.scalar_one_or_none()

        if payment and payment.payment_intent_id:
            stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
            if stripe_key and not stripe_key.startswith("sk_test_xxxxx"):
                stripe_sdk.api_key = stripe_key
                try:
                    session_obj = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: stripe_sdk.checkout.Session.retrieve(payment.payment_intent_id),
                    )
                    pi_id = session_obj.payment_intent
                    if pi_id:
                        stripe_refund = await asyncio.get_running_loop().run_in_executor(
                            None,
                            lambda: stripe_sdk.Refund.create(
                                payment_intent=pi_id,
                                reason="requested_by_customer",
                            ),
                        )
                        refund_id = stripe_refund.id
                        refund_amount = float(stripe_refund.amount) / 100

                        payment.status = "refunded"
                        payment.refunded_at = datetime.utcnow()
                        payment.refund_amount = refund_amount
                        payment.refund_reason = data.reason or "ביטול על ידי לקוח"

                        existing_inv = (
                            await db.execute(select(Invoice).where(Invoice.order_id == order.id))
                        ).scalar_one_or_none()
                        if not existing_inv:
                            db.add(
                                Invoice(
                                    invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}",
                                    order_id=order.id,
                                    user_id=current_user.id,
                                    business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                                    issued_at=datetime.utcnow(),
                                )
                            )
                except Exception as stripe_err:
                    print(f"[Stripe refund error] {stripe_err}")

        ret_number = f"REF-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}"
        db.add(
            Return(
                return_number=ret_number,
                order_id=order.id,
                user_id=current_user.id,
                reason="cancellation",
                description=data.reason or "ביטול על ידי לקוח",
                original_amount=order.total_amount,
                refund_amount=refund_amount or order.total_amount,
                status="approved" if refund_id else "pending",
            )
        )

        _cancel_title = "ביטול והחזר כספי" + (" ✅" if refund_id else " 🔄")
        _cancel_msg = (
            f"הזמנה {order.order_number} בוטלה. "
            + (
                f"החזר כספי של ₪{refund_amount:.2f} נשלח לכרטיס האשראי שלך."
                if refund_id
                else "בקשת ההחזר הכספי בטיפול."
            )
        )
        db.add(
            Notification(
                user_id=current_user.id,
                title=_cancel_title,
                message=_cancel_msg,
                type="refund_initiated",
            )
        )

        from BACKEND_AUTH_SECURITY import publish_notification

        asyncio.create_task(
            _guarded_task(
                publish_notification(
                    str(current_user.id),
                    {
                        "type": "refund_initiated",
                        "title": _cancel_title,
                        "message": _cancel_msg,
                    },
                )
            )
        )

    await db.commit()
    return {
        "message": "Order cancelled",
        "refund_initiated": was_paid,
        "refund_id": refund_id,
        "refund_amount": refund_amount,
    }


@router.post("/api/v1/orders/{order_id}/return")
async def create_order_return(
    order_id: uuid.UUID,
    data: ReturnRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return_number = f"RET-2026-{str(uuid.uuid4())[:8].upper()}"
    ret = Return(
        return_number=return_number,
        order_id=order.id,
        user_id=current_user.id,
        reason=data.reason,
        description=data.description,
        original_amount=order.total_amount,
        status="pending",
    )
    db.add(ret)
    await db.commit()
    await db.refresh(ret)
    return {"return_id": str(ret.id), "return_number": ret.return_number, "status": "pending"}


@router.delete("/api/v1/orders/{order_id}")
async def delete_order(order_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ["pending_payment", "cancelled"]:
        raise HTTPException(status_code=400, detail="ניתן למחוק רק הזמנות שבוטלו או שממתינות לתשלום")

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


@router.get("/api/v1/orders/{order_id}/invoice")
async def get_order_invoice(
    order_id: uuid.UUID,
    inline: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Generate and stream a Hebrew PDF invoice for a paid order."""
    from invoice_generator import generate_invoice_pdf

    ord_res = await db.execute(select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id)))
    order = ord_res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    invoice_allowed = {"paid", "processing", "supplier_ordered", "confirmed", "shipped", "delivered", "refunded"}
    if order.status not in invoice_allowed:
        raise HTTPException(status_code=402, detail="החשבונית זמינה רק לאחר אישור תשלום")

    items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    items = items_res.scalars().all()

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
