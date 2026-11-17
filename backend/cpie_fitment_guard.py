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

import json
import os
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


# ── C1 (2026-11-17): ICE-only products on pure battery-electric vehicles ──

_BEV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bev_models.json")


def _load_bev(path: str = _BEV_FILE):
    """Fails LOUD at import if the file is missing/invalid: a silently empty list would disable the rule."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    slugs = frozenset(s.strip("/").lower() for s in d["vehicle_slugs"])
    models = frozenset((r["manufacturer"], r["model"]) for r in d["c1_scope"] + d["extended"])
    assert slugs and not (slugs & {s.strip("/").lower() for s in d.get("not_bev", {})}), "bev_models.json lists a non-BEV slug"
    return slugs, models


BEV_SLUGS, BEV_MODELS = _load_bev()

_OILNEG = r"transmission|gearbox|gear oil|automatic|\batf\b|differential|axle|power steering|steering|hydraulic|shock|brake|compressor|\bdsg\b|cvt|haldex"
_ICE_ONLY = [(re.compile(p), re.compile(n) if n else None) for p, n in (
    (r"\bengine oil\b|\bmotor oil\b", _OILNEG),
    (r"\boil filter\b", _OILNEG),
    (r"\bsump\b|\boil drain plug\b|\boil pan\b|\bdipstick\b|\boil filler\b", _OILNEG),
    (r"\bcrankcase\b", None),
    (r"\boil cooler\b", _OILNEG),
    (r"\boil pressure (switch|sensor)\b", _OILNEG),
    (r"\bfuel\b", None),
    (r"\bspark ?plugs?\b|\bglow ?plugs?\b|\bignition (coil|lead|leads|cable|cables|cable kit|distributor)\b", None),
    (r"\bair filter\b", r"cabin|pollen|interior|compressor|charcoal|activated|carbon|breather|dryer|drier|suspension"),
    (r"\bexhaust\b|\bsilencer\b|\bmuffler\b|\bcatalytic\b|\bcatalyst\b|\bparticulate\b|\bdpf\b|\blambda\b|\boxygen sensor\b|\begr\b", None),
    (r"\bturbo(charger)?\b|\bintercooler\b|\bcharge air (cooler|hose|pipe)\b", None),
    (r"\bclutch\b|\bflywheel\b|\bdual[- ]mass\b|\brelease bearing\b", r"air conditioning|compressor|a/c|haldex|viscous|fan|magnetic"),
    (r"\btiming (belt|chain)\b|\bcambelt\b|\bcamshaft\b|\bcrankshaft\b|\bvalve stem\b|\btappet\b|\brocker (arm|cover)\b|\bcylinder head\b|\bhead gasket\b"
     r"|\bvalve cover\b|\bpistons?\b|\bconnecting rod\b|\bpiston rings?\b", r"brake|caliper|shock|damper|strut"),
    (r"\bengine starter\b|\bstarter motor\b|\bstarter\b|\balternator\b", r"battery|jump"),
    (r"\binjector\b|\binjection (pump|valve|nozzle)\b|\binjection\b", r"washer|water|windscreen|headlight"),
    (r"\bintake manifold\b|\bthrottle (body|valve|housing)\b|\bair mass\b|\bair flow (meter|sensor)\b|\bmass air\b|\bcarburett?or\b", None),
    (r"\badblue\b|\burea\b|\bdiesel exhaust fluid\b", None),
)]


def is_ice_only(name: str) -> bool:
    n = (name or "").lower()
    return any(p.search(n) and not (neg and neg.search(n)) for p, neg in _ICE_ONLY)


def _is_bev(vehicle_slug: str = "", manufacturer: str = "", model: str = "") -> bool:
    return (vehicle_slug or "").strip("/").lower() in BEV_SLUGS or (manufacturer, model) in BEV_MODELS


def fitment_allowed(payload_size: int, name: str = "", slug: str = "", extra: str = "",
                    vehicle_slug: str = "", manufacturer: str = "", model: str = "") -> bool:
    """A payload under SMALL_PAYLOAD items is a page-wide 'popular parts' block (10 of 6,497 files, all confirmed generic):
    it carries no vehicle-specific evidence at all, so it creates no positive fitment.
    In a FULL-size payload the same block still appears: a block-style title on a listing page of a DIFFERENT category than the
    item's own name is that block -> no fitment.
    C1 (2026-11-17, rule A): on a pure battery-electric vehicle (data/bev_models.json) an ICE-only product (engine oil, oil/fuel
    filter, spark/glow plug, timing, clutch, exhaust, turbo, starter ...) NEVER creates fitment - a BEV has no such part.
    Hybrids/PHEVs are not in the list. Rule B (make-level / block titles on BEV pages) is deliberately NOT applied: not proven wrong."""
    if _is_bev(vehicle_slug, manufacturer, model) and is_ice_only(name):
        return False
    if payload_size < SMALL_PAYLOAD:
        return False
    if _BLOCK_TITLE_RE.match(name or "") and slug:
        slug_cat = categorize_slug(slug)
        name_cat = categorize_on_ingest(name=name, url="", extra=extra)
        if slug_cat != CATCH_ALL and name_cat != CATCH_ALL and slug_cat != name_cat:
            return False
    return True
