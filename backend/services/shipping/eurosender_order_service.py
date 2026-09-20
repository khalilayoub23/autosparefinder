"""
Script: eurosender_order_service.py
Purpose: Orchestrates a single Eurosender shipment creation attempt: runs the
         dimension/weight/HS/declared-value policy gates, then (only if every
         gate passes) calls the adapter's create_shipment() under the timeout
         state machine. This is the ONE function trigger_supplier_fulfillment()
         calls for an Eurosender-eligible supplier — it never calls the
         adapter directly.
Process:
  1. Build one PackageRequest-shaped package per distinct item category
     (dimension + weight policy applied per category).
  2. Any BLOCK or MANUAL_REVIEW verdict on any package -> the WHOLE shipment
     is routed to manual_review; no partial-shipment API call is made.
  3. Resolve HS code per category for the later proforma step (Phase 12) —
     a MANUAL_REVIEW HS verdict also blocks auto-creation.
  4. Compute declared value from customer-facing item totals only.
  5. Set eurosender_status=pending_creation, call adapter.create_shipment()
     with customerInternalReference=order_id.
  6. On success: eurosender_status=created, orderCode persisted.
     On ambiguous failure (timeout / connection error / 5xx-with-no-body):
       eurosender_status=timeout_pending_reconciliation — the CALLER must not
       invoke this function again for the same order_id from that state
       (see eurosender_timeout.can_attempt_creation()).
     On a clean 4xx (validation error, no ambiguity): eurosender_status=failed.
Data Imported/Modified: none directly — returns a ShipmentResult; the caller
  (routes/utils.py trigger_supplier_fulfillment) persists it onto the real
  Order / SupplierPayment rows.
Missing Data Delegation: any manual_review reason is returned verbatim so the
  caller can surface it to ops — never silently swallowed.
Last Updated: 2026-09-08
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import httpx

from services.shipping import eurosender_content as content_mod
from services.shipping import eurosender_declared_value as declared_value_mod
from services.shipping import eurosender_dimensions as dims_mod
from services.shipping import eurosender_hs_codes as hs_mod
from services.shipping import eurosender_timeout as timeout_mod
from services.shipping import eurosender_weight as weight_mod
from services.shipping.eurosender_adapter import EurosenderAdapter, EurosenderNotConfigured, parse_warnings

logger = logging.getLogger(__name__)


class ShipmentVerdict(str, Enum):
    CREATED = "created"
    MANUAL_REVIEW = "manual_review"
    BLOCKED = "blocked"
    TIMEOUT_PENDING_RECONCILIATION = "timeout_pending_reconciliation"
    FAILED = "failed"
    NOT_ELIGIBLE = "not_eligible"


@dataclass
class ShipmentResult:
    verdict: ShipmentVerdict
    eurosender_status: str
    eurosender_order_code: str | None = None
    label_url: str | None = None
    hs_codes: dict[str, str] = field(default_factory=dict)
    declared_value_eur: int | None = None
    weight_kg: float | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list = field(default_factory=list)  # list[eurosender_adapter.QuoteWarning]


def _build_packages_and_gates(items: list[dict]) -> tuple[list[dict], list[str], dict[str, str]]:
    """Returns (packages, block_reasons, hs_by_category).

    items: [{"category": str, "quantity": int, "content": str}, ...]
    One package per distinct category (quantity aggregated); Eurosender's
    PackageRequest.quantity field covers identical-item multiples.
    """
    packages: list[dict] = []
    block_reasons: list[str] = []
    hs_by_category: dict[str, str] = {}

    by_category: dict[str, dict] = {}
    for item in items:
        cat = (item.get("category") or "").strip()
        qty = max(int(item.get("quantity") or 1), 1)
        if cat not in by_category:
            by_category[cat] = {"quantity": 0, "item_content": item.get("content")}
        by_category[cat]["quantity"] += qty

    for cat, agg in by_category.items():
        dim_decision = dims_mod.classify_category(cat)
        if dim_decision.verdict == dims_mod.DimensionVerdict.BLOCK:
            block_reasons.append(f"[dimensions] {dim_decision.reason}")
            continue
        if dim_decision.verdict == dims_mod.DimensionVerdict.MANUAL_REVIEW:
            block_reasons.append(f"[dimensions] {dim_decision.reason}")
            continue

        # Defense-in-depth: even an ALLOW-verdict estimate must itself be a
        # valid positive dimension. Eurosender's live Sandbox API does not
        # reliably reject a zero dimension (verified 2026-09-11) — the
        # application is the real safety boundary, not the API.
        est = dim_decision.estimate
        dim_validation = dims_mod.validate_dimensions(est.length_cm, est.width_cm, est.height_cm)
        if not dim_validation.valid:
            block_reasons.append(f"[dimensions] {dim_validation.reason}")
            continue

        weight_kg = weight_mod.aggregate_weight_kg([{"category": cat, "quantity": agg["quantity"]}])
        weight_decision = weight_mod.check_weight_ceiling(weight_kg)
        if weight_decision.verdict == weight_mod.WeightVerdict.BLOCK_MANUAL_REVIEW:
            block_reasons.append(f"[weight] {weight_decision.reason}")
            continue

        hs_decision = hs_mod.classify_hs(cat)
        if hs_decision.verdict == hs_mod.HsVerdict.MANUAL_REVIEW:
            block_reasons.append(f"[hs_code] {hs_decision.reason}")
            continue
        hs_by_category[cat] = hs_decision.hs_code

        # Content must fit Eurosender's verified 17-char limit (Phase 17,
        # 2026-09-11) — never truncated/invented here (see eurosender_content.py).
        content_resolution = content_mod.resolve_package_content(agg["item_content"], cat)
        if not content_resolution.ok:
            block_reasons.append(f"[content] {content_resolution.reason}")
            continue

        packages.append({
            "parcelId": f"CAT-{cat or 'unknown'}",
            "quantity": agg["quantity"],
            "weight": weight_kg,
            "length": est.length_cm,
            "width": est.width_cm,
            "height": est.height_cm,
            "content": content_resolution.content,
            # value is filled in by the caller once the aggregate declared
            # value is known (Eurosender requires a per-package value; Phase-1
            # splits total declared value evenly across packages).
        })

    return packages, block_reasons, hs_by_category


async def evaluate_and_create_shipment(
    order_id: str,
    items: list[dict],
    item_total_prices_ils: list[float],
    pickup_address: dict,
    delivery_address: dict,
    pickup_contact: dict,
    delivery_contact: dict,
    order_contact: dict,
    service_type: str = "regular_plus",
    current_eurosender_status: str | None = None,
    adapter: EurosenderAdapter | None = None,
) -> ShipmentResult:
    if not timeout_mod.can_attempt_creation(current_eurosender_status):
        return ShipmentResult(
            verdict=ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION,
            eurosender_status=timeout_mod.EurosenderStatus.TIMEOUT_PENDING_RECONCILIATION.value,
            reasons=[
                f"Order {order_id} is already {current_eurosender_status} — refusing to "
                "re-POST /v1/orders. Must be reconciled via webhook/GET first."
            ],
        )

    packages, block_reasons, hs_by_category = _build_packages_and_gates(items)

    if block_reasons:
        return ShipmentResult(
            verdict=ShipmentVerdict.MANUAL_REVIEW,
            eurosender_status=current_eurosender_status or "",
            hs_codes=hs_by_category,
            reasons=block_reasons,
        )

    if not packages:
        return ShipmentResult(
            verdict=ShipmentVerdict.MANUAL_REVIEW,
            eurosender_status=current_eurosender_status or "",
            reasons=["No shippable packages resolved from the given items."],
        )

    try:
        declared_total_eur = declared_value_mod.declared_value_eur(item_total_prices_ils)
    except declared_value_mod.DeclaredValueError as exc:
        return ShipmentResult(
            verdict=ShipmentVerdict.MANUAL_REVIEW,
            eurosender_status=current_eurosender_status or "",
            hs_codes=hs_by_category,
            reasons=[f"[declared_value] {exc}"],
        )

    # Split declared value evenly across packages (Eurosender requires a
    # per-package integer `value`); remainder goes to the first package so
    # the sum always equals the true total (no under/over-declaration).
    per_pkg = declared_total_eur // len(packages)
    remainder = declared_total_eur - per_pkg * len(packages)
    for idx, pkg in enumerate(packages):
        pkg["value"] = per_pkg + (remainder if idx == 0 else 0)
        pkg["value"] = max(pkg["value"], 1)

    total_weight_kg = sum(p["weight"] for p in packages)

    client = adapter or EurosenderAdapter()

    try:
        # pending_creation is set by the CALLER immediately before this
        # function runs the network call (see routes/utils.py wiring) so the
        # window between "decided to create" and "POST sent" is covered too.
        response = await client.create_shipment(
            pickup_address=pickup_address,
            delivery_address=delivery_address,
            packages=packages,
            service_type=service_type,
            payment_method="credit",
            order_contact=order_contact,
            pickup_contact=pickup_contact,
            delivery_contact=delivery_contact,
            customer_internal_reference=str(order_id),
        )
    except EurosenderNotConfigured as exc:
        return ShipmentResult(
            verdict=ShipmentVerdict.FAILED,
            eurosender_status=timeout_mod.EurosenderStatus.FAILED.value,
            hs_codes=hs_by_category,
            declared_value_eur=declared_total_eur,
            weight_kg=total_weight_kg,
            reasons=[str(exc)],
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
        # AMBIGUOUS outcome — Eurosender may or may not have created the
        # shipment. Never retry from here; wait for webhook reconciliation.
        logger.warning("Eurosender create_shipment ambiguous failure for order %s: %s", order_id, exc)
        return ShipmentResult(
            verdict=ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION,
            eurosender_status=timeout_mod.EurosenderStatus.TIMEOUT_PENDING_RECONCILIATION.value,
            hs_codes=hs_by_category,
            declared_value_eur=declared_total_eur,
            weight_kg=total_weight_kg,
            reasons=[f"Ambiguous network failure — awaiting webhook/GET reconciliation: {exc}"],
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code and status_code >= 500:
            # Server error — Eurosender's own docs don't guarantee it didn't
            # partially process the order. Treat as ambiguous, not clean-failed.
            return ShipmentResult(
                verdict=ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION,
                eurosender_status=timeout_mod.EurosenderStatus.TIMEOUT_PENDING_RECONCILIATION.value,
                hs_codes=hs_by_category,
                declared_value_eur=declared_total_eur,
                weight_kg=total_weight_kg,
                reasons=[f"Ambiguous 5xx response — awaiting webhook/GET reconciliation: {exc}"],
            )
        # Clean 4xx (validation error etc.) — not ambiguous, safe to mark failed.
        return ShipmentResult(
            verdict=ShipmentVerdict.FAILED,
            eurosender_status=timeout_mod.EurosenderStatus.FAILED.value,
            hs_codes=hs_by_category,
            declared_value_eur=declared_total_eur,
            weight_kg=total_weight_kg,
            reasons=[f"Clean 4xx failure (not retried automatically): {exc}"],
        )

    order_code = response.get("orderCode")
    if not order_code:
        # Defensive: a 2xx with no orderCode is itself ambiguous.
        return ShipmentResult(
            verdict=ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION,
            eurosender_status=timeout_mod.EurosenderStatus.TIMEOUT_PENDING_RECONCILIATION.value,
            hs_codes=hs_by_category,
            declared_value_eur=declared_total_eur,
            weight_kg=total_weight_kg,
            reasons=["2xx response with no orderCode field — treating as ambiguous."],
        )

    resp_status = str(response.get("status") or "")
    eurosender_status = (
        timeout_mod.EurosenderStatus.AWAITING_CUSTOMS.value
        if "customs" in resp_status.lower()
        else timeout_mod.EurosenderStatus.CREATED.value
    )

    return ShipmentResult(
        verdict=ShipmentVerdict.CREATED,
        eurosender_status=eurosender_status,
        eurosender_order_code=order_code,
        label_url=response.get("labelLink"),
        hs_codes=hs_by_category,
        declared_value_eur=declared_total_eur,
        weight_kg=total_weight_kg,
        reasons=[f"Eurosender order {order_code} created (status={resp_status or 'unknown'})."],
        warnings=parse_warnings(response),
    )
