"""
Script:  cpie_identity.py
Purpose: ONE place that decides the identity keys of a car-parts.ie product (workstream C, owner-approved option 1).
         A car-parts.ie article number is unique only within a BRAND. The brand comes from part_identity's vocabulary
         (never from the harvested `brand` field, which is often a vehicle make). Unresolved brand -> None -> the caller
         HOLDS the row (fail closed).
Process:  part_sku()  - legacy `<make5>-<article>`; brand-qualified only when the existing row under that sku is a
                        DIFFERENT brand.
          offer_key() - the bare article (what the supplier is ordered with) unless a DIFFERENT brand already owns it.
          article_of()- strip the qualifier again for ordering/display.
Data Imported/Modified: none (pure functions).
Data Sources: part_identity vocabulary (backend/data/article_brands.json).
Missing Data Delegation: none.
Last Updated: 2026-11-15
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

import part_identity

SEP = "~"


def brand_of(name, article) -> Optional[str]:
    return part_identity.title_brand(name, article)


def _tok(brand: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", brand.upper()).strip("-")


def part_sku(make_prefix: str, article: str, brand: Optional[str], existing_brand: Optional[str], existing: bool) -> Optional[str]:
    """None = hold the row. `existing` = a row already holds the legacy sku."""
    if not brand:
        return None
    legacy = f"{make_prefix}-{article}"
    if not existing:
        return legacy
    if existing_brand is None:
        return None  # a row exists but we cannot say whose it is: never overwrite it
    return legacy if existing_brand == brand else f"{make_prefix}-{_tok(brand)}{SEP}{article}"


def offer_key(article: str, brand: Optional[str], bare_owner_brand: Optional[str], bare_exists: bool, same_url: bool = False) -> Optional[str]:
    """None = skip the offer write (owner of the bare key cannot be identified).
    same_url: the incoming product page IS the page the existing bare offer was harvested from - the same listing,
    whatever the brand text says (evidence that keeps price updates flowing ONLY to offers whose holder brand is unresolved: a listing page shows several
    brands, so with a RESOLVED holder brand the same page proves nothing - 2026-11-15 reviewer finding, 59008Set)."""
    if not brand:
        return None
    if not bare_exists:
        return article
    if bare_owner_brand is None:
        return article if same_url else None
    return article if bare_owner_brand == brand else f"{_tok(brand)}{SEP}{article}"


def article_of(key: Optional[str]) -> Tuple[Optional[str], str]:
    """(brand token or None, bare article). A bare article containing '~' is not produced by car-parts.ie."""
    k = key or ""
    if SEP in k:
        b, a = k.split(SEP, 1)
        return b, a
    return None, k
