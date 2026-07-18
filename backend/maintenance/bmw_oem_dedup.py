#!/usr/bin/env python3
"""
BMW duplicate-OEM dedup — FK-safe, batched, SOFT-delete (reversible).

Why: OEMPartsOnline imported BMW OEMs WITH dashes (51759-2B300) and Delek imported
the SAME parts WITHOUT dashes (517592B300), so the catalog holds ~36,756 groups of
the same normalized OEM as separate active rows — ~69,058 removable duplicates,
carrying ~63,782 supplier_parts + ~70,913 fitment rows that must be REPOINTED to a
single canonical row before the duplicate is retired (never orphan price/fitment).

Per normalized-OEM group (>1 active BMW row):
  canonical = the priced row (base_price>0) if any, else lowest id.
  losers    = the rest.
  1. supplier_parts: repoint loser rows to canonical where canonical has no offer
     from that supplier yet; if canonical already has that supplier, keep the CHEAPER
     price on canonical and delete the loser's redundant offer. (The (part_id,
     supplier_id) unique constraint is the only one at risk — (supplier_id,
     supplier_sku) is unchanged by a part_id move.)
  3. part_vehicle_fitment: repoint loser rows to canonical, dropping exact-duplicate
     fitment rows canonical already has.
  4. loser row: is_active=FALSE + specifications.dedup_merged_into=<canonical id>
     (SOFT delete — fully reversible; nothing is hard-deleted from parts_catalog).

Safety: processes N groups per transaction (default 300), SELECT ... FOR UPDATE SKIP
LOCKED so it never fights the live harvester, SET LOCAL statement_timeout, deadlock
retry, and a --limit so a run is bounded. Idempotent: already-merged losers are
is_active=FALSE and no longer appear in the group scan.

Usage:
  python3 bmw_oem_dedup.py --dry-run --limit 20      # analyze only, no writes
  python3 bmw_oem_dedup.py --limit 300               # process up to 300 groups
  python3 bmw_oem_dedup.py                            # process all (bounded loop)
  python3 bmw_oem_dedup.py --brand toyota            # any brand, not just BMW
"""
import argparse
import asyncio
import json
import os

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

NORM = "REPLACE(REPLACE(REPLACE(UPPER(oem_number),' ',''),'-',''),'.','')"


def _work_table(brand):
    return "dedup_work_" + "".join(ch if ch.isalnum() else "_" for ch in brand.lower())


async def _ensure_work_table(conn, brand):
    """Materialize dup groups ONCE (one expensive GROUP BY) into a work table so
    every subsequent batch is a cheap indexed read instead of re-scanning the whole
    manufacturer. Rebuilt only if it doesn't already exist. Returns pending count."""
    wt = _work_table(brand)
    exists = await conn.fetchval("SELECT to_regclass($1)", f"public.{wt}")
    if not exists:
        print(f"[bmw_dedup] building work table {wt} (one-time full scan)...", flush=True)
        await conn.execute("SET statement_timeout = '600s'")
        await conn.execute(f"""
            CREATE TABLE {wt} AS
            WITH b AS (
                SELECT id, {NORM} AS norm, base_price
                FROM parts_catalog
                WHERE is_active AND LOWER(manufacturer)=LOWER($1)
                  AND oem_number IS NOT NULL AND oem_number <> ''
            )
            SELECT norm,
                   array_agg(id ORDER BY (base_price > 0) DESC NULLS LAST, id) AS ids,
                   FALSE AS done
            FROM b GROUP BY norm HAVING COUNT(*) > 1
        """, brand)
        await conn.execute(f"ALTER TABLE {wt} ADD PRIMARY KEY (norm)")
        await conn.execute(f"CREATE INDEX ON {wt} (done)")
    pending = await conn.fetchval(f"SELECT COUNT(*) FROM {wt} WHERE NOT done")
    print(f"[bmw_dedup] work table {wt}: {pending} groups pending", flush=True)
    return pending


async def _fetch_dup_groups(conn, brand, batch, dry=False):
    """Cheap batched read from the work table. In real runs it claims rows and marks
    them done; in dry runs it just peeks (leaves done=FALSE)."""
    wt = _work_table(brand)
    if dry:
        rows = await conn.fetch(f"SELECT norm, ids FROM {wt} WHERE NOT done ORDER BY norm LIMIT $1", batch)
    else:
        rows = await conn.fetch(f"""
            WITH picked AS (
                SELECT norm FROM {wt} WHERE NOT done
                ORDER BY norm LIMIT $1 FOR UPDATE SKIP LOCKED
            )
            UPDATE {wt} w SET done = TRUE
            FROM picked WHERE w.norm = picked.norm
            RETURNING w.norm, w.ids
        """, batch)
    return [(r["norm"], r["ids"]) for r in rows]


