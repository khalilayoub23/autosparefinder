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

from category_map import categorize_on_ingest, categorize_slug

SMALL_PAYLOAD = 100          # 10 of 6,497 payload files are < 100 items and all 10 are page-wide blocks (audit 2026-11-17)
CATCH_ALL = "כללי"


def category_for(name: str, slug: str, url: str = "", extra: str = "") -> str:
    """Product identity first (name), listing-page taxonomy only when the name is uninformative."""
    by_name = categorize_on_ingest(name=name, url="", extra=extra)
    if by_name != CATCH_ALL:
        return by_name
    by_slug = categorize_slug(slug or "")
    return by_slug if by_slug != CATCH_ALL else categorize_on_ingest(name=name, url=url, extra=extra)


def fitment_allowed(payload_size: int, name: str, slug: str, extra: str = "") -> bool:
    if payload_size >= SMALL_PAYLOAD:
        return True
    slug_cat = categorize_slug(slug or "")
    name_cat = categorize_on_ingest(name=name, url="", extra=extra)
    return slug_cat != CATCH_ALL and name_cat != CATCH_ALL and slug_cat == name_cat
