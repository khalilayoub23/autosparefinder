"""Payments - all /api/v1/payments/* endpoints extracted from BACKEND_API_ROUTES.py."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, text, desc, func
from datetime import datetime
import asyncio
import json
import os
import uuid
import re
from urllib.parse import urlparse

from BACKEND_DATABASE_MODELS import (
    get_pii_db,
    Order,
    OrderItem,
    Payment,
    SupplierPayment,
    Invoice,
    Return,
    Notification,
    StripeWebhookLog,
    async_session_factory,
    pii_session_factory,
)
from currency_rate import get_usd_to_ils_rate
from BACKEND_AUTH_SECURITY import (
    get_current_user,
    get_current_verified_user,
    get_current_admin_user,
    get_redis,
    check_rate_limit,
    publish_notification,
)
from routes.schemas import MultiCheckoutRequest
from routes.stripe_config import resolve_stripe_secret_key, is_valid_stripe_secret_key
from routes.utils import (
    _guarded_task,
    _get_frontend_url,
    trigger_supplier_fulfillment,
    trigger_supplier_refund,
)

router = APIRouter()


def _is_truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _allow_simulated_payments() -> bool:
    return _is_truthy_env(os.getenv("ALLOW_SIMULATED_PAYMENTS", "0"))


def _is_production_environment() -> bool:
    env = (os.getenv("ENVIRONMENT", "development") or "").strip().lower()
    return env in {"prod", "production"}


def _first_header_value(raw_value: str | None) -> str:
    if not raw_value:
        return ""
    return str(raw_value).split(",", 1)[0].strip()


def _extract_hostname(raw_value: str | None) -> str:
    value = (raw_value or "").strip()
    if not value:
        return ""

    # Host headers often come as "host:port" without URL scheme.
    if "://" not in value:
        value = f"http://{value}"

    try:
        return (urlparse(value).hostname or "").strip().lower()
    except Exception:
        return ""


def _is_private_ipv4(hostname: str) -> bool:
    if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", hostname):
        return False

    try:
        octets = [int(x) for x in hostname.split(".")]
    except Exception:
        return False

    if any(o < 0 or o > 255 for o in octets):
        return False

    if octets[0] == 10:
        return True
    if octets[0] == 127:
        return True
    if octets[0] == 192 and octets[1] == 168:
        return True
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return True
    return False


def _is_public_request_host(hostname: str) -> bool:
    host = (hostname or "").strip().lower()
    if not host:
        return False

    if host in {
        "localhost",
        "backend",
        "frontend",
        "nginx",
        "redis",
        "meilisearch",
        "postgres_pii",
        "postgres_catalog",
        "host.docker.internal",
    }:
        return False

    if host.endswith(".internal") or host.endswith(".local"):
        return False

    if _is_private_ipv4(host):
        return False

    return True


def _request_candidate_hosts(request: Request) -> list[str]:
    candidates: list[str] = []

    origin = _first_header_value(request.headers.get("origin"))
    referer = _first_header_value(request.headers.get("referer"))
    fwd_host = _first_header_value(request.headers.get("x-forwarded-host"))
    host = _first_header_value(request.headers.get("host"))

    for raw in (origin, referer, fwd_host, host):
        parsed = _extract_hostname(raw)
        if parsed and parsed not in candidates:
            candidates.append(parsed)

    return candidates


def _allow_simulated_payments_for_request(request: Request) -> bool:
    if not _allow_simulated_payments():
        return False

    if _is_truthy_env(os.getenv("ALLOW_SIMULATED_PAYMENTS_PUBLIC", "0")):
        return True

    # In production, never allow silent simulation unless explicitly overridden.
    if _is_production_environment():
        return False

    return not any(_is_public_request_host(host) for host in _request_candidate_hosts(request))


def _is_valid_stripe_secret_key(raw_key: str | None) -> bool:
    return is_valid_stripe_secret_key(raw_key)


def _build_tracking_url_from_number(tracking_number: str | None, tracking_url: str | None = None) -> str:
    if tracking_url and str(tracking_url).strip():
        return str(tracking_url).strip()

    n = (tracking_number or "").strip()
    if not n:
        return ""
    if re.fullmatch(r"1Z[A-Z0-9]{16}", n, re.IGNORECASE):
        return f"https://www.ups.com/track?tracknum={n}&requester=ST/trackdetails"
    if re.fullmatch(r"\d{12}", n):
        return f"https://www.fedex.com/fedextrack/?trknbr={n}"
    if re.fullmatch(r"\d{10}", n):
        return f"https://www.dhl.com/en/express/tracking.html?AWB={n}"
    return f"https://parcelsapp.com/en/tracking/{n}"


def _dedupe_orders_from_rows(rows) -> list:
    """Return unique Order objects from (Payment, Order) rows while preserving order."""
    deduped_orders = []
    seen_orders = set()
    for _pay, ord_obj in rows or []:
        oid = str(ord_obj.id)
        if oid in seen_orders:
            continue
        seen_orders.add(oid)
        deduped_orders.append(ord_obj)
    return deduped_orders


def _build_paid_verify_response(orders: list) -> dict:
    """Build verify-session payload for already-paid order list."""
    if not orders:
        return {
            "status": "unpaid",
            "order_status": "pending_payment",
            "order_number": None,
            "order_id": None,
            "amount": 0.0,
        }

    if len(orders) > 1:
        return {
            "status": "paid",
            "is_multi": True,
            "orders": [
                {
                    "order_id": str(o.id),
                    "order_number": o.order_number,
                    "order_status": o.status,
                    "amount": float(o.total_amount),
                }
                for o in orders
            ],
            "order_number": orders[0].order_number,
            "order_id": str(orders[0].id),
            "amount": sum(float(o.total_amount) for o in orders),
        }

    order = orders[0]
    return {
        "status": "paid",
        "order_status": order.status,
        "order_number": order.order_number,
        "order_id": str(order.id),
        "amount": float(order.total_amount),
    }


def _build_verify_response_from_local_rows(rows) -> dict:
    """Build verify-session payload from local Payment/Order rows."""
    if not rows:
        return {
            "status": "unpaid",
            "order_status": "pending_payment",
            "order_number": None,
            "order_id": None,
            "amount": 0.0,
        }

    deduped_orders = _dedupe_orders_from_rows(rows)
    payment_statuses = []

    for pay, ord in rows:
        payment_statuses.append((pay.status or "").lower())

    # Local fallback must not report paid unless all local records are paid/refunded.
    if payment_statuses and all(s in {"paid", "refunded"} for s in payment_statuses):
        derived_status = "paid"
    elif payment_statuses and any(s == "pending" for s in payment_statuses):
        derived_status = "pending"
    else:
        derived_status = "unpaid"

    if len(deduped_orders) > 1:
        return {
            "status": derived_status,
            "is_multi": True,
            "orders": [
                {
                    "order_id": str(o.id),
                    "order_number": o.order_number,
                    "order_status": o.status,
                    "amount": float(o.total_amount),
                }
                for o in deduped_orders
            ],
            "order_number": deduped_orders[0].order_number,
            "order_id": str(deduped_orders[0].id),
            "amount": sum(float(o.total_amount) for o in deduped_orders),
        }

    order = deduped_orders[0]
    return {
        "status": derived_status,
        "order_status": order.status,
        "order_number": order.order_number,
        "order_id": str(order.id),
        "amount": float(order.total_amount),
    }


def _extract_checkout_session_id(payment: Payment) -> str:
    """Return Checkout Session ID (cs_...) from stored payment identifiers."""
    for raw in (payment.payment_intent_id, payment.provider_transaction_id):
        token = str(raw or "").strip()
        if token.startswith("cs_"):
            return token.split(":", 1)[0]
    return ""


async def _reconcile_pending_checkout_sessions_for_user(user_id, db: AsyncSession) -> dict:
    """Self-heal pending Stripe checkout sessions when webhooks are delayed/missing."""
    import stripe as stripe_sdk

    stripe_key, _ = resolve_stripe_secret_key()
    if not _is_valid_stripe_secret_key(stripe_key):
        return {"checked_sessions": 0, "paid_sessions": 0, "fulfilled_orders": 0}

    stripe_sdk.api_key = stripe_key

    rows_res = await db.execute(
        select(Payment, Order)
        .join(Order, Payment.order_id == Order.id)
        .where(
            and_(
                Order.user_id == user_id,
                Order.deleted_at.is_(None),
                Payment.provider == "stripe",
                Payment.status == "pending",
                or_(
                    Payment.payment_intent_id.like("cs_%"),
                    Payment.provider_transaction_id.like("cs_%"),
                ),
            )
        )
    )
    rows = rows_res.all()
    if not rows:
        return {"checked_sessions": 0, "paid_sessions": 0, "fulfilled_orders": 0}

    session_ids = sorted({
        sid
        for payment, _order in rows
        for sid in [_extract_checkout_session_id(payment)]
        if sid
    })
    if not session_ids:
        return {"checked_sessions": 0, "paid_sessions": 0, "fulfilled_orders": 0}

    changed = False
    paid_sessions = 0
    orders_to_fulfill_ids: set = set()

    for session_id in session_ids:
        try:
            session = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda sid=session_id: stripe_sdk.checkout.Session.retrieve(sid),
            )
        except Exception:
            continue

        if getattr(session, "payment_status", "") != "paid":
            continue

        paid_sessions += 1
        pay_method = "card"
        pay_method_types = getattr(session, "payment_method_types", None)
        if isinstance(pay_method_types, (list, tuple)) and pay_method_types:
            pay_method = str(pay_method_types[0] or "card")

        rel_res = await db.execute(
            select(Payment, Order)
            .join(Order, Payment.order_id == Order.id)
            .where(
                and_(
                    Order.user_id == user_id,
                    or_(
                        Payment.payment_intent_id == session_id,
                        Payment.payment_intent_id.like(f"{session_id}:%"),
                        Payment.provider_transaction_id == session_id,
                    ),
                )
            )
        )
        rel_rows = rel_res.all()

        for payment, order in rel_rows:
            if payment.status != "paid":
                payment.status = "paid"
                payment.paid_at = datetime.utcnow()
                payment.payment_method = pay_method
                changed = True

            if order.status == "pending_payment":
                order.status = "paid"
                orders_to_fulfill_ids.add(order.id)
                changed = True

            inv_exists = await db.execute(select(Invoice).where(Invoice.order_id == order.id))
            if not inv_exists.scalar_one_or_none():
                db.add(Invoice(
                    invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{str(order.id)[:8].upper()}",
                    order_id=order.id,
                    user_id=order.user_id,
                    business_number=os.getenv("COMPANY_NUMBER", "060633880"),
                    issued_at=datetime.utcnow(),
                ))
                changed = True

    if orders_to_fulfill_ids:
        ord_res = await db.execute(select(Order).where(Order.id.in_(list(orders_to_fulfill_ids))))
        paid_orders = ord_res.scalars().all()
        if paid_orders:
            await trigger_supplier_fulfillment(paid_orders, db)
            changed = True

    if changed:
        await db.commit()

    return {
        "checked_sessions": len(session_ids),
        "paid_sessions": paid_sessions,
        "fulfilled_orders": len(orders_to_fulfill_ids),
    }


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

    stripe_key, _ = resolve_stripe_secret_key()
    stripe_configured = _is_valid_stripe_secret_key(stripe_key)
    allow_simulation = _allow_simulated_payments_for_request(request)

    # Load order
    result = await db.execute(
        select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ("pending_payment", "confirmed"):
        raise HTTPException(status_code=400, detail=f"Order is already {order.status}")

    # Reject invalid payable orders early.
    if float(order.total_amount or 0) <= 0:
        raise HTTPException(status_code=400, detail="לא ניתן לפתוח תשלום להזמנה בסכום 0")

    order_items_check = await db.execute(select(func.count(OrderItem.id)).where(OrderItem.order_id == order.id))
    if int(order_items_check.scalar() or 0) == 0:
        raise HTTPException(status_code=400, detail="לא ניתן לפתוח תשלום להזמנה ללא פריטים")

    # ── Live price validation ──────────────────────────────────────────────
    from decimal import Decimal
    async with async_session_factory() as _cat:
        _rate = await get_usd_to_ils_rate(_cat)

    _items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    _order_items = _items_res.scalars().all()
    _price_changed = False
    _max_shipping = 0.0
    _new_items_total = Decimal("0")

    try:
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

                if _sp_row is None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "part_unavailable",
                            "message": "אחד או יותר מהחלקים אינם זמינים כרגע. אנא צור קשר עם שירות הלקוחות.",
                        },
                    )

                if _sp_row[0] is not None:
                    _live_unit = round(float(_sp_row[0]) * 1.45 * 1.18, 2)
                    _live_ship = float(_sp_row[1]) if _sp_row[1] is not None else 91.0
                    _max_shipping = max(_max_shipping, _live_ship)
                    if abs(_live_unit - round(float(_item.unit_price), 2)) > 0.01:
                        _price_changed = True
                        _item.unit_price = _live_unit
                        _item.total_price = round(_live_unit * _item.quantity, 2)
                _new_items_total += Decimal(str(float(_item.total_price)))
    except HTTPException:
        raise
    except Exception as _e:
        print(f"[Payment] Live price check error (using stored prices): {_e}")
        _new_items_total = Decimal("0")
        _price_changed = False
        _max_shipping = 0.0
        for _item in _order_items:
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

    if not stripe_configured and not allow_simulation:
        raise HTTPException(
            status_code=503,
            detail="שירות התשלומים אינו מוגדר כראוי. יש להגדיר מפתח Stripe תקין.",
        )

    # ── Simulated payment (explicitly enabled) ─────────────────────────────
    if not stripe_configured and allow_simulation:
        sim_session_id = f"SIM-{str(uuid.uuid4())[:12].upper()}"
        # Mark order as confirmed immediately
        order.status = "confirmed"
        existing_pay = await db.execute(
            select(Payment).where(Payment.order_id == order.id)
        )
        if not existing_pay.scalar_one_or_none():
            db.add(Payment(
                order_id=order.id,
                user_id=current_user.id,
                payment_intent_id=sim_session_id,
                provider="simulated",
                provider_transaction_id=sim_session_id,
                amount_ils=order.total_amount,
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
            "mode": "simulated",
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
        if int(item.quantity or 0) <= 0:
            raise HTTPException(status_code=400, detail="כמות פריט לא תקינה בהזמנה")

        unit_amount_agorot = int(round(float(item.total_price) / item.quantity * 100))
        if unit_amount_agorot <= 0:
            raise HTTPException(status_code=400, detail="מחיר פריט לא תקין בהזמנה")

        line_items.append({
            "price_data": {
                "currency": "ils",
                "product_data": {
                    "name": item.part_name,
                    "description": f"{item.manufacturer} | אחריות {item.warranty_months} חודשים",
                },
                "unit_amount": unit_amount_agorot,  # agorot per unit
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
    try:
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
    except Exception as stripe_err:
        print(f"[Payments] create-checkout Stripe error: {stripe_err}")
        raise HTTPException(status_code=503, detail="שירות התשלומים אינו זמין כרגע. נסה שוב מאוחר יותר.")

    # Save pending payment record (guard against duplicate on retry)
    existing_pay = await db.execute(
        select(Payment).where(Payment.payment_intent_id == session.id)
    )
    if not existing_pay.scalar_one_or_none():
        db.add(Payment(
            order_id=order.id,
            user_id=current_user.id,
            payment_intent_id=session.id,
            provider="stripe",
            provider_transaction_id=session.id,
            amount_ils=order.total_amount,
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

    stripe_key, _ = resolve_stripe_secret_key()
    stripe_configured = _is_valid_stripe_secret_key(stripe_key)
    allow_simulation = _allow_simulated_payments_for_request(request)
    if stripe_configured:
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

    invalid_amount_orders = [o.order_number for o in orders if float(o.total_amount or 0) <= 0]
    if invalid_amount_orders:
        raise HTTPException(status_code=400, detail=f"הזמנות בסכום 0 לא ניתנות לתשלום: {', '.join(invalid_amount_orders)}")

    for o in orders:
        cnt_res = await db.execute(select(func.count(OrderItem.id)).where(OrderItem.order_id == o.id))
        if int(cnt_res.scalar() or 0) == 0:
            raise HTTPException(status_code=400, detail=f"הזמנה ללא פריטים אינה ניתנת לתשלום: {o.order_number}")

    # ── Live price validation ──────────────────────────────────────────────
    from decimal import Decimal
    async with async_session_factory() as _cat:
        _rate = await get_usd_to_ils_rate(_cat)

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
            if int(item.quantity or 0) <= 0:
                raise HTTPException(status_code=400, detail=f"כמות פריט לא תקינה בהזמנה: {order.order_number}")

            unit_amount_agorot = int(round(float(item.total_price) / item.quantity * 100))
            if unit_amount_agorot <= 0:
                raise HTTPException(status_code=400, detail=f"מחיר פריט לא תקין בהזמנה: {order.order_number}")

            line_items.append({
                "price_data": {
                    "currency": "ils",
                    "product_data": {
                        "name": f"[{order.order_number}] {item.part_name}",
                        "description": f"{item.manufacturer} | אחריות {item.warranty_months} חודשים",
                    },
                    "unit_amount": unit_amount_agorot,
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

    if not line_items:
        raise HTTPException(status_code=400, detail="לא נמצאו פריטים תקינים לתשלום")

    frontend_url = _get_frontend_url(request)

    if not stripe_configured and not allow_simulation:
        raise HTTPException(
            status_code=503,
            detail="שירות התשלומים אינו מוגדר כראוי. יש להגדיר מפתח Stripe תקין.",
        )

    # ── Simulated multi-order payment (explicitly enabled) ─────────────────
    if not stripe_configured and allow_simulation:
        sim_session_id = f"SIM-{str(uuid.uuid4())[:12].upper()}"
        for order in orders:
            order.status = "confirmed"
            composite_id = f"{sim_session_id}:{str(order.id)}"
            existing_pay = await db.execute(
                select(Payment).where(Payment.payment_intent_id == composite_id)
            )
            if not existing_pay.scalar_one_or_none():
                db.add(Payment(
                    order_id=order.id,
                    user_id=current_user.id,
                    payment_intent_id=composite_id,
                    provider="simulated",
                    provider_transaction_id=sim_session_id,
                    amount_ils=order.total_amount,
                    amount=order.total_amount,
                    currency="ILS",
                    status="paid",
                ))

        await db.commit()

        order_ids = [o.id for o in orders]

        async def _fulfill_bg_multi():
            async with pii_session_factory() as bg_db:
                try:
                    res = await bg_db.execute(select(Order).where(Order.id.in_(order_ids)))
                    bg_orders = res.scalars().all()
                    if bg_orders:
                        await trigger_supplier_fulfillment(bg_orders, bg_db)
                        await bg_db.commit()
                except Exception as _e:
                    print(f"[Fulfillment BG multi] error: {_e}")

        asyncio.create_task(_guarded_task(_fulfill_bg_multi()))

        return {
            "checkout_url": f"{frontend_url}/payment/success?session_id={sim_session_id}&simulated=1",
            "session_id": sim_session_id,
            "order_count": len(orders),
            "total_amount": sum(float(o.total_amount) for o in orders),
            "currency": "ILS",
            "mode": "simulated",
        }

    try:
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
    except Exception as stripe_err:
        print(f"[Payments] create-multi-checkout Stripe error: {stripe_err}")
        raise HTTPException(status_code=503, detail="שירות התשלומים אינו זמין כרגע. נסה שוב מאוחר יותר.")

    # Create a pending Payment record per order.
    # Use "<session_id>:<order_id>" to keep payment_intent_id unique per row
    # (the UNIQUE constraint is per-column; one Stripe session covers all orders).
    for order in orders:
        composite_id = f"{session.id}:{str(order.id)}"
        existing_pay = await db.execute(
            select(Payment).where(Payment.payment_intent_id == composite_id)
        )
        if not existing_pay.scalar_one_or_none():
            db.add(Payment(
                order_id=order.id,
                user_id=current_user.id,
                payment_intent_id=composite_id,
                provider="stripe",
                provider_transaction_id=session.id,
                amount_ils=order.total_amount,
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
    request: Request,
    session_id: str,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Called after Stripe redirects back — verifies payment and marks order(s) paid."""
    import stripe as stripe_sdk

    async def _fetch_session_rows_for_user(session_key: str):
        rows_res = await db.execute(
            select(Payment, Order)
            .join(Order, Payment.order_id == Order.id)
            .where(
                and_(
                    Order.user_id == current_user.id,
                    Payment.payment_intent_id.like(f"{session_key}:%"),
                )
            )
        )
        rows = rows_res.all()
        if rows:
            return rows

        rows_res = await db.execute(
            select(Payment, Order)
            .join(Order, Payment.order_id == Order.id)
            .where(
                and_(
                    Order.user_id == current_user.id,
                    Payment.payment_intent_id == session_key,
                )
            )
        )
        return rows_res.all()

    def _session_metadata_dict(session_obj) -> dict:
        meta = getattr(session_obj, "metadata", None)
        if isinstance(meta, dict):
            return meta
        if meta is not None:
            try:
                return dict(meta)
            except Exception:
                return {}
        return {}

    async def _fulfill_paid_orders_bg(order_ids: list):
        """Run supplier fulfillment in a detached session so verify returns fast."""
        if not order_ids:
            return
        async with pii_session_factory() as bg_db:
            try:
                res = await bg_db.execute(select(Order).where(Order.id.in_(order_ids)))
                bg_orders = res.scalars().all()
                if bg_orders:
                    await trigger_supplier_fulfillment(bg_orders, bg_db)
                    await bg_db.commit()
            except Exception as e:
                print(f"[Payments] fulfillment warning (verify bg): {e}")

    # ── Simulated payment (session_id starts with SIM-) ───────────────────────
    if session_id.startswith("SIM-"):
        if not _allow_simulated_payments_for_request(request):
            raise HTTPException(status_code=404, detail="Simulated sessions are disabled")
        # Find the payment record linked to this simulated session
        pay_res = await db.execute(select(Payment).where(Payment.payment_intent_id == session_id))
        pay = pay_res.scalar_one_or_none()
        if pay:
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

        # Multi simulated checkout stores records as "SIM-...:<order_id>"
        sim_rows = await db.execute(
            select(Payment, Order)
            .join(Order, Payment.order_id == Order.id)
            .where(
                and_(
                    Order.user_id == current_user.id,
                    Payment.payment_intent_id.like(f"{session_id}:%"),
                )
            )
        )
        pay_rows = sim_rows.all()
        if not pay_rows:
            raise HTTPException(status_code=404, detail="תשלום סימולציה לא נמצא")

        for payment, order in pay_rows:
            if order.status == "pending_payment":
                order.status = "confirmed"
            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            payment.payment_method = "simulated"
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
        return _build_verify_response_from_local_rows(pay_rows)

    stripe_key, _ = resolve_stripe_secret_key()
    if not _is_valid_stripe_secret_key(stripe_key):
        raise HTTPException(status_code=503, detail="Stripe not configured")

    stripe_sdk.api_key = stripe_key

    try:
        session = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: stripe_sdk.checkout.Session.retrieve(session_id)
        )
    except Exception as stripe_err:
        # Stripe may reject old/foreign session IDs (or transiently fail).
        # If we already have local paid records for this session, treat it as
        # verified to avoid false-negative payment failures on the success page.
        pay_rows = await _fetch_session_rows_for_user(session_id)

        if pay_rows:
            return _build_verify_response_from_local_rows(pay_rows)

        raise HTTPException(
            status_code=400,
            detail="לא ניתן לאמת את סשן התשלום מול Stripe. עבור להזמנות שלך לבדיקה.",
        ) from stripe_err

    # ── MULTI-ORDER SESSION ────────────────────────────────────────────────────
    metadata = _session_metadata_dict(session)
    order_ids_str = (metadata.get("order_ids") or "").strip()
    if order_ids_str:
        order_id_list = [oid.strip() for oid in order_ids_str.split(",") if oid.strip()]
        orders_res = await db.execute(
            select(Order).where(and_(Order.id.in_(order_id_list), Order.user_id == current_user.id))
        )
        multi_orders = orders_res.scalars().all()
        if not multi_orders:
            raise HTTPException(status_code=404, detail="Orders not found")

        if session.payment_status == "paid":
            newly_paid_order_ids = []
            for ord in multi_orders:
                if ord.status == "pending_payment":
                    ord.status = "paid"
                    newly_paid_order_ids.append(ord.id)
            # Update all payment records tied to this session.
            # Multi-checkout stores them as "<session_id>:<order_id>" so use LIKE.
            pays_res = await db.execute(select(Payment).where(Payment.payment_intent_id.like(f"{session_id}:%")))
            for pay in pays_res.scalars().all():
                pay.status = "paid"
                pay.paid_at = datetime.utcnow()
                pay.payment_method = session.payment_method_types[0] if session.payment_method_types else "card"
            # Create invoices idempotently (verify endpoint can be called multiple times).
            for ord in multi_orders:
                inv_exists = await db.execute(select(Invoice).where(Invoice.order_id == ord.id))
                if not inv_exists.scalar_one_or_none():
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
            await db.commit()

            # Run supplier automation after response-critical payment confirmation
            # to keep the success page fast.
            if newly_paid_order_ids:
                asyncio.create_task(_guarded_task(_fulfill_paid_orders_bg(newly_paid_order_ids)))

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
    order_id = metadata.get("order_id")
    if not order_id:
        # Some gateways/sessions return sparse metadata; fall back to local rows.
        pay_rows = await _fetch_session_rows_for_user(session_id)
        if pay_rows:
            return _build_verify_response_from_local_rows(pay_rows)
        raise HTTPException(status_code=400, detail="Invalid session metadata")

    result = await db.execute(
        select(Order).where(and_(Order.id == order_id, Order.user_id == current_user.id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if session.payment_status == "paid" and order.status == "pending_payment":
        order.status = "paid"
        paid_now_order_id = order.id
        pay_result = await db.execute(select(Payment).where(Payment.payment_intent_id == session_id))
        payment = pay_result.scalar_one_or_none()
        if payment:
            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            payment.payment_method = session.payment_method_types[0] if session.payment_method_types else "card"
        inv_exists = await db.execute(select(Invoice).where(Invoice.order_id == order.id))
        if not inv_exists.scalar_one_or_none():
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
        await db.commit()

        # Fire-and-forget supplier automation so verify-session returns immediately.
        asyncio.create_task(_guarded_task(_fulfill_paid_orders_bg([paid_now_order_id])))

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


@router.get("/api/v1/payments/suppliers/list")
async def list_supplier_payments(
    current_user=Depends(get_current_user),
    limit: int = 100,
    db: AsyncSession = Depends(get_pii_db),
):
    """Customer view: supplier payment lifecycle for this user's paid orders."""
    try:
        await _reconcile_pending_checkout_sessions_for_user(current_user.id, db)
    except Exception as e:
        print(f"[Payments] supplier list reconcile warning: {e}")

    rows_res = await db.execute(
        select(SupplierPayment, Order)
        .join(Order, SupplierPayment.order_id == Order.id)
        .where(
            and_(
                Order.user_id == current_user.id,
                Order.deleted_at.is_(None),
            )
        )
        .order_by(desc(SupplierPayment.created_at))
        .limit(limit)
    )
    rows = rows_res.all()

    # Keep only latest per (order, supplier) to avoid legacy duplicates in UI.
    unique: dict[str, dict] = {}
    suppressed = 0
    for supplier_payment, order in rows:
        supplier_key = str(supplier_payment.supplier_id) if supplier_payment.supplier_id else supplier_payment.supplier_name
        dedupe_key = f"{order.id}:{supplier_key}"
        if dedupe_key in unique:
            suppressed += 1
            continue
        unique[dedupe_key] = {
            "id": str(supplier_payment.id),
            "order_id": str(order.id),
            "order_number": order.order_number,
            "order_status": order.status,
            "supplier_name": supplier_payment.supplier_name,
            "amount_ils": float(supplier_payment.amount_ils),
            "customer_amount_ils": float(order.total_amount or 0),
            "supplier_customer_delta_ils": round(float(order.total_amount or 0) - float(supplier_payment.amount_ils or 0), 2),
            "currency": supplier_payment.currency,
            "status": supplier_payment.status,
            "provider": supplier_payment.provider,
            "spend_provider": (supplier_payment.metadata_json or {}).get("spend_provider") or supplier_payment.provider,
            "provider_payment_id": supplier_payment.provider_payment_id,
            "provider_reference": supplier_payment.provider_reference,
            "issuing_authorization_id": (supplier_payment.metadata_json or {}).get("issuing_authorization_id"),
            "issuing_authorization_status": (supplier_payment.metadata_json or {}).get("issuing_authorization_status"),
            "issuing_card_id": (supplier_payment.metadata_json or {}).get("issuing_card_id"),
            "supplier_purchase_status": (supplier_payment.metadata_json or {}).get("supplier_purchase_status"),
            "supplier_purchase_carrier": (supplier_payment.metadata_json or {}).get("supplier_purchase_carrier"),
            "tracking_number": supplier_payment.tracking_number or order.tracking_number,
            "tracking_url": supplier_payment.tracking_url or order.tracking_url,
            "failure_reason": supplier_payment.failure_reason,
            "paid_at": supplier_payment.paid_at,
            "supplier_refund_status": (supplier_payment.metadata_json or {}).get("supplier_refund_status"),
            "supplier_refund_amount_ils": (supplier_payment.metadata_json or {}).get("supplier_refund_amount_ils"),
            "supplier_refund_id": (supplier_payment.metadata_json or {}).get("supplier_refund_id"),
            "supplier_refunded_at": (supplier_payment.metadata_json or {}).get("supplier_refunded_at"),
            "created_at": supplier_payment.created_at,
        }

    return {
        "supplier_payments": list(unique.values()),
        "duplicate_rows_suppressed": suppressed,
    }


@router.post("/api/v1/payments/suppliers/{supplier_payment_id}/retry")
async def retry_supplier_payment(
    supplier_payment_id: str,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Admin: retry supplier charge for a failed/pending supplier payment."""
    row_res = await db.execute(
        select(SupplierPayment, Order)
        .join(Order, SupplierPayment.order_id == Order.id)
        .where(SupplierPayment.id == supplier_payment_id)
    )
    row = row_res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Supplier payment not found")

    supplier_payment, order = row
    if supplier_payment.status in ("paid", "tracking_received"):
        return {
            "message": "Supplier payment already completed",
            "status": supplier_payment.status,
            "supplier_payment_id": str(supplier_payment.id),
        }

    await trigger_supplier_fulfillment([order], db)
    await db.commit()
    await db.refresh(supplier_payment)

    return {
        "message": "Supplier payment retry executed",
        "supplier_payment_id": str(supplier_payment.id),
        "status": supplier_payment.status,
        "failure_reason": supplier_payment.failure_reason,
        "provider_payment_id": supplier_payment.provider_payment_id,
    }


@router.post("/api/v1/payments/suppliers/{supplier_payment_id}/refund-retry")
async def retry_supplier_refund(
    supplier_payment_id: str,
    request: Request,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Admin: retry supplier refund for a refunded/cancelled customer order."""
    row_res = await db.execute(
        select(SupplierPayment, Order)
        .join(Order, SupplierPayment.order_id == Order.id)
        .where(SupplierPayment.id == supplier_payment_id)
    )
    row = row_res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Supplier payment not found")

    supplier_payment, order = row
    try:
        body = await request.json()
    except Exception:
        body = {}

    reason = str(body.get("reason") or "retry_supplier_refund").strip()
    requested_amount = body.get("amount_ils")
    try:
        requested_amount_ils = float(requested_amount) if requested_amount is not None else None
    except Exception:
        raise HTTPException(status_code=422, detail="amount_ils must be a number")

    summary = await trigger_supplier_refund(
        order=order,
        db=db,
        reason=reason,
        customer_refund_amount_ils=requested_amount_ils,
    )
    await db.commit()

    return {
        "message": "Supplier refund retry executed",
        "supplier_payment_id": str(supplier_payment.id),
        "order_id": str(order.id),
        "order_number": order.order_number,
        "summary": summary,
    }


@router.put("/api/v1/payments/suppliers/{supplier_payment_id}/tracking")
async def update_supplier_tracking(
    supplier_payment_id: str,
    request: Request,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_pii_db),
):
    """Admin: update supplier-provided tracking details and move order to supplier_ordered."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    tracking_number = str(body.get("tracking_number") or "").strip()
    tracking_url_raw = str(body.get("tracking_url") or "").strip()
    if not tracking_number:
        raise HTTPException(status_code=422, detail="tracking_number is required")

    row_res = await db.execute(
        select(SupplierPayment, Order)
        .join(Order, SupplierPayment.order_id == Order.id)
        .where(SupplierPayment.id == supplier_payment_id)
    )
    row = row_res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Supplier payment not found")

    supplier_payment, order = row
    tracking_url = _build_tracking_url_from_number(tracking_number, tracking_url_raw)

    supplier_payment.tracking_number = tracking_number
    supplier_payment.tracking_url = tracking_url
    supplier_payment.status = "tracking_received"

    order.tracking_number = tracking_number
    order.tracking_url = tracking_url
    order.status = "supplier_ordered"

    title = "📦 מספר מעקב עודכן להזמנה"
    msg = (
        f"הספק עדכן מספר מעקב להזמנה {order.order_number}.\n"
        f"מספר מעקב: {tracking_number}\n"
        + (f"קישור מעקב: {tracking_url}" if tracking_url else "")
    )
    db.add(Notification(
        user_id=order.user_id,
        title=title,
        message=msg,
        type="order_update",
        data={
            "order_id": str(order.id),
            "order_number": order.order_number,
            "supplier_payment_id": str(supplier_payment.id),
            "tracking_number": tracking_number,
            "tracking_url": tracking_url,
        },
    ))
    asyncio.create_task(_guarded_task(publish_notification(str(order.user_id), {"type": "order_update", "title": title, "message": msg})))

    await db.commit()
    return {
        "message": "Tracking updated",
        "supplier_payment_id": str(supplier_payment.id),
        "order_id": str(order.id),
        "order_number": order.order_number,
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
    }


@router.post("/api/v1/payments/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_pii_db)):
    """Stripe webhook for async payment confirmation (backup to verify-session)."""
    import stripe as stripe_sdk
    from BACKEND_DATABASE_MODELS import StripeWebhookLog
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_key, _ = resolve_stripe_secret_key()
    stripe_sdk.api_key = stripe_key

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

    try:
        # Use the verified raw payload as canonical JSON for robust access.
        event_payload = json.loads(payload.decode("utf-8"))
    except Exception:
        if isinstance(event, dict):
            event_payload = event
        elif hasattr(event, "to_dict"):
            event_payload = event.to_dict()
        else:
            event_payload = {}

    # ── Gap 6: Idempotency check (webhook deduplication) ────────────────────────────────
    # Check if we've already processed this exact event
    event_id = str(event_payload.get("id", "") or "")
    event_type = str(event_payload.get("type", "") or "")
    
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
                payload=event_payload,
                processed=False,
            )
            db.add(existing_log)
            await db.commit()

    # ── Process the webhook event ──────────────────────────────────────────────────────
    processing_error = None
    try:
        if event_type == "checkout.session.completed":
            session = (event_payload.get("data") or {}).get("object") or {}
            if str(session.get("payment_status") or "") == "paid":
                orders_to_fulfill = []
                metadata = session.get("metadata") or {}
                session_id = str(session.get("id") or "")
                payment_method_types = session.get("payment_method_types") or []
                payment_method = "card"
                if isinstance(payment_method_types, list) and payment_method_types:
                    payment_method = str(payment_method_types[0] or "card")

                # Single-order
                order_id = metadata.get("order_id")
                if order_id:
                    res = await db.execute(select(Order).where(Order.id == order_id))
                    order = res.scalar_one_or_none()
                    if order and order.status == "pending_payment":
                        order.status = "paid"
                        orders_to_fulfill.append(order)

                # Multi-order
                order_ids_str = metadata.get("order_ids", "")
                if order_ids_str:
                    oid_list = [x.strip() for x in order_ids_str.split(",") if x.strip()]
                    res = await db.execute(select(Order).where(Order.id.in_(oid_list)))
                    for ord in res.scalars().all():
                        if ord.status == "pending_payment":
                            ord.status = "paid"
                            orders_to_fulfill.append(ord)

                # Mark related payment records as paid for this checkout session.
                if session_id:
                    pays_res = await db.execute(
                        select(Payment).where(
                            or_(
                                Payment.payment_intent_id == session_id,
                                Payment.provider_transaction_id == session_id,
                                Payment.payment_intent_id.like(f"{session_id}:%"),
                            )
                        )
                    )
                    for pay in pays_res.scalars().all():
                        if pay.status != "paid":
                            pay.status = "paid"
                            pay.paid_at = datetime.utcnow()
                            pay.payment_method = payment_method

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

    stripe_key, _ = resolve_stripe_secret_key()
    if not _is_valid_stripe_secret_key(stripe_key):
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
        supplier_refund_summary = None
        if order:
            supplier_refund_summary = await trigger_supplier_refund(
                order=order,
                db=db,
                reason=reason,
                customer_refund_amount_ils=refund_ils,
            )

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
        return {
            "message": "Refund processed",
            "refund_id": stripe_refund.id,
            "amount": refund_ils,
            "supplier_refund": supplier_refund_summary,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/v1/payments/history")
async def get_payment_history(current_user=Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
    try:
        await _reconcile_pending_checkout_sessions_for_user(current_user.id, db)
    except Exception as e:
        print(f"[Payments] history reconcile warning: {e}")

    result = await db.execute(
        select(Payment, Order)
        .join(Order, Payment.order_id == Order.id)
        .where(
            and_(
                Order.user_id == current_user.id,
                Order.deleted_at.is_(None),
            )
        )
        .order_by(desc(Payment.created_at))
        .limit(max(limit * 3, 100))
    )
    rows = result.all()

    seen_keys = set()
    items = []
    for payment, order in rows:
        key = payment.provider_transaction_id or payment.payment_intent_id or f"{order.id}:{payment.status}:{payment.amount}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        items.append({
            "id": str(payment.id),
            "order_id": str(order.id),
            "order_number": order.order_number,
            "amount": float(payment.amount),
            "status": payment.status,
            "payment_method": payment.payment_method,
            "provider": payment.provider,
            "provider_transaction_id": payment.provider_transaction_id,
            "created_at": payment.created_at,
        })
        if len(items) >= limit:
            break

    return {"payments": items}
