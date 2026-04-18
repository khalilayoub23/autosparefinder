"""Shared utilities for route modules, independent of BACKEND_API_ROUTES."""
import asyncio
import os
import io
import hashlib as _hashlib
import base64
import uuid
import clamd as _clamd
from types import SimpleNamespace
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import (
    User,
    Order,
    OrderItem,
    Notification,
    Supplier,
    SupplierPart,
    SupplierPayment,
    async_session_factory,
)
from BACKEND_AUTH_SECURITY import publish_notification
from routes.stripe_config import resolve_stripe_secret_key, is_valid_stripe_secret_key

# Cap fire-and-forget asyncio.create_task() fan-out.
_TASK_SEMAPHORE = asyncio.Semaphore(50)


async def _guarded_task(coro) -> None:
    """Acquire the shared semaphore before running a fire-and-forget coroutine."""
    async with _TASK_SEMAPHORE:
        await coro


def _scan_bytes_for_virus(content: bytes) -> tuple:
    """
    Scan raw bytes with ClamAV daemon.
    Returns: ('clean', None) | ('infected', '<VirusName>') | ('skipped', None)
    Tries Unix socket first, falls back to TCP, then skips gracefully.
    """
    for _make_scanner in (
        lambda: _clamd.ClamdUnixSocket(),
        lambda: _clamd.ClamdNetworkSocket(host=os.getenv("CLAMD_HOST", "clamav"), port=3310),
    ):
        try:
            scanner = _make_scanner()
            result = scanner.instream(io.BytesIO(content))
            status, virus_name = result.get("stream", ("skipped", None))
            return (status.lower(), virus_name)
        except Exception:
            continue
    # ClamAV daemon unavailable — skip scan (dev/CI without ClamAV)
    return ("skipped", None)