async def _merge_group(conn, canonical, losers, dry):
    """Repoint FKs from losers -> canonical, then soft-delete losers. Returns stats."""
    st = {"sp_moved": 0, "sp_dropped": 0, "fit_moved": 0, "fit_dropped": 0, "losers": len(losers)}
    if dry:
        st["sp_moved"] = await conn.fetchval(
            "SELECT COUNT(*) FROM supplier_parts WHERE part_id = ANY($1::uuid[])", losers) or 0
        st["fit_moved"] = await conn.fetchval(
            "SELECT COUNT(*) FROM part_vehicle_fitment WHERE part_id = ANY($1::uuid[])", losers) or 0
        return st

    # 1a. supplier_parts that CONFLICT (canonical already has that supplier): keep the
    #     cheaper price on canonical, then delete the loser's redundant offer.
    await conn.execute("""
        UPDATE supplier_parts c
        SET price_ils = LEAST(NULLIF(c.price_ils,0), NULLIF(l.price_ils,0)),
            updated_at = NOW()
        FROM supplier_parts l
        WHERE l.part_id = ANY($2::uuid[]) AND c.part_id = $1
          AND c.supplier_id = l.supplier_id
          AND COALESCE(l.price_ils,0) > 0
          AND (COALESCE(c.price_ils,0) = 0 OR l.price_ils < c.price_ils)
    """, canonical, losers)
    r = await conn.execute("""
        DELETE FROM supplier_parts l
        WHERE l.part_id = ANY($2::uuid[])
          AND EXISTS (SELECT 1 FROM supplier_parts c
                      WHERE c.part_id = $1 AND c.supplier_id = l.supplier_id)
    """, canonical, losers)
    st["sp_dropped"] = int(r.split()[-1])
    # 1a2. loser-vs-loser: two losers can share a supplier_id and would then collide
    #      on (part_id, supplier_id) once both point at canonical. Keep the CHEAPEST
    #      loser offer per supplier, drop the rest.
    r = await conn.execute("""
        DELETE FROM supplier_parts l
        WHERE l.part_id = ANY($1::uuid[])
          AND EXISTS (SELECT 1 FROM supplier_parts l2
                      WHERE l2.part_id = ANY($1::uuid[]) AND l2.supplier_id = l.supplier_id
                        AND (COALESCE(NULLIF(l2.price_ils,0), 1e18) < COALESCE(NULLIF(l.price_ils,0), 1e18)
                             OR (COALESCE(NULLIF(l2.price_ils,0), 1e18) = COALESCE(NULLIF(l.price_ils,0), 1e18)
                                 AND l2.id < l.id)))
    """, losers)
    st["sp_dropped"] += int(r.split()[-1])
    # 1b. non-conflicting loser offers -> repoint to canonical
    r = await conn.execute("""
        UPDATE supplier_parts SET part_id = $1, updated_at = NOW()
        WHERE part_id = ANY($2::uuid[])
    """, canonical, losers)
    st["sp_moved"] = int(r.split()[-1])

    # 3a. drop loser fitment rows that would collide on the actual unique index
    #     uix_pvf_part_mfr_model_year_from = (part_id, manufacturer, model, year_from)
    #     — note year_to is NOT part of the key, so match on (mfr, model, year_from)
    #     only (canonical keeps its row; a differing year_to is an acceptable merge).
    r = await conn.execute("""
        DELETE FROM part_vehicle_fitment l
        WHERE l.part_id = ANY($2::uuid[])
          AND EXISTS (SELECT 1 FROM part_vehicle_fitment c
                      WHERE c.part_id = $1
                        AND COALESCE(c.manufacturer,'') = COALESCE(l.manufacturer,'')
                        AND COALESCE(c.model,'') = COALESCE(l.model,'')
                        AND COALESCE(c.year_from,0) = COALESCE(l.year_from,0))
    """, canonical, losers)
    st["fit_dropped"] = int(r.split()[-1])
    # 3a2. loser-vs-loser: two losers with the same (mfr, model, year_from) would
    #      collide once both point at canonical. Keep the lowest-id one, drop the rest.
    r = await conn.execute("""
        DELETE FROM part_vehicle_fitment l
        WHERE l.part_id = ANY($1::uuid[])
          AND EXISTS (SELECT 1 FROM part_vehicle_fitment l2
                      WHERE l2.part_id = ANY($1::uuid[])
                        AND COALESCE(l2.manufacturer,'') = COALESCE(l.manufacturer,'')
                        AND COALESCE(l2.model,'') = COALESCE(l.model,'')
                        AND COALESCE(l2.year_from,0) = COALESCE(l.year_from,0)
                        AND l2.id < l.id)
    """, losers)
    st["fit_dropped"] += int(r.split()[-1])
    # 3b. repoint remaining loser fitment to canonical
    r = await conn.execute("""
        UPDATE part_vehicle_fitment SET part_id = $1
        WHERE part_id = ANY($2::uuid[])
    """, canonical, losers)
    st["fit_moved"] = int(r.split()[-1])

    # 4. soft-delete losers (reversible), record where they went
    await conn.execute("""
        UPDATE parts_catalog
        SET is_active = FALSE,
            specifications = jsonb_set(COALESCE(specifications,'{}'::jsonb),
                                       '{dedup_merged_into}', to_jsonb($1::text), true),
            updated_at = NOW()
        WHERE id = ANY($2::uuid[])
    """, canonical, losers)
    return st


