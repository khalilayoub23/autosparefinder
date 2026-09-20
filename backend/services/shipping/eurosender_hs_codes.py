"""
Script: eurosender_hs_codes.py
Purpose: Phase-1 HS (Harmonized System) customs classification policy for
         Eurosender proforma invoices (mandatory for Israel — non-EU
         destination, confirmed in the sandbox-contract research). This is
         NOT a universal 870899 fallback — the owner has explicitly NOT
         authorized treating one code as legally correct for every part.
         Categories without a specific, defensible classification route to
         MANUAL_REVIEW instead.
Process:
  classify_hs(category) -> HsDecision:
    MAPPED         - category has an explicit, specific HS code below
    MANUAL_REVIEW  - no confident classification; a human must assign the
                     correct code before this item can appear on a proforma
Data Imported/Modified: none (pure policy lookup).
Data Sources: WCO Harmonized System nomenclature, chapter 87 (vehicles) and
              the specific headings named in the sandbox-implementation
              authorization (Phase 7): brakes/gearbox/suspension/wheels/
              lighting/filters. Configurable via HS_CODE_OVERRIDES env (JSON)
              so the owner or a customs broker can correct/extend this table
              without a code change.
Missing Data Delegation: unmapped categories -> manual_review. No code here
  ever claims a classification it cannot defend.
Last Updated: 2026-09-08
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum


class HsVerdict(str, Enum):
    MAPPED = "mapped"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class HsDecision:
    verdict: HsVerdict
    category: str
    hs_code: str | None
    reason: str


# Explicit Phase-1 classifications — named directly in the sandbox-
# implementation authorization. Each is a specific chapter-87 (or the correct
# non-87 chapter, e.g. lighting/filters) heading, never the bare "8708" or
# "870899" catch-all chapter code.
_HS_MAP: dict[str, str] = {
    "brakes":               "870830",  # Brakes and servo-brakes, and parts thereof
    "gearbox":              "870840",  # Gear boxes and parts thereof
    "suspension":           "870880",  # Suspension shock-absorbers
    "suspension-steering":  "870880",  # Suspension shock-absorbers (steering parts differ; see note below)
    "wheels-bearings":      "870870",  # Road wheels and parts/accessories thereof
    "lighting":             "851220",  # Electrical lighting/signalling equipment for vehicles
    "filters":              "842123",  # Oil or petrol filters for internal combustion engines
}

# A dedicated note: "suspension-steering" bundles two different real-world
# families (suspension components vs. steering components, e.g. tie rods /
# steering racks, which are HS 8708.94). Phase-1 maps the whole bucket to
# 870880 (suspension) as the more common case in this category's actual
# catalog population, but this is a KNOWN approximation, not a verified
# per-item classification — flagged here rather than silently assumed exact.
_KNOWN_APPROXIMATIONS: dict[str, str] = {
    "suspension-steering": (
        "This category mixes suspension AND steering parts (steering racks/"
        "tie rods are properly HS 870894, not 870880). Mapped to 870880 as "
        "an approximation pending a category split; a customs broker should "
        "confirm before high-volume use."
    ),
}


def _overrides() -> dict[str, str]:
    """Allow the owner/a customs broker to correct or extend the map without
    a code deploy: HS_CODE_OVERRIDES='{"exhaust": "870892"}' (JSON object).
    Malformed JSON is ignored (fail safe to the built-in map, never crash).
    """
    raw = os.getenv("HS_CODE_OVERRIDES", "") or ""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k).strip().lower(): str(v).strip() for k, v in parsed.items()}
    except Exception:
        pass
    return {}


def classify_hs(category: str) -> HsDecision:
    cat = (category or "").strip()
    cat_key = cat.lower()

    overrides = _overrides()
    if cat_key in overrides:
        return HsDecision(
            verdict=HsVerdict.MAPPED,
            category=cat,
            hs_code=overrides[cat_key],
            reason=f"HS code for '{cat}' supplied via HS_CODE_OVERRIDES.",
        )

    if cat_key in _HS_MAP:
        note = _KNOWN_APPROXIMATIONS.get(cat_key, "")
        reason = f"Category '{cat}' -> HS {_HS_MAP[cat_key]} (Phase-1 explicit mapping)."
        if note:
            reason += f" NOTE: {note}"
        return HsDecision(
            verdict=HsVerdict.MAPPED,
            category=cat,
            hs_code=_HS_MAP[cat_key],
            reason=reason,
        )

    return HsDecision(
        verdict=HsVerdict.MANUAL_REVIEW,
        category=cat,
        hs_code=None,
        reason=(
            f"No verified HS classification for category '{cat}'. The owner "
            "has not authorized a universal fallback code (e.g. 870899) for "
            "every automotive part — this item requires manual customs "
            "classification before it can appear on a Eurosender proforma."
        ),
    )