def _mask_supplier(name: str) -> str:
    """Return a deterministic numbered alias for supplier names."""
    if not name:
        return "ספק"
    digest = int(_hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
    num = (digest % 9999) + 1
    return f"ספק #{num}"


def _normalize_base_url(raw: str) -> str | None:
    """Return normalized scheme://host[:port] or None for invalid input."""
    if not raw:
        return None
    value = str(raw).strip()
    if not value or value.lower() == "null":
        return None
    try:
        parsed = urlsplit(value)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _first_header_value(raw: str) -> str:
    return (raw or "").split(",", 1)[0].strip()


def _is_internal_host(host_value: str) -> bool:
    host = _first_header_value(host_value).lower()
    host_no_port = host.split(":", 1)[0]
    internal_hosts = {
        "frontend",
        "backend",
        "nginx",
        "localhost",
        "127.0.0.1",
        "autospare_backend",
        "autospare_frontend",
        "autospare_nginx",
    }
    if host_no_port in internal_hosts:
        return True
    return host_no_port.endswith(".internal") or host_no_port.endswith(".local")


def _origin_from_request_headers(request: Request) -> str | None:
    # Priority: explicit frontend origin header, then browser origin, then referer.
    # Browser requests (real users and Playwright) naturally include one of these.
    for header in ("x-frontend-origin", "origin", "referer"):
        candidate = _first_header_value(request.headers.get(header, ""))
        normalized = _normalize_base_url(candidate)
        if normalized:
            return normalized
    return None


def _get_frontend_url(request: Request) -> str:
    """Resolve frontend base URL across external browsers and internal E2E traffic."""
    header_origin = _origin_from_request_headers(request)
    if header_origin:
        return header_origin

    forwarded_host = _first_header_value(request.headers.get("x-forwarded-host", ""))
    host = forwarded_host or _first_header_value(request.headers.get("host", ""))
    proto = _first_header_value(request.headers.get("x-forwarded-proto", "")) or request.url.scheme or "http"
    derived_request_base = _normalize_base_url(f"{proto}://{host}") if host else None

    frontend_public = _normalize_base_url(os.getenv("FRONTEND_PUBLIC_URL", ""))
    frontend_internal = _normalize_base_url(os.getenv("FRONTEND_INTERNAL_URL", ""))
    frontend_legacy = _normalize_base_url(os.getenv("FRONTEND_URL", ""))

    if host and _is_internal_host(host):
        return frontend_internal or frontend_legacy or "http://frontend"

    if derived_request_base:
        return derived_request_base

    if frontend_public:
        return frontend_public

    codespace = os.getenv("CODESPACE_NAME", "")
    if codespace:
        domain = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
        return f"https://{codespace}-5173.{domain}"

    return frontend_legacy or "http://localhost:5173"


def _normalize_supplier_spend_provider(raw_provider: str | None) -> str:
    provider = (raw_provider or "").strip().lower()
    if provider in {"payments", "issuing"}:
        return provider
    return "payments"


def _resolve_supplier_spend_provider(supplier_credentials: dict | None) -> str:
    from_supplier = ""
    if isinstance(supplier_credentials, dict):
        from_supplier = str(supplier_credentials.get("supplier_spend_provider") or "").strip()
    default_provider = os.getenv("SUPPLIER_SPEND_PROVIDER", "payments")
    return _normalize_supplier_spend_provider(from_supplier or default_provider)


def _convert_ils_to_minor_units(amount_ils: float, currency: str, ils_per_usd: float) -> tuple[int, str, float]:
    ccy = (currency or "ils").strip().lower()
    if ccy == "ils":
        major = float(amount_ils)
    elif ccy == "usd":
        if ils_per_usd <= 0:
            raise ValueError("ILS/USD rate must be positive")
        major = float(amount_ils) / float(ils_per_usd)
    else:
        raise ValueError(f"Unsupported issuing currency: {currency}")

    minor = int(round(major * 100))
    if minor <= 0:
        raise ValueError("Supplier amount must be positive")
    return minor, ccy, round(major, 2)


def _create_test_issuing_authorization_https(
    stripe_key: str,
    card_id: str,
    amount_minor: int,
    currency: str,
    supplier_name: str,
    order_number: str,
) -> dict:
    import json
    import urllib.parse
    import urllib.request
    import urllib.error

    form_data = {
        "card": card_id,
        "amount": str(amount_minor),
        "currency": currency,
        "merchant_data[name]": f"Supplier {supplier_name}",
        "merchant_data[category]": "miscellaneous_general_merchandise",
        "merchant_data[country]": "US",
        "merchant_data[city]": "New York",
        "merchant_data[network_id]": f"mid_{order_number[-8:]}",
    }

    payload = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(
        "https://api.stripe.com/v1/test_helpers/issuing/authorizations",
        data=payload,
        method="POST",
    )
    basic = base64.b64encode(f"{stripe_key}:".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as http_err:
        body = ""
        try:
            body = http_err.read().decode("utf-8")
        except Exception:
            body = str(http_err)
        raise RuntimeError(f"Issuing authorization failed: {body[:500]}") from http_err


def _build_agent_supplier_payload(by_supplier: dict[str, dict], supplier_keys: set[str]) -> dict:
    payload: dict[str, dict] = {}
    for supplier_key in supplier_keys:
        bucket = by_supplier.get(supplier_key)
        if not bucket:
            continue
        supplier = SimpleNamespace(
            id=supplier_key,
            name=bucket.get("supplier_name") or "Unknown supplier",
            country=bucket.get("supplier_country") or "il",
            website=bucket.get("supplier_website") or "",
        )
        payload[supplier_key] = {
            "supplier": supplier,
            "items": [
                {
                    "part_name": oi.part_name,
                    "part_sku": oi.part_sku or "",
                    "supplier_sku": "",
                    "manufacturer": oi.manufacturer or "",
                    "quantity": int(oi.quantity or 0),
                    "unit_cost_ils": float(oi.unit_price or 0),
                    "shipping_ils": 0.0,
                    "item_total_ils": round(float(oi.total_price or 0), 2),
                    "warranty_months": oi.warranty_months or 12,
                    "availability": "In Stock",
                    "estimated_delivery_days": 14,
                }
                for oi in bucket.get("items", [])
            ],
            "total_cost_ils": round(float(bucket.get("total_ils") or 0), 2),
        }
    return payload


def _tracking_by_supplier_name(rows: list[dict]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for row in rows or []:
        supplier_name = str(row.get("supplier") or "").strip()
        if supplier_name and supplier_name not in mapping:
            mapping[supplier_name] = row
    return mapping


async def trigger_supplier_fulfillment(paid_orders: list, db: AsyncSession) -> None:
    """Run supplier-fulfillment flow only after payment confirmation.

        This function creates auditable supplier spend records and supports two spend
        providers:
            - payments: Stripe PaymentIntent charge
            - issuing : Stripe Issuing authorization (sandbox test helper)
        After successful supplier spend, the OrdersAgent is invoked to continue the
        supplier purchase cycle (tracking/order progression).
    """

    import stripe as stripe_sdk

    def _env_flag(name: str, default: str = "0") -> bool:
        return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")

    def _tracking_url(carrier: str, tracking_number: str) -> str:
        n = (tracking_number or "").strip()
        c = (carrier or "").lower()
        if not n:
            return ""
        if c == "ups" or n.upper().startswith("1Z"):
            return f"https://www.ups.com/track?tracknum={n}&requester=ST/trackdetails"
        if c == "fedex" or n.isdigit() and len(n) == 12:
            return f"https://www.fedex.com/fedextrack/?trknbr={n}"
        if c == "dhl" or n.isdigit() and len(n) == 10:
            return f"https://www.dhl.com/en/express/tracking.html?AWB={n}"
        return f"https://parcelsapp.com/en/tracking/{n}"

    def _fake_tracking_number() -> tuple[str, str]:
        token = str(uuid.uuid4()).replace("-", "").upper()
        return (f"1Z{token[:16]}", "UPS")

    admins_res = await db.execute(select(User).where(User.is_admin == True))
    admins = admins_res.scalars().all()

    stripe_key, _ = resolve_stripe_secret_key()
    stripe_configured = is_valid_stripe_secret_key(stripe_key)
    if stripe_configured:
        stripe_sdk.api_key = stripe_key

    default_supplier_payment_method = (os.getenv("SUPPLIER_TEST_PAYMENT_METHOD", "pm_card_visa") or "pm_card_visa").strip()
    default_issuing_card_id = (os.getenv("SUPPLIER_ISSUING_CARD_ID", "") or "").strip()
    default_issuing_currency = (os.getenv("SUPPLIER_ISSUING_CURRENCY", "usd") or "usd").strip().lower()
    default_ils_per_usd = float(os.getenv("SUPPLIER_ISSUING_ILS_PER_USD", "3.65") or "3.65")
    allow_simulated_supplier_payments = _env_flag("ALLOW_SIMULATED_SUPPLIER_PAYMENTS", "0")

    for order in paid_orders:
        order_res = await db.execute(select(Order).where(Order.id == order.id))
        order_db = order_res.scalar_one_or_none()
        if not order_db:
            continue

        items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order_db.id))
        order_items = items_res.scalars().all()

        if not order_items:
            order_db.status = "processing"
            for admin in admins:
                _title = f"⚠️ {order_db.order_number} – אין נתוני ספק"
                _msg = f"ההזמנה {order_db.order_number} שולמה אך אין פריטים. טיפול ידני נדרש."
                db.add(Notification(
                    user_id=admin.id,
                    type="supplier_order",
                    title=_title,
                    message=_msg,
                    data={"order_id": str(order_db.id), "order_number": order_db.order_number, "needs_manual": True},
                ))
                asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "supplier_order", "title": _title, "message": _msg})))
            continue

        supplier_part_ids = [oi.supplier_part_id for oi in order_items if oi.supplier_part_id]
        supplier_meta: dict[str, dict] = {}
        if supplier_part_ids:
            async with async_session_factory() as cat_db:
                supplier_rows = await cat_db.execute(
                    select(SupplierPart.id, Supplier.id, Supplier.name, Supplier.country, Supplier.website, Supplier.credentials)
                    .join(Supplier, SupplierPart.supplier_id == Supplier.id)
                    .where(SupplierPart.id.in_(supplier_part_ids))
                )
                for supplier_part_id, supplier_id, supplier_name, supplier_country, supplier_website, supplier_credentials in supplier_rows.all():
                    supplier_meta[str(supplier_part_id)] = {
                        "supplier_id": supplier_id,
                        "supplier_name": supplier_name or "Unknown supplier",
                        "supplier_country": supplier_country or "il",
                        "supplier_website": supplier_website or "",
                        "credentials": supplier_credentials or {},
                    }

        by_supplier: dict[str, dict] = {}
        for oi in order_items:
            meta = supplier_meta.get(str(oi.supplier_part_id))
            supplier_id = str(meta["supplier_id"]) if meta else "unknown"
            supplier_name = meta["supplier_name"] if meta else (oi.supplier_name or "Unknown supplier")
            supplier_credentials = meta["credentials"] if meta else {}

            if supplier_id not in by_supplier:
                by_supplier[supplier_id] = {
                    "supplier_id": meta["supplier_id"] if meta else None,
                    "supplier_name": supplier_name,
                    "supplier_country": meta.get("supplier_country") if meta else "il",
                    "supplier_website": meta.get("supplier_website") if meta else "",
                    "credentials": supplier_credentials,
                    "items": [],
                    "total_ils": 0.0,
                }

            item_cost = float(oi.total_price or (oi.unit_price * oi.quantity))
            by_supplier[supplier_id]["items"].append(oi)
            by_supplier[supplier_id]["total_ils"] += item_cost

        supplier_paid_count = 0
        supplier_payments_by_key: dict[str, SupplierPayment] = {}
        suppliers_ready_for_purchase: set[str] = set()

        for supplier_key, bucket in by_supplier.items():
            supplier_id = bucket["supplier_id"]
            supplier_name = bucket["supplier_name"]
            supplier_credentials = bucket["credentials"] if isinstance(bucket["credentials"], dict) else {}
            supplier_total = round(float(bucket["total_ils"]), 2)
            spend_provider = _resolve_supplier_spend_provider(supplier_credentials)

            sp_res = await db.execute(
                select(SupplierPayment).where(
                    SupplierPayment.order_id == order_db.id,
                    SupplierPayment.supplier_id == supplier_id,
                )
            )
            supplier_payment = sp_res.scalar_one_or_none()

            if not supplier_payment:
                supplier_payment = SupplierPayment(
                    order_id=order_db.id,
                    user_id=order_db.user_id,
                    supplier_id=supplier_id,
                    supplier_name=supplier_name,
                    amount_ils=supplier_total,
                    currency="ILS",
                    status="pending",
                    provider="stripe",
                    metadata_json={
                        "order_number": order_db.order_number,
                        "supplier_items_count": len(bucket["items"]),
                    },
                )
                db.add(supplier_payment)
                await db.flush()
            else:
                supplier_payment.supplier_name = supplier_name
                supplier_payment.amount_ils = supplier_total

            supplier_payments_by_key[str(supplier_key)] = supplier_payment

            metadata = dict(supplier_payment.metadata_json or {})
            metadata["spend_provider"] = spend_provider
            metadata["supplier_items_count"] = len(bucket["items"])
            metadata["order_number"] = order_db.order_number
            supplier_payment.metadata_json = metadata

            if spend_provider == "issuing":
                supplier_payment.provider = "stripe_issuing"
            elif supplier_payment.provider in (None, "", "stripe_issuing"):
                supplier_payment.provider = "stripe"

            if supplier_payment.status in ("paid", "tracking_received"):
                supplier_paid_count += 1
                if supplier_payment.status == "paid" and not supplier_payment.tracking_number:
                    suppliers_ready_for_purchase.add(str(supplier_key))
                continue

            supplier_pm = str(
                supplier_credentials.get("stripe_test_payment_method")
                or default_supplier_payment_method
            ).strip()
            auto_fake_tracking = bool(supplier_credentials.get("auto_fake_tracking", False))

            try:
                if spend_provider == "issuing":
                    issuing_card_id = str(
                        supplier_credentials.get("stripe_issuing_card_id")
                        or default_issuing_card_id
                    ).strip()
                    if not issuing_card_id:
                        raise ValueError("Missing stripe_issuing_card_id for supplier")

                    if not stripe_configured and not allow_simulated_supplier_payments:
                        raise ValueError("Stripe supplier payout is not configured")

                    if not stripe_configured and allow_simulated_supplier_payments:
                        fake_provider_id = f"SIM-IAUTH-{str(uuid.uuid4())[:12].upper()}"
                        supplier_payment.status = "paid"
                        supplier_payment.provider = "simulated"
                        supplier_payment.provider_payment_id = fake_provider_id
                        supplier_payment.provider_reference = fake_provider_id
                        supplier_payment.payment_method = "simulated"
                        supplier_payment.paid_at = datetime.utcnow()
                        supplier_payment.failure_reason = None

                        metadata = dict(supplier_payment.metadata_json or {})
                        metadata["issuing_authorization_id"] = fake_provider_id
                        metadata["issuing_card_id"] = issuing_card_id
                        metadata["simulated"] = True
                        supplier_payment.metadata_json = metadata
                    else:
                        if not stripe_key.startswith("sk_test_"):
                            raise ValueError("Automated Issuing authorization is supported in sandbox mode only")

                        issuing_currency = str(
                            supplier_credentials.get("stripe_issuing_currency")
                            or default_issuing_currency
                        ).strip().lower()
                        ils_per_usd = float(
                            supplier_credentials.get("stripe_issuing_ils_per_usd")
                            or default_ils_per_usd
                        )
                        amount_minor, normalized_currency, amount_major = _convert_ils_to_minor_units(
                            supplier_total,
                            issuing_currency,
                            ils_per_usd,
                        )
                        auth = await asyncio.get_running_loop().run_in_executor(
                            None,
                            lambda: _create_test_issuing_authorization_https(
                                stripe_key=stripe_key,
                                card_id=issuing_card_id,
                                amount_minor=amount_minor,
                                currency=normalized_currency,
                                supplier_name=supplier_name,
                                order_number=order_db.order_number,
                            ),
                        )

                        balance_tx = (auth.get("balance_transactions") or [])
                        balance_tx_id = balance_tx[0].get("id") if balance_tx else None

                        supplier_payment.status = "paid"
                        supplier_payment.provider = "stripe_issuing"
                        supplier_payment.provider_payment_id = auth.get("id")
                        supplier_payment.provider_reference = balance_tx_id
                        supplier_payment.payment_method = f"issuing_card:{issuing_card_id[-4:]}"
                        supplier_payment.paid_at = datetime.utcnow()
                        supplier_payment.failure_reason = None

                        metadata = dict(supplier_payment.metadata_json or {})
                        metadata.update({
                            "issuing_authorization_id": auth.get("id"),
                            "issuing_authorization_status": auth.get("status"),
                            "issuing_card_id": issuing_card_id,
                            "issuing_currency": normalized_currency,
                            "issuing_amount_minor": amount_minor,
                            "issuing_amount_major": amount_major,
                            "issuing_ils_per_usd": ils_per_usd,
                        })
                        supplier_payment.metadata_json = metadata
                else:
                    if not stripe_configured and not allow_simulated_supplier_payments:
                        raise ValueError("Stripe supplier payout is not configured")

                    if not stripe_configured and allow_simulated_supplier_payments:
                        fake_provider_id = f"SIM-SP-{str(uuid.uuid4())[:12].upper()}"
                        supplier_payment.status = "paid"
                        supplier_payment.provider = "simulated"
                        supplier_payment.provider_payment_id = fake_provider_id
                        supplier_payment.provider_reference = fake_provider_id
                        supplier_payment.payment_method = "simulated"
                        supplier_payment.paid_at = datetime.utcnow()
                        supplier_payment.failure_reason = None
                    else:
                        amount_agorot = int(round(float(supplier_total) * 100))
                        if amount_agorot <= 0:
                            raise ValueError("Supplier amount must be positive")

                        payment_intent = await asyncio.get_running_loop().run_in_executor(
                            None,
                            lambda: stripe_sdk.PaymentIntent.create(
                                amount=amount_agorot,
                                currency="ils",
                                confirm=True,
                                payment_method=supplier_pm,
                                automatic_payment_methods={
                                    "enabled": True,
                                    "allow_redirects": "never",
                                },
                                description=f"Supplier payout {order_db.order_number} -> {supplier_name}",
                                metadata={
                                    "type": "supplier_payout",
                                    "order_id": str(order_db.id),
                                    "order_number": order_db.order_number,
                                    "supplier_id": str(supplier_id) if supplier_id else "",
                                    "supplier_name": supplier_name,
                                },
                            ),
                        )

                        supplier_payment.status = "paid"
                        supplier_payment.provider = "stripe"
                        supplier_payment.provider_payment_id = getattr(payment_intent, "id", None)
                        supplier_payment.provider_reference = getattr(payment_intent, "latest_charge", None)
                        supplier_payment.payment_method = supplier_pm
                        supplier_payment.paid_at = datetime.utcnow()
                        supplier_payment.failure_reason = None

                supplier_paid_count += 1
                suppliers_ready_for_purchase.add(str(supplier_key))
            except Exception as pay_err:
                if spend_provider == "issuing":
                    supplier_payment.provider = "stripe_issuing"
                supplier_payment.status = "failed"
                supplier_payment.failure_reason = str(pay_err)[:500]

            # Optional cycle-test mode for fake suppliers: create tracking instantly
            # after successful supplier charge.
            if supplier_payment.status == "paid" and auto_fake_tracking and not supplier_payment.tracking_number:
                number, carrier = _fake_tracking_number()
                url = _tracking_url(carrier, number)
                supplier_payment.tracking_number = number
                supplier_payment.tracking_url = url
                supplier_payment.status = "tracking_received"
                order_db.tracking_number = number
                order_db.tracking_url = url
                order_db.status = "supplier_ordered"

                _title_user = "📦 ההזמנה הועברה לספק ונוצר מספר מעקב"
                _msg_user = (
                    f"הזמנה {order_db.order_number} שולמה לספק {supplier_name}.\n"
                    f"מספר מעקב: {number}\n"
                    f"קישור מעקב: {url}"
                )
                db.add(Notification(
                    user_id=order_db.user_id,
                    type="order_update",
                    title=_title_user,
                    message=_msg_user,
                    data={
                        "order_id": str(order_db.id),
                        "order_number": order_db.order_number,
                        "tracking_number": number,
                        "tracking_url": url,
                    },
                ))
                asyncio.create_task(_guarded_task(publish_notification(str(order_db.user_id), {"type": "order_update", "title": _title_user, "message": _msg_user})))
                metadata = dict(supplier_payment.metadata_json or {})
                metadata["supplier_purchase_status"] = "ordered"
                metadata["supplier_purchase_carrier"] = carrier
                supplier_payment.metadata_json = metadata

            if supplier_payment.status == "paid":
                for admin in admins:
                    _title_admin = f"💸 תשלום לספק נקלט עבור {order_db.order_number}"
                    _msg_admin = (
                        f"שולם לספק {supplier_name} עבור הזמנה {order_db.order_number}.\n"
                        f"סכום: ₪{supplier_total:.2f}\n"
                        f"מסלול: {supplier_payment.provider}\n"
                        f"מזהה תשלום: {supplier_payment.provider_payment_id or '—'}"
                    )
                    db.add(Notification(
                        user_id=admin.id,
                        type="supplier_order",
                        title=_title_admin,
                        message=_msg_admin,
                        data={
                            "order_id": str(order_db.id),
                            "order_number": order_db.order_number,
                            "supplier_name": supplier_name,
                            "supplier_payment_id": str(supplier_payment.id),
                            "amount_ils": supplier_total,
                            "provider": supplier_payment.provider,
                            "provider_payment_id": supplier_payment.provider_payment_id,
                            "tracking_required": not bool(supplier_payment.tracking_number),
                        },
                    ))
                    asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "supplier_order", "title": _title_admin, "message": _msg_admin})))
            elif supplier_payment.status == "failed":
                for admin in admins:
                    _title_fail = f"⚠️ תשלום לספק נכשל עבור {order_db.order_number}"
                    _msg_fail = (
                        f"נכשל תשלום לספק {supplier_name} עבור הזמנה {order_db.order_number}.\n"
                        f"שגיאה: {supplier_payment.failure_reason or 'Unknown error'}"
                    )
                    db.add(Notification(
                        user_id=admin.id,
                        type="supplier_order",
                        title=_title_fail,
                        message=_msg_fail,
                        data={
                            "order_id": str(order_db.id),
                            "order_number": order_db.order_number,
                            "supplier_name": supplier_name,
                            "supplier_payment_id": str(supplier_payment.id),
                            "status": "failed",
                            "failure_reason": supplier_payment.failure_reason,
                        },
                    ))
                    asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "supplier_order", "title": _title_fail, "message": _msg_fail})))

        # Continue supplier purchase cycle through OrdersAgent after successful supplier spend.
        if suppliers_ready_for_purchase:
            try:
                from BACKEND_AI_AGENTS import OrdersAgent

                agent_payload = _build_agent_supplier_payload(by_supplier, suppliers_ready_for_purchase)
                if agent_payload:
                    orders_agent = OrdersAgent()
                    tracking_rows = await orders_agent.auto_fulfill_order(order_db, agent_payload, db)
                    tracking_by_supplier = _tracking_by_supplier_name(tracking_rows or [])

                    for supplier_key in suppliers_ready_for_purchase:
                        sp_obj = supplier_payments_by_key.get(str(supplier_key))
                        if not sp_obj:
                            continue
                        meta = dict(sp_obj.metadata_json or {})
                        tracking = tracking_by_supplier.get(sp_obj.supplier_name)
                        if tracking:
                            sp_obj.tracking_number = tracking.get("tracking_number")
                            sp_obj.tracking_url = tracking.get("tracking_url")
                            if sp_obj.status == "paid":
                                sp_obj.status = "tracking_received"
                            meta["supplier_purchase_status"] = "ordered"
                            meta["supplier_purchase_carrier"] = tracking.get("carrier")
                        else:
                            meta["supplier_purchase_status"] = "submitted"
                        sp_obj.metadata_json = meta
            except Exception as agent_err:
                err_msg = str(agent_err)[:500]
                print(f"[Fulfillment] OrdersAgent handoff failed for {order_db.order_number}: {err_msg}")
                for supplier_key in suppliers_ready_for_purchase:
                    sp_obj = supplier_payments_by_key.get(str(supplier_key))
                    if not sp_obj:
                        continue
                    meta = dict(sp_obj.metadata_json or {})
                    meta["supplier_purchase_status"] = "agent_failed"
                    meta["supplier_purchase_error"] = err_msg
                    sp_obj.metadata_json = meta

        # Keep order lifecycle accurate: no supplier_ordered status unless tracking exists.
        if order_db.status in ("paid", "confirmed") and supplier_paid_count > 0 and not order_db.tracking_number:
            order_db.status = "processing"

    await db.flush()


