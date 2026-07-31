#!/usr/bin/env python3
"""
Script:  maintenance/tag_warranty_source.py
Purpose: Stamp provenance on supplier_parts.warranty_months.

Why: warranty is a COMMERCIAL PROMISE shown to customers. Once the platform
starts filling a default where a supplier stated nothing, the two must stay
distinguishable forever — otherwise nobody can later tell which warranties we
were actually told and which we chose. `warranty_source` is that distinction:
    'supplier'         — the figure came from the supplier's own data
    'platform_default' — no supplier figure existed; platform policy applied

PERFORMANCE — this is the documented "scan-for-nothing" anti-pattern:
`WHERE warranty_months IS NOT NULL AND warranty_source IS NULL` has no index, so
each successive batch scans further past already-tagged rows and the statement
timeout is hit within a few batches (observed live). Driving from the PRIMARY KEY
with a keyset cursor (`id > :last ORDER BY id`) is a plain index scan with
constant cost per batch, exactly like the meili_sync fix.

Usage:  python3 /app/maintenance/tag_warranty_source.py [--source supplier]
Data Modified: supplier_parts.warranty_source
Last Updated:  2026-07-28
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="supplier")
    ap.add_argument("--batch", type=int, default=20000)
    a = ap.parse_args()

    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '120s'")
    t0 = time.monotonic()
    last = "00000000-0000-0000-0000-000000000000"
    tagged = scanned = 0

    while True:
        rows = await conn.fetch(
            """
            WITH b AS (
                SELECT id FROM supplier_parts
                WHERE id > $1::uuid
                ORDER BY id
                LIMIT $2
                -- The harvester writes supplier_parts continuously. Without
                -- SKIP LOCKED this UPDATE queues behind its row locks and dies
                -- on the statement timeout having written NOTHING (observed:
                -- 0 rows across the whole run).
                FOR UPDATE SKIP LOCKED
            ), u AS (
                UPDATE supplier_parts sp SET warranty_source = $3
                FROM b
                WHERE sp.id = b.id
                  AND sp.warranty_months IS NOT NULL
                  AND sp.warranty_source IS NULL
                RETURNING sp.id
            )
            SELECT (SELECT COUNT(*) FROM u) AS upd,
                   (SELECT COUNT(*) FROM b) AS seen,
                   -- NOT MAX(id): Postgres has no max(uuid) aggregate. This exact
                   -- error already bit normalize_categories and is in the Mistake
                   -- Log; the cursor must be taken with ORDER BY ... LIMIT 1.
                   (SELECT id FROM b ORDER BY id DESC LIMIT 1) AS last_id
            """,
            last, a.batch, a.source,
        )
        r = rows[0]
        if not r["seen"]:
            break
        tagged += r["upd"]
        scanned += r["seen"]
        last = str(r["last_id"])
        if scanned % (a.batch * 20) == 0:
            print(f"  scanned {scanned:,} tagged {tagged:,}", flush=True)

    print(f"[warranty_source] tagged {tagged:,} of {scanned:,} scanned "
          f"in {time.monotonic()-t0:.0f}s")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
