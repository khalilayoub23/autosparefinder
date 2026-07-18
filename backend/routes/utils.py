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
from currency_rate import get_usd_to_ils_rate
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
    default_provider = os.getenv("SUPPLIER_SPEND_PROVIDER", "issuing")
    return _normalize_supplier_spend_provider(from_supplier or default_provider)


def _list_test_virtual_issuing_card_ids(stripe_key: str, limit: int = 25) -> list[str]:
    """Return active virtual issuing card ids from Stripe test mode."""
    import stripe as stripe_sdk

    if not str(stripe_key or "").startswith("sk_test_"):
        return []

    stripe_sdk.api_key = stripe_key
    cards = stripe_sdk.issuing.Card.list(limit=limit).data
    ids: list[str] = []
    for card in cards:
        if getattr(card, "status", None) == "active" and getattr(card, "type", None) == "virtual":
            ids.append(str(card.id))
    return ids


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


def _extract_issuing_decline_reason(auth: dict) -> str:
    history = auth.get("request_history") or []
    if not history:
        return ""
    first = history[0] if isinstance(history[0], dict) else {}
    return str(first.get("reason") or "").strip().lower()


def _compute_test_topup_amount_minor(amount_minor: int, buffer_minor: int, max_minor: int) -> int:
    amount = max(int(amount_minor or 0), 0)
    buffer_value = max(int(buffer_minor or 0), 0)
    max_value = max(int(max_minor or 0), 0)
    if amount <= 0:
        raise ValueError("Topup amount requires positive authorization amount")
    if max_value <= 0:
        raise ValueError("Topup max amount must be positive")
    candidate = amount + buffer_value
    if candidate <= 0:
        candidate = amount
    return min(candidate, max_value)


