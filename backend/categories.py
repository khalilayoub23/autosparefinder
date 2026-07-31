"""
categories.py — DEPRECATED COMPATIBILITY SHIM.

All category logic now lives in ONE file: `category_map.py`.
This module only re-exports from it so pre-merge import sites keep working.

DO NOT add rules, keywords or maps here. Add them to `category_map.py`.

What changed in the 2026-07-27 merge:
  • `guess_category_by_text()` is now `category_map.guess_category_by_text` — the
    same contract (canonical slug, or None when nothing matches) but backed by
    the merged RULES, so it can no longer disagree with `categorize()`.
  • `CATEGORY_MAP` used to be keyed by part_type_taxonomy LABELS ('Air Filters',
    'Timing Belts', 131 of them). Callers did `list(CATEGORY_MAP.keys())` and
    treated that as the canonical category list — but `parts_catalog.category`
    stores SLUGS ('filters', 'brakes', 'כללי'), so those two sets never
    intersected and every check against them silently failed. It is now keyed by
    CANONICAL SLUG, which is what the callers actually needed.
"""
from __future__ import annotations

from typing import Dict, List

from category_map import (  # noqa: F401  (re-exported for back-compat)
    CANONICAL,
    CATCH_ALL,
    RULES,
    categorize,
    categorize_on_ingest,
    categorize_slug,
    display_name,
    guess_category_by_text,
    is_canonical,
    normalize_category_label,
)


def _build_category_map() -> Dict[str, Dict[str, List[str]]]:
    """
    {canonical_slug: {"he": [rtl terms], "en": [latin terms]}}

    Keyed by canonical SLUG (see module docstring — the old label keys were the
    bug). "he" holds the RTL array, which is Hebrew + Arabic combined, because
    both alphabets are matched against the same text fields.
    """
    out: Dict[str, Dict[str, List[str]]] = {}
    for cat, rtl_kws, en_kws in RULES:
        bucket = out.setdefault(cat, {"he": [], "en": []})
        bucket["he"].extend(rtl_kws)
        bucket["en"].extend(en_kws)
    # Every canonical category must be present, even those with no keyword rules.
    for cat in CANONICAL:
        out.setdefault(cat, {"he": [], "en": []})
    return out


CATEGORY_MAP: Dict[str, Dict[str, List[str]]] = _build_category_map()

# Retained for the few call sites that iterated it; ordering is RULES order.
ORDERED_CATEGORY_TERMS: List[tuple] = [
    (cat, list(rtl) + [k.lower() for k in en]) for cat, rtl, en in RULES
]

__all__ = [
    "CATEGORY_MAP", "ORDERED_CATEGORY_TERMS", "CANONICAL", "CATCH_ALL",
    "guess_category_by_text", "categorize", "categorize_on_ingest",
    "categorize_slug", "normalize_category_label", "is_canonical", "display_name",
]
