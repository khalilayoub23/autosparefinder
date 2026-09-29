"""
Eurosender webhook.

POST /api/v1/webhooks/eurosender

Operates whenever EUROSENDER_SANDBOX is on (testing) OR EUROSENDER_ENABLED is
on (production is live) — refuses only when the integration is disabled in
both directions. See the route function's own comment for why this must
never be an unconditional "sandbox only" ban (production-hardening closure,
2026-09-29): that used to make this endpoint reject every real production
webhook forever, the moment production was ever activated.

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


async def _apply_tracking_codes(db: AsyncSession, order: Order, order_code: str, tracking_codes: list) -> bool:
    """Persist the REAL carrier tracking number for an Eurosender order and
    notify the customer — the single place this happens, shared by
    order_tracking_ready AND order_submitted_to_courier.

    Why shared (Phase 26, real-delivery evidence 2026-09-25): Eurosender's own
    docs state order_tracking_ready fires only "if they weren't already
    available when submitted". Real Sandbox order_submitted_to_courier
    deliveries for 3 of 4 tested orders already carried `trackingCodes` with a
    real trackingNumber, and the matching order_tracking_ready never came — so
    handling tracking ONLY on order_tracking_ready left orders.tracking_number
    NULL forever for the common case.

    Returns True if a tracking number was present in the payload (whether
    newly stored or already stored), False if there is nothing to apply.
    Idempotent: the same number arriving again (e.g. via both events) never
    re-notifies the customer.
    Multi-leg shipments: the LAST entry is the final-mile carrier — expose that
    one to the customer (sandbox-contract Area 10 note).
    """
    if not tracking_codes:
        return False
    final_leg = tracking_codes[-1]
    tracking_number = final_leg.get("trackingNumber")
    tracking_url = final_leg.get("trackingUrl")
    if not tracking_number:
        return False
    if order.tracking_number == tracking_number:
        return True  # already stored — do not double-notify the customer

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

    # ONLY place a Eurosender order's customer gets a tracking notification —
    # a REAL carrier tracking number now exists (Phase 13: never notify before
    # this point, never fabricate a number).
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
    return True


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
        # Real payloads carry trackingCodes here when tracking already exists
        # at submission (order_tracking_ready then never fires). null
        # trackingNumber (e.g. 'selection' service) is normal: nothing to apply.
        await _apply_tracking_codes(db, order, order_code, payload.get("trackingCodes") or [])

    elif event == "order_tracking_ready":
        order = await _find_order_by_eurosender_code(db, order_code)
        if not order:
            logger.info("[EurosenderWebhook] order_tracking_ready for unknown orderCode=%s", order_code)
            return
        tracking_codes = payload.get("trackingCodes") or []
        if not tracking_codes:
            logger.warning("[EurosenderWebhook] order_tracking_ready with empty trackingCodes for order %s", order_code)
            return
        if not await _apply_tracking_codes(db, order, order_code, tracking_codes):
            logger.warning("[EurosenderWebhook] order_tracking_ready with no trackingNumber for order %s", order_code)
            return

    elif event == "order_cancelled":
        order = await _find_order_by_eurosender_code(db, order_code)
        if not order:
            logger.info("[EurosenderWebhook] order_cancelled for unknown orderCode=%s", order_code)
            return
        if order.eurosender_status == "cancelled":
            # Idempotent at the business-logic level, not just via the Redis
            # dedup key: Redis is best-effort (a down Redis makes the route's
            # dedup fast-path a no-op, per _get_redis_safe()'s own fallback),
            # so a genuine duplicate delivery must never re-alert admins on
            # its own. This is the only handler with a real per-event side
            # effect (an unconditional admin Notification) — the other
            # handlers are already naturally idempotent (status/tracking
            # writes are no-ops on an unchanged value; _apply_tracking_codes
            # explicitly short-circuits on an unchanged tracking number).
            logger.info("[EurosenderWebhook] order_cancelled already applied for order %s — skipping duplicate alert", order_code)
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
        # No inner commit here — the route's single commit (after
        # handle_event returns) is the one transaction boundary now that a
        # handler exception rolls back AND returns a retryable non-2xx; a
        # second, earlier commit point would let the alert survive even if a
        # later exception forced the caller to report failure and expect a
        # clean retry.

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
    # Root-fixed (production-hardening closure, 2026-09-29): this used to
    # refuse whenever EUROSENDER_SANDBOX was falsy, unconditionally — a
    # leftover from before the signature contract was verified (Phase 22)
    # AND a mirror of the adapter's old unconditional production ban (see
    # eurosender_adapter.py's _preflight() docstring for the matching fix).
    # Left as-is, it would make this endpoint permanently reject every real
    # production webhook (label_ready/submitted_to_courier/tracking_ready/
    # cancelled/delivery_status_updated) the moment EUROSENDER_SANDBOX is
    # ever set to 0 to activate production — silently breaking the entire
    # order lifecycle for every real customer order. Now gated on the SAME
    # kill switch the adapter uses: refuse only when the integration is
    # disabled in BOTH directions (no sandbox testing AND production not
    # enabled). Today's real config (EUROSENDER_SANDBOX=1) is unaffected.
    if not (eurosender_config.sandbox_mode() or eurosender_config.eurosender_enabled()):
        return Response(
            status_code=501,
            content="Eurosender integration is disabled (EUROSENDER_SANDBOX=0 and "
                    "EUROSENDER_ENABLED=0) — this endpoint has nothing to process.",
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

    # Dedup semantics (root-fixed — see FIXES_TRACKER.md "webhook retry /
    # dedup" closure): the dedup key is a FAST-PATH READ here, checked before
    # doing any work, but it is only ever WRITTEN after handle_event() has
    # actually committed successfully, further down. This is deliberate:
    # marking a webhook "seen" before it was successfully processed (the
    # previous design) meant a transient DB error mid-processing would leave
    # the webhook permanently un-processed AND permanently un-retryable —
    # Eurosender never retries a 200, and our own dedup key would then have
    # silently swallowed the one retry Eurosender might otherwise have sent.
    # A handler failure below now returns a non-2xx instead, so Eurosender's
    # own retry mechanism (confirmed real — Phase 20 evidence, ids 10790/10793
    # retried after 401s) can redeliver it, and this fast-path read lets a
    # genuine redelivery skip reprocessing once it already succeeded.
    r = None
    dedup_key = f"eurosender:webhook_seen:{webhook_id}" if webhook_id else None
    if dedup_key:
        try:
            r = await _get_redis_safe()
            if r is not None and await r.exists(dedup_key):
                logger.info("[EurosenderWebhook] duplicate Webhook-Id=%s (already processed), skipping", webhook_id)
                return Response(status_code=200)
        except Exception as exc:
            logger.warning("[EurosenderWebhook] Redis dedup read failed (continuing without the fast-path): %s", exc)

    if webhook_event not in KNOWN_EVENTS:
        logger.warning("[EurosenderWebhook] unrecognized event type: %s", webhook_event)
        return Response(status_code=200)

    try:
        await handle_event(webhook_event, payload, db)
        await db.commit()
    except Exception:
        logger.exception("[EurosenderWebhook] handler error for event=%s id=%s — requesting retry", webhook_event, webhook_id)
        await db.rollback()
        # Non-2xx: this webhook was NOT successfully processed and MUST stay
        # retryable — the dedup key above was never written for it.
        return Response(status_code=503)

    if dedup_key and r is not None:
        try:
            await r.set(dedup_key, "1", ex=_DEDUP_TTL_S)
        except Exception as exc:
            logger.warning("[EurosenderWebhook] Redis dedup write failed after successful processing: %s", exc)

    return Response(status_code=200)
