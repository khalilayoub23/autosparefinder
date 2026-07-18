#!/usr/bin/env python3
"""
recategorize_backlog.py — backfill categories for the drifted catalog (goal G6, 2026-07-13).

Three passes, all lock-safe (per-batch commit + FOR UPDATE SKIP LOCKED so it never
blocks / gets blocked by the harvester/agents):

  Pass A — VARIANT NORMALIZE: fold ~70 messy duplicate labels ("Brakes", "Oil
           Filters", "Doors"…) into the 22 canonical slugs (category_map.VARIANT_MAP).
  Pass B — KEYWORD/SLUG: categorize 'general'/'כללי' via category_map.categorize()
           (URL slug + name + name_he). Deterministic, ~22%.
  Pass C — OEM-TWIN: a still-uncategorized part that shares a normalized OEM with an
           already-categorized part inherits that category (same OEM = same part).

Whatever has no signal stays 'general' (honest — the LLM enrichment task closes those).

Usage: python3 recategorize_backlog.py [--pass A|B|C|all] [--limit N]
"""
import argparse
import asyncio
import os

import asyncpg

from category_map import categorize, VARIANT_MAP

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
BATCH = 4000


async def pass_a_variants(conn) -> int:
    """Bulk-normalize messy labels → canonical. Lock-safe: session lock_timeout makes a
    contended UPDATE fail fast; retry a few times, then move on (variant sets are tiny)."""
    total = 0
    for variant, canon in VARIANT_MAP.items():
        if variant == canon:
            continue
        for attempt in range(4):
            try:
                r = await conn.execute(
                    "UPDATE parts_catalog SET category=$1, updated_at=NOW() "
                    "WHERE is_active AND lower(btrim(category))=$2 AND category<>$1",
                    canon, variant)
                n = int(r.split()[-1])
                total += n
                if n:
                    print(f"  [A] {variant!r} -> {canon}: {n:,}", flush=True)
                break
            except (asyncpg.exceptions.LockNotAvailableError,
                    asyncpg.exceptions.QueryCanceledError,
                    asyncpg.exceptions.DeadlockDetectedError):
                await asyncio.sleep(2 + attempt * 2)
    return total


async def pass_b_keyword(conn, limit) -> int:
    done = 0
    empty = 0
    while limit is None or done < limit:
        take = BATCH if limit is None else min(BATCH, limit - done)
        n_updated = 0
        got = 0
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout='4s'")
                rows = await conn.fetch("""
                    SELECT id, name, name_he,
                           COALESCE(specifications->>'product_url', specifications->>'source_url') url
                    FROM parts_catalog
                    WHERE is_active AND category IN ('כללי','general','accessories')
                    ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED
                """, take)
                got = len(rows)
                buckets: dict[str, list] = {}
                for r in rows:
                    cat = categorize(r["name"] or "", r["name_he"] or "", r["url"] or "", "")
                    if cat:
                        buckets.setdefault(cat, []).append(r["id"])
                for cat, ids in buckets.items():
                    await conn.execute(
                        "UPDATE parts_catalog SET category=$1, updated_at=NOW() WHERE id=ANY($2::uuid[])",
                        cat, ids)
                    n_updated += len(ids)
        except (asyncpg.exceptions.LockNotAvailableError,
                asyncpg.exceptions.QueryCanceledError,
                asyncpg.exceptions.DeadlockDetectedError):
            await asyncio.sleep(3)
            continue
        done += n_updated
        if got == 0:
            empty += 1
            if empty >= 3:
                break
            await asyncio.sleep(5)
            continue
        empty = 0
        if done and done % (BATCH * 10) < BATCH:
            print(f"  [B] categorized so far: {done:,}", flush=True)
        await asyncio.sleep(0.1)
    return done


async def pass_c_oem_twin(conn, limit) -> int:
    """Inherit category from a categorized part sharing the normalized OEM. Batched by
    a keyset over id; each batch does a set-based UPDATE joining on normalized OEM."""
    done = 0
    last = "00000000-0000-0000-0000-000000000000"
    while limit is None or done < limit:
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout='4s'")
                await conn.execute("SET LOCAL statement_timeout='60s'")
                r = await conn.execute("""
                WITH bl AS (
                    SELECT id, REPLACE(REPLACE(UPPER(oem_number),' ',''),'-','') norm
                    FROM parts_catalog
                    WHERE is_active AND category IN ('כללי','general')
                      AND oem_number IS NOT NULL AND oem_number<>'' AND id > $1
                    ORDER BY id LIMIT $2
                ), twin AS (
                    SELECT DISTINCT ON (bl.id) bl.id, p2.category
                    FROM bl JOIN parts_catalog p2
                      ON REPLACE(REPLACE(UPPER(p2.oem_number),' ',''),'-','') = bl.norm
                     AND p2.is_active AND p2.category IS NOT NULL
                     AND p2.category NOT IN ('כללי','general','accessories','')
                )
                UPDATE parts_catalog t SET category=twin.category, updated_at=NOW()
                FROM twin WHERE t.id=twin.id
            """, last, BATCH)
            n = int(r.split()[-1])
            # advance cursor to the last id scanned (whether or not it had a twin)
            nxt = await conn.fetchval("""
                SELECT MAX(id) FROM (
                    SELECT id FROM parts_catalog
                    WHERE is_active AND category IN ('כללי','general')
                      AND oem_number IS NOT NULL AND oem_number<>'' AND id > $1
                    ORDER BY id LIMIT $2) s""", last, BATCH)
        except (asyncpg.exceptions.LockNotAvailableError,
                asyncpg.exceptions.QueryCanceledError,
                asyncpg.exceptions.DeadlockDetectedError):
            await asyncio.sleep(3)
            continue
        done += n
        if nxt is None:
            break
        last = str(nxt)
        if done and (done % (BATCH * 5) < BATCH):
            print(f"  [C] OEM-twin categorized so far: {done:,}", flush=True)
        await asyncio.sleep(0.1)
    return done


async def main(which, limit):
    conn = await asyncpg.connect(DB)
    await conn.execute("SET lock_timeout='5s'")          # fail fast on contention
    await conn.execute("SET statement_timeout='90s'")
    try:
        if which in ("A", "all"):
            print("=== Pass A: variant normalize ===", flush=True)
            print(f"[A] normalized {await pass_a_variants(conn):,} rows", flush=True)
        if which in ("B", "all"):
            print("=== Pass B: keyword/slug ===", flush=True)
            print(f"[B] categorized {await pass_b_keyword(conn, limit):,} rows", flush=True)
        if which in ("C", "all"):
            print("=== Pass C: OEM-twin inheritance ===", flush=True)
            print(f"[C] categorized {await pass_c_oem_twin(conn, limit):,} rows", flush=True)
    finally:
        await conn.close()
    print("[recat] DONE", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="which", default="all", choices=["A", "B", "C", "all"])
    ap.add_argument("--limit", type=int, default=None)
    asyncio.run(main(ap.parse_args().which, ap.parse_args().limit))
