"""
Script: eurosender_fulfillment.py
Purpose: The ONE integration point between trigger_supplier_fulfillment()
         (routes/utils.py) and the Eurosender adapter/policy stack. Splits
         Eurosender-eligible suppliers out of the normal fake-tracking
         OrdersAgent path so a Eurosender-eligible supplier NEVER falls
         through to auto_fake_tracking / OrdersAgent's synthetic tracking
         generator, per the sandbox-implementation authorization (Phase 15).
Process:
  handle_eligible_suppliers(...) is called from trigger_supplier_fulfillment
  BEFORE the existing OrdersAgent handoff. For each supplier bucket:
    1. is_eurosender_eligible(supplier_id) gate (routing.py) — if False,
       the supplier is left untouched in the returned "remaining" set so
       the caller's EXISTING OrdersAgent code path runs exactly as before.
    2. If eligible, the supplier is ALWAYS removed from "remaining" —
       whether it succeeds, needs manual review, or fails, it never reaches
       the fake-tracking fallback.
    3. Resolves pickup address/contact from Supplier.shipping_info (catalog
       DB) and delivery address/contact from the order + its user (PII DB).
       Missing/incomplete data -> manual_review, never a fabricated address.
    4. Resolves each order item's category (needed for dimension/weight/HS
       policy) from parts_catalog.category via OrderItem.part_id. A missing
       category maps to 'כללי', which the dimension policy already routes
       to MANUAL_REVIEW (safe default, not a new special case).
    5. Delegates to eurosender_order_service.evaluate_and_create_shipment().
    6. Persists eurosender_status / eurosender_order_code / shipping_provider
       onto the Order row and shipping_provider_ref onto the SupplierPayment
       row. On CREATED: also sets order tracking fields to "pending" (no
       fake number) and order.status stays whatever it already is — the
       real tracking number only lands via the order_tracking_ready webhook
       (see routes/eurosender_webhook.py), matching Phase 13.
Data Imported/Modified: orders.eurosender_*, orders.shipping_provider,
  orders.shipping_label_url, supplier_payments.shipping_provider_ref,
  supplier_payments.metadata_json.
Missing Data Delegation: any internal exception is caught per-supplier and
  converted to a FAILED verdict + ops alert — it never raises out of
  trigger_supplier_fulfillment and never silently retries.
Last Updated: 2026-09-08
"""
from __future__ import annotations

import logging

from services.shipping.eurosender_order_service import (
    ShipmentResult,
    ShipmentVerdict,
    evaluate_and_create_shipment,
)
from services.shipping.eurosender_routing import is_eurosender_eligible

logger = logging.getLogger(__name__)


def _extract_delivery_address(order_db) -> dict | None:
    addr = order_db.shipping_address if isinstance(order_db.shipping_address, dict) else {}
    street = (addr.get("address_line1") or addr.get("address") or "").strip()
    city = (addr.get("city") or "").strip()
    zip_code = (addr.get("zip") or addr.get("postal_code") or addr.get("zip_code") or "").strip()
    country = (addr.get("country") or "IL").strip().upper()
    if not (street and city):
        return None
    return {"country": country, "zip": zip_code, "city": city, "street": street}


def _extract_delivery_contact(order_db, user) -> dict | None:
    name = getattr(user, "full_name", None) or ""
    email = getattr(user, "email", None) or ""
    phone = getattr(user, "phone", None) or ""
    if not (name and email):
        return None
    return {"name": name, "email": email, "phone": phone}


def _extract_pickup_from_shipping_info(shipping_info: dict) -> tuple[dict | None, dict | None]:
    if not isinstance(shipping_info, dict):
        return None, None
    addr = shipping_info.get("address") or {}
    contact = shipping_info.get("contact") or {}
    required_addr = {"country", "zip", "city", "street"}
    if not required_addr.issubset(addr.keys()) or not all(str(addr.get(k) or "").strip() for k in required_addr):
        return None, None
    required_contact = {"name", "email", "phone"}
    if not required_contact.issubset(contact.keys()) or not all(str(contact.get(k) or "").strip() for k in required_contact):
        return None, None
    return addr, contact


