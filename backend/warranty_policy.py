"""
warranty_policy.py — THE single source of truth for part warranty.

Owner directive 2026-07-28: "do the fix of capturing warranty at source and
update the warranty to all parts."

WHY THIS IS ONE MODULE
    Warranty is a commercial promise shown to customers, so it follows the same
    rule as pricing (see `_customer_price_fields`): ONE server-side function that
    every surface calls, never a per-importer re-implementation. Before this,
    warranty was captured by some importers into three inconsistent JSONB keys
    (`warranty`, `warranty_months`, `warranty_text`) and not at all by REX's
    scraper — 293,263 rows from one supplier had none.

THE DEFAULT IS EVIDENCE-BASED, NOT INVENTED
    12 months is the dominant value in our OWN catalog — 2,919,823 of 3,734,378
    populated rows (78.2%); 24 months is next at 792,381 (21.2%). So the default
    reflects what suppliers actually grant on this catalog rather than a guess.
    Override with PLATFORM_DEFAULT_WARRANTY_MONTHS.

PROVENANCE IS MANDATORY
    Every warranty carries `warranty_source`:
        'supplier'         — the supplier stated it
        'platform_default' — no supplier figure; platform policy applied
    Never collapse these. A customer-facing surface that wants to say "supplier
    warranty" must check the source, or it will present our own default as the
    manufacturer's promise.

Last Updated:  2026-07-28
"""
from __future__ import annotations

import os
import re

# Dominant real value in the live catalog (78.2%). Configurable, not hardcoded.
DEFAULT_MONTHS = int(os.getenv("PLATFORM_DEFAULT_WARRANTY_MONTHS", "12"))

SOURCE_SUPPLIER = "supplier"
SOURCE_PLATFORM = "platform_default"

# Sane bounds — anything outside is a parse error, not a warranty.
MIN_MONTHS, MAX_MONTHS = 1, 120

_YEARS_HE = {
    "שנה": 12, "שנתיים": 24, "שלוש שנים": 36, "שלש שנים": 36,
    "ארבע שנים": 48, "חמש שנים": 60,
}


def parse_months(text) -> int | None:
    """Free-text warranty → whole months, or None when it isn't a duration.

    Deliberately conservative — an unrecognised string yields None rather than a
    guess. Patterns are taken from the real values present in the catalog, e.g.
    'אחריות לשנתיים כולל עבודה', "ל 6 חודשים או 10000 ק'מ", '24חודשים',
    '12 months / 100,000 km', and the misspelled 'חריות לשנה'.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        n = int(text)
        return n if MIN_MONTHS <= n <= MAX_MONTHS else None
    t = str(text).strip().lower()
    if not t or "תיאור" in t:
        return None

    # A BARE NUMERIC STRING is the common case from a CSV/JSON column named
    # `warranty_months` — csv.DictReader yields '24', not 24. Without this the
    # unit-bearing regexes below all miss it and a supplier-stated warranty is
    # silently replaced by the platform default (caught by test 2026-07-29:
    # resolve('24', None) returned (12, 'platform_default')).
    if re.fullmatch(r"\d{1,3}(?:\.0+)?", t):
        n = int(float(t))
        return n if MIN_MONTHS <= n <= MAX_MONTHS else None

    m = re.search(r"(\d{1,3})\s*(?:חודש|חודשים|months?|mo\b)", t)
    if m:
        n = int(m.group(1))
        return n if MIN_MONTHS <= n <= MAX_MONTHS else None
    m = re.search(r"(\d{1,2})\s*(?:שנים|שנה|years?|yr)\b", t)
    if m:
        n = int(m.group(1)) * 12
        return n if MIN_MONTHS <= n <= MAX_MONTHS else None
    for word, months in sorted(_YEARS_HE.items(), key=lambda kv: -len(kv[0])):
        if word in t:
            return months
    return None


def resolve(*candidates) -> tuple[int, str]:
    """Pick a warranty from whatever the source gave us, with its provenance.

    Pass every field the source might carry (numeric or free text) in priority
    order. The first that parses wins and is marked 'supplier'; if none do, the
    platform default applies and is marked 'platform_default'.

    Returns (months, source) — never None, so a caller can always write a row.
    """
    for c in candidates:
        months = parse_months(c)
        if months is not None:
            return months, SOURCE_SUPPLIER
    return DEFAULT_MONTHS, SOURCE_PLATFORM


def is_supplier_stated(warranty_source) -> bool:
    """True when the warranty came from the supplier rather than our default.

    NULL means LEGACY SUPPLIER DATA. 3,705,669 rows were populated by importers
    long before `warranty_source` existed, and rewriting all of them purely to
    stamp provenance would have meant ~4h of UPDATEs contending with the live
    harvester and bloating a 4.1M-row table — not worth it for a field whose
    value is already known. So NULL is defined as 'supplier', and only the
    platform default is ever written explicitly.

    ALWAYS use this instead of `warranty_source == 'supplier'` — that bare
    comparison silently treats every legacy row as non-supplier.
    """
    return warranty_source != SOURCE_PLATFORM


__all__ = [
    "DEFAULT_MONTHS", "SOURCE_SUPPLIER", "SOURCE_PLATFORM",
    "parse_months", "resolve", "is_supplier_stated",
]