async def trigger_supplier_refund(
    order: Order,
    db: AsyncSession,
    reason: str,
    customer_refund_amount_ils: float | None = None,
) -> dict:
    """Refund supplier payouts after customer refund is issued.

    The refund amount is proportional to the customer refund when partial refunds
    are used. Refund metadata is stored in SupplierPayment.metadata_json.
    """

    import stripe as stripe_sdk

    def _env_flag(name: str, default: str = "0") -> bool:
        return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")

    summary = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "processed": 0,
        "refunded": 0,
        "skipped": 0,
        "failed": 0,
        "items": [],
    }

    sp_res = await db.execute(select(SupplierPayment).where(SupplierPayment.order_id == order.id))
    supplier_payments = sp_res.scalars().all()
    if not supplier_payments:
        return summary

    order_total = float(order.total_amount or 0)
    req_amount = float(customer_refund_amount_ils or 0)
    if req_amount <= 0:
        req_amount = order_total

    ratio = 1.0
    if order_total > 0 and req_amount > 0:
        ratio = max(0.0, min(1.0, req_amount / order_total))

    stripe_key, _ = resolve_stripe_secret_key()
    stripe_configured = is_valid_stripe_secret_key(stripe_key)
    allow_simulated_supplier_refunds = _env_flag("ALLOW_SIMULATED_SUPPLIER_PAYMENTS", "0")
    if stripe_configured:
        stripe_sdk.api_key = stripe_key

    for sp in supplier_payments:
        summary["processed"] += 1
        meta = dict(sp.metadata_json or {})
        existing_refund_status = str(meta.get("supplier_refund_status") or "").lower().strip()

        if existing_refund_status in ("succeeded", "simulated"):
            summary["skipped"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "already_refunded",
            })
            continue

        # Refund only supplier payments that were actually paid before.
        if sp.status not in ("paid", "tracking_received", "cancelled"):
            summary["skipped"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "not_paid_yet",
            })
            continue

        target_amount_ils = round(float(sp.amount_ils or 0) * ratio, 2)
        if target_amount_ils <= 0:
            summary["skipped"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "zero_amount",
            })
            continue

        if sp.provider == "simulated" or str(sp.provider_payment_id or "").startswith("SIM-SP-"):
            meta.update({
                "supplier_refund_status": "simulated",
                "supplier_refund_amount_ils": target_amount_ils,
                "supplier_refunded_at": datetime.utcnow().isoformat(),
                "supplier_refund_reason": reason,
            })
            sp.metadata_json = meta
            sp.status = "cancelled"
            sp.failure_reason = None
            summary["refunded"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "simulated",
                "refund_amount_ils": target_amount_ils,
            })
            continue

        if sp.provider == "stripe_issuing" or str(sp.provider_payment_id or "").startswith("iauth_"):
            meta.update({
                "supplier_refund_status": "manual_required",
                "supplier_refund_error": "Issuing refunds require card-network reversal/credit flow",
                "supplier_refund_attempted_at": datetime.utcnow().isoformat(),
                "supplier_refund_reason": reason,
            })
            sp.metadata_json = meta
            sp.failure_reason = "Supplier refund requires issuing reversal workflow"
            summary["failed"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "manual_required",
                "error": "Issuing reversal required",
            })
            continue

        if not stripe_configured and not allow_simulated_supplier_refunds:
            meta.update({
                "supplier_refund_status": "failed",
                "supplier_refund_error": "Stripe not configured for supplier refund",
                "supplier_refund_attempted_at": datetime.utcnow().isoformat(),
            })
            sp.metadata_json = meta
            sp.failure_reason = "Supplier refund failed: Stripe not configured"
            summary["failed"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "failed",
                "error": "Stripe not configured",
            })
            continue

        if not sp.provider_payment_id:
            meta.update({
                "supplier_refund_status": "failed",
                "supplier_refund_error": "Missing supplier provider payment ID",
                "supplier_refund_attempted_at": datetime.utcnow().isoformat(),
            })
            sp.metadata_json = meta
            sp.failure_reason = "Supplier refund failed: missing provider payment ID"
            summary["failed"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "failed",
                "error": "Missing provider payment ID",
            })
            continue

        try:
            refund_cents = int(round(target_amount_ils * 100))
            if refund_cents <= 0:
                raise ValueError("Computed supplier refund amount is too small")

            stripe_refund = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: stripe_sdk.Refund.create(
                    payment_intent=sp.provider_payment_id,
                    amount=refund_cents,
                    reason="requested_by_customer",
                    metadata={
                        "type": "supplier_refund",
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "supplier_payment_id": str(sp.id),
                        "supplier_name": sp.supplier_name,
                    },
                ),
            )

            refunded_ils = float(stripe_refund.amount) / 100
            meta.update({
                "supplier_refund_status": "succeeded",
                "supplier_refund_id": stripe_refund.id,
                "supplier_refund_amount_ils": refunded_ils,
                "supplier_refunded_at": datetime.utcnow().isoformat(),
                "supplier_refund_reason": reason,
            })
            sp.metadata_json = meta
            sp.failure_reason = None
            # For full refunds mark supplier payment lifecycle as cancelled.
            if ratio >= 0.999:
                sp.status = "cancelled"

            summary["refunded"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "succeeded",
                "refund_amount_ils": refunded_ils,
                "refund_id": stripe_refund.id,
            })
        except Exception as refund_err:
            err_msg = str(refund_err)[:500]
            meta.update({
                "supplier_refund_status": "failed",
                "supplier_refund_error": err_msg,
                "supplier_refund_attempted_at": datetime.utcnow().isoformat(),
            })
            sp.metadata_json = meta
            sp.failure_reason = f"Supplier refund failed: {err_msg}"
            summary["failed"] += 1
            summary["items"].append({
                "supplier_payment_id": str(sp.id),
                "supplier_name": sp.supplier_name,
                "status": "failed",
                "error": err_msg,
            })

    return summary


def _clamd_ping() -> bool:
    """Returns True if ClamAV daemon is reachable."""
    for _make_scanner in (
        lambda: _clamd.ClamdUnixSocket(),
        lambda: _clamd.ClamdNetworkSocket(
            host=os.getenv("CLAMD_HOST", "clamav"), port=3310),
    ):
        try:
            _make_scanner().ping()
            return True
        except Exception:
            continue
    return False