async def _resolve_item_categories(items, cat_db) -> dict:
    """part_id -> category, via a single catalog-DB query. Missing/unmapped
    part_ids fall back to 'כללי' (the platform's only real catch-all
    category) — never invented, and already routed to manual_review by the
    dimension policy.
    """
    from sqlalchemy import select
    from BACKEND_DATABASE_MODELS import PartsCatalog

    part_ids = [oi.part_id for oi in items if oi.part_id]
    if not part_ids:
        return {}
    rows = await cat_db.execute(
        select(PartsCatalog.id, PartsCatalog.category).where(PartsCatalog.id.in_(part_ids))
    )
    return {str(pid): (cat or "כללי") for pid, cat in rows.all()}


async def handle_eligible_suppliers(
    by_supplier: dict,
    suppliers_ready_for_purchase: set,
    order_db,
    supplier_payments_by_key: dict,
    db,
) -> set:
    """Returns the subset of suppliers_ready_for_purchase that should still
    go through the EXISTING OrdersAgent path (i.e. every supplier this
    function did not touch, because it was not Eurosender-eligible).

    SAFETY INVARIANT: this function must NEVER raise. Any Eurosender-eligible
    supplier identified in the first (pure, low-risk) scan loop below is
    guaranteed to be excluded from the returned set even if everything after
    that point throws — an unexpected internal error must route that
    supplier to manual review, never let it silently fall through to
    OrdersAgent's fake-tracking path (Phase 15 hard rule). The caller
    (routes/utils.py) also wraps this call in a try/except as defense in
    depth, but the guarantee must hold here, not there.
    """
    remaining = set(suppliers_ready_for_purchase)

    eligible_keys = []
    for supplier_key in suppliers_ready_for_purchase:
        try:
            bucket = by_supplier.get(supplier_key)
            if not bucket:
                continue
            supplier_id = bucket.get("supplier_id")
            if supplier_id and is_eurosender_eligible(supplier_id):
                eligible_keys.append(supplier_key)
        except Exception:
            logger.exception("[Eurosender] eligibility check failed for supplier %s — excluding from OrdersAgent fallback", supplier_key)
            eligible_keys.append(supplier_key)

    # Discard EVERY identified-eligible key immediately, before any further
    # work that could raise. From this line on, no exception can put an
    # eligible supplier back into the OrdersAgent/fake-tracking path.
    for supplier_key in eligible_keys:
        remaining.discard(supplier_key)

    if not eligible_keys:
        return remaining  # nothing to do — every supplier stays on the existing path

    try:
        from BACKEND_DATABASE_MODELS import async_session_factory, Supplier, User
        from sqlalchemy import select
    except Exception:
        logger.exception("[Eurosender] failed to import DB models — %d supplier(s) left unhandled, not fake-tracked", len(eligible_keys))
        for supplier_key in eligible_keys:
            sp_obj = supplier_payments_by_key.get(str(supplier_key))
            if sp_obj:
                meta = dict(sp_obj.metadata_json or {})
                meta["eurosender_verdict"] = "failed"
                meta["eurosender_reasons"] = ["Internal import error before Eurosender call could run."]
                sp_obj.metadata_json = meta
        return remaining

    for supplier_key in eligible_keys:
        # already discarded from `remaining` above — this loop only decides
        # CREATED vs manual_review/failed, it no longer affects fallback routing.
        bucket = by_supplier[supplier_key]
        sp_obj = supplier_payments_by_key.get(str(supplier_key))

        try:
            result = await _attempt_shipment(bucket, order_db, db, async_session_factory, Supplier, User, select)
        except Exception as exc:  # noqa: BLE001 — this boundary must never propagate
            logger.exception("[Eurosender] internal error for supplier %s on order %s", supplier_key, order_db.order_number)
            result = ShipmentResult(
                verdict=ShipmentVerdict.FAILED,
                eurosender_status="failed",
                reasons=[f"Internal error before/during Eurosender call: {exc}"],
            )

        _persist_result(order_db, sp_obj, result)

    return remaining


