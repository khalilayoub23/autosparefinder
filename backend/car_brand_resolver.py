"""
Script: car_brand_resolver.py
Purpose: ONE rule that turns a source brand (car-parts.ie slug / display name) into a canonical car_brands row, so a part's
         parts_catalog.manufacturer_id always satisfies the FK and points at the RIGHT brand (FIXES_TRACKER #140, owner option B).
Process:
  brand_key(text)                 -> accent-/case-/punctuation-free comparison key ("Citroën" -> "citroen", "Land-Rover" -> "landrover").
  plan_resolution(slug, brands, aliases, verified) -> pure decision, no I/O (unit-tested):
        1. explicit alias override (ALIAS_OVERRIDES: vw -> volkswagen, mercedes -> mercedes-benz, regional Ford sites -> Ford, ds)
        2. brand_aliases row (normalized == key)            -> action "alias"
        3. car_brands name (active first) or aliases[] hit  -> action "existing" (an INACTIVE record is reused, never reactivated)
        4. source-validated and unknown                    -> action "create"
        5. otherwise                                       -> action "unresolved" (caller skips and reports; nothing is guessed)
  resolve_source_brand(conn, slug) -> BrandResolution; executes the plan idempotently (INSERT .. ON CONFLICT DO NOTHING, then
        re-read), records provenance in brand_aliases (source='car_parts_ie') and in car_brands.notes for created rows.
Validation: a brand may only be CREATED when the site itself lists it (harvest_queue.source='car_parts_ie_full' enumerates the
        site's /car-brands page) and the slug is a plain [a-z0-9-] token. Never create from free text.
Data Imported/Modified: car_brands (insert only), brand_aliases (insert only). Never updates or deletes an existing brand.
Data Sources: car-parts.ie /car-brands (via harvest_queue), existing car_brands / brand_aliases.
Missing Data Delegation: unresolved brands are reported by the importer (skip_reasons "brand_unresolved") for the owner.
Last Updated: 2026-11-09
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

SOURCE = "car_parts_ie"

# source brand key -> canonical brand key. Only unambiguous SPELLING / REGIONAL-SITE variants of one marque; a different
# marque (Vauxhall, Austin, Daimler ...) is never aliased - it becomes its own brand when the site lists it.
ALIAS_OVERRIDES: Dict[str, str] = {
    "vw": "volkswagen",
    "mercedes": "mercedesbenz",
    "ds": "dsautomobiles",
    "fordusa": "ford",
    "fordotosan": "ford",
    "fordasiaozeania": "ford",
}

# Display casing for brands CREATED from a slug (title-case would give "Daf", "Ktm").
_ACRONYMS = {"ac", "daf", "dr", "fso", "gaz", "ktm", "ldv", "nsu", "tvr", "uaz", "umm"}
_SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")


def brand_key(text: object) -> str:
    s = unicodedata.normalize("NFKD", str(text or "")).casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s)


def slug_of(text: object) -> str:
    s = "".join(ch for ch in unicodedata.normalize("NFKD", str(text or "")).casefold() if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def display_name(slug: str) -> str:
    return " ".join(w.upper() if w in _ACRONYMS else w.capitalize() for w in slug.split("-") if w)


@dataclass(frozen=True)
class BrandResolution:
    action: str                 # existing | alias | created | unresolved
    brand_id: Optional[str]
    name: Optional[str]
    source: str                 # the source brand text as received
    reason: str = ""
    inactive: bool = False


def plan_resolution(source: str, brands: Dict[str, tuple], aliases: Dict[str, tuple], verified: bool) -> dict:
    """brands: key -> (id, name, is_active) (active rows win); aliases: key -> (brand_id, name). Pure."""
    key = brand_key(source)
    if not key or key in ("unknown", "none", "null"):
        return {"action": "unresolved", "reason": "empty or placeholder brand"}
    target = ALIAS_OVERRIDES.get(key)
    if target:
        hit = brands.get(target)
        if hit:
            return {"action": "alias", "id": hit[0], "name": hit[1], "inactive": not hit[2], "reason": f"override {key}->{target}"}
    if key in aliases:
        bid, name = aliases[key]
        return {"action": "alias", "id": bid, "name": name, "reason": "brand_aliases"}
    hit = brands.get(key)
    if hit:
        return {"action": "existing", "id": hit[0], "name": hit[1], "inactive": not hit[2], "reason": "name match"}
    if verified and _SLUG_RX.match(slug_of(source) or ""):
        return {"action": "create", "slug": slug_of(source), "name": display_name(slug_of(source)), "reason": "site-listed, no canonical record"}
    return {"action": "unresolved", "reason": "not a site-listed brand" if not verified else "unusable slug"}


async def _load_maps(conn) -> tuple[Dict[str, tuple], Dict[str, tuple]]:
    brands: Dict[str, tuple] = {}
    for r in await conn.fetch("SELECT id::text AS id, name, is_active, aliases FROM car_brands ORDER BY is_active ASC, created_at"):
        # ORDER BY is_active ASC: inactive first so an ACTIVE row of the same key overwrites it.
        brands[brand_key(r["name"])] = (r["id"], r["name"], r["is_active"])
        for a in (r["aliases"] or []):
            brands.setdefault(brand_key(a), (r["id"], r["name"], r["is_active"]))
    aliases = {r["normalized"]: (r["brand_id"], r["name"]) for r in await conn.fetch(
        "SELECT a.normalized, a.brand_id::text AS brand_id, b.name FROM brand_aliases a JOIN car_brands b ON b.id = a.brand_id")}
    return brands, aliases


async def _site_lists(conn, source: str) -> bool:
    key = brand_key(source)
    rows = await conn.fetch("SELECT DISTINCT brand_en FROM harvest_queue WHERE source = 'car_parts_ie_full'")
    return any(brand_key(r["brand_en"]) == key for r in rows)


async def _record_alias(conn, brand_id: str, source: str) -> None:
    alias = slug_of(source)
    if alias:
        await conn.execute(
            "INSERT INTO brand_aliases(brand_id, alias, normalized, source) VALUES($1::uuid,$2,$3,$4) "
            "ON CONFLICT ON CONSTRAINT uq_brand_aliases_brand_alias DO NOTHING",
            brand_id, alias, brand_key(source), SOURCE)


async def resolve_source_brand(conn, source: str, cache: Optional[dict] = None) -> BrandResolution:
    """Idempotent: the same source always resolves to the same record; concurrent callers cannot create duplicates."""
    ck = brand_key(source)
    if cache is not None and ck in cache:
        return cache[ck]
    brands, aliases = await _load_maps(conn)
    verified = await _site_lists(conn, source) if ck else False
    plan = plan_resolution(source, brands, aliases, verified)
    if plan["action"] == "create":
        await conn.execute(
            "INSERT INTO car_brands(name, aliases, notes, is_active) VALUES($1, ARRAY[$2]::text[], $3, TRUE) ON CONFLICT DO NOTHING",
            plan["name"], plan["slug"],
            f"Created by car_parts_ie importer from source brand '{source}' (site-listed); country/group unverified.")
        brands, aliases = await _load_maps(conn)     # re-read: another worker may have created the same row
        plan2 = plan_resolution(source, brands, aliases, verified)
        if plan2["action"] in ("existing", "alias"):
            res = BrandResolution("created", plan2["id"], plan2["name"], source, plan["reason"])
            await _record_alias(conn, plan2["id"], source)
        else:
            res = BrandResolution("unresolved", None, None, source, "create did not produce a readable row")
    elif plan["action"] in ("existing", "alias"):
        res = BrandResolution(plan["action"], plan["id"], plan["name"], source, plan["reason"], plan.get("inactive", False))
        if plan["action"] == "alias" and plan["reason"].startswith("override"):
            await _record_alias(conn, plan["id"], source)
    else:
        res = BrandResolution("unresolved", None, None, source, plan["reason"])
    if cache is not None:
        cache[ck] = res
    return res


def summarize(results: Iterable[BrandResolution]) -> Dict[str, int]:
    out = {"existing": 0, "alias": 0, "created": 0, "unresolved": 0, "inactive_reused": 0}
    for r in results:
        out[r.action] += 1
        out["inactive_reused"] += 1 if r.inactive else 0
    return out