async def run(brand, limit, batch, dry):
    conn = await asyncpg.connect(DB)
    totals = {"groups": 0, "losers": 0, "sp_moved": 0, "sp_dropped": 0, "fit_moved": 0, "fit_dropped": 0}
    try:
        await _ensure_work_table(conn, brand)
        if dry:
            groups = await _fetch_dup_groups(conn, brand, batch if limit is None else min(batch, limit), dry=True)
            for norm, ids in groups:
                canonical, losers = str(ids[0]), [str(x) for x in ids[1:]]
                if not losers:
                    continue
                st = await _merge_group(conn, canonical, losers, True)
                totals["groups"] += 1
                for k in ("losers", "sp_moved", "sp_dropped", "fit_moved", "fit_dropped"):
                    totals[k] += st.get(k, 0)
            print(f"[bmw_dedup] DRY -> {json.dumps(totals)}", flush=True)
            return
        processed = 0
        consecutive_fail = 0
        while limit is None or processed < limit:
            take = batch if limit is None else min(batch, limit - processed)
            got = 0
            ok = False
            # claim+merge are ONE tx so a rollback un-claims the groups (done stays
            # FALSE) — safe to retry. Retry on deadlock OR statement-timeout (both are
            # transient lock contention with the live harvester on the same tables).
            for attempt in range(5):
                try:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL statement_timeout = '90s'")
                        groups = await _fetch_dup_groups(conn, brand, take, dry=False)
                        got = len(groups)
                        for norm, ids in groups:
                            canonical, losers = str(ids[0]), [str(x) for x in ids[1:]]
                            if not losers:
                                continue
                            st = await _merge_group(conn, canonical, losers, False)
                            totals["groups"] += 1
                            for k in ("losers", "sp_moved", "sp_dropped", "fit_moved", "fit_dropped"):
                                totals[k] += st.get(k, 0)
                    ok = True
                    break
                except (asyncpg.exceptions.DeadlockDetectedError,
                        asyncpg.exceptions.QueryCanceledError):
                    await asyncio.sleep(2 + attempt * 3)  # back off, let the harvester drain
            if not ok:
                consecutive_fail += 1
                if consecutive_fail >= 5:
                    print("[bmw_dedup] 5 consecutive batch failures — box too busy; "
                          "stopping (resume later; work table remembers progress).", flush=True)
                    break
                continue
            consecutive_fail = 0
            if got == 0:
                break
            processed += got
            print(f"[bmw_dedup] processed {processed} groups... "
                  f"soft-deleted={totals['losers']} sp_moved={totals['sp_moved']} "
                  f"sp_dropped={totals['sp_dropped']} fit_moved={totals['fit_moved']} "
                  f"fit_dropped={totals['fit_dropped']}", flush=True)
            await asyncio.sleep(1.0)  # be gentle: yield DB to the harvester between batches
            if dry:
                break
    finally:
        await conn.close()
    print(f"[bmw_dedup] DONE dry={dry} brand={brand} -> {json.dumps(totals)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="bmw")
    ap.add_argument("--limit", type=int, default=None, help="max groups to process (default: all)")
    ap.add_argument("--batch", type=int, default=300, help="groups per transaction")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.brand, a.limit, a.batch, a.dry_run))
