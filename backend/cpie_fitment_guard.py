"""
Script:  cpie_fitment_guard.py
Purpose: ONE place that decides (a) the CATEGORY of a car-parts.ie product and (b) whether a harvested item is allowed to create
         a positive vehicle fitment. 2026-11-17: ten small EV/new-model listings (BYD Atto 3, Ex5, V80, ...) are page-wide
         "popular parts" blocks - spark plugs on a brake-pads page - and every item was attached to the vehicle (246 rows) and
         took the listing-page slug as its category (glow plug -> brakes).
Process:  category_for()  - the product's own NAME decides; the page slug is only a fallback when the name says nothing.
          fitment_allowed() - in a SMALL payload (< SMALL_PAYLOAD items) an item may create fitment only when its name-derived
                          category equals the listing page's category; anything else is a page-wide block item (no vehicle-
                          specific evidence) -> no positive fitment (fail closed, counted). Full-size payloads are unchanged.
Data Imported/Modified: none (pure functions).
Data Sources: category_map (single category vocabulary).
Missing Data Delegation: none.
Last Updated: 2026-11-17
"""
from __future__ import annotations

import re

from category_map import categorize_on_ingest, categorize_slug

SMALL_PAYLOAD = 100          # 10 of 6,497 payload files are < 100 items and all 10 are page-wide blocks (audit 2026-11-17)
CATCH_ALL = "כללי"


def category_for(name: str, slug: str, url: str = "", extra: str = "", by_name: str | None = None,
                 payload_size: int = 10**6) -> str:
    """Full-size payloads keep the long-standing rule (categorize_on_ingest over name+URL: consultant sample 2026-11-17 found
    name-first WORSE there, e.g. windshield wipers / 'Hatchback' radiators). A SMALL (page-wide block) payload takes the product's
    own NAME first - its listing page says nothing about the item - and the page slug only when the name is uninformative."""
    if payload_size >= SMALL_PAYLOAD:
        return categorize_on_ingest(name=name, url=url, extra=extra)
    by_name = by_name or categorize_on_ingest(name=name, url="", extra=extra)
    if by_name != CATCH_ALL:
        return by_name
    by_slug = categorize_slug(slug or "")
    return by_slug if by_slug != CATCH_ALL else categorize_on_ingest(name=name, url=url, extra=extra)


_BLOCK_TITLE_RE = re.compile(r"^[^()]+\([^,()]+, [^()]+\)$")   # 'Name (BRAND, SKU)' - how the page-wide 'popular parts' block titles items


def slug_of(url: str) -> str:
    """Listing-page slug from a product/source URL ('.../car-parts/byd/seal/timing-belt' -> 'timing-belt')."""
    return (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def fitment_allowed(payload_size: int, name: str = "", slug: str = "", extra: str = "") -> bool:
    """A payload under SMALL_PAYLOAD items is a page-wide 'popular parts' block (10 of 6,497 files, all confirmed generic):
    it carries no vehicle-specific evidence at all, so it creates no positive fitment.
    In a FULL-size payload the same block still appears (consultant 2026-11-17: ~8 items per slug on timing-belt/clutch/alternator
    pages of EV models): a block-style title that sits on a listing page of a DIFFERENT category than the item's own name is that
    block -> no fitment. Everything else in a full-size payload is unchanged."""
    if payload_size < SMALL_PAYLOAD:
        return False
    if _BLOCK_TITLE_RE.match(name or "") and slug:
        slug_cat = categorize_slug(slug)
        name_cat = categorize_on_ingest(name=name, url="", extra=extra)
        if slug_cat != CATCH_ALL and name_cat != CATCH_ALL and slug_cat != name_cat:
            return False
    return True
