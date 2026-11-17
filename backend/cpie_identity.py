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


def offer_key(article: str, brand: Optional[str], bare_owner_brand: Optional[str], bare_exists: bool, same_url: bool = False,
              same_part: bool = False) -> Optional[str]:
    """None = skip the offer write (owner of the bare key cannot be identified).
    same_url: the incoming product page IS the page the existing bare offer was harvested from - the same listing,
    whatever the brand text says (evidence that keeps price updates flowing ONLY to offers whose holder brand is unresolved: a listing page shows several
    brands, so with a RESOLVED holder brand the same page proves nothing - 2026-11-15 reviewer finding, 59008Set).
    same_part (2026-11-16, independent review): with an UNRESOLVED holder brand the bare key may still be written only by the
    HOLDER'S OWN part row. A same listing page alone is not identity (a listing shows several brands: ORIGINAL IMPERIUM's
    strut-mount price was written into part "Coil spring SUPLEX 35573" that way) -> fail closed, counted as a skip."""
    if not brand:
        return None
    if not bare_exists:
        return article
    if bare_owner_brand is None:
        return article if (same_url and same_part) else None
    return article if bare_owner_brand == brand else f"{_tok(brand)}{SEP}{article}"


_SCOPE_RE = re.compile(r"^[A-Z0-9]{1,5}@(?=.)")
PART_SEP = "@"  # part-scoped offer key: `<MAKE5>@<offer key>` (one article, one brand, one offer per make-specific part row)


async def choose_offer_key(article: str, brand: Optional[str], make: str, part_id: str, fetch_holder, url: Optional[str] = None,
                           own_key: Optional[str] = None) -> Optional[str]:
    """Offer key for THIS part row, or None = skip the write (fail closed).
    fetch_holder(key) -> None | {"name","supplier_url","part_id"}: who holds that supplier_sku today.
    2026-11-17 (bare-key identity): parts_catalog keeps one row per vehicle make for ONE physical article, but
    (supplier, sku) is unique, so the first make's part kept the offer and every later make's row of the same
    brand+article had none (371/806 Renault items). Same brand + same article held by ANOTHER part row is therefore
    given its own part-scoped key; a holder of a different or unresolved brand is never written (fail closed)."""
    if not brand:
        return None
    if own_key:   # (supplier, part) is unique: THIS part already holds an offer - reuse it when it is this article+brand, else fail closed
        b, a = article_of(own_key)
        return own_key if (a == article and b in (None, _tok(brand))) else None
    h0 = await fetch_holder(article)
    k1 = offer_key(article, brand, brand_of(h0["name"], article) if h0 else None, h0 is not None,
                   bool(h0 and url and (h0.get("supplier_url") or "").strip().lower() == url.strip().lower()), same_part=bool(h0 and str(h0["part_id"]) == str(part_id)))
    if k1 is None:
        return None
    h1 = h0 if k1 == article else await fetch_holder(k1)
    if h1 is None or str(h1["part_id"]) == str(part_id):
        return k1
    if brand_of(h1["name"], article) != brand:
        return None  # key held by a part of another/unresolved brand
    k2 = f"{make}{PART_SEP}{k1}"
    h2 = await fetch_holder(k2)
    return k2 if (h2 is None or str(h2["part_id"]) == str(part_id)) else None


def article_of(key: Optional[str]) -> Tuple[Optional[str], str]:
    """(brand token or None, bare article). A bare article containing '~' is not produced by car-parts.ie.
    A part-scope prefix `<MAKE5>@` is stripped first (internal only: never shown, never used to order)."""
    k = key or ""
    m = _SCOPE_RE.match(k)       # anchored: other suppliers' SKUs may contain '@' (e.g. '1233014L00@') and are never touched
    if m:
        k = k[m.end():]
    if SEP in k:
        b, a = k.split(SEP, 1)
        return b, a
    return None, k
