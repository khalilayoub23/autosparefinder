"""Payments - all /api/v1/payments/* endpoints extracted from BACKEND_API_ROUTES.py."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text, desc
from datetime import datetime
import asyncio
import os
import uuid

from BACKEND_DATABASE_MODELS import (
    get_pii_db,
    Order,
    OrderItem,
    Payment,
    Invoice,
    Return,
    Notification,
    StripeWebhookLog,
    USD_TO_ILS,
    async_session_factory,
    pii_session_factory,
)
from BACKEND_AUTH_SECURITY import (
    get_current_user,
    get_current_verified_user,
    get_current_admin_user,
    get_redis,
    check_rate_limit,
    publish_notification,
)
from routes.schemas import MultiCheckoutRequest
from routes.utils import _guarded_task, _get_frontend_url, trigger_supplier_fulfillment

router = APIRouter()


@router.post("/api/v1/payments/create-checkout")
async def create_checkout_session(
    order_id: str,
    request: Request,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    redis=Depends(get_redis),
):
    """Create a Stripe Checkout Session (or simulate payment if Stripe not configured)."""
    import stripe as stripe_sdk

    if redis:
        allowed = await check_rate_limit(redis, f'rate:create_checkout:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_configured = bool(stripe_key and not stripe_key.startswith("sk_test_xxxxx"))

    # Load order
    result = await db.execute(
        select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ("pending_payment", "confirmed"):
        raise HTTPException(status_code=400, detail=f"Order is already {order.status}")

    # ── Live price validation ──────────────────────────────────────────────
    from decimal import Decimal
    _rate = USD_TO_ILS
    try:
        async with async_session_factory() as _cat:
            _ss = (await _cat.execute(
                text("SELECT value FROM system_settings WHERE key = 'ils_per_usd' LIMIT 1")
            )).fetchone()
            if _ss:
                _rate = float(_ss[0])
    except Exception:
        pass

    _items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    _order_items = _items_res.scalars().all()
    _price_changed = False
    _max_shipping = 0.0
    _new_items_total = Decimal("0")

    async with async_session_factory() as _cat:
        for _item in _order_items:
            if not _item.part_id:
                _new_items_total += Decimal(str(float(_item.total_price)))
                continue
            _sp_row = (await _cat.execute(
                text("""
                    SELECT
                        COALESCE(price_ils, price_usd * :rate) AS cost_ils,
                        COALESCE(shipping_cost_ils, shipping_cost_usd * :rate) AS ship_ils
                    FROM supplier_parts
                    WHERE part_id = :part_id AND is_available = TRUE
                    ORDER BY COALESCE(price_ils, price_usd * :rate) ASC
                    LIMIT 1
                """),
                {"part_id": str(_item.part_id), "rate": _rate},
            )).fetchone()

            if _sp_row:
                _live_unit = round(float(_sp_row[0]) * 1.45 * 1.18, 2)
                _live_ship = float(_sp_row[1]) if _sp_row[1] is not None else 91.0
                _max_shipping = max(_max_shipping, _live_ship)
            else:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "part_unavailable",
                        "message": "אחד או יותר מהחלקים אינם זמינים כרגע. אנא צור קשר עם שירות הלקוחות.",
                    },
                )

            if abs(_live_unit - round(float(_item.unit_price), 2)) > 0.01:
                _price_changed = True
                _item.unit_price = _live_unit
                _item.total_price = round(_live_unit * _item.quantity, 2)
            _new_items_total += Decimal(str(float(_item.total_price)))

    if _max_shipping > 0 and abs(_max_shipping - round(float(order.shipping_cost), 2)) > 0.01:
        _price_changed = True
        order.shipping_cost = round(_max_shipping, 2)

    if _price_changed:
        order.total_amount = round(float(_new_items_total) + float(order.shipping_cost), 2)
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "price_updated",
                "new_total": float(order.total_amount),
                "message": "המחיר עודכן. אנא אשר את ההזמנה מחדש.",
            },
        )
    # ── End price validation ───────────────────────────────────────────────

    frontend_url = _get_frontend_url(request)

    # ── Simulated payment (no Stripe key) ─────────────────────────────────
    if not stripe_configured:
        sim_session_id = f"SIM-{str(uuid.uuid4())[:12].upper()}"
        # Mark order as confirmed immediately
        order.status = "confirmed"
        existing_pay = await db.execute(
            select(Payment).where(Payment.order_id == order.id)
        )
        if not existing_pay.scalar_one_or_none():
            db.add(Payment(
                order_id=order.id,
                payment_intent_id=sim_session_id,
                amount=order.total_amount,
                currency="ILS",
                status="paid",
            ))
        await db.commit()
        # Trigger supplier fulfillment in background with its own session
        # (request db will close after handler returns)
        _order_id = order.id
        async def _fulfill_bg():
            async with pii_session_factory() as bg_db:
                try:
                    result2 = await bg_db.execute(select(Order).where(Order.id == _order_id))
                    bg_order = result2.scalar_one_or_none()
                    if bg_order:
                        await trigger_supplier_fulfillment([bg_order], bg_db)
                        await bg_db.commit()
                except Exception as _e:
                    print(f"[Fulfillment BG] error: {_e}")
        asyncio.create_task(_guarded_task(_fulfill_bg()))
        return {
            "checkout_url": f"{frontend_url}/payment/success?session_id={sim_session_id}&simulated=1",
            "session_id": sim_session_id,
            "amount": float(order.total_amount),
            "currency": "ILS",
        }
    # ── Real Stripe Checkout ───────────────────────────────────────────────
    stripe_sdk.api_key = stripe_key

    # Load order items for line items
    items_result = await db.execute(
        select(OrderItem).where(OrderItem.order_id == order.id)
    )
    order_items = items_result.scalars().all()

    # Build Stripe line items
    line_items = []
    for item in order_items:
        line_items.append({
            "price_data": {
                "currency": "ils",
                "product_data": {
                    "name": item.part_name,
                    "description": f"{item.manufacturer} | אחריות {item.warranty_months} חודשים",
                },
                "unit_amount": int(float(item.total_price) / item.quantity * 100),  # agorot per unit
            },
            "quantity": item.quantity,
        })

    # Add shipping as a line item if not zero
    if order.shipping_cost and float(order.shipping_cost) > 0:
        line_items.append({
            "price_data": {
                "currency": "ils",
                "product_data": {"name": "משלוח"},
                "unit_amount": int(float(order.shipping_cost) * 100),
            },
            "quantity": 1,
        })

    # Create Stripe Checkout Session (async)
    session = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: stripe_sdk.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=f"{frontend_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/cart",
            customer_email=current_user.email,
            metadata={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "user_id": str(current_user.id),
            },
            locale="auto",
            idempotency_key=f"order:{order.id}",  # Gap 6: Idempotency
        )
    )

    # Save pending payment record
    db.add(Payment(
        order_id=order.id,
        payment_intent_id=session.id,
        amount=order.total_amount,
        currency="ILS",
        status="pending",
    ))
    await db.commit()

    return {
        "checkout_url": session.url,
        "session_id": session.id,
        "amount": float(order.total_amount),
        "currency": "ILS",
    }


@router.post("/api/v1/payments/create-multi-checkout")
async def create_multi_checkout_session(
    payload: MultiCheckoutRequest,
    request: Request,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    redis=Depends(get_redis),
):
    """Create a single Stripe Checkout Session for multiple pending orders."""
    import stripe as stripe_sdk

    if redis:
        allowed = await check_rate_limit(redis, f'rate:create_multi_checkout:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key or stripe_key.startswith("sk_test_xxxxx"):
        raise HTTPException(status_code=503, detail="Stripe not configured. Add STRIPE_SECRET_KEY to .env")
    stripe_sdk.api_key = stripe_key

    if not payload.order_ids:
        raise HTTPException(status_code=400, detail="No order IDs provided")

    # Load & validate all orders belong to this user and are pending_payment
    orders_res = await db.execute(
        select(Order).where(and_(Order.id.in_(payload.order_ids), Order.user_id == current_user.id))
    )
    orders = orders_res.scalars().all()

    if len(orders) != len(payload.order_ids):
        raise HTTPException(status_code=404, detail="One or more orders not found")

    non_pending = [o.order_number for o in orders if o.status != "pending_payment"]
    if non_pending:
        raise HTTPException(status_code=400, detail=f"הזמנות אלו אינן ממתינות לתשלום: {', '.join(non_pending)}")

    # ── Live price validation ──────────────────────────────────────────────
    from decimal import Decimal
    _rate = USD_TO_ILS
    try:
        async with async_session_factory() as _cat:
            _ss = (await _cat.execute(
                text("SELECT value FROM system_settings WHERE key = 'ils_per_usd' LIMIT 1")
            )).fetchone()
            if _ss:
                _rate = float(_ss[0])
    except Exception:
        pass

    _updated_orders: list[str] = []
    async with async_session_factory() as _cat:
        for _order in orders:
            _items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == _order.id))
            _order_items = _items_res.scalars().all()
            _order_changed = False
            _max_shipping = 0.0
            _new_items_total = Decimal("0")

            for _item in _order_items:
                if not _item.part_id:
                    _new_items_total += Decimal(str(float(_item.total_price)))
                    continue
                _sp_row = (await _cat.execute(
                    text("""
                        SELECT
                            COALESCE(price_ils, price_usd * :rate) AS cost_ils,
                            COALESCE(shipping_cost_ils, shipping_cost_usd * :rate) AS ship_ils
                        FROM supplier_parts
                        WHERE part_id = :part_id AND is_available = TRUE
                        ORDER BY COALESCE(price_ils, price_usd * :rate) ASC
                        LIMIT 1
                    """),
                    {"part_id": str(_item.part_id), "rate": _rate},
                )).fetchone()

                if _sp_row:
                    _live_unit = round(float(_sp_row[0]) * 1.45 * 1.18, 2)
                    _live_ship = float(_sp_row[1]) if _sp_row[1] is not None else 91.0
                    _max_shipping = max(_max_shipping, _live_ship)
                else:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "part_unavailable",
                            "message": "אחד או יותר מהחלקים אינם זמינים כרגע. אנא צור קשר עם שירות הלקוחות.",
                        },
                    )

                if abs(_live_unit - round(float(_item.unit_price), 2)) > 0.01:
                    _order_changed = True
                    _item.unit_price = _live_unit
                    _item.total_price = round(_live_unit * _item.quantity, 2)
                _new_items_total += Decimal(str(float(_item.total_price)))

            if _max_shipping > 0 and abs(_max_shipping - round(float(_order.shipping_cost), 2)) > 0.01:
                _order_changed = True
                _order.shipping_cost = round(_max_shipping, 2)

            if _order_changed:
                _order.total_amount = round(float(_new_items_total) + float(_order.shipping_cost), 2)
                _updated_orders.append(_order.order_number)

    if _updated_orders:
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "price_updated",
                "updated_orders": _updated_orders,
                "message": "המחיר עודכן בחלק מההזמנות. אנא אשר מחדש.",
            },
        )
    # ── End price validation ───────────────────────────────────────────────

    # Build combined Stripe line items
    line_items = []
    for order in orders:
        items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
        for item in items_res.scalars().all():
            line_items.append({
                "price_data": {
                    "currency": "ils",
                    "product_data": {
                        "name": f"[{order.order_number}] {item.part_name}",
                        "description": f"{item.manufacturer} | אחריות {item.warranty_months} חודשים",
                    },
                    "unit_amount": int(float(item.total_price) / item.quantity * 100),
                },
                "quantity": item.quantity,
            })
        if order.shipping_cost and float(order.shipping_cost) > 0:
            line_items.append({
                "price_data": {
                    "currency": "ils",
                    "product_data": {"name": f"משלוח [{order.order_number}]"},
                    "unit_amount": int(float(order.shipping_cost) * 100),
                },
                "quantity": 1,
            })

    frontend_url = _get_frontend_url(request)
    session = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: stripe_sdk.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=f"{frontend_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/orders",
            customer_email=current_user.email,
            metadata={
                "order_ids": ",".join(str(o.id) for o in orders),
                "order_count": str(len(orders)),
                "user_id": str(current_user.id),
            },
            locale="auto",
            idempotency_key=f"orders:{':'.join(str(o.id) for o in orders)}",  # Gap 6: Idempotency
        )
    )

    # Create a pending Payment record per order
    for order in orders:
        db.add(Payment(
            order_id=order.id,
            payment_intent_id=session.id,
            amount=order.total_amount,
            currency="ILS",
            status="pending",
        ))
    await db.commit()

    return {
        "checkout_url": session.url,
        "session_id": session.id,
        "order_count": len(orders),
        "total_amount": sum(float(o.total_amount) for o in orders),
        "currency": "ILS",
    }


@router.get("/api/v1/payments/verify-session")
async def verify_checkout_session(
    session_id: str,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Called after Stripe redirects back — verifies payment and marks order(s) paid."""
    import stripe as stripe_sdk

    # ── Simulated payment (session_id starts with SIM-) ───────────────────────
    if session_id.startswith("SIM-"):
        # Find the payment record linked to this simulated session
        pay_res = await db.execute(select(Payment).where(Payment.payment_intent_id == session_id))
        pay = pay_res.scalar_one_or_none()
        if not pay:
            raise HTTPException(status_code=404, detail="תשלום סימולציה לא נמצא")
        ord_res = await db.execute(
            select(Order).where(and_(Order.id == pay.order_id, Order.user_id == current_user.id))
        )
        order = ord_res.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="הזמנה לא נמצאה")
        # Ensure order is marked confirmed and payment paid
        order.status = "confirmed"
        pay.status = "paid"
        pay.paid_at = datetime.utcnow()
        pay.payment_method = "simulated"
        # Create invoice if not already exists
        inv_check = await db.execute(select(Invoice).where(Invoice.order_id == order.id))
        if not inv_check.scalar_one_or_none():
            inv_num = f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}"
            db.add(Invoice(
                invoice_number=inv_num,
                order_id=order.id,
                user_id=current_user.id,
                business_number=os.getenv("COMPANY_NUMBER", "060633880"),
            ))
        await db.commit()
        return {
            "status": "paid",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "amount": float(order.total_amount),
            "currency": "ILS",
        }

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key or stripe_key.startswith("sk_test_xxxxx"):
        raise HTTPException(status_code=503, detail="Stripe not configured")

    stripe_sdk.api_key = stripe_key

    session = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: stripe_sdk.checkout.Session.retrieve(session_id)
    )

    # ── MULTI-ORDER SESSION ────────────────────────────────────────────────────
    order_ids_str = session.metadata.get("order_ids")
    if order_ids_str:
        order_id_list = [oid.strip() for oid in order_ids_str.split(",") if oid.strip()]
        orders_res = await db.execute(
            select(Order).where(and_(Order.id.in_(order_id_list), Order.user_id == current_user.id))
        )
        multi_orders = orders_res.scalars().all()
        if not multi_orders:
            raise HTTPException(status_code=404, detail="Orders not found")

        if session.payment_status == "paid":
            for ord in multi_orders:
                if ord.status == "pending_payment":
                    ord.status = "paid"
            # Update all payment records tied to this session
            pays_res = await db.execute(select(Payment).where(Payment.payment_intent_id == session_id))
            for pay in pays_res.scalars().all():
                pay.status = "paid"
                pay.paid_at = datetime.utcnow()
                pay.payment_method = session.payment_method_types[0] if session.payment_method_types else "card"
            # Create invoices
            for ord in multi_orders:
                inv_num = f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(ord.id)[:8].upper()}"
                db.add(Invoice(
                    invoice_number=inv_num,
                    order_id=ord.id,
                    user_id=current_user.id,
                    business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                ))
            paid_nums = ", ".join(o.order_number for o in multi_orders)
            _multi_msg = f"{len(multi_orders)} הזמנות אושרו: {paid_nums}"
            db.add(Notification(
                user_id=current_user.id,
                title="תשלום התקבל ✅",
                message=_multi_msg,
                type="payment_success",
            ))
            asyncio.create_task(_guarded_task(publish_notification(str(current_user.id), {"type": "payment_success", "title": "תשלום התקבל ✅", "message": _multi_msg})))
            # ── Dropshipping: notify admin(s) per supplier & advance → processing
            await trigger_supplier_fulfillment(list(multi_orders), db)
            await db.commit()

        return {
            "status": session.payment_status,
            "is_multi": True,
            "orders": [{"order_id": str(o.id), "order_number": o.order_number,
                         "order_status": o.status, "amount": float(o.total_amount)} for o in multi_orders],
            "order_number": multi_orders[0].order_number,
            "order_id": str(multi_orders[0].id),
            "amount": sum(float(o.total_amount) for o in multi_orders),
        }

    # ── SINGLE-ORDER SESSION ───────────────────────────────────────────────────
    order_id = session.metadata.get("order_id")
    if not order_id:
        raise HTTPException(status_code=400, detail="Invalid session metadata")

    result = await db.execute(
        select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if session.payment_status == "paid" and order.status == "pending_payment":
        order.status = "paid"
        pay_result = await db.execute(select(Payment).where(Payment.payment_intent_id == session_id))
        payment = pay_result.scalar_one_or_none()
        if payment:
            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            payment.payment_method = session.payment_method_types[0] if session.payment_method_types else "card"
        invoice_number = f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}"
        db.add(Invoice(
            invoice_number=invoice_number,
            order_id=order.id,
            user_id=current_user.id,
            business_number=os.getenv("COMPANY_NUMBER", "060633880"),
        ))
        _single_pay_msg = f"הזמנה {order.order_number} אושרה."
        db.add(Notification(
            user_id=current_user.id,
            title="תשלום התקבל ✅",
            message=_single_pay_msg,
            type="payment_success",
        ))
        asyncio.create_task(_guarded_task(publish_notification(str(current_user.id), {"type": "payment_success", "title": "תשלום התקבל ✅", "message": _single_pay_msg})))
        # ── Dropshipping: notify admin(s) per supplier & advance → processing ─
        await trigger_supplier_fulfillment([order], db)
        await db.commit()

    return {
        "status": session.payment_status,
        "order_status": order.status,
        "order_number": order.order_number,
        "order_id": str(order.id),
        "amount": float(order.total_amount),
    }