async def _attempt_shipment(bucket, order_db, db, async_session_factory, Supplier, User, select) -> ShipmentResult:
    supplier_id = bucket["supplier_id"]

    async with async_session_factory() as cat_db:
        sup_row = await cat_db.execute(select(Supplier.shipping_info).where(Supplier.id == supplier_id))
        shipping_info = sup_row.scalar_one_or_none() or {}
        pickup_address, pickup_contact = _extract_pickup_from_shipping_info(shipping_info)

        item_categories = await _resolve_item_categories(bucket["items"], cat_db)

    if not pickup_address or not pickup_contact:
        return ShipmentResult(
            verdict=ShipmentVerdict.MANUAL_REVIEW,
            eurosender_status="",
            reasons=[
                f"Supplier {bucket.get('supplier_name')} has no complete "
                "shipping_info.address/contact on record — cannot create a "
                "real Eurosender shipment without a real pickup address."
            ],
        )

    delivery_address = _extract_delivery_address(order_db)
    if not delivery_address:
        return ShipmentResult(
            verdict=ShipmentVerdict.MANUAL_REVIEW,
            eurosender_status="",
            reasons=["Order shipping_address is incomplete (missing street/city)."],
        )

    user_row = await db.execute(select(User).where(User.id == order_db.user_id))
    user = user_row.scalar_one_or_none()
    delivery_contact = _extract_delivery_contact(order_db, user)
    if not delivery_contact:
        return ShipmentResult(
            verdict=ShipmentVerdict.MANUAL_REVIEW,
            eurosender_status="",
            reasons=["Customer profile is missing name/email — cannot create a real Eurosender shipment."],
        )

    items = [
        {
            "category": item_categories.get(str(oi.part_id), "כללי"),
            "quantity": oi.quantity,
            "content": oi.part_name,
        }
        for oi in bucket["items"]
    ]
    item_total_prices_ils = [float(oi.total_price or 0) for oi in bucket["items"]]

    result = await evaluate_and_create_shipment(
        order_id=str(order_db.id),
        items=items,
        item_total_prices_ils=item_total_prices_ils,
        pickup_address=pickup_address,
        delivery_address=delivery_address,
        pickup_contact=pickup_contact,
        delivery_contact=delivery_contact,
        order_contact={"email": delivery_contact["email"]},
        current_eurosender_status=order_db.eurosender_status,
    )

    if result.verdict == ShipmentVerdict.CREATED and result.eurosender_status == "awaiting_customs":
        await _submit_proforma(
            result, items, item_total_prices_ils,
            pickup_address=pickup_address, pickup_contact=pickup_contact,
            delivery_address=delivery_address, delivery_contact=delivery_contact,
        )

    return result


