"""
Eurosender webhook — SANDBOX ONLY.

POST /api/v1/webhooks/eurosender

Signature contract (SUPERSEDED 2026-09-25 by a second, more precise official
Eurosender support answer — the 2026-09-21 "raw body only" contract below was
tested against a genuine Sandbox delivery and did NOT match):

    message = Webhook-Event + Webhook-Id + raw_request_body   (no delimiter,
              no separator, no whitespace — plain concatenation of the exact
              header string values and the exact raw body bytes)
    signature = HMAC-SHA256(sandbox_webhook_signing_secret_utf8, message)
    header value = "sha256=" + hex(signature)                 (prefix format
              empirically evidenced 2026-09-21 against a real delivery; the
              support answer did not re-specify the header encoding, so the
              already-observed hex+prefix format is kept, not re-guessed)
    No timestamp, no nonce. Sandbox and Production use SEPARATE signing
    secrets — this file only ever uses eurosender_config.webhook_secret(),
    which must hold the SANDBOX dashboard secret while EUROSENDER_SANDBOX=1.

verify_signature() enforces this on the raw bytes BEFORE JSON parsing, dedup
or any handling; a missing/malformed/wrong signature (or unset secret) -> 401.

VERIFIED 2026-09-25 (Phase 22): this exact contract was cryptographically
matched against a genuine Eurosender Sandbox delivery (cancelling real
Sandbox order 935766-26 produced a live `order_cancelled` webhook, Webhook-Id
10847; the HMAC computed from its captured event/id/raw-body against the
Sandbox signing secret equalled the received signature, and this same
unmodified verify_signature() accepted it). See FIXES_TRACKER.md 2026-09-25
(Phases 19-22) for the full chain of evidence, including the earlier
2026-09-21 non-match that was under the since-superseded "raw body only"
contract, not this one.

This endpoint still:
  - refuses to operate at all unless EUROSENDER_SANDBOX=1 (returns 501) — the
    signature algorithm being verified does not by itself enable production;
    EUROSENDER_ENABLED / EUROSENDER_SANDBOX / the supplier allowlist are the
    separate, independent gates for that.
  - eurosender_config.EUROSENDER_WEBHOOK_SIGNATURE_VERIFIED is now True,
    recording that this signature contract is confirmed — not that the
    integration overall is production-ready.

Events handled (payload shapes taken from integrators.eurosender.com/apis/
webhooks, confirmed 2026-09-08):
  order_label_ready            {triggerId, orderCode}
  order_submitted_to_courier   {triggerId, orderCode, courierId}
  order_tracking_ready         {triggerId, orderCode, trackingCodes:[...]}
  order_cancelled              {triggerId, orderCode}
  delivery_status_updated      {notifications:[{trackingDetails:{parcels:[...]}}]}

Only orders with shipping_provider == 'eurosender' are ever touched here —
every other order in the system is structurally unreachable from this file.
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import Notification, Order, SupplierPayment, User, get_pii_db
from BACKEND_AUTH_SECURITY import publish_notification
from services.shipping import eurosender_config

logger = logging.getLogger("eurosender_webhook")
router = APIRouter()

_DEDUP_TTL_S = 86400

KNOWN_EVENTS = frozenset({
    "order_label_ready",
    "order_submitted_to_courier",
    "order_tracking_ready",
    "order_cancelled",
    "delivery_status_updated",
})

# Order.status values that mean "already advanced past supplier_ordered" —
# a webhook event must never move status BACKWARD past one of these.
_TERMINAL_OR_ADVANCED = ("shipped", "delivered", "cancelled", "refunded")


_SIG_PREFIX = "sha256="


def verify_signature(webhook_event: str, webhook_id: str, body: bytes, signature_header: str, secret: str) -> bool:
    """Official Eurosender contract (support answer, 2026-09-25):
        message = webhook_event + webhook_id + raw_body_bytes   (plain
                  concatenation — no delimiter, no JSON parsing/reserialization)
        HMAC-SHA256(sandbox_signing_secret_utf8, message), compared in
        constant time against the `sha256=<hex>` header.
    `webhook_event` / `webhook_id` must be the EXACT header string values —
    never normalized, defaulted, or substituted. Fails closed on an empty
    secret, a missing/malformed header, or a non-bytes body.
    """
    if not secret or not signature_header or not isinstance(body, (bytes, bytearray)):
        return False
    if not webhook_event or not webhook_id:
        return False
    if not signature_header.startswith(_SIG_PREFIX):
        return False
    received = signature_header[len(_SIG_PREFIX):].strip().lower()
    message = webhook_event.encode("utf-8") + webhook_id.encode("utf-8") + bytes(body)
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), received.encode("utf-8", "replace"))


async def _get_redis_safe():
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        return await get_redis()
    except Exception:
        return None


async def _find_order_by_eurosender_code(db: AsyncSession, order_code: str) -> Order | None:
    if not order_code:
        return None
    res = await db.execute(
        select(Order).where(
            Order.eurosender_order_code == order_code,
            Order.shipping_provider == "eurosender",
        )
    )
    return res.scalar_one_or_none()


async def _alert_admins(db: AsyncSession, title: str, message: str, data: dict) -> None:
    admins_res = await db.execute(select(User).where(User.is_admin == True))
    for admin in admins_res.scalars().all():
        db.add(Notification(user_id=admin.id, type="system", title=title, message=message, data=data))
        try:
            await publish_notification(str(admin.id), {"type": "system", "title": title, "message": message})
        except Exception:
            pass


def _normalize_notification_payload(payload: dict) -> dict:
    """PROVEN 2026-09-12 against a genuine live Eurosender Sandbox delivery
    (order 408540-26, event order_cancelled): the real payload was
    `{"notifications": [{"orderCode": "408540-26", "triggerId": 4}]}` —
    NOT the flat `{triggerId, orderCode}` shape the public docs show for
    events 1-4. This wraps the same `notifications[]` envelope already
    correctly handled for delivery_status_updated (event 5).

    Checks for a top-level orderCode FIRST (preserves compatibility if the
    documented flat shape is ever genuinely used for some event) and falls
    back to notifications[0] only when orderCode is absent at the top level
    — never asserts one shape is exclusively correct without evidence.
    """
    if payload.get("orderCode") is not None:
        return payload
    notifications = payload.get("notifications")
    if isinstance(notifications, list) and notifications and isinstance(notifications[0], dict):
        return notifications[0]
    return payload


async def handle_event(event: str, payload: dict, db: AsyncSession) -> None:
    # delivery_status_updated has its OWN pre-existing, correct handling of a
    # differently-shaped (and potentially multi-element) notifications[]
    # envelope further below — do not unwrap it here, or that loop silently
    # stops iterating anything.
    if event != "delivery_status_updated":
        payload = _normalize_notification_payload(payload)
    order_code = payload.get("orderCode")

    if event == "order_label_ready":
        order = await _find_order_by_eurosender_code(db, order_code)
        if not order:
            logger.info("[EurosenderWebhook] order_label_ready for unknown orderCode=%s (not ours or not yet reconciled)", order_code)
            return
        order.eurosender_status = "label_ready"
        await db.flush()

    elif event == "order_submitted_to_courier":
        order = await _find_order_by_eurosender_code(db, order_code)
        if not order:
            logger.info("[EurosenderWebhook] order_submitted_to_courier for unknown orderCode=%s", order_code)
            return
        if order.status not in _TERMINAL_OR_ADVANCED and order.status != "supplier_ordered":
            order.status = "supplier_ordered"
        await db.flush()

    elif event == "order_tracking_ready":
        order = await _find_order_by_eurosender_code(db, order_code)
        if not order:
            logger.info("[EurosenderWebhook] order_tracking_ready for unknown orderCode=%s", order_code)
            return
        tracking_codes = payload.get("trackingCodes") or []
        if not tracking_codes:
            logger.warning("[EurosenderWebhook] order_tracking_ready with empty trackingCodes for order %s", order_code)
            return
        # Multi-leg shipments: the LAST entry is the final-mile carrier —
        # expose that one to the customer (per the sandbox-contract Area 10 note).
        final_leg = tracking_codes[-1]
        tracking_number = final_leg.get("trackingNumber")
        tracking_url = final_leg.get("trackingUrl")
        if not tracking_number:
            logger.warning("[EurosenderWebhook] order_tracking_ready with no trackingNumber for order %s", order_code)
            return

        order.tracking_number = tracking_number
        order.tracking_url = tracking_url
        if order.status not in _TERMINAL_OR_ADVANCED:
            order.status = "supplier_ordered"

        sp_res = await db.execute(
            select(SupplierPayment).where(SupplierPayment.shipping_provider_ref == order_code)
        )
        for sp in sp_res.scalars().all():
            sp.tracking_number = tracking_number
            sp.tracking_url = tracking_url
            if sp.status == "paid":
                sp.status = "tracking_received"

        # ONLY place a Eurosender order's customer gets a tracking
        # notification — a REAL carrier tracking number now exists (Phase 13:
        # never notify before this point, never fabricate a number).
        db.add(Notification(
            user_id=order.user_id,
            type="order_update",
            title=f"📦 ההזמנה {order.order_number} הועברה למוביל",
            message=(
                f"ההזמנה {order.order_number} בדרך אליך.\n"
                f"מספר מעקב: {tracking_number}\n"
                + (f"קישור מעקב: {tracking_url}" if tracking_url else "")
            ),
            data={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "tracking_number": tracking_number,
                "tracking_url": tracking_url,
                "shipping_provider": "eurosender",
            },
        ))
        try:
            await publish_notification(str(order.user_id), {
                "type": "order_update",
                "title": f"📦 ההזמנה {order.order_number} הועברה למוביל",
                "message": f"מספר מעקב: {tracking_number}",
            })
        except Exception:
            pass
        await db.flush()

    elif event == "order_cancelled":
        order = await _find_order_by_eurosender_code(db, order_code)
        if not order:
            logger.info("[EurosenderWebhook] order_cancelled for unknown orderCode=%s", order_code)
            return
        order.eurosender_status = "cancelled"
        await db.flush()
        # Cancellation is a financial event — flag for manual review rather
        # than auto-triggering a refund (Phase 15: alert + manual-review
        # workflow, never an automatic refund from a webhook handler).
        await _alert_admins(
            db,
            title=f"⚠️ Eurosender ביטל את המשלוח עבור {order.order_number}",
            message=(
                f"Eurosender orderCode={order_code} בוטל. נדרש טיפול ידני: "
                "בדוק אם יש להנפיק החזר ללקוח."
            ),
            data={"order_id": str(order.id), "order_number": order.order_number, "eurosender_order_code": order_code, "needs_manual_review": True},
        )
        await db.commit()

    elif event == "delivery_status_updated":
        notifications = payload.get("notifications") or []
        for note in notifications:
            parcels = (note.get("trackingDetails") or {}).get("parcels") or []
            for parcel in parcels:
                p_order_code = parcel.get("orderCode")
                order = await _find_order_by_eurosender_code(db, p_order_code)
                if not order:
                    continue
                current_status = str(parcel.get("currentStatus") or "")
                if current_status == "Delivered" and order.status not in ("delivered", "cancelled", "refunded"):
                    order.status = "delivered"
                    order.delivered_at = datetime.utcnow()
                elif current_status == "InTransit" and order.status not in _TERMINAL_OR_ADVANCED:
                    order.status = "shipped"
                    order.shipped_at = order.shipped_at or datetime.utcnow()
                await db.flush()
    else:
        logger.warning("[EurosenderWebhook] unrecognized event type: %s", event)


@router.post("/api/v1/webhooks/eurosender")
async def eurosender_webhook(request: Request, db: AsyncSession = Depends(get_pii_db)):
    if not eurosender_config.sandbox_mode():
        return Response(
            status_code=501,
            content="Eurosender webhook is sandbox-only (EUROSENDER_SANDBOX must be 1) — "
                    "the Webhook-Signature algorithm is unverified, so this endpoint refuses "
                    "to operate in a non-sandbox configuration.",
        )

    webhook_id = request.headers.get("Webhook-Id", "")
    webhook_event = request.headers.get("Webhook-Event", "")
    webhook_signature = request.headers.get("Webhook-Signature", "")

    body_bytes = await request.body()

    # Verify on the exact received bytes, before any parse/dedup/handling.
    signature_ok = verify_signature(webhook_event, webhook_id, body_bytes, webhook_signature, eurosender_config.webhook_secret())
    if not signature_ok:
        logger.warning(
            "[EurosenderWebhook] signature rejected event=%s id=%s signature_present=%s",
            webhook_event, webhook_id, bool(webhook_signature),
        )
        return Response(status_code=401)

    try:
        payload = json.loads(body_bytes or b"{}")
    except Exception:
        logger.warning("[EurosenderWebhook] invalid JSON body, event=%s id=%s", webhook_event, webhook_id)
        return Response(status_code=200)

    # Safe structured log only — never the raw body or signature value.
    # The real payload shape (notifications[]-wrapped for events 1-4) was
    # captured and root-caused 2026-09-12 against genuine Sandbox deliveries
    # (orders 408540-26, 425907-26); see _normalize_notification_payload()
    # and test_eurosender_webhook.py's real-payload regression tests.
    logger.info(
        "[EurosenderWebhook][sandbox] event=%s id=%s signature_present=%s signature_verified=%s",
        webhook_event, webhook_id, bool(webhook_signature), signature_ok,
    )

    if webhook_id:
        try:
            r = await _get_redis_safe()
            if r is not None:
                dedup_key = f"eurosender:webhook_seen:{webhook_id}"
                if await r.exists(dedup_key):
                    logger.info("[EurosenderWebhook] duplicate Webhook-Id=%s, skipping", webhook_id)
                    return Response(status_code=200)
                await r.set(dedup_key, "1", ex=_DEDUP_TTL_S)
        except Exception as exc:
            logger.warning("[EurosenderWebhook] Redis dedup check failed (continuing without dedup): %s", exc)

    if webhook_event not in KNOWN_EVENTS:
        logger.warning("[EurosenderWebhook] unrecognized event type: %s", webhook_event)
        return Response(status_code=200)

    try:
        await handle_event(webhook_event, payload, db)
        await db.commit()
    except Exception:
        logger.exception("[EurosenderWebhook] handler error for event=%s id=%s", webhook_event, webhook_id)
        await db.rollback()

    return Response(status_code=200)
