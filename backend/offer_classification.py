"""
Script: offer_classification.py
Purpose: ONE rule for product classification (OEM / OE-equivalent / aftermarket) at the OFFER level, so that
         a supplier offer of a different class (e.g. an AliExpress aftermarket listing on an OEM catalog part)
         coexists with the existing offers and is never silently substituted for them.
Process: `supplier_parts.part_type` labels each offer (NULL = legacy offer, inherits the catalog part's class).
         Default selections / price bases prefer offers whose class is COMPATIBLE with the catalog part
         (`class_rank` 0), then cheapest. Offers of another class (`class_rank` 1) are always listed, labeled
         and selectable — never hidden, never overwritten — they just are not the *default* for that part.
Data Imported/Modified: none (pure functions + SQL fragments).
Data Sources: platform product model (Original OEM / OEM-equivalent / Aftermarket — CLAUDE.md §3).
Missing Data Delegation: NULL classification on either side = compatible (legacy data is never demoted).
Last Updated: 2026-09-21

Owner rule (2026-09-21): OEM + Aftermarket + Equivalent offers may coexist and are presented according to
product classification, supplier and price. AliExpress is simply another supplier offer.
"""
from __future__ import annotations

import re
from typing import Optional

# normalised vocabulary: oem | oe_equivalent | aftermarket | remanufactured | used
_NORMAL = {
    "original": "oem", "genuine": "oem", "oem": "oem", "oe": "oem",
    "oem_equivalent": "oe_equivalent", "oe_equivalent": "oe_equivalent", "oe equivalent": "oe_equivalent",
    "oem equivalent": "oe_equivalent",
    "aftermarket": "aftermarket", "remanufactured": "remanufactured", "used": "used",
}


def normalize_type(value: Optional[str]) -> Optional[str]:
    """Canonical class of a part_type/offer label, or None when unknown/blank (never guessed)."""
    v = (value or "").strip().lower()
    if not v or v in ("unknown", "none", "null"):
        return None
    return _NORMAL.get(v, v)


def is_compatible(part_type: Optional[str], offer_type: Optional[str]) -> bool:
    """True when the offer may be the DEFAULT for this part: same class, or either side unclassified."""
    p, o = normalize_type(part_type), normalize_type(offer_type)
    return p is None or o is None or p == o


def class_rank(part_type: Optional[str], offer_type: Optional[str]) -> int:
    """0 = default-eligible (compatible), 1 = alternative class (shown/selectable, not the default)."""
    return 0 if is_compatible(part_type, offer_type) else 1


def offer_label(part_type: Optional[str], offer_type: Optional[str]) -> Optional[str]:
    """Class to DISPLAY for an offer: its own label, else the catalog part's (legacy offers inherit)."""
    return normalize_type(offer_type) or normalize_type(part_type)


def norm_type_sql(col: str) -> str:
    """SQL twin of normalize_type() for a column expression ('' when unknown)."""
    c = f"lower(btrim(coalesce({col}, '')))"
    return (f"(CASE WHEN {c} IN ('unknown','none','null') THEN '' "
            f"WHEN {c} IN ('original','genuine','oe') THEN 'oem' "
            f"WHEN {c} IN ('oem_equivalent','oe equivalent','oem equivalent') THEN 'oe_equivalent' ELSE {c} END)")


def class_rank_sql(sp_col: str = "sp.part_type", pc_col: str = "pc.part_type") -> str:
    """SQL twin of class_rank(): 0 compatible / 1 alternative. Use as the FIRST ORDER BY key."""
    s, p = norm_type_sql(sp_col), norm_type_sql(pc_col)
    return f"(CASE WHEN {s} = '' OR {p} = '' OR {s} = {p} THEN 0 ELSE 1 END)"


# ── marketplace-listing classification (AliExpress DS and similar third-party marketplaces) ────────────
# A marketplace listing can NEVER be classified as genuine OEM: AliExpress sellers write "OEM"/"genuine"/"original"
# in titles for non-genuine goods, and provenance cannot be verified. So a listing is either
#   * 'oe_equivalent' — the title names a known OE-supplier brand (Bosch, Denso, Valeo, ...), or
#   * 'aftermarket'   — everything else.
OE_SUPPLIER_BRANDS = frozenset({
    "bosch", "denso", "valeo", "ngk", "gates", "skf", "fag", "luk", "sachs", "monroe", "brembo", "ate", "hella",
    "mahle", "mann", "febi", "meyle", "trw", "delphi", "continental", "kayaba", "gabriel", "moog", "corteco",
    "elring", "victor reinz",
})
_BRAND_RX = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(b) for b in sorted(OE_SUPPLIER_BRANDS, key=len, reverse=True)) + r")(?![a-z0-9])", re.I)


def classify_marketplace_listing(title: Optional[str]) -> str:
    """'oe_equivalent' | 'aftermarket' (never 'oem') for a third-party marketplace listing title."""
    return "oe_equivalent" if _BRAND_RX.search(title or "") else "aftermarket"


_LABEL_HE = {"oem": "OEM", "oe_equivalent": "שווה ערך ל-OEM", "aftermarket": "חליפי (Aftermarket)",
             "remanufactured": "משופץ", "used": "משומש"}


def class_label_he(cls: Optional[str]) -> str:
    """Hebrew display label of a product class for customer-facing TEXT (chat/WhatsApp). '' when unknown."""
    return _LABEL_HE.get(normalize_type(cls) or "", "")
