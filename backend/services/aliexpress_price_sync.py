"""
aliexpress_price_sync.py — AliExpress DS price sync worker

Mirrors ebay_price_sync.py structure.
Queries active parts, searches AliExpress DS, writes supplier_parts + price_history,
updates parts_catalog.min_price_ils, syncs to Meilisearch per part.
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from types import SimpleNamespace
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import warranty_policy
from offer_classification import classify_marketplace_listing
from currency_rate import get_usd_to_ils_rate
from services.suppliers.aliexpress_supplier import AliExpressSearchUnconfirmed, AliExpressSupplier

logger = logging.getLogger(__name__)

# Rotating id cursor over the unpriced backlog (see ebay_price_sync for rationale).
_LAST_ID_ZERO = "00000000-0000-0000-0000-000000000000"
# Circuit breaker for the matching loop: this many targets in a row whose search got NO answer (transport / HTTP /
# flow control / API error) end the run instead of hammering a struggling API. The remaining targets are left
# untouched (still eligible next run). An auth failure ends the run at once (see the loop). Any real answer resets it.
_MAX_CONSECUTIVE_UNCONFIRMED = 10
_ALIEXPRESS_SYNC_LAST_ID = _LAST_ID_ZERO            # cursor: population 1 — parts WITHOUT a supplier price
_ALIEXPRESS_SYNC_LAST_ID_PRICED = _LAST_ID_ZERO     # cursor: population 2 — priced parts that can gain an extra offer

aliexpress = AliExpressSupplier()
USD_TO_ILS_FALLBACK = float(os.getenv("USD_TO_ILS", "3.72"))


def _normalize_image_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in urls:
        url = str(raw or "").strip()
        if not url or len(url) > 500:
            continue
        if url in seen:
            continue
        seen.add(url)
        output.append(url)
    return output


async def _meili_sync_part(doc: dict[str, Any]) -> bool:
    meili_url = os.getenv("MEILI_URL", "").strip()
    if not meili_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.put(
                f"{meili_url}/indexes/parts/documents",
                headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                json=[doc],
            )
        return resp.status_code < 300
    except Exception as exc:
        logger.warning("Meili per-part sync failed for %s: %s", doc.get("id"), exc)
        return False


async def _update_catalog_min_price(
    db: AsyncSession,
    *,
    part_id: str,
    candidate_min_price_ils: float,
) -> Optional[dict]:
    """Update min_price_ils only if candidate is lower; return doc for Meili sync."""
    result = await db.execute(
        text("""
            UPDATE parts_catalog
            SET
                -- 0/NULL mean "no price yet" (99% of the unpriced backlog holds 0.00): a real candidate must
                -- replace them, otherwise `:candidate < 0` is never true and the minimum is never set.
                min_price_ils = CASE
                    WHEN min_price_ils IS NULL OR min_price_ils <= 0 OR :candidate < min_price_ils
                    THEN :candidate
                    ELSE min_price_ils
                END,
                updated_at = NOW()
            WHERE id = CAST(:part_id AS uuid)
              AND is_active = TRUE
            RETURNING id, sku, name, name_he, oem_number, manufacturer,
                      category, part_type, base_price, min_price_ils, is_active
        """),
        {"part_id": part_id, "candidate": candidate_min_price_ils},
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "id": str(row.id),
        "sku": row.sku,
        "name": row.name,
        "name_he": row.name_he,
        "oem_number": row.oem_number,
        "manufacturer": row.manufacturer,
        "category": row.category,
        "part_type": row.part_type,
        "base_price": float(row.base_price or 0),
        "min_price_ils": float(row.min_price_ils or 0),
        "is_active": row.is_active,
    }


async def _write_supplier_part(
    db: AsyncSession,
    *,
    supplier_id: str,
    part_id: str,
    part_number: str,
    price_usd: Decimal,
    price_ils: Decimal,
    ils_per_usd_rate: Any,
    item_url: str,
    image_urls: list[str],
    warranty: tuple,
    offer_part_type: Optional[str] = None,
    touch_catalog: bool = True,
) -> Optional[dict]:
    """All DB writes for ONE matched part. Runs inside a SAVEPOINT (caller) so a failure here
    rolls back only this part — never the batch. Returns counters, or None when the OEM already
    belongs to another part of this supplier (supplier_parts_supplier_id_supplier_sku_key).

    Isolation: only the AliExpress supplier_parts row (+ its price_history / images) is written; other
    suppliers' rows are never touched. `offer_part_type` labels the OFFER (aftermarket / oe_equivalent) so it
    coexists with OEM offers on the same catalog part. `touch_catalog=False` (priced parts) leaves
    parts_catalog entirely untouched."""
    out = {"writes": 0, "catalog_doc": None, "images": 0, "history": 0}
    existing = (await db.execute(
        text("""SELECT id, price_usd, price_ils FROM supplier_parts
                WHERE supplier_id = CAST(:supplier_id AS uuid) AND part_id = CAST(:part_id AS uuid) LIMIT 1"""),
        {"supplier_id": supplier_id, "part_id": part_id})).fetchone()

    old_price_usd: Optional[Decimal] = None
    old_price_ils: Optional[Decimal] = None
    if existing:
        supplier_part_id = str(existing.id)
        old_price_usd = Decimal(str(existing.price_usd)) if existing.price_usd is not None else None
        old_price_ils = Decimal(str(existing.price_ils)) if existing.price_ils is not None else None
        await db.execute(
            text("""UPDATE supplier_parts
                    SET price_usd = :price_usd, price_ils = :price_ils, supplier_sku = :supplier_sku,
                        supplier_url = :supplier_url, is_available = TRUE, availability = 'in_stock',
                        part_type = COALESCE(:ptype, part_type),
                        warranty_months = COALESCE(warranty_months, :w_months),
                        warranty_source = CASE WHEN warranty_months IS NULL THEN :w_source ELSE warranty_source END,
                        last_checked_at = NOW(), updated_at = NOW()
                    WHERE id = CAST(:supplier_part_id AS uuid)"""),
            {"supplier_part_id": supplier_part_id, "price_usd": float(price_usd), "price_ils": float(price_ils),
             "supplier_sku": part_number, "supplier_url": item_url, "w_months": warranty[0], "w_source": warranty[1],
             "ptype": offer_part_type},
        )
    else:
        # Conflict target per IMPORTER_RULES: the constraint that actually fires. DO NOTHING (not UPDATE):
        # a conflict means the same OEM is already this supplier's row for a DIFFERENT part — never
        # overwrite another part's price with this one's.
        ins = await db.execute(
            text("""INSERT INTO supplier_parts (
                        id, supplier_id, part_id, supplier_sku, price_usd, price_ils,
                        availability, is_available, supplier_url, warranty_months, warranty_source, part_type,
                        last_checked_at, created_at, updated_at)
                    VALUES (CAST(:id AS uuid), CAST(:supplier_id AS uuid), CAST(:part_id AS uuid), :supplier_sku,
                            :price_usd, :price_ils, 'in_stock', TRUE, :supplier_url, :w_months, :w_source, :ptype,
                            NOW(), NOW(), NOW())
                    ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO NOTHING
                    RETURNING id"""),
            {"id": str(uuid.uuid4()), "supplier_id": supplier_id, "part_id": part_id, "supplier_sku": part_number,
             "price_usd": float(price_usd), "price_ils": float(price_ils), "supplier_url": item_url,
             "w_months": warranty[0], "w_source": warranty[1], "ptype": offer_part_type},
        )
        new_row = ins.fetchone()
        if not new_row:
            return None
        supplier_part_id = str(new_row[0])
    out["writes"] += 1

    if touch_catalog:
        out["catalog_doc"] = await _update_catalog_min_price(db, part_id=part_id, candidate_min_price_ils=float(price_ils))
        if out["catalog_doc"]:
            out["writes"] += 1

    # The listing image is only ever a fallback for a part with NO image at all (never a second/primary image
    # for a part another supplier already illustrated). Customer surfaces show bucket thumbnails only anyway.
    has_image = (await db.execute(text("SELECT 1 FROM parts_images WHERE part_id = CAST(:p AS uuid) LIMIT 1"),
                                  {"p": part_id})).first() is not None
    for idx, image_url in enumerate([] if has_image else image_urls[:5]):
        # parts_images has NO unique (part_id, url) index (verified) — ON CONFLICT would raise on every
        # part; use NOT EXISTS instead.
        r = await db.execute(
            text("""INSERT INTO parts_images (id, part_id, url, is_primary, sort_order, embedding_generated, created_at)
                    SELECT gen_random_uuid(), CAST(:part_id AS uuid), CAST(:url AS varchar), :is_primary, :sort_order, FALSE, NOW()
                    WHERE NOT EXISTS (SELECT 1 FROM parts_images WHERE part_id = CAST(:part_id AS uuid) AND url = CAST(:url AS varchar))"""),
            {"part_id": part_id, "url": image_url, "is_primary": idx == 0, "sort_order": idx},
        )
        if r.rowcount:
            out["images"] += 1
            out["writes"] += 1

    if (old_price_usd is None or old_price_ils is None or old_price_usd != price_usd or old_price_ils != price_ils):
        change_pct = None
        if old_price_ils is not None and old_price_ils > 0:
            change_pct = float(((price_ils - old_price_ils) / old_price_ils * Decimal("100")).quantize(Decimal("0.0001")))
            change_pct = max(-999.9999, min(999.9999, change_pct))   # NUMERIC(7,4)
        await db.execute(
            text("""INSERT INTO price_history (id, supplier_part_id, old_price_ils, new_price_ils, old_price_usd,
                        new_price_usd, change_pct, source, ils_per_usd_rate, created_at)
                    VALUES (CAST(:id AS uuid), CAST(:supplier_part_id AS uuid), :old_price_ils, :new_price_ils,
                            :old_price_usd, :new_price_usd, :change_pct, 'aliexpress_sync', :rate, NOW())"""),
            {"id": str(uuid.uuid4()), "supplier_part_id": supplier_part_id,
             "old_price_ils": float(old_price_ils) if old_price_ils is not None else None,
             "new_price_ils": float(price_ils),
             "old_price_usd": float(old_price_usd) if old_price_usd is not None else None,
             "new_price_usd": float(price_usd), "change_pct": change_pct, "rate": float(ils_per_usd_rate)},
        )
        out["history"] += 1
        out["writes"] += 1
    return out


def _csv_env(name: str, default: str) -> list[str]:
    return [x.strip() for x in (os.getenv(name, default) or "").split(",") if x.strip()]


async def _select_blind_targets(db: AsyncSession, ali_supplier_id: str, limit: int) -> list:
    """BLIND cursor targeting (legacy; measured yield ~0 — 0/130 unpriced, 2/144 priced). Kept as an explicit fallback
    (ALIEXPRESS_TARGETING=blind, or ALIEXPRESS_BLIND_SHARE>0). Dual-population eligibility, each walked by its own cursor:
      1. UNPRICED parts (no base_price) — fills the price gap (same as before);
      2. PRICED parts that already have >=1 other CUSTOMER-VISIBLE offer (active supplier, available, priced, with a
         supplier_url — the search's own listing predicate) and no fresh AliExpress check — AliExpress can add an
         ADDITIONAL, separately-classified offer (never replaces/overwrites/hides the existing ones).
    Quotas: ALIEXPRESS_PRICED_SHARE (default 0.5) of `limit` goes to population 2. Population 2 is restricted to
    ALIEXPRESS_PRICED_CATEGORIES (measured: AliExpress carries consumable/wear parts, not dealer trim/panels);
    empty = no category filter. ALIEXPRESS_RECHECK_DAYS (default 7) bounds re-checks of the same part."""
    global _ALIEXPRESS_SYNC_LAST_ID, _ALIEXPRESS_SYNC_LAST_ID_PRICED
    share = min(1.0, max(0.0, float(os.getenv("ALIEXPRESS_PRICED_SHARE", "0.5"))))
    n_priced = int(round(limit * share))
    n_unpriced = max(0, limit - n_priced)
    cats = _csv_env("ALIEXPRESS_PRICED_CATEGORIES", "filters,brakes")
    recheck_days = int(os.getenv("ALIEXPRESS_RECHECK_DAYS", "7"))
    targets: list = []

    if n_unpriced:
        rows = (await db.execute(text("""
            SELECT id, oem_number, name, manufacturer FROM parts_catalog
            WHERE is_active = TRUE AND oem_number IS NOT NULL AND oem_number != ''
              AND (base_price IS NULL OR base_price = 0) AND id > CAST(:last_id AS uuid)
            ORDER BY id LIMIT :limit"""), {"limit": n_unpriced, "last_id": _ALIEXPRESS_SYNC_LAST_ID})).fetchall()
        _ALIEXPRESS_SYNC_LAST_ID = _LAST_ID_ZERO if len(rows) < n_unpriced else str(rows[-1].id)
        targets += [SimpleNamespace(id=r.id, oem_number=r.oem_number, name=r.name, manufacturer=r.manufacturer,
                                    population="unpriced") for r in rows]

    if n_priced:
        cat_clause = "AND pc.category = ANY(:cats)" if cats else ""
        params = {"limit": n_priced, "last_id": _ALIEXPRESS_SYNC_LAST_ID_PRICED, "ali": ali_supplier_id,
                  "days": recheck_days}
        if cats:
            params["cats"] = cats
        rows = (await db.execute(text(f"""
            SELECT pc.id, pc.oem_number, pc.name, pc.manufacturer FROM parts_catalog pc
            WHERE pc.is_active = TRUE AND pc.oem_number IS NOT NULL AND pc.oem_number != ''
              AND pc.base_price > 0 {cat_clause} AND pc.id > CAST(:last_id AS uuid)
              AND EXISTS (SELECT 1 FROM supplier_parts sp JOIN suppliers su ON su.id = sp.supplier_id
                          WHERE sp.part_id = pc.id AND su.is_active AND su.id <> CAST(:ali AS uuid)
                            AND sp.is_available AND sp.price_ils > 0
                            -- the SAME visibility predicate the customer search uses for listing offers: an
                            -- AliExpress offer may only ever be ADDITIONAL to an offer the customer can already
                            -- see. Otherwise (existing offers have no supplier_url, e.g. IL importer price lists)
                            -- a URL-bearing AliExpress row would become the ONLY listed offer and hide the part's
                            -- existing OEM/own price — found by the live production sample 2026-09-21.
                            AND NULLIF(BTRIM(sp.supplier_url), '') IS NOT NULL
                            AND su.name NOT IN ('Official Manufacturer Sites', 'Sandbox Supplier QA'))
              AND NOT EXISTS (SELECT 1 FROM supplier_parts sa WHERE sa.part_id = pc.id
                              AND sa.supplier_id = CAST(:ali AS uuid)
                              AND sa.last_checked_at > NOW() - make_interval(days => :days))
            ORDER BY pc.id LIMIT :limit"""), params)).fetchall()
        _ALIEXPRESS_SYNC_LAST_ID_PRICED = _LAST_ID_ZERO if len(rows) < n_priced else str(rows[-1].id)
        targets += [SimpleNamespace(id=r.id, oem_number=r.oem_number, name=r.name, manufacturer=r.manufacturer,
                                    population="priced") for r in rows]
    return targets


# ── Discovery-seeded targeting ────────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE of the ~0 yield: nothing in the system records WHICH catalog parts AliExpress carries, so the sync probed
# the catalog blindly (0/130 unpriced, 2/144 priced). The only reliable signal is the OE numbers sellers CITE in
# listing titles — but it was thrown away after every search. Discovery now (1) runs generic part-type x make searches,
# (2) harvests the OE numbers cited in the titles (same token-bounded guard as matching), (3) maps them to catalog
# parts and persists them in `aliexpress_candidates`; the sync then forward-matches ONLY those (measured ~50% yield
# vs ~1% blind). Matching rules are untouched — candidates only decide WHICH parts are worth a call.
_CANDIDATES_DDL = """
CREATE TABLE IF NOT EXISTS aliexpress_candidates (
    part_id        uuid PRIMARY KEY,
    oem_norm       text NOT NULL,
    cited_item_id  text,
    status         text NOT NULL DEFAULT 'cited',      -- cited | matched | no_match
    attempts       integer NOT NULL DEFAULT 0,
    discovered_at  timestamptz NOT NULL DEFAULT now(),
    last_checked_at timestamptz
)"""
_CANDIDATES_IDX = "CREATE INDEX IF NOT EXISTS ix_aliexpress_candidates_status ON aliexpress_candidates (status, discovered_at)"

# What AliExpress actually sells for cars (wear/consumable/replacement parts), crossed with the makes that dominate OUR
# catalog. Vocabulary only — every accepted candidate is still proven by an OE number cited in a real listing.
DISCOVERY_TERMS = [
    "oil filter", "air filter", "cabin filter", "fuel filter", "brake pad", "brake disc", "brake caliper", "spark plug",
    "ignition coil", "water pump", "thermostat", "timing belt", "serpentine belt", "belt tensioner", "wiper blade",
    "engine mount", "control arm", "ball joint", "tie rod end", "stabilizer link", "shock absorber", "wheel bearing",
    "cv axle", "clutch pedal pad", "brake pedal pad", "oxygen sensor", "camshaft sensor", "crankshaft sensor", "radiator",
    "radiator hose", "fuel pump", "headlight", "tail light", "door handle", "door pull handle", "window regulator",
    "side mirror", "trunk handle", "oil dipstick", "expansion tank cap",
]
_TOKEN_RX = re.compile(r"\b[A-Z0-9][A-Z0-9\-\.]{6,15}[A-Z0-9]\b")
_CURSOR_KEY = "aliexpress_discovery_cursor"


async def ensure_candidates_table(db: AsyncSession) -> None:
    """Idempotent. DDL only when the table is missing (background job, never a request path)."""
    exists = (await db.execute(text("SELECT to_regclass('public.aliexpress_candidates')"))).scalar()
    if not exists:
        await db.execute(text(_CANDIDATES_DDL))
        await db.execute(text(_CANDIDATES_IDX))
        await db.commit()


async def _discovery_brands(db: AsyncSession, n: int = 14) -> list[str]:
    rows = (await db.execute(text("""
        SELECT manufacturer FROM parts_catalog TABLESAMPLE SYSTEM (1)
        WHERE is_active AND manufacturer IS NOT NULL AND btrim(manufacturer) <> ''
        GROUP BY manufacturer ORDER BY count(*) DESC LIMIT :n"""), {"n": n})).fetchall()
    return [r[0] for r in rows]


async def _get_cursor(db: AsyncSession) -> int:
    v = (await db.execute(text("SELECT value FROM system_settings WHERE key = :k"), {"k": _CURSOR_KEY})).scalar()
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


async def _set_cursor(db: AsyncSession, value: int) -> None:
    r = await db.execute(text("UPDATE system_settings SET value = :v, updated_at = NOW() WHERE key = :k"), {"v": str(value), "k": _CURSOR_KEY})
    if not r.rowcount:
        await db.execute(text("""INSERT INTO system_settings (id, key, value, value_type, description, is_public, updated_at)
            VALUES (gen_random_uuid(), :k, :v, 'string', 'AliExpress discovery query rotation cursor (internal)', false, NOW())"""),
                         {"k": _CURSOR_KEY, "v": str(value)})


def _discovery_query(i: int, brands: list[str]) -> tuple[str, int]:
    """i-th discovery query: (make + part term, result page). Deterministic rotation: terms x makes, then page 2, 3 ..."""
    combos = len(DISCOVERY_TERMS) * max(1, len(brands))
    page = 1 + (i // combos)
    j = i % combos
    return f"{brands[j % len(brands)]} {DISCOVERY_TERMS[j // len(brands)]}", page


async def _pending_candidates(db: AsyncSession, ali_supplier_id: str) -> dict:
    """Eligible cited candidates per population (same eligibility as selection)."""
    recheck_days = int(os.getenv("ALIEXPRESS_RECHECK_DAYS", "7"))
    row = (await db.execute(text(_CANDIDATE_SELECT.replace("__COLS__", "count(*) FILTER (WHERE " + _UNPRICED + ") AS unpriced, "
                                                            "count(*) FILTER (WHERE " + _PRICED_VISIBLE + ") AS priced").replace("__ORDER__", "").replace("__LIMIT__", "")
                             .replace("__POP__", "TRUE")),
                             {"ali": ali_supplier_id, "days": recheck_days})).fetchone()
    return {"unpriced": int(row.unpriced or 0), "priced": int(row.priced or 0)}


async def discover_candidates(db: AsyncSession, ali_supplier_id: str, want: dict, max_queries: int, report: dict) -> None:
    """Run discovery queries until each population has >= `want` pending candidates or `max_queries` is spent."""
    brands = await _discovery_brands(db)
    if not brands:
        return
    cursor = await _get_cursor(db)
    pending = await _pending_candidates(db, ali_supplier_id)
    used = 0
    while used < max_queries and (pending["unpriced"] < want["unpriced"] or pending["priced"] < want["priced"]):
        query, page = _discovery_query(cursor + used, brands)
        used += 1
        try:
            listings = await aliexpress.text_search(query, limit=30, page=page)
        except Exception as exc:                                    # never let discovery kill the run
            report["errors"].append(f"discovery:{type(exc).__name__}")
            break
        await asyncio.sleep(0.3)
        cited: dict[str, str] = {}
        for p in listings:
            title = str(p.get("title") or "")
            for m in _TOKEN_RX.finditer(title.upper()):
                raw = m.group(0).strip()
                norm = AliExpressSupplier._norm_oem(raw)
                if 8 <= len(norm) <= 14 and re.search(r"\d", norm) and AliExpressSupplier._oem_matches_title(raw, title):
                    cited.setdefault(norm, str(p.get("itemId") or ""))
        if not cited:
            continue
        rows = (await db.execute(text("""
            SELECT id, regexp_replace(upper(COALESCE(oem_number,'')),'[^A-Z0-9]','','g') AS n FROM parts_catalog
            WHERE is_active = TRUE AND regexp_replace(upper(COALESCE(oem_number,'')),'[^A-Z0-9]','','g') = ANY(:arr)"""),
                                  {"arr": list(cited)})).fetchall()
        for r in rows:
            res = await db.execute(text("""
                INSERT INTO aliexpress_candidates (part_id, oem_norm, cited_item_id) VALUES (:p, :n, :i)
                ON CONFLICT (part_id) DO UPDATE SET cited_item_id = EXCLUDED.cited_item_id,
                    status = CASE WHEN aliexpress_candidates.status = 'no_match'
                                   AND aliexpress_candidates.last_checked_at > NOW() - INTERVAL '30 days' THEN 'no_match'
                                  WHEN aliexpress_candidates.status = 'matched' THEN 'matched' ELSE 'cited' END
                RETURNING (xmax = 0) AS inserted"""), {"p": r.id, "n": r.n, "i": cited.get(r.n)})
            if res.scalar():
                report["candidates_added"] += 1
        await db.commit()
        pending = await _pending_candidates(db, ali_supplier_id)
    report["discovery_queries"] += used
    await _set_cursor(db, cursor + used)
    await db.commit()


async def _mark_candidate(db: AsyncSession, part_id, status: str) -> None:
    await db.execute(text("UPDATE aliexpress_candidates SET status = :s, attempts = attempts + 1, last_checked_at = NOW() WHERE part_id = :p"),
                     {"s": status, "p": part_id})


# eligibility predicates over (pc = parts_catalog, c = aliexpress_candidates)
_UNPRICED = "(pc.base_price IS NULL OR pc.base_price = 0)"
_PRICED_VISIBLE = """(pc.base_price > 0 AND EXISTS (SELECT 1 FROM supplier_parts sp JOIN suppliers su ON su.id = sp.supplier_id
        WHERE sp.part_id = pc.id AND su.is_active AND su.id <> CAST(:ali AS uuid) AND sp.is_available AND sp.price_ils > 0
          AND NULLIF(BTRIM(sp.supplier_url), '') IS NOT NULL AND su.name NOT IN ('Official Manufacturer Sites', 'Sandbox Supplier QA')))"""
_CANDIDATE_SELECT = """
    SELECT __COLS__
    FROM aliexpress_candidates c JOIN parts_catalog pc ON pc.id = c.part_id
    WHERE (c.status = 'cited' OR (c.status = 'no_match' AND c.last_checked_at < NOW() - INTERVAL '30 days'))
      AND pc.is_active AND pc.oem_number IS NOT NULL AND pc.oem_number <> ''
      AND __POP__
      AND NOT EXISTS (SELECT 1 FROM supplier_parts sa WHERE sa.part_id = pc.id AND sa.supplier_id = CAST(:ali AS uuid)
                      AND sa.last_checked_at > NOW() - make_interval(days => :days))
    __ORDER__ __LIMIT__"""


async def _select_candidate_targets(db: AsyncSession, ali_supplier_id: str, n_unpriced: int, n_priced: int) -> list:
    recheck_days = int(os.getenv("ALIEXPRESS_RECHECK_DAYS", "7"))
    out: list = []
    for pop, pred, n in (("unpriced", _UNPRICED, n_unpriced), ("priced", _PRICED_VISIBLE, n_priced)):
        if n <= 0:
            continue
        sql = (_CANDIDATE_SELECT.replace("__COLS__", "pc.id, pc.oem_number, pc.name, pc.manufacturer")
               .replace("__POP__", pred).replace("__ORDER__", "ORDER BY c.discovered_at, c.part_id").replace("__LIMIT__", "LIMIT :limit"))
        rows = (await db.execute(text(sql), {"ali": ali_supplier_id, "days": recheck_days, "limit": n})).fetchall()
        out += [SimpleNamespace(id=r.id, oem_number=r.oem_number, name=r.name, manufacturer=r.manufacturer, population=pop) for r in rows]
    return out


def _allocate_candidate_targets(pending: dict, n: int, priced_share: float) -> dict:
    """Split a request budget of `n` between the two populations using their REAL eligible pending counts, not a
    blind fixed split. Root cause fixed here (2026-09-22): the old caller requested round(n*share) from each
    population unconditionally — when a population's real pool was smaller than its request (proven live: the
    unpriced pool held only 1-9 eligible candidates while priced held 78-247), the shortfall was simply never
    selected, silently discarding run capacity instead of handing it to the population that still had eligible
    candidates. `priced_share` (ALIEXPRESS_PRICED_SHARE, default 0.5) stays the ambition when both pools can meet
    it — that default is an explicit business rule (serve both populations, see G-series dual-population
    eligibility), not something this function invents — but it is now a ceiling clamped to the measured pool,
    with any unclaimed capacity reallocated to whichever population still has spare eligible candidates
    (proportional to each side's spare, so neither pop is starved twice by the same shortfall)."""
    up = max(0, int(pending.get("unpriced", 0)))
    pp = max(0, int(pending.get("priced", 0)))
    if n <= 0 or up + pp <= 0:
        return {"unpriced": 0, "priced": 0}
    if up + pp <= n:
        return {"unpriced": up, "priced": pp}                    # both pools fit entirely inside the budget
    share = min(1.0, max(0.0, priced_share))
    ideal_priced = int(round(n * share))
    ideal_unpriced = n - ideal_priced
    unpriced = min(ideal_unpriced, up)
    priced = min(ideal_priced, pp)
    leftover = n - unpriced - priced
    if leftover > 0:
        spare_unpriced = up - unpriced
        spare_priced = pp - priced
        spare_total = spare_unpriced + spare_priced
        if spare_total > 0:
            take_unpriced = min(spare_unpriced, int(round(leftover * spare_unpriced / spare_total)))
            unpriced += take_unpriced
            leftover -= take_unpriced
            priced += min(spare_priced, leftover)
    return {"unpriced": unpriced, "priced": priced}


async def _select_targets(db: AsyncSession, ali_supplier_id: str, limit: int) -> list:
    """Targets for one run. DEFAULT (ALIEXPRESS_TARGETING=discovery): only parts whose OE number sellers cite in real
    listings (aliexpress_candidates), split between the two populations by their REAL eligible pool sizes
    (`_allocate_candidate_targets`), with ALIEXPRESS_PRICED_SHARE as the ceiling/ambition when both pools can meet
    it. A blind-cursor remainder is available only when ALIEXPRESS_BLIND_SHARE > 0 (default 0)."""
    if (os.getenv("ALIEXPRESS_TARGETING", "discovery") or "discovery").strip().lower() == "blind":
        return await _select_blind_targets(db, ali_supplier_id, limit)
    share = min(1.0, max(0.0, float(os.getenv("ALIEXPRESS_PRICED_SHARE", "0.5"))))
    blind = min(1.0, max(0.0, float(os.getenv("ALIEXPRESS_BLIND_SHARE", "0"))))
    n_blind = int(round(limit * blind))
    n_cand = limit - n_blind
    pending = await _pending_candidates(db, ali_supplier_id)
    alloc = _allocate_candidate_targets(pending, n_cand, share)
    targets = await _select_candidate_targets(db, ali_supplier_id, alloc["unpriced"], alloc["priced"])
    if n_blind:
        targets += await _select_blind_targets(db, ali_supplier_id, n_blind)
    return targets


async def sync_aliexpress_prices(
    db: AsyncSession,
    limit_per_run: int = int(os.getenv("ALIEXPRESS_PRICE_SYNC_LIMIT", "200")),
) -> dict:
    report = {
        "parts_checked": 0,
        "parts_updated": 0,
        "parts_not_found": 0,
        "price_history_rows": 0,
        "catalog_rows_updated": 0,
        "parts_images_added": 0,
        "parts_indexed": 0,
        "index_failures": 0,
        "sku_conflicts": 0,
        "unpriced_checked": 0, "unpriced_updated": 0, "priced_checked": 0, "priced_updated": 0,
        "discovery_queries": 0, "candidates_added": 0, "api_calls": 0, "api_call_limit_hits": 0, "targeting": "",
        # searches that got NO answer from AliExpress (never recorded as no_match; the target stays eligible)
        "searches_unconfirmed": 0, "search_failure_kinds": {}, "search_aborted": False,
        "errors": [],
    }
    _calls0 = aliexpress.api_calls
    _limit_hits0 = aliexpress.api_call_limit_hits

    pending_writes = 0
    consecutive_unconfirmed = 0

    # Fail loudly instead of "checking" thousands of parts against a dead token: the search layer
    # swallows API/auth errors into empty results, which would read as N x "not found".
    if not await aliexpress._ensure_token():
        report["errors"].append("aliexpress credentials not ready (no valid OAuth token) — sync skipped")
        logger.error("AliExpress price sync skipped: credentials not ready")
        return report

    try:
        result = await db.execute(
            text("SELECT id FROM suppliers WHERE name = 'AliExpress' LIMIT 1")
        )
        row = result.fetchone()
        if not row:
            # Auto-create the supplier row
            new_id = str(uuid.uuid4())
            await db.execute(
                text("""
                    INSERT INTO suppliers (id, name, country, is_active, created_at, updated_at)
                    VALUES (CAST(:id AS uuid), 'AliExpress', 'CN', TRUE, NOW(), NOW())
                    ON CONFLICT DO NOTHING
                """),
                {"id": new_id},
            )
            await db.commit()
            result = await db.execute(
                text("SELECT id FROM suppliers WHERE name = 'AliExpress' LIMIT 1")
            )
            row = result.fetchone()
            if not row:
                logger.error("Could not create AliExpress supplier row")
                return report

        aliexpress_supplier_id = str(row[0])
        ils_per_usd_rate = await get_usd_to_ils_rate(db, fallback=USD_TO_ILS_FALLBACK)
        report["ils_per_usd_rate"] = float(ils_per_usd_rate)
        report["run_started_at"] = datetime.utcnow().isoformat() + "Z"

        report["targeting"] = (os.getenv("ALIEXPRESS_TARGETING", "discovery") or "discovery").strip().lower()
        if report["targeting"] != "blind":
            await ensure_candidates_table(db)
            share = min(1.0, max(0.0, float(os.getenv("ALIEXPRESS_PRICED_SHARE", "0.5"))))
            n_priced_q = int(round(limit_per_run * share))
            await discover_candidates(db, aliexpress_supplier_id,
                                      {"unpriced": limit_per_run - n_priced_q, "priced": n_priced_q},
                                      int(os.getenv("ALIEXPRESS_DISCOVERY_MAX_QUERIES", "150")), report)
        part_rows = await _select_targets(db, aliexpress_supplier_id, limit_per_run)
        logger.info("AliExpress price sync: checking %d parts (targeting=%s)", len(part_rows), report["targeting"])

        for part in part_rows:
            report["parts_checked"] += 1
            report[part.population + "_checked"] += 1
            is_priced = part.population == "priced"
            part_number = str(part.oem_number or "")

            results = []
            search_failed = False
            unconfirmed_kind = None
            try:
                # raise_on_unconfirmed: a search that got NO answer must never look like "answered: nothing found"
                # (root cause of the 2026-09-26 false no_match: text_search swallowed every failure into []).
                results = await aliexpress.search_by_oem(part_number, limit=3, brand=str(part.manufacturer or ""), name=str(part.name or ""),
                                                        raise_on_unconfirmed=True)
                consecutive_unconfirmed = 0              # a real answer (even an empty one) proves the API is responding
            except AliExpressSearchUnconfirmed as exc:
                unconfirmed_kind = exc.kind
                report["errors"].append(f"search:{part_number}:{exc.kind}:{exc.detail}")
                logger.warning("AliExpress search unconfirmed for %s (%s) — target stays eligible for the next run", part_number, exc)
            except Exception as exc:
                unconfirmed_kind = "error"
                logger.error("AliExpress sync search error for %s: %s", part_number, exc)
                report["errors"].append(f"search:{part_number}:{exc}")

            if unconfirmed_kind is not None:
                search_failed = True                     # NOT a confirmed negative: no no_match, no 30-day negative cache
                consecutive_unconfirmed += 1
                report["searches_unconfirmed"] += 1
                report["search_failure_kinds"][unconfirmed_kind] = report["search_failure_kinds"].get(unconfirmed_kind, 0) + 1
                if unconfirmed_kind == "auth" or consecutive_unconfirmed >= _MAX_CONSECUTIVE_UNCONFIRMED:
                    # persistent failure: stop instead of hammering the API; untouched targets stay eligible next run
                    report["search_aborted"] = True
                    why = "auth failure" if unconfirmed_kind == "auth" else f"{consecutive_unconfirmed} consecutive unconfirmed searches"
                    report["errors"].append(f"search aborted ({why}, last kind={unconfirmed_kind}); "
                                            f"{len(part_rows) - report['parts_checked']} remaining targets left untouched for the next run")
                    logger.error("AliExpress price sync search loop aborted: %s", report["errors"][-1])
                    break

            # Throttle to respect rate limits
            await asyncio.sleep(0.5)

            if not results:
                if not search_failed:                    # a CONFIRMED negative only (unconfirmed ones are counted in searches_unconfirmed)
                    report["parts_not_found"] += 1
                    await _mark_candidate(db, part.id, "no_match"); pending_writes += 1
                if pending_writes >= 25:
                    await db.commit()
                    pending_writes = 0
                continue

            cheapest = min(results, key=lambda r: float(getattr(r, "total_cost", 0) or 0))
            detail = None
            if getattr(cheapest, "item_id", None):
                try:
                    detail = await aliexpress.get_part_details(str(cheapest.item_id))
                except Exception as exc:
                    report["errors"].append(f"details:{part_number}:{exc}")

            selected = detail or cheapest

            price_usd = Decimal(str(getattr(selected, "price", None) or getattr(cheapest, "price", 0) or 0))
            if price_usd <= 0:
                report["parts_not_found"] += 1
                await _mark_candidate(db, part.id, "no_match"); pending_writes += 1
                continue

            price_ils = (price_usd * Decimal(str(ils_per_usd_rate))).quantize(Decimal("0.01"))
            candidate_min_price_ils = float(price_ils)

            image_urls = _normalize_image_urls(
                list(getattr(selected, "image_urls", None) or [])
                + ([str(getattr(selected, "image_url", "") or "")] if getattr(selected, "image_url", None) else [])
            )

            selected_item_url = str(getattr(selected, "item_url", "") or "")[:1000]
            warranty = warranty_policy.resolve(getattr(selected, "warranty_text", None),
                                               getattr(selected, "warranty_months", None))

            try:
                async with db.begin_nested():     # SAVEPOINT: a failing part rolls back alone
                    outcome = await _write_supplier_part(
                        db, supplier_id=aliexpress_supplier_id, part_id=str(part.id), part_number=part_number,
                        price_usd=price_usd, price_ils=price_ils, ils_per_usd_rate=ils_per_usd_rate,
                        item_url=selected_item_url, image_urls=image_urls, warranty=warranty,
                        offer_part_type=classify_marketplace_listing(getattr(cheapest, "title", "") or getattr(selected, "title", "")),
                        touch_catalog=not is_priced)
            except Exception as exc:
                report["errors"].append(f"write:{part_number}:{type(exc).__name__}")
                logger.error("AliExpress write failed for %s: %s", part_number, type(exc).__name__)
                continue
            if outcome is None:
                await _mark_candidate(db, part.id, "no_match"); pending_writes += 1
                report["sku_conflicts"] += 1
                logger.warning("AliExpress skip %s: OEM already held by another part of this supplier", part_number)
                continue

            report["parts_updated"] += 1
            report[part.population + "_updated"] += 1
            await _mark_candidate(db, part.id, "matched")
            report["parts_images_added"] += outcome["images"]
            report["price_history_rows"] += outcome["history"]
            pending_writes += outcome["writes"]
            catalog_doc = outcome["catalog_doc"]
            if catalog_doc:
                report["catalog_rows_updated"] += 1
                if await _meili_sync_part(catalog_doc):
                    report["parts_indexed"] += 1
                else:
                    report["index_failures"] += 1

            logger.info(
                "AliExpress updated part %s: usd=%s ils=%s",
                part.oem_number, float(price_usd), float(price_ils),
            )

            if pending_writes >= 25:
                await db.commit()
                pending_writes = 0

        if pending_writes:
            await db.commit()

        report["api_calls"] = aliexpress.api_calls - _calls0
        report["api_call_limit_hits"] = aliexpress.api_call_limit_hits - _limit_hits0
        logger.info("AliExpress price sync complete: %s", report)
        return report

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.error("sync_aliexpress_prices failed: %s", exc, exc_info=True)
        report["errors"].append(str(exc))
        return report
