"""
Script: eurosender_dimensions.py
Purpose: Phase-1 category dimension policy for Eurosender parcel shipments.
         Eurosender's PackageRequest REQUIRES width/height/length (confirmed
         from the OpenAPI bundle — there is no optional-dimensions path), so
         every category routed to Eurosender needs an explicit, reviewable
         estimate. This module is the single source of that policy.
Process:
  classify_category(category) -> DimensionDecision with one of:
    BLOCK          - never auto-route to Eurosender (freight-class risk)
    ALLOW          - safe estimated dimensions, low size/weight variance
    MANUAL_REVIEW  - variable/uncertain size; a human must confirm real
                     dimensions before this category ships via Eurosender
  Every ALLOW decision carries is_estimate=True on its dimensions — never
  represent a category default as if it were measured supplier data.
Data Imported/Modified: none (pure policy lookup).
Data Sources: category slugs match parts_catalog.category (English slugs,
              see category_map.py) and the sendcloud_shipping_sync.py weight
              map's own category vocabulary — kept consistent deliberately.
Missing Data Delegation: any category not explicitly classified below falls
  to MANUAL_REVIEW, never to ALLOW-by-default.
Last Updated: 2026-09-08
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DimensionVerdict(str, Enum):
    BLOCK = "block"
    ALLOW = "allow"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class DimensionEstimate:
    length_cm: int
    width_cm: int
    height_cm: int
    is_estimate: bool = True  # always True for anything produced by this module


@dataclass(frozen=True)
class DimensionDecision:
    verdict: DimensionVerdict
    category: str
    estimate: DimensionEstimate | None
    reason: str


# Categories that must NEVER auto-route through the Eurosender parcel flow.
# Named explicitly per the sandbox-implementation authorization (Phase 5):
# body-exterior, engine, gearbox, exhaust. Freight-class risk: dimensions/
# weight for these routinely exceed both Eurosender parcel service ceilings
# (Standard 30 kg, Priority 70 kg) and any safe estimated box size.
BLOCKED_CATEGORIES: frozenset[str] = frozenset({
    "body-exterior",
    "engine",
    "gearbox",
    "exhaust",
})

# Safe estimated dimensions for compact/low-variance categories. Estimates
# are deliberately conservative (upper end of what's typically shipped in
# that category) to reduce dimensional-weight surcharge risk, but they are
# still estimates, not measurements — is_estimate=True always applies.
_ALLOW_DIMENSIONS: dict[str, DimensionEstimate] = {
    "filters":            DimensionEstimate(20, 15, 10),
    "electrical":         DimensionEstimate(20, 15, 10),
    "belts-chains":       DimensionEstimate(20, 15, 10),
    "fasteners":          DimensionEstimate(15, 10, 8),
    "service-general":    DimensionEstimate(20, 15, 10),
    "wipers-washers":     DimensionEstimate(25, 8, 8),
    "audio-electronics":  DimensionEstimate(20, 15, 10),
    "brakes":             DimensionEstimate(35, 25, 12),
    "suspension-steering": DimensionEstimate(35, 25, 12),
    "suspension":         DimensionEstimate(35, 25, 12),
    "wheels-bearings":    DimensionEstimate(35, 25, 15),
    "interior-comfort":   DimensionEstimate(30, 20, 15),
    "accessories":        DimensionEstimate(25, 20, 15),
    "merchandise":        DimensionEstimate(25, 20, 15),
    "safety-systems":     DimensionEstimate(25, 20, 15),
    "fuel-air":           DimensionEstimate(25, 20, 12),
}

# Categories with genuinely variable real-world size — never auto-allow;
# always require a human to confirm actual dimensions before this specific
# shipment goes through Eurosender.
_MANUAL_REVIEW_REASONS: dict[str, str] = {
    "lighting":                  "Headlamp/taillight assemblies vary 15-45cm; auto-estimate risks dimensional-weight surcharge or carrier rejection.",
    "cooling":                   "Radiators/condensers vary widely by vehicle class; no safe single estimate.",
    "air-conditioning-heating":  "Compressor/condenser/heater-core sizes vary too widely for one estimate.",
    "clutch-drivetrain":         "Clutch kits are compact but prop-shafts/driveshafts in the same category are not — needs per-item confirmation.",
    "hybrid-ev":                 "Battery modules and inverters vary enormously in size/weight; never estimate.",
    "fluids":                    "Liquid volumes vary by container size and may be freight-restricted (hazmat) regardless of dimensions.",
    "כללי":                      "Uncategorized catch-all — real part identity unknown, so no dimension estimate is safe.",
    "general":                   "Uncategorized catch-all — real part identity unknown, so no dimension estimate is safe.",
}


def classify_category(category: str) -> DimensionDecision:
    cat = (category or "").strip()
    cat_key = cat.lower()

    if cat_key in BLOCKED_CATEGORIES:
        return DimensionDecision(
            verdict=DimensionVerdict.BLOCK,
            category=cat,
            estimate=None,
            reason=f"Category '{cat}' is on the Phase-1 Eurosender block list (freight-class risk).",
        )

    if cat in _MANUAL_REVIEW_REASONS:
        return DimensionDecision(
            verdict=DimensionVerdict.MANUAL_REVIEW,
            category=cat,
            estimate=None,
            reason=_MANUAL_REVIEW_REASONS[cat],
        )

    if cat_key in _ALLOW_DIMENSIONS:
        return DimensionDecision(
            verdict=DimensionVerdict.ALLOW,
            category=cat,
            estimate=_ALLOW_DIMENSIONS[cat_key],
            reason=f"Category '{cat}' has an approved Phase-1 estimated dimension profile.",
        )

    # Fail-safe default: an unrecognized/unmapped category NEVER auto-ships.
    return DimensionDecision(
        verdict=DimensionVerdict.MANUAL_REVIEW,
        category=cat,
        estimate=None,
        reason=f"Category '{cat}' has no Phase-1 dimension policy entry — defaulting to manual review, not auto-allow.",
    )


@dataclass(frozen=True)
class DimensionValidation:
    valid: bool
    reason: str


def validate_dimensions(length_cm, width_cm, height_cm) -> DimensionValidation:
    """Deterministic, API-independent guard on the three dimension values.

    Live Sandbox verification (2026-09-11, Phase 17) confirmed the Eurosender
    API ACCEPTS a zero length/width/height value without rejection (a 20x0x10
    package quoted successfully, same price as 20x15x10) — it only rejects a
    NEGATIVE value (HTTP 422). The API therefore cannot be trusted as the
    safety boundary for this invariant; the application is.

    Every ALLOW-verdict estimate from classify_category() is currently a
    hardcoded positive constant from _ALLOW_DIMENSIONS, so this validator is
    defense-in-depth against a future data-entry error in that table (or a
    future caller that supplies real measured dimensions instead of an
    estimate) — it does not change today's classify_category() behavior.
    """
    for name, val in (("length_cm", length_cm), ("width_cm", width_cm), ("height_cm", height_cm)):
        if val is None:
            return DimensionValidation(False, f"{name} is missing (None).")
        try:
            numeric = float(val)
        except (TypeError, ValueError):
            return DimensionValidation(False, f"{name} is not numeric: {val!r}.")
        if numeric <= 0:
            return DimensionValidation(
                False,
                f"{name} must be > 0, got {numeric}. Eurosender's Sandbox API "
                "does not reliably reject a zero dimension (verified 2026-09-11) "
                "— this is an application-level guard, not reliance on the API.",
            )
    return DimensionValidation(True, "length_cm, width_cm, and height_cm are all positive numeric values.")
