"""
Script:  services/ebay_listing_identity.py
Purpose: THE Listing Identity Guard for eBay offers (2026-11-19). One pure, fail-closed decision function used by every
         eBay write path (`services/ebay_price_sync.py` refresh + discovery, `maintenance/ebay_listing_repair.py`).
         It decides whether live eBay data may be written onto a stored supplier offer. Insufficient evidence = NO write.
Process: 1. `legacy_item_id(url)`    - the numeric eBay item id of a stored offer URL.
         2. `check_refresh(...)`     - the STORED listing, fetched BY ITS ID, may update its own offer only.
         3. `check_candidate(...)`   - a listing found by search may become/replace an offer only after product,
                                       condition, shipping, currency and price are all verified.
         4. `normalize_total(...)`   - price and shipping are interpreted separately (never summed into the price).
Data Imported/Modified: none (pure functions; callers persist the Verdict in `supplier_offer_checks`).
Data Sources: eBay Browse API item / item_summary payloads mapped to `Live` by the caller.
Missing Data Delegation: anything the data cannot prove is returned as AMBIGUOUS / MISSING with a reason and a retry date.
Last Updated: 2026-11-19
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# --- verdict codes (stable strings: stored in supplier_offer_checks.verdict and reported) -------------------------
EXACT_MATCH = "exact_match"                    # same listing id, still New, ships to IL, sane currency/price -> may update price
VERIFIED_NEW_LISTING = "verified_new_listing"  # a search candidate that passed every candidate check
SELLER_MISMATCH = "seller_mismatch"
PRODUCT_MISMATCH = "product_mismatch"          # title/part number/brand does not identify the part
VARIANT_MISMATCH = "variant_mismatch"          # condition / quality class differs from the offer
MISSING_LISTING = "missing_listing"            # item ended / not found
NO_ISRAEL_SHIPPING = "no_israel_shipping"
DIFFERENT_LISTING = "different_listing"        # the live item is not the one stored on the offer
AMBIGUOUS_IDENTITY = "ambiguous_identity"      # API error, missing fields, several candidates that cannot be told apart
CURRENCY_AMBIGUOUS = "currency_ambiguous"
SHIPPING_AMBIGUOUS = "shipping_ambiguous"
NO_STORED_LISTING = "no_stored_listing"        # the offer carries no eBay item id at all

ALLOWED_CURRENCIES = ("USD", "ILS")            # anything else is NEVER treated as USD (the old code did)

# verdicts after which the stored offer is no longer something a customer can be sent to
UNAVAILABLE_VERDICTS = frozenset({MISSING_LISTING, NO_ISRAEL_SHIPPING})
# verdicts that never change the offer and are simply retried (the API said nothing reliable)
RETRY_SOON = frozenset({AMBIGUOUS_IDENTITY})
# retry horizon, in days, written to supplier_offer_checks.retry_after
_RETRY_DAYS = {EXACT_MATCH: 30, VERIFIED_NEW_LISTING: 30, MISSING_LISTING: 7, NO_ISRAEL_SHIPPING: 7, DIFFERENT_LISTING: 7,
               SELLER_MISMATCH: 14, PRODUCT_MISMATCH: 14, VARIANT_MISMATCH: 14, AMBIGUOUS_IDENTITY: 1,
               CURRENCY_AMBIGUOUS: 7, SHIPPING_AMBIGUOUS: 7, NO_STORED_LISTING: 7}

# makes used only to REJECT a candidate whose title names a different make than the part's (cheap, conservative)
_MAKES = ("mercedes", "bmw", "audi", "volkswagen", "vw", "toyota", "honda", "hyundai", "kia", "nissan", "ford", "mazda",
          "mitsubishi", "subaru", "volvo", "porsche", "opel", "peugeot", "citroen", "renault", "fiat", "skoda",
          "lexus", "jaguar", "land rover", "suzuki", "chevrolet", "jeep", "dodge", "cadillac")  # no 'seat'/'mini': ordinary words


@dataclass(frozen=True)
class Stored:
    """What the offer row already says (never changed by a failed check)."""
    item_id: Optional[str]            # numeric legacy id parsed from supplier_url
    oem_number: str
    brand: str = ""                   # parts_catalog.manufacturer
    seller: Optional[str] = None      # last seen seller for this offer, when known
    condition: str = "new"            # eBay offers are New-only (search filter conditionIds 1000)
    part_type: Optional[str] = None   # typed (variant) offers are never refreshed from eBay


@dataclass(frozen=True)
class Live:
    """eBay data mapped by the caller (`ok=False` => the API gave no usable answer)."""
    ok: bool = True
    status: str = "ok"                # ok | ended | error
    item_id: Optional[str] = None
    title: str = ""
    seller: Optional[str] = None
    condition: str = ""
    price: Optional[float] = None
    currency: str = ""
    shipping: Optional[float] = None  # None = eBay stated no shipping figure for IL
    ships_to_israel: bool = False
    mpn: Optional[str] = None
    raw: Optional[dict] = field(default=None, compare=False, repr=False)   # full Browse payload (specs/images); never compared


@dataclass(frozen=True)
class Verdict:
    code: str
    reason: str
    retry_days: int

    @property
    def ok(self) -> bool:
        """True only when the live price/listing may be written."""
        return self.code in (EXACT_MATCH, VERIFIED_NEW_LISTING)

    @property
    def makes_unavailable(self) -> bool:
        return self.code in UNAVAILABLE_VERDICTS


def _v(code: str, reason: str) -> Verdict:
    return Verdict(code, reason, _RETRY_DAYS.get(code, 7))


def legacy_item_id(url: Optional[str]) -> Optional[str]:
    """Numeric eBay item id from `https://www.ebay.com/itm/<id>?...` (or a Browse id `v1|<id>|0`)."""
    if not url:
        return None
    m = re.search(r"/itm/(?:[^/?]*/)?(\d{9,})", url) or re.search(r"v1\|(\d{9,})\|", url) or re.fullmatch(r"\s*(\d{9,})\s*", url)
    return m.group(1) if m else None


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _title_names_part(title: str, mpn: Optional[str], oem: str) -> bool:
    """The part number must appear in the title (spaces/dashes ignored) or in the listing's MPN field."""
    o = _norm(oem)
    if len(o) < 5:
        return False
    return o in _norm(title) or o == _norm(mpn) or (len(_norm(mpn)) >= 5 and o in _norm(mpn))