def _build_topup_source_candidates(configured_source: str | None) -> list[str]:
    candidates: list[str] = []
    for source in [configured_source, "btok_us_verified", "tok_visa_debit"]:
        value = str(source or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _create_test_topup_https(
    stripe_key: str,
    amount_minor: int,
    currency: str,
    source_token: str,
    description: str,
) -> dict:
    import json
    import urllib.parse
    import urllib.request
    import urllib.error

    form_data = {
        "amount": str(amount_minor),
        "currency": currency,
        "source": source_token,
        "description": description,
    }

    payload = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(
        "https://api.stripe.com/v1/topups",
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
        raise RuntimeError(f"Stripe topup failed: {body[:500]}") from http_err


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


async def _send_post_payment_notification(order_db, tracking_number: str, tracking_url: str, db: AsyncSession) -> None:
    """
    Sends order confirmation + tracking to customer via their original channel.
    - WA_* orders → WhatsApp message
    - TG_* orders → Telegram message
    - WEB_* orders → email + in-app (in-app already done by caller)
    """
    try:
        order_num = order_db.order_number or ""
        frontend_url = os.getenv("FRONTEND_URL", "https://autosparefinder.co.il")

        # Determine channel from order number prefix
        if order_num.startswith("WA"):
            channel = "whatsapp"
        elif order_num.startswith("TG"):
            channel = "telegram"
        else:
            channel = "web"

        # Build customer-friendly message
        msg_he = (
            f"✅ ההזמנה שלך אושרה! 🎉\n\n"
            f"מספר הזמנה: {order_num}\n"
            f"מספר מעקב: {tracking_number}\n"
            f"🔗 עקוב אחר החבילה: {tracking_url}\n\n"
            f"נשמח לעזור בכל שאלה 😊"
        )

        if channel == "whatsapp":
            # Look up phone from conversation context
            from sqlalchemy import select as _sel
            from BACKEND_DATABASE_MODELS import Conversation
            conv_res = await db.execute(
                _sel(Conversation)
                .where(Conversation.user_id == order_db.user_id)
                .order_by(Conversation.last_message_at.desc())
                .limit(1)
            )
            conv = conv_res.scalar_one_or_none()
            phone = None
            if conv and isinstance(conv.context, dict):
                phone = conv.context.get("phone_e164") or conv.context.get("phone")
            if phone:
                from social.whatsapp_provider import send_message as _wa_send
                await _wa_send(phone, msg_he)

        elif channel == "telegram":
            # Look up chat_id from conversation context
            from sqlalchemy import select as _sel
            from BACKEND_DATABASE_MODELS import Conversation
            conv_res = await db.execute(
                _sel(Conversation)
                .where(Conversation.user_id == order_db.user_id)
                .order_by(Conversation.last_message_at.desc())
                .limit(1)
            )
            conv = conv_res.scalar_one_or_none()
            chat_id = None
            if conv and isinstance(conv.context, dict):
                chat_id = conv.context.get("telegram_chat_id")
            if chat_id:
                from social.telegram_publisher import send_telegram_message
                await send_telegram_message(chat_id, msg_he)

        elif channel == "web":
            # Send email to registered user
            try:
                from sqlalchemy import select as _sel
                from BACKEND_DATABASE_MODELS import pii_session_factory, User
                async with pii_session_factory() as pii_db:
                    user_res = await pii_db.execute(
                        _sel(User).where(User.id == order_db.user_id)
                    )
                    user = user_res.scalar_one_or_none()
                    if user and user.email:
                        from routes.email_utils import send_order_confirmation_email
                        await send_order_confirmation_email(
                            to_email=user.email,
                            full_name=user.full_name or "לקוח יקר",
                            order_number=order_num,
                            tracking_number=tracking_number,
                            tracking_url=tracking_url,
                            order_url=f"{frontend_url}/orders",
                        )
            except Exception as email_err:
                print(f"[PostPayment] Email send failed: {email_err}")

    except Exception as e:
        print(f"[PostPayment] Notification failed for order {getattr(order_db, 'order_number', '?')}: {e}")


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
    try:
        async with async_session_factory() as _cat_rate_db:
            default_ils_per_usd = await get_usd_to_ils_rate(_cat_rate_db, fallback=default_ils_per_usd)
    except Exception:
        pass
    default_auto_topup_on_insufficient = _env_flag("SUPPLIER_ISSUING_AUTO_TOPUP_ON_INSUFFICIENT_FUNDS", "1")
    default_auto_topup_source = (os.getenv("SUPPLIER_ISSUING_AUTO_TOPUP_SOURCE", "btok_us_verified") or "btok_us_verified").strip()
    default_auto_topup_buffer_minor = int(os.getenv("SUPPLIER_ISSUING_TOPUP_BUFFER_MINOR", "5000") or "5000")
    default_auto_topup_max_minor = int(os.getenv("SUPPLIER_ISSUING_TOPUP_MAX_MINOR", "500000") or "500000")
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
                    select(
                        SupplierPart.id,
                        Supplier.id,
                        Supplier.name,
                        Supplier.country,
                        Supplier.website,
                        Supplier.credentials,
                        SupplierPart.price_ils,
                        SupplierPart.price_usd,
                        SupplierPart.shipping_cost_ils,
                        SupplierPart.shipping_cost_usd,
                    )
                    .join(Supplier, SupplierPart.supplier_id == Supplier.id)
                    .where(SupplierPart.id.in_(supplier_part_ids))
                )
                for (
                    supplier_part_id,
                    supplier_id,
                    supplier_name,
                    supplier_country,
                    supplier_website,
                    supplier_credentials,
                    supplier_price_ils,
                    supplier_price_usd,
                    supplier_shipping_ils,
                    supplier_shipping_usd,
                ) in supplier_rows.all():
                    supplier_meta[str(supplier_part_id)] = {
                        "supplier_id": supplier_id,
                        "supplier_name": supplier_name or "Unknown supplier",
                        "supplier_country": supplier_country or "il",
                        "supplier_website": supplier_website or "",
                        "credentials": supplier_credentials or {},
                        "supplier_price_ils": supplier_price_ils,
                        "supplier_price_usd": supplier_price_usd,
                        "supplier_shipping_ils": supplier_shipping_ils,
                        "supplier_shipping_usd": supplier_shipping_usd,
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
                    "shipping_ils": 0.0,
                }

            qty = max(int(oi.quantity or 1), 1)

            supplier_unit_ils = 0.0
            if meta:
                raw_price_ils = meta.get("supplier_price_ils")
                raw_price_usd = meta.get("supplier_price_usd")
                if raw_price_ils is not None and float(raw_price_ils or 0) > 0:
                    supplier_unit_ils = float(raw_price_ils)
                elif raw_price_usd is not None and float(raw_price_usd or 0) > 0:
                    supplier_unit_ils = float(raw_price_usd) * float(default_ils_per_usd)

            if supplier_unit_ils <= 0:
                fallback_total = float(oi.total_price or (oi.unit_price * qty) or 0)
                supplier_unit_ils = fallback_total / qty if qty else fallback_total

            item_cost = supplier_unit_ils * qty

            supplier_shipping_ils = 0.0
            if meta:
                raw_shipping_ils = meta.get("supplier_shipping_ils")
                raw_shipping_usd = meta.get("supplier_shipping_usd")
                if raw_shipping_ils is not None and float(raw_shipping_ils or 0) > 0:
                    supplier_shipping_ils = float(raw_shipping_ils)
                elif raw_shipping_usd is not None and float(raw_shipping_usd or 0) > 0:
                    supplier_shipping_ils = float(raw_shipping_usd) * float(default_ils_per_usd)

            by_supplier[supplier_id]["items"].append(oi)
            by_supplier[supplier_id]["total_ils"] += item_cost
            by_supplier[supplier_id]["shipping_ils"] = max(
                float(by_supplier[supplier_id].get("shipping_ils") or 0.0),
                supplier_shipping_ils,
            )

        for bucket in by_supplier.values():
            bucket["items_cost_ils"] = round(float(bucket.get("total_ils") or 0.0), 2)
            bucket["shipping_ils"] = round(float(bucket.get("shipping_ils") or 0.0), 2)
            bucket["total_ils"] = round(bucket["items_cost_ils"] + bucket["shipping_ils"], 2)

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
                    configured_issuing_card_id = str(
                        supplier_credentials.get("stripe_issuing_card_id")
                        or default_issuing_card_id
                    ).strip()

                    if not stripe_configured and not allow_simulated_supplier_payments:
                        raise ValueError("Stripe supplier payout is not configured")

                    if not stripe_configured and allow_simulated_supplier_payments:
                        issuing_card_id = configured_issuing_card_id or default_issuing_card_id
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

                        candidate_card_ids: list[str] = []
                        if configured_issuing_card_id:
                            candidate_card_ids.append(configured_issuing_card_id)

                        discovered_card_ids = await asyncio.get_running_loop().run_in_executor(
                            None,
                            lambda: _list_test_virtual_issuing_card_ids(stripe_key),
                        )
                        for discovered_card_id in discovered_card_ids:
                            if discovered_card_id and discovered_card_id not in candidate_card_ids:
                                candidate_card_ids.append(discovered_card_id)

                        if not candidate_card_ids:
                            raise ValueError("Missing stripe_issuing_card_id for supplier and no active virtual card found")

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

                        auto_topup_on_insufficient = bool(
                            supplier_credentials.get("stripe_issuing_auto_topup_on_insufficient_funds", default_auto_topup_on_insufficient)
                        )
                        auto_topup_source = str(
                            supplier_credentials.get("stripe_issuing_auto_topup_source")
                            or default_auto_topup_source
                        ).strip()
                        topup_source_candidates = _build_topup_source_candidates(auto_topup_source)
                        auto_topup_buffer_minor = int(
                            supplier_credentials.get("stripe_issuing_topup_buffer_minor")
                            or default_auto_topup_buffer_minor
                        )
                        auto_topup_max_minor = int(
                            supplier_credentials.get("stripe_issuing_topup_max_minor")
                            or default_auto_topup_max_minor
                        )

                        auth = None
                        issuing_card_id = ""
                        last_reject_detail = ""
                        topup_obj: dict | None = None
                        topup_attempted = False
                        for candidate_card_id in candidate_card_ids:
                            candidate_auth = await asyncio.get_running_loop().run_in_executor(
                                None,
                                lambda cid=candidate_card_id: _create_test_issuing_authorization_https(
                                    stripe_key=stripe_key,
                                    card_id=cid,
                                    amount_minor=amount_minor,
                                    currency=normalized_currency,
                                    supplier_name=supplier_name,
                                    order_number=order_db.order_number,
                                ),
                            )
                            candidate_status = str(candidate_auth.get("status") or "")
                            candidate_approved = bool(candidate_auth.get("approved"))
                            candidate_reason = _extract_issuing_decline_reason(candidate_auth)

                            if candidate_approved:
                                auth = candidate_auth
                                issuing_card_id = candidate_card_id
                                break

                            if (
                                not topup_attempted
                                and auto_topup_on_insufficient
                                and candidate_reason == "insufficient_funds"
                                and normalized_currency == "usd"
                                and topup_source_candidates
                            ):
                                topup_attempted = True
                                topup_amount_minor = _compute_test_topup_amount_minor(
                                    amount_minor=amount_minor,
                                    buffer_minor=auto_topup_buffer_minor,
                                    max_minor=auto_topup_max_minor,
                                )
                                topup_error: Exception | None = None
                                selected_topup_source = ""
                                for source_candidate in topup_source_candidates:
                                    try:
                                        topup_obj = await asyncio.get_running_loop().run_in_executor(
                                            None,
                                            lambda src=source_candidate: _create_test_topup_https(
                                                stripe_key=stripe_key,
                                                amount_minor=topup_amount_minor,
                                                currency="usd",
                                                source_token=src,
                                                description=f"Auto topup for supplier payout {order_db.order_number}",
                                            ),
                                        )
                                        selected_topup_source = source_candidate
                                        break
                                    except Exception as source_err:
                                        topup_error = source_err

                                if not topup_obj:
                                    if topup_error:
                                        raise topup_error
                                    raise ValueError("Stripe topup failed without a detailed error")

                                retry_auth = await asyncio.get_running_loop().run_in_executor(
                                    None,
                                    lambda cid=candidate_card_id: _create_test_issuing_authorization_https(
                                        stripe_key=stripe_key,
                                        card_id=cid,
                                        amount_minor=amount_minor,
                                        currency=normalized_currency,
                                        supplier_name=supplier_name,
                                        order_number=order_db.order_number,
                                    ),
                                )
                                if bool(retry_auth.get("approved")):
                                    auth = retry_auth
                                    issuing_card_id = candidate_card_id
                                    if selected_topup_source:
                                        retry_auth["_auto_topup_source"] = selected_topup_source
                                    break
                                candidate_auth = retry_auth
                                candidate_status = str(candidate_auth.get("status") or "")
                                candidate_reason = _extract_issuing_decline_reason(candidate_auth)

                            last_reject_detail = (
                                f"card={candidate_card_id}, status={candidate_status}, "
                                f"reason={candidate_reason or 'unknown'}, auth_id={candidate_auth.get('id')}"
                            )

                        if not auth:
                            topup_detail = ""
                            if topup_obj:
                                metadata = dict(supplier_payment.metadata_json or {})
                                metadata.update({
                                    "issuing_auto_topup_id": topup_obj.get("id"),
                                    "issuing_auto_topup_status": topup_obj.get("status"),
                                    "issuing_auto_topup_amount": topup_obj.get("amount"),
                                    "issuing_auto_topup_currency": topup_obj.get("currency"),
                                })
                                supplier_payment.metadata_json = metadata
                                topup_detail = (
                                    f", topup_id={topup_obj.get('id')}, "
                                    f"topup_status={topup_obj.get('status')}"
                                )
                            raise ValueError(
                                "Issuing authorization was not approved on available virtual cards"
                                + (f" ({last_reject_detail}{topup_detail})" if (last_reject_detail or topup_detail) else "")
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
                            "issuing_authorization_decline_reason": _extract_issuing_decline_reason(auth),
                            "issuing_card_id": issuing_card_id,
                            "issuing_currency": normalized_currency,
                            "issuing_amount_minor": amount_minor,
                            "issuing_amount_major": amount_major,
                            "issuing_ils_per_usd": ils_per_usd,
                        })
                        if topup_obj:
                            metadata.update({
                                "issuing_auto_topup_id": topup_obj.get("id"),
                                "issuing_auto_topup_status": topup_obj.get("status"),
                                "issuing_auto_topup_amount": topup_obj.get("amount"),
                                "issuing_auto_topup_currency": topup_obj.get("currency"),
                                "issuing_auto_topup_source": auth.get("_auto_topup_source") if isinstance(auth, dict) else None,
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

                # ── Post-payment notifications: WhatsApp / Telegram / Web email ──────
                asyncio.create_task(_guarded_task(_send_post_payment_notification(
                    order_db=order_db,
                    tracking_number=number,
                    tracking_url=url,
                    db=db,
                )))

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
