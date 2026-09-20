"""
Script: eurosender_weight.py
Purpose: Phase-1 weight policy for Eurosender parcel shipments. Reuses the
         EXISTING Sendcloud category weight-default map (sendcloud_shipping_sync
         ._default_weight) rather than maintaining a second, divergent map —
         explicit instruction from the sandbox-implementation authorization
         (Phase 6): "Do not duplicate a second unrelated weight map if the
         existing map can safely be reused."
Process:
  1. aggregate_weight_kg(items) sums _default_weight(category) * quantity
     across every order item bound for this shipment.
  2. check_weight_ceiling(weight_kg) compares against EUROSENDER_MAX_WEIGHT_KG
     (default 25). Exceeding it BLOCKS + flags manual_review — the declared
     weight is never silently reduced to fit under the ceiling.
Data Imported/Modified: none (pure policy lookup; reads sendcloud_shipping_sync
  module-level category weight table).
Data Sources: services/sendcloud_shipping_sync.py _DEFAULT_WEIGHT_KG /
              _default_weight() — same table already used for real Sendcloud
              rate lookups on Car-Parts.ie / SNG Barratt / BMW Spare Parts EU.
Missing Data Delegation: an unrecognized category falls through to
  sendcloud_shipping_sync._DEFAULT_WEIGHT_FALLBACK (1.0 kg) — same fallback
  already trusted in production for Sendcloud rate lookups.
Last Updated: 2026-09-08
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from services.sendcloud_shipping_sync import _default_weight
from services.shipping import eurosender_config


class WeightVerdict(str, Enum):
    ALLOW = "allow"
    BLOCK_MANUAL_REVIEW = "block_manual_review"


@dataclass(frozen=True)
class WeightDecision:
    verdict: WeightVerdict
    weight_kg: float
    ceiling_kg: float
    is_estimate: bool
    reason: str


def aggregate_weight_kg(items: list[dict]) -> float:
    """items: [{"category": str, "quantity": int}, ...]

    Uses the SAME _default_weight() lookup Sendcloud rate sync already trusts
    in production. Returns the summed estimated shipment weight in kg.
    """
    total = 0.0
    for item in items:
        category = item.get("category")
        qty = max(int(item.get("quantity") or 1), 1)
        total += _default_weight(category) * qty
    return round(total, 3)


def check_weight_ceiling(weight_kg: float, ceiling_kg: float | None = None) -> WeightDecision:
    """Never alters weight_kg to fit the ceiling — only blocks + flags review.

    ceiling_kg defaults to EUROSENDER_MAX_WEIGHT_KG (25 kg) if not given.

    Also enforces weight_kg > 0. Live Sandbox verification (2026-09-11,
    Phase 17) confirmed the Eurosender API ACCEPTS weight=0 without rejection
    (same price as 0.5kg) — the API cannot be trusted to catch this, so this
    is the application-level guard. weight_kg is never silently substituted
    with a default; a non-positive value is reported as invalid so the
    caller can route to manual_review.
    """
    if weight_kg is None:
        return WeightDecision(
            verdict=WeightVerdict.BLOCK_MANUAL_REVIEW,
            weight_kg=0.0,
            ceiling_kg=ceiling_kg if ceiling_kg is not None else eurosender_config.max_weight_kg(),
            is_estimate=True,
            reason="weight_kg is missing (None) — cannot ship without a weight value.",
        )
    if weight_kg <= 0:
        return WeightDecision(
            verdict=WeightVerdict.BLOCK_MANUAL_REVIEW,
            weight_kg=weight_kg,
            ceiling_kg=ceiling_kg if ceiling_kg is not None else eurosender_config.max_weight_kg(),
            is_estimate=True,
            reason=(
                f"weight_kg must be > 0, got {weight_kg}. Eurosender's Sandbox "
                "API does not reliably reject zero weight (verified 2026-09-11) "
                "— this is an application-level guard, not reliance on the API."
            ),
        )
    ceiling = ceiling_kg if ceiling_kg is not None else eurosender_config.max_weight_kg()
    if weight_kg > ceiling:
        return WeightDecision(
            verdict=WeightVerdict.BLOCK_MANUAL_REVIEW,
            weight_kg=weight_kg,
            ceiling_kg=ceiling,
            is_estimate=True,
            reason=(
                f"Estimated shipment weight {weight_kg}kg exceeds configured "
                f"EUROSENDER_MAX_WEIGHT_KG ceiling ({ceiling}kg). Declared weight "
                "is not altered to fit — this shipment requires manual review "
                "and, if genuinely oversized, must not route through Eurosender."
            ),
        )
    return WeightDecision(
        verdict=WeightVerdict.ALLOW,
        weight_kg=weight_kg,
        ceiling_kg=ceiling,
        is_estimate=True,
        reason=f"Estimated weight {weight_kg}kg is within the {ceiling}kg ceiling.",
    )