@router.post("/api/v1/payments/create-intent")
async def create_payment_intent_legacy(order_id: str, request: Request, current_user=Depends(get_current_verified_user), db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Legacy endpoint – redirects to create-checkout."""
    if redis:
        allowed = await check_rate_limit(redis, f'rate:create_intent:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    return await create_checkout_session(order_id, request, current_user, db, redis)


@router.post("/api/v1/payments/confirm")
async def confirm_payment(payment_intent_id: str, current_user=Depends(get_current_verified_user), db: AsyncSession = Depends(get_pii_db), request: Request = None, redis=Depends(get_redis)):
    if redis:
        allowed = await check_rate_limit(redis, f'rate:confirm_payment:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    return {"status": "redirect_to_stripe", "message": "Use /payments/create-checkout to get a Stripe Checkout URL"}


@router.get("/api/v1/payments/refunds/list")
async def list_refunds(current_user=Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    """Return all refund records for the current user (from Return table + safety-net from payments)."""
    # 1. Return records (manual returns + cancellation refunds)
    rets_res = await db.execute(
        select(Return, Order).join(Order, Return.order_id == Order.id).where(
            Return.user_id == current_user.id
        ).order_by(desc(Return.requested_at))
    )
    return_rows = rets_res.all()

    # 2. Payments that were refunded (status=refunded)
    pays_res = await db.execute(
        select(Payment, Order).join(Order, Payment.order_id == Order.id).where(
            and_(Order.user_id == current_user.id, Payment.status == "refunded")
        ).order_by(desc(Payment.refunded_at))
    )
    refunded_payments = pays_res.all()

    # 3. Safety-net: cancelled orders with still-paid payment and no Return record
    orphan_res = await db.execute(
        select(Payment, Order).join(Order, Payment.order_id == Order.id).where(
            and_(Order.user_id == current_user.id, Order.status == "cancelled", Payment.status == "paid")
        )
    )
    orphan_rows = orphan_res.all()

    # Build combined list
    order_ids_covered = set()
    items = []

    for ret, order in return_rows:
        order_ids_covered.add(str(order.id))
        items.append({
            "id": str(ret.id),
            "type": "cancellation" if ret.reason == "cancellation" else "return",
            "return_number": ret.return_number,
            "order_number": order.order_number,
            "order_id": str(order.id),
            "reason": ret.reason,
            "description": ret.description,
            "original_amount": float(ret.original_amount) if ret.original_amount else None,
            "refund_amount": float(ret.refund_amount) if ret.refund_amount else None,
            "status": ret.status,
            "date": ret.requested_at.isoformat() if ret.requested_at else None,
        })

    for payment, order in refunded_payments:
        if str(order.id) in order_ids_covered:
            continue
        order_ids_covered.add(str(order.id))
        items.append({
            "id": str(payment.id),
            "type": "cancellation",
            "return_number": f"REF-{str(payment.id)[:8].upper()}",
            "order_number": order.order_number,
            "order_id": str(order.id),
            "reason": payment.refund_reason or "ביטול",
            "description": None,
            "original_amount": float(payment.amount) if payment.amount else None,
            "refund_amount": float(payment.refund_amount) if payment.refund_amount else None,
            "status": "approved",
            "date": payment.refunded_at.isoformat() if payment.refunded_at else None,
        })

    # Safety-net: show as "בטיפול" if no Return/refund record exists
    for payment, order in orphan_rows:
        if str(order.id) in order_ids_covered:
            continue
        order_ids_covered.add(str(order.id))
        items.append({
            "id": str(payment.id),
            "type": "cancellation",
            "return_number": f"REF-{str(order.id)[:8].upper()}",
            "order_number": order.order_number,
            "order_id": str(order.id),
            "reason": "cancellation",
            "description": "ביטול על ידי לקוח",
            "original_amount": float(order.total_amount) if order.total_amount else None,
            "refund_amount": float(order.total_amount) if order.total_amount else None,
            "status": "pending",
            "date": order.cancelled_at.isoformat() if order.cancelled_at else None,
        })

    items.sort(key=lambda x: x["date"] or "", reverse=True)
    return {"refunds": items}


@router.get("/api/v1/payments/{payment_id}")
async def get_payment(payment_id: str, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    # Join with Order to enforce ownership — prevents IDOR
    result = await db.execute(
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(
            and_(
                Payment.id == payment_id,
                Order.user_id == current_user.id,
            )
        )
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"id": str(payment.id), "amount": float(payment.amount), "status": payment.status, "payment_method": payment.payment_method, "created_at": payment.created_at}


@router.post("/api/v1/payments/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_pii_db)):
    """Stripe webhook for async payment confirmation (backup to verify-session)."""
    import stripe as stripe_sdk
    from BACKEND_DATABASE_MODELS import StripeWebhookLog
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_sdk.api_key = os.getenv("STRIPE_SECRET_KEY", "")

    # Reject webhook silently (return 400) when secret is not configured — never
    # fall through to forged-event handling.
    if not webhook_secret:
        raise HTTPException(status_code=400, detail="Webhook secret not configured")

    try:
        event = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: stripe_sdk.Webhook.construct_event(payload, sig_header, webhook_secret)
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ── Gap 6: Idempotency check (webhook deduplication) ────────────────────────────────
    # Check if we've already processed this exact event
    event_id = event.get("id", "")
    event_type = event.get("type", "")
    
    existing_log = None
    if event_id:
        result = await db.execute(
            select(StripeWebhookLog).where(StripeWebhookLog.event_id == event_id)
        )
        existing_log = result.scalar_one_or_none()
    
    # If we've seen this event AND processed it successfully, return 200 immediately
    if existing_log and existing_log.processed:
        logger.info(f"[Webhook] Deduped event {event_id} (already processed)")
        return {"received": True, "status": "deduped"}
    
    # If we've seen it but processing failed, retry processing it
    if existing_log:
        logger.info(f"[Webhook] Retrying failed event {event_id}")
    else:
        # First time seeing this event — create a log entry
        if event_id:
            existing_log = StripeWebhookLog(
                event_id=event_id,
                event_type=event_type,
                payload=event,
                processed=False,
            )
            db.add(existing_log)
            await db.commit()

    # ── Process the webhook event ──────────────────────────────────────────────────────
    processing_error = None
    try:
        if event.type == "checkout.session.completed":
            session = event.data.object
            if session.payment_status == "paid":
                orders_to_fulfill = []

                # Single-order
                order_id = session.metadata.get("order_id")
                if order_id:
                    res = await db.execute(select(Order).where(Order.id == order_id))
                    order = res.scalar_one_or_none()
                    if order and order.status == "pending_payment":
                        order.status = "paid"
                        orders_to_fulfill.append(order)

                # Multi-order
                order_ids_str = session.metadata.get("order_ids", "")
                if order_ids_str:
                    oid_list = [x.strip() for x in order_ids_str.split(",") if x.strip()]
                    res = await db.execute(select(Order).where(Order.id.in_(oid_list)))
                    for ord in res.scalars().all():
                        if ord.status == "pending_payment":
                            ord.status = "paid"
                            orders_to_fulfill.append(ord)

                if orders_to_fulfill:
                    await trigger_supplier_fulfillment(orders_to_fulfill, db)
                    # Auto-create Invoice record for every newly-paid order
                    for ord in orders_to_fulfill:
                        existing = (await db.execute(
                            select(Invoice).where(Invoice.order_id == ord.id)
                        )).scalar_one_or_none()
                        if not existing:
                            db.add(Invoice(
                                invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(ord.id)[:8].upper()}",
                                order_id=ord.id,
                                user_id=ord.user_id,
                                business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                                issued_at=datetime.utcnow(),
                            ))
                    await db.commit()
    except Exception as e:
        processing_error = str(e)[:500]
        logger.error(f"[Webhook] Error processing event {event_id}: {processing_error}")

    # ── Mark event as processed (or failed) in log ─────────────────────────────────────
    if existing_log:
        existing_log.processed = (processing_error is None)
        existing_log.result = {
            "processed": existing_log.processed,
            "error": processing_error,
            "processed_at": datetime.utcnow().isoformat(),
        }
        await db.commit()

    # Return 200 to Stripe regardless (prevents redelivery)
    return {"received": True}

@router.post("/api/v1/payments/refund")
async def refund_payment(
    payment_id: str,
    amount: float,
    reason: str,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """Admin: manually refund a payment via Stripe."""
    import stripe as stripe_sdk

    if redis:
        allowed = await check_rate_limit(redis, f'rate:refund:{current_user.id}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    result = await db.execute(
        select(Payment).options().where(Payment.id == payment_id)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status not in ("paid", "succeeded"):
        raise HTTPException(status_code=400, detail=f"Payment status '{payment.status}' cannot be refunded")

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key or stripe_key.startswith("sk_test_xxxxx"):
        raise HTTPException(status_code=503, detail="Stripe not configured")

    stripe_sdk.api_key = stripe_key
    try:
        # payment_intent_id field stores the Checkout Session ID (cs_...)
        session_obj = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: stripe_sdk.checkout.Session.retrieve(payment.payment_intent_id)
        )
        pi_id = session_obj.payment_intent
        if not pi_id:
            raise HTTPException(status_code=400, detail="No payment intent found for this session")

        refund_cents = int(amount * 100)
        stripe_refund = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: stripe_sdk.Refund.create(
                payment_intent=pi_id,
                amount=refund_cents,
                reason="requested_by_customer",
            )
        )
        refund_ils = float(stripe_refund.amount) / 100

        payment.status = "refunded"
        payment.refunded_at = datetime.utcnow()
        payment.refund_amount = refund_ils
        payment.refund_reason = reason

        # Get order for notification + Invoice
        order_res = await db.execute(select(Order).where(Order.id == payment.order_id))
        order = order_res.scalar_one_or_none()
        if order:
            _refund_msg = f"החזר כספי של ₪{refund_ils:.2f} בוצע עבור הזמנה {order.order_number}."
            db.add(Notification(
                user_id=order.user_id,
                type="refund",
                title="החזר כספי אושר על ידי מנהל",
                message=_refund_msg,
            ))
            asyncio.create_task(_guarded_task(publish_notification(str(order.user_id), {"type": "refund", "title": "החזר כספי אושר על ידי מנהל", "message": _refund_msg})))
            # Ensure an Invoice record exists (credit note for the refund)
            existing_inv = (await db.execute(
                select(Invoice).where(Invoice.order_id == order.id)
            )).scalar_one_or_none()
            if not existing_inv:
                db.add(Invoice(
                    invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}",
                    order_id=order.id,
                    user_id=order.user_id,
                    business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                    issued_at=datetime.utcnow(),
                ))

        await db.commit()
        return {"message": "Refund processed", "refund_id": stripe_refund.id, "amount": refund_ils}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/v1/payments/history")
async def get_payment_history(current_user=Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Payment).join(Order).where(Order.user_id == current_user.id).order_by(Payment.created_at.desc()).limit(limit))
    payments = result.scalars().all()
    return {"payments": [{"id": str(p.id), "amount": float(p.amount), "status": p.status, "created_at": p.created_at} for p in payments]}