async def _submit_proforma(
    result: ShipmentResult,
    items: list[dict],
    item_total_prices_ils: list[float],
    pickup_address: dict,
    pickup_contact: dict,
    delivery_address: dict,
    delivery_contact: dict,
) -> None:
    """Israel (non-EU) shipments land in 'awaiting_customs' immediately after
    creation — Eurosender requires a proforma before the shipment proceeds.
    Failure here does NOT undo the already-created shipment; it is recorded
    on the result for ops visibility (Phase 12: sandbox only, never fabricate
    customs data — build_proforma_items() already refuses to guess).

    Contract verified 2026-09-11 (live Sandbox test + official OpenAPI spec):
    the proforma request needs shipper/receiver contact blocks and a
    per-item weight/country — all built here from the SAME pickup/delivery
    data already used for create_shipment(), never invented. country_of_origin
    defaults to the pickup (shipper) country as a documented approximation
    (the true country of manufacture per part is not tracked anywhere in the
    catalog today) — see eurosender_proforma.py's module docstring.
    """
    from services.shipping.eurosender_proforma import (
        REASON_COMMERCIAL,
        ProformaBuildError,
        build_proforma_contact,
        build_proforma_items,
    )
    from services.shipping.eurosender_adapter import EurosenderAdapter
    from services.shipping import eurosender_weight as weight_mod

    origin_country = pickup_address.get("country") or ""

    by_category: dict[str, dict] = {}
    for item, price_ils in zip(items, item_total_prices_ils):
        cat = item["category"]
        if cat not in by_category:
            by_category[cat] = {
                "category": cat, "quantity": 0,
                "content": item.get("content") or cat,
                "total_price_ils": 0.0,
                "country_of_origin": origin_country,
            }
        qty = max(int(item.get("quantity") or 1), 1)
        by_category[cat]["quantity"] += qty
        by_category[cat]["total_price_ils"] += price_ils

    for cat, agg in by_category.items():
        agg["weight_kg"] = weight_mod.aggregate_weight_kg([{"category": cat, "quantity": agg["quantity"]}])

    items_for_proforma = list(by_category.values())

    try:
        import os
        eur_to_ils = float(os.getenv("EUR_TO_ILS", "4.05") or "4.05")
        proforma_items = build_proforma_items(items_for_proforma, result.hs_codes, eur_to_ils)
        shipper = build_proforma_contact(pickup_contact, pickup_address)
        receiver = build_proforma_contact(delivery_contact, delivery_address)
        client = EurosenderAdapter()
        await client.create_proforma(
            result.eurosender_order_code, proforma_items,
            shipper=shipper, receiver=receiver, reason=REASON_COMMERCIAL,
        )
        result.reasons.append(f"Proforma submitted for order {result.eurosender_order_code}.")
    except ProformaBuildError as exc:
        logger.error("[Eurosender] proforma build failed for order %s: %s", result.eurosender_order_code, exc)
        result.reasons.append(f"[proforma] build failed, NOT submitted: {exc}")
    except Exception as exc:  # noqa: BLE001 — must not crash the already-successful shipment creation
        logger.exception("[Eurosender] proforma submission failed for order %s", result.eurosender_order_code)
        result.reasons.append(f"[proforma] submission failed: {exc}")


def _persist_result(order_db, sp_obj, result: ShipmentResult) -> None:
    order_db.eurosender_status = result.eurosender_status or order_db.eurosender_status

    if result.verdict == ShipmentVerdict.CREATED:
        order_db.eurosender_order_code = result.eurosender_order_code
        order_db.shipping_provider = "eurosender"
        order_db.shipping_label_url = result.label_url
        # No tracking number yet — genuine carrier tracking only arrives via
        # the order_tracking_ready webhook (Phase 13). Do NOT touch
        # order_db.status here; it advances on real webhook events only.
        if sp_obj:
            sp_obj.shipping_provider_ref = result.eurosender_order_code
            sp_obj.status = "paid"  # tracking_received is reserved for a REAL tracking number
            meta = dict(sp_obj.metadata_json or {})
            meta["eurosender_status"] = result.eurosender_status
            meta["eurosender_hs_codes"] = result.hs_codes
            meta["eurosender_declared_value_eur"] = result.declared_value_eur
            sp_obj.metadata_json = meta
        logger.info("[Eurosender] order %s -> %s (%s)", order_db.order_number, result.eurosender_order_code, result.eurosender_status)
        return

    # MANUAL_REVIEW / BLOCKED / TIMEOUT_PENDING_RECONCILIATION / FAILED / NOT_ELIGIBLE
    if sp_obj:
        meta = dict(sp_obj.metadata_json or {})
        meta["eurosender_verdict"] = result.verdict.value
        meta["eurosender_reasons"] = result.reasons
        sp_obj.metadata_json = meta
        if result.verdict == ShipmentVerdict.FAILED:
            sp_obj.status = "failed"
            sp_obj.failure_reason = "; ".join(result.reasons)[:500]

    logger.warning(
        "[Eurosender] order %s supplier verdict=%s reasons=%s",
        order_db.order_number, result.verdict.value, result.reasons,
    )