def _foreign_make(title: str, brand: str) -> Optional[str]:
    t, b = (title or "").lower(), (brand or "").lower()
    for m in _MAKES:
        if re.search(rf"\b{re.escape(m)}\b", t) and m not in b and not (m == "vw" and "volkswagen" in b):
            # a make word that is part of the part's own brand is fine; a DIFFERENT make is a different product
            return m
    return None


def _price_checks(live: Live) -> Optional[Verdict]:
    cur = (live.currency or "").upper().strip()
    if cur not in ALLOWED_CURRENCIES:
        return _v(CURRENCY_AMBIGUOUS, f"currency {cur or 'missing'!r} is neither USD nor ILS")
    if live.price is None or live.price <= 0:
        return _v(AMBIGUOUS_IDENTITY, "no positive item price in the response")
    if live.shipping is not None and live.shipping < 0:
        return _v(SHIPPING_AMBIGUOUS, "negative shipping figure")
    return None


def check_refresh(stored: Stored, live: Live) -> Verdict:
    """The stored listing fetched BY ITS OWN ID. May update only that offer, and only with the same listing's data."""
    if stored.part_type:
        return _v(VARIANT_MISMATCH, "typed variant offers are never refreshed from eBay")
    if not stored.item_id:
        return _v(NO_STORED_LISTING, "offer has no eBay item id in supplier_url")
    if live.status == "ended":
        return _v(MISSING_LISTING, "item not found / ended on eBay")
    if not live.ok or live.status != "ok":
        return _v(AMBIGUOUS_IDENTITY, "eBay returned no usable answer (error/rate limit); nothing changed")
    if not live.item_id or live.item_id != stored.item_id:
        return _v(DIFFERENT_LISTING, f"live item {live.item_id!r} is not the stored listing {stored.item_id!r}")
    if not live.ships_to_israel:
        return _v(NO_ISRAEL_SHIPPING, "listing no longer offers shipping to Israel")
    if (live.condition or "").strip().lower() not in ("new", "new with box", "new without box", "new with tags", "new other"):
        return _v(VARIANT_MISMATCH, f"condition is {live.condition!r}, the offer is New")
    if stored.seller and live.seller and stored.seller.lower() != live.seller.lower():
        return _v(SELLER_MISMATCH, f"seller {live.seller!r} != stored {stored.seller!r}")
    bad = _price_checks(live)
    return bad or _v(EXACT_MATCH, "same listing id; condition New; ships to IL; USD/ILS price")


def check_candidate(stored_oem: str, brand: str, live: Live, *, existing_item_id: Optional[str] = None,
                    existing_verified_missing: bool = False) -> Verdict:
    """A listing found by SEARCH. It may create an offer, or replace an existing one ONLY if that one is verified missing."""
    if existing_item_id and live.item_id != existing_item_id and not existing_verified_missing:
        return _v(DIFFERENT_LISTING, "the offer's stored listing is not verified missing; a different listing never replaces it")
    if not live.ok or not live.item_id:
        return _v(AMBIGUOUS_IDENTITY, "candidate has no item id")
    if not live.ships_to_israel:
        return _v(NO_ISRAEL_SHIPPING, "candidate does not ship to Israel")
    if (live.condition or "").strip().lower() not in ("new", "new with box", "new without box", "new with tags", "new other"):
        return _v(VARIANT_MISMATCH, f"candidate condition {live.condition!r}, need New")
    if not _title_names_part(live.title, live.mpn, stored_oem):
        return _v(PRODUCT_MISMATCH, "part number is not in the candidate's title or MPN")
    fm = _foreign_make(live.title, brand)
    if fm:
        return _v(PRODUCT_MISMATCH, f"candidate title names another make ({fm!r}) than the part's brand {brand!r}")
    bad = _price_checks(live)
    return bad or _v(VERIFIED_NEW_LISTING, "part number, New condition, IL shipping, USD/ILS price all verified")


def normalize_total(price: float, shipping: Optional[float]) -> tuple[float, float]:
    """(item price, shipping) kept SEPARATE; an unknown shipping is 0.0 only because the caller keeps the old stored
    shipping when `shipping is None` (see ebay_price_sync). Never folded into the price."""
    return float(price), float(shipping or 0.0)
