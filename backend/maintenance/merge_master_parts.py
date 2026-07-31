#!/usr/bin/env python3
"""
Script:  maintenance/merge_master_parts.py
Purpose: Build ONE MASTER RECORD per real part, merging every duplicate row that
         shares a manufacturer + OEM number into a single enriched, authoritative
         part that carries all available data and all supplier relationships.

OWNER DIRECTIVE 2026-07-29 — the mindset change that defines this script:

    dedup is NOT "find duplicate records and retire them"
    dedup IS  "build one master record for each real part"

That distinction matters for a multi-source catalog of millions of rows. The old
`bmw_oem_dedup.py` picked a winner and repointed what it could — so anything the
LOSER knew that the winner did not (a name, an image, fitment, a cheaper supplier
cost) was simply lost with it. Measured live: it left 27 supplier offers stranded
on retired rows, 22 of them at a LOWER cost than the surviving record — and since
the customer price is derived from the CHEAPEST reachable offer
(routes/parts.py: `ORDER BY sp.price_ils ASC`, and search filters
`pc.is_active = TRUE`), a stranded cheaper cost silently raises the price the
customer is quoted. Nothing may be abandoned.

IDENTITY — what counts as the same part
    manufacturer_id + NORMALISED oem_number.
    OEM is the manufacturer's unique identifier for a physical part; the NAME is
    not an identity (owner decision 2026-07-29). Generic names are reused across
    thousands of different components — 'key insert' 37,426 rows, 'harness'
    28,577 — so a name-based rule produced 265,774 false duplicates from a
    2,000-row batch. Normalisation strips spaces/dashes/dots to match
    idx_parts_catalog_norm_oem, so `51759-2B300` and `517592B300` are one part.

CANONICAL SELECTION — highest score wins, ties broken by oldest id
    +8  has a real price (base_price > 0)
    +4  has supplier offers                (count, capped)
    +3  has vehicle fitment rows           (count, capped)
    +2  has an image
    +1  per populated data field           (name_he, description, barcode,
                                            category, specifications, ...)
    The winner is the row that is ALREADY the most complete, so enrichment has
    the least to move and the fewest FK repoints are needed.

ENRICHMENT — the part the old script was missing
    Every field the canonical lacks is filled from the best loser that has it.
    Every supplier offer, fitment row and image is moved across (deduped). A
    supplier offer is NEVER dropped without comparison: if the canonical already
    has an offer from that supplier, the CHEAPER cost wins.

SAFETY
    • SOFT delete only: loser -> is_active=FALSE +
      specifications.dedup_merged_into = <canonical id>. Fully reversible;
      nothing is hard-deleted from parts_catalog.
    • FOR UPDATE SKIP LOCKED so it never fights the live harvester.
    • Bounded batches + SET LOCAL statement_timeout.
    • --dry-run reports exactly what WOULD move, writing nothing.

Usage:
    python3 /app/maintenance/merge_master_parts.py --brand jaguar --dry-run --limit 20
    python3 /app/maintenance/merge_master_parts.py --brand jaguar --limit 500
    python3 /app/maintenance/merge_master_parts.py --repair-stranded

Data Modified: parts_catalog, supplier_parts, part_vehicle_fitment, parts_images
Last Updated:  2026-07-29
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# Matches idx_parts_catalog_norm_oem so the scan can use the index.
NORM = "REPLACE(REPLACE(REPLACE(UPPER(oem_number),' ',''),'-',''),'.','')"

# Fields copied into the canonical when it is missing them. Ordered by how much
# they matter to a customer-facing record.
ENRICH_FIELDS = [
    "name_he", "description", "barcode", "oem_number", "category",
    "part_type", "part_condition", "weight_kg", "aftermarket_tier",
    "aftermarket_brand_id", "superseded_by_sku", "customs_tariff_code",
    "thumbnail_url", "thumbnail_status",
]
# Price fields: take the best available value rather than any value.
PRICE_MAX = ["max_price_ils"]
PRICE_MIN = ["min_price_ils"]
PRICE_ANY = ["base_price", "importer_price_ils", "online_price_ils"]


def score_row(r: Dict[str, Any]) -> int:
    """Rank a candidate for being the master record. Higher = more complete."""
    s = 0
    if (r.get("base_price") or 0) > 0:
        s += 8
    s += min(int(r.get("n_offers") or 0), 4) * 4
    s += min(int(r.get("n_fitment") or 0), 3) * 3
    if int(r.get("n_images") or 0) > 0:
        s += 2
    for f in ENRICH_FIELDS:
        v = r.get(f)
        if v is not None and str(v).strip() != "":
            s += 1
    if r.get("specifications"):
        s += 1
    if r.get("compatible_vehicles"):
        s += 1
    return s


async def fetch_group(conn, ids: List[str]) -> List[Dict[str, Any]]:
    cols = ", ".join(f"p.{f}" for f in ENRICH_FIELDS + PRICE_ANY + PRICE_MAX + PRICE_MIN)
    rows = await conn.fetch(
        f"""
        SELECT p.id, p.sku, p.created_at, p.specifications, p.compatible_vehicles,
               {cols},
               (SELECT COUNT(*) FROM supplier_parts sp WHERE sp.part_id = p.id)        AS n_offers,
               (SELECT COUNT(*) FROM part_vehicle_fitment f WHERE f.part_id = p.id)    AS n_fitment,
               (SELECT COUNT(*) FROM parts_images i WHERE i.part_id = p.id)            AS n_images
        FROM parts_catalog p
        WHERE p.id = ANY($1::uuid[])
        ORDER BY p.created_at
        """,
        ids,
    )
    return [dict(r) for r in rows]


async def merge_group(conn, rows: List[Dict[str, Any]], dry: bool) -> Dict[str, int]:
    """Merge one identity group into a single master record."""
    st = {"losers": 0, "fields_filled": 0, "sp_moved": 0, "sp_price_improved": 0,
          "sp_dropped": 0, "fit_moved": 0, "fit_dropped": 0, "img_moved": 0}

    # created_at is NULLABLE on this table, and comparing None to a datetime
    # raises TypeError. The exception is swallowed by the per-group handler, so
    # every duplicate group containing a NULL created_at was silently SKIPPED —
    # it logged one line and moved on, and the group stayed duplicated forever.
    # A NULL creation date sorts LAST (treated as unknown/newest) so it never
    # wins the canonical slot on a tie by accident.
    _far_future = datetime(9999, 1, 1, tzinfo=timezone.utc)

    def _created(r):
        v = r["created_at"]
        if v is None:
            return _far_future
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    ranked = sorted(rows, key=lambda r: (-score_row(r), _created(r)))
    canon, losers = ranked[0], ranked[1:]
    if not losers:
        return st
    st["losers"] = len(losers)
    cid = canon["id"]

    # ── 1. ENRICH: fill every field the master is missing ────────────────────
    updates: Dict[str, Any] = {}
    for f in ENRICH_FIELDS:
        cur = canon.get(f)
        if cur is not None and str(cur).strip() != "":
            continue
        for l in losers:
            v = l.get(f)
            if v is not None and str(v).strip() != "":
                updates[f] = v
                break
    for f in PRICE_ANY + PRICE_MAX:
        best = max([float(r.get(f) or 0) for r in rows] or [0])
        if best > float(canon.get(f) or 0):
            updates[f] = best
    for f in PRICE_MIN:
        vals = [float(r.get(f) or 0) for r in rows if (r.get(f) or 0) > 0]
        if vals and (float(canon.get(f) or 0) <= 0 or min(vals) < float(canon[f])):
            updates[f] = min(vals)

    # specifications: union, canonical wins on conflict, provenance recorded
    merged_specs = {}
    for l in reversed(losers):
        if isinstance(l.get("specifications"), dict):
            merged_specs.update(l["specifications"])
    if isinstance(canon.get("specifications"), dict):
        merged_specs.update(canon["specifications"])
    merged_specs["merged_from"] = [str(l["id"]) for l in losers][:20]
    updates["specifications"] = json.dumps(merged_specs, ensure_ascii=False, default=str)

    if not canon.get("compatible_vehicles"):
        for l in losers:
            if l.get("compatible_vehicles"):
                updates["compatible_vehicles"] = json.dumps(l["compatible_vehicles"], default=str)
                break

    st["fields_filled"] = len([k for k in updates if k != "specifications"])

    if not dry and updates:
        sets, vals = [], []
        for i, (k, v) in enumerate(updates.items(), start=2):
            cast = "::jsonb" if k in ("specifications", "compatible_vehicles") else ""
            sets.append(f"{k} = ${i}{cast}")
            vals.append(v)
        await conn.execute(
            f"UPDATE parts_catalog SET {', '.join(sets)}, updated_at = NOW() WHERE id = $1",
            cid, *vals)

    for l in losers:
        lid = l["id"]

        # ── 2. SUPPLIER OFFERS — never abandon one ───────────────────────────
        offers = await conn.fetch(
            "SELECT id, supplier_id, price_ils FROM supplier_parts WHERE part_id = $1", lid)
        for o in offers:
            same = await conn.fetchrow(
                "SELECT id, price_ils FROM supplier_parts "
                "WHERE part_id = $1 AND supplier_id = $2", cid, o["supplier_id"])
            if same is None:
                st["sp_moved"] += 1
                if not dry:
                    await conn.execute(
                        "UPDATE supplier_parts SET part_id = $1, updated_at = NOW() "
                        "WHERE id = $2", cid, o["id"])
            else:
                # The canonical already has this supplier. Keep the CHEAPER cost —
                # the customer price is derived from the cheapest reachable offer,
                # so silently keeping the dearer one raises what we quote.
                lp, cp = float(o["price_ils"] or 0), float(same["price_ils"] or 0)
                if lp > 0 and (cp <= 0 or lp < cp):
                    st["sp_price_improved"] += 1
                    if not dry:
                        await conn.execute(
                            "UPDATE supplier_parts SET price_ils = $1, updated_at = NOW() "
                            "WHERE id = $2", lp, same["id"])
                st["sp_dropped"] += 1
                if not dry:
                    await conn.execute("DELETE FROM supplier_parts WHERE id = $1", o["id"])

        # ── 3. FITMENT ───────────────────────────────────────────────────────
        fits = await conn.fetch(
            "SELECT id, manufacturer, model, year_from FROM part_vehicle_fitment "
            "WHERE part_id = $1", lid)
        for f in fits:
            dup = await conn.fetchval(
                "SELECT 1 FROM part_vehicle_fitment WHERE part_id = $1 AND manufacturer = $2 "
                "AND model = $3 AND year_from = $4",
                cid, f["manufacturer"], f["model"], f["year_from"])
            if dup:
                st["fit_dropped"] += 1
                if not dry:
                    await conn.execute("DELETE FROM part_vehicle_fitment WHERE id = $1", f["id"])
            else:
                st["fit_moved"] += 1
                if not dry:
                    await conn.execute(
                        "UPDATE part_vehicle_fitment SET part_id = $1 WHERE id = $2", cid, f["id"])

        # ── 4. IMAGES ────────────────────────────────────────────────────────
        imgs = await conn.fetch("SELECT id, url FROM parts_images WHERE part_id = $1", lid)
        for im in imgs:
            dup = await conn.fetchval(
                "SELECT 1 FROM parts_images WHERE part_id = $1 AND url = $2::varchar",
                cid, im["url"])
            if not dup:
                st["img_moved"] += 1
                if not dry:
                    await conn.execute(
                        "UPDATE parts_images SET part_id = $1 WHERE id = $2", cid, im["id"])

        # ── 5. SOFT DELETE with a reference back to the master ───────────────
        if not dry:
            await conn.execute(
                """
                UPDATE parts_catalog
                   SET is_active = FALSE,
                       specifications = COALESCE(specifications, '{}'::jsonb)
                                        || jsonb_build_object('dedup_merged_into', $2::text),
                       updated_at = NOW()
                 WHERE id = $1
                """,
                lid, str(cid))
    return st


async def run(brand: str, limit: Optional[int], batch: int, dry: bool) -> None:
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    # 120s is the right ceiling for the per-GROUP write transactions below, but
    # NOT for the group-DISCOVERY query: grouping (manufacturer_id, normalised
    # OEM) across the whole catalogue is a ~90s aggregate on 4.3M rows, and it
    # got slower (correctly) when manufacturer_id joined the key. Discovery is a
    # read, so it gets its own, longer ceiling; the writes keep the tight one.
    discovery_timeout = os.getenv("MERGE_DISCOVERY_TIMEOUT", "600s")
    await conn.execute(f"SET statement_timeout = '{discovery_timeout}'")
    t0 = time.monotonic()
    totals: Dict[str, int] = {}
    groups_done = 0

    # GROUP BY MUST include manufacturer_id. The owner's dedup rule is "same
    # MANUFACTURER + same OEM number" — an OEM number is only unique WITHIN a
    # manufacturer, and the same digits are reused across brands. Grouping on
    # the normalised OEM alone happened to be harmless while --brand pinned the
    # query to one manufacturer, but it silently becomes a CROSS-BRAND merge the
    # moment a wildcard is passed (e.g. the job queue running all brands), which
    # would fuse unrelated parts and is not reversible in any useful sense.
    rows = await conn.fetch(
        f"""
        SELECT {NORM} AS o, array_agg(id::text) AS ids
        FROM parts_catalog
        WHERE is_active AND manufacturer ILIKE $1
          AND oem_number IS NOT NULL AND btrim(oem_number) <> ''
          AND manufacturer_id IS NOT NULL
        GROUP BY manufacturer_id, {NORM}
        HAVING COUNT(*) > 1
        LIMIT $2
        """,
        brand, limit or 1000000,
    )
    print(f"[merge] {brand}: {len(rows)} identity groups to build "
          f"(discovery {time.monotonic() - t0:.0f}s)", flush=True)
    # Back to the tight ceiling for the write path.
    await conn.execute("SET statement_timeout = '120s'")

    for r in rows:
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = '60s'")
                grp = await fetch_group(conn, r["ids"])
                if len(grp) < 2:
                    continue
                st = await merge_group(conn, grp, dry)
                for k, v in st.items():
                    totals[k] = totals.get(k, 0) + v
            groups_done += 1
            if groups_done % batch == 0:
                print(f"[merge] {groups_done} groups -> {totals}", flush=True)
        except Exception as exc:
            print(f"[merge] group {r['o']} failed: {str(exc)[:120]}", flush=True)

    print(f"[merge] DONE dry={dry} brand={brand} groups={groups_done} "
          f"{totals} in {time.monotonic()-t0:.0f}s")
    await conn.close()


async def repair_stranded(dry: bool) -> None:
    """Rescue supplier offers left on already-retired rows by the OLD dedup.

    The previous script abandoned some loser offers instead of moving or
    price-comparing them. Because search only reads active parts and derives the
    customer price from the CHEAPEST reachable offer, a stranded cheaper cost
    inflates what we quote. Measured live: 27 stranded, 22 cheaper, avg 41%.
    """
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '180s'")
    rows = await conn.fetch(
        """
        SELECT p.id AS loser, (p.specifications->>'dedup_merged_into')::uuid AS canon,
               sp.id AS off_id, sp.supplier_id, sp.price_ils
        FROM parts_catalog p
        JOIN supplier_parts sp ON sp.part_id = p.id
        WHERE p.specifications ? 'dedup_merged_into' AND NOT p.is_active
        """
    )
    moved = improved = dropped = 0
    for r in rows:
        same = await conn.fetchrow(
            "SELECT id, price_ils FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2",
            r["canon"], r["supplier_id"])
        if same is None:
            moved += 1
            if not dry:
                await conn.execute(
                    "UPDATE supplier_parts SET part_id=$1, updated_at=NOW() WHERE id=$2",
                    r["canon"], r["off_id"])
        else:
            lp, cp = float(r["price_ils"] or 0), float(same["price_ils"] or 0)
            if lp > 0 and (cp <= 0 or lp < cp):
                improved += 1
                if not dry:
                    await conn.execute(
                        "UPDATE supplier_parts SET price_ils=$1, updated_at=NOW() WHERE id=$2",
                        lp, same["id"])
            dropped += 1
            if not dry:
                await conn.execute("DELETE FROM supplier_parts WHERE id=$1", r["off_id"])
    print(f"[repair] stranded={len(rows)} moved={moved} price_improved={improved} "
          f"dropped_redundant={dropped} dry={dry}")
    await conn.close()


CURSOR_PATH = "/app/state/merge_master_cursor.json"


async def run_all_brands(limit: Optional[int], batch: int, dry: bool) -> None:
    """Merge across every manufacturer, ONE MANUFACTURER AT A TIME.

    WHY (measured 2026-07-29): the catalogue-wide form of the discovery query
    (`manufacturer ILIKE '%'` + GROUP BY over the whole table) gets SLOWER the
    more work it completes. Early on duplicates are dense and `LIMIT n` stops
    quickly; as they are merged away the remaining groups become sparse, so
    Postgres scans further and further to find n of them. Measured across one
    run: 90s -> 369s -> past the 600s statement timeout, at which point batches
    started FAILING outright. A job whose unit cost rises as it progresses does
    not finish — it stalls.

    Scoping discovery to a single manufacturer_id makes each scan bounded and
    index-supported (idx_parts_catalog_manufacturer_id), so cost stays flat no
    matter how much has already been merged. There are only 77 manufacturers,
    and a brand with nothing left to do costs one cheap indexed query.

    A cursor persists which manufacturer to resume from, so successive batches
    do not re-scan brands that are already clean.
    """
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '300s'")
    try:
        mfrs = [r["manufacturer_id"] for r in await conn.fetch("""
            SELECT DISTINCT manufacturer_id FROM parts_catalog
            WHERE is_active AND manufacturer_id IS NOT NULL
            ORDER BY manufacturer_id
        """)]
    finally:
        await conn.close()
    if not mfrs:
        print("[merge] no manufacturers found")
        return

    start = 0
    try:
        with open(CURSOR_PATH) as fh:
            last = json.load(fh).get("last_manufacturer_id")
        if last in [str(m) for m in mfrs]:
            start = [str(m) for m in mfrs].index(last) + 1
    except Exception:
        pass

    budget = limit or 1000000
    done_total, t0 = 0, time.monotonic()
    # One full pass around the ring at most, so a batch always terminates even
    # if every remaining manufacturer is already clean.
    for i in range(len(mfrs)):
        idx = (start + i) % len(mfrs)
        mfr = mfrs[idx]
        # Stop once the remaining budget is too small to be worth another
        # discovery scan. The first run of this loop finished its 300-group
        # budget after two manufacturers and then spent ~9 more minutes
        # scanning the other 75 for the sake of 4 remaining groups.
        if budget < max(25, (limit or 0) // 20):
            break
        n = await run_one_manufacturer(mfr, budget, batch, dry)
        done_total += n
        budget -= n
        try:
            os.makedirs(os.path.dirname(CURSOR_PATH), exist_ok=True)
            with open(CURSOR_PATH, "w") as fh:
                json.dump({"last_manufacturer_id": str(mfr)}, fh)
        except Exception:
            pass
    print(f"[merge] ALL-BRANDS DONE groups={done_total} in {time.monotonic()-t0:.0f}s")


async def run_one_manufacturer(mfr, limit: int, batch: int, dry: bool) -> int:
    """Discovery + merge scoped to ONE manufacturer_id. Returns groups merged."""
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '300s'")
    t0 = time.monotonic()
    totals: Dict[str, int] = {}
    groups_done = 0
    try:
        rows = await conn.fetch(
            f"""
            SELECT {NORM} AS o, array_agg(id::text) AS ids
            FROM parts_catalog
            WHERE is_active AND manufacturer_id = $1
              AND oem_number IS NOT NULL AND btrim(oem_number) <> ''
            GROUP BY {NORM}
            HAVING COUNT(*) > 1
            LIMIT $2
            """, mfr, limit)
        if not rows:
            return 0
        disc = time.monotonic() - t0
        await conn.execute("SET statement_timeout = '120s'")
        for r in rows:
            try:
                async with conn.transaction():
                    await conn.execute("SET LOCAL statement_timeout = '60s'")
                    grp = await fetch_group(conn, r["ids"])
                    if len(grp) < 2:
                        continue
                    st = await merge_group(conn, grp, dry)
                    for k, v in st.items():
                        totals[k] = totals.get(k, 0) + v
                    groups_done += 1
            except Exception as exc:
                print(f"[merge] group {r['o']} failed: {type(exc).__name__}: {exc}",
                      flush=True)
        print(f"[merge] mfr={mfr} groups={groups_done} discovery={disc:.0f}s "
              f"total={time.monotonic()-t0:.0f}s {totals}", flush=True)
    finally:
        await conn.close()
    return groups_done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="bmw")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all-brands", action="store_true",
                    help="iterate every manufacturer (bounded per-brand scans) "
                         "instead of one catalogue-wide scan that degrades as it runs")
    ap.add_argument("--repair-stranded", action="store_true",
                    help="rescue offers abandoned on already-retired rows")
    a = ap.parse_args()
    if a.repair_stranded:
        asyncio.run(repair_stranded(a.dry_run))
    elif a.all_brands:
        asyncio.run(run_all_brands(a.limit, a.batch, a.dry_run))
    else:
        asyncio.run(run(a.brand, a.limit, a.batch, a.dry_run))


if __name__ == "__main__":
    main()
