#!/usr/bin/env python3
"""
Script:  maintenance/meili_purge_inactive.py
Purpose: Delete Meilisearch documents for parts that are no longer active.

Why this exists (found 2026-07-28):
    `meili_sync.py` only ever ADDS or UPDATES documents, and its source queries
    all carry `WHERE is_active = TRUE`. Nothing ever removes a document when a
    part is later deactivated, so the index accumulates phantom docs forever.
    Measured on the live system: Meilisearch held 4,419,309 documents against
    4,350,159 active parts — **69,150 stale documents**, 81% of them in 'כללי'
    (because deactivated parts skew heavily to the catch-all).

    Customer impact is currently limited: `routes/parts.py` builds its result set
    with `conditions_base = ["pc.is_active = TRUE"]`, so a stale Meili hit is
    dropped at the DB join and never reaches a customer. But the FACET COUNTS
    served from Meilisearch are wrong, the index carries dead weight, and the
    drift grows every time a part is deactivated.

    The document id IS the part id, so deletion is a straight id-batch call.
    A filter-based delete would NOT work: the stale docs were written while the
    part was still active, so their stored `is_active` field is `true`.

Process:
  1. Read inactive part ids from the catalog.
  2. Delete them from the index in batches (Meilisearch delete-batch).
  3. Report before/after document counts.

Data Modified: Meilisearch 'parts' index only. Never touches Postgres.
Last Updated:  2026-07-28
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
MEILI_URL = os.environ.get("MEILI_URL", "http://meilisearch:7700").rstrip("/")
MEILI_KEY = os.environ.get("MEILI_MASTER_KEY", "")
INDEX = os.environ.get("MEILI_INDEX", "parts")
BATCH = int(os.getenv("MEILI_PURGE_BATCH", "10000"))


def _req(path: str, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{MEILI_URL}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {MEILI_KEY}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read() or b"{}")


def _doc_count() -> int:
    return _req(f"/indexes/{INDEX}/stats").get("numberOfDocuments", 0)


async def main() -> None:
    before = _doc_count()
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '300s'")

    active = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active")
    rows = await conn.fetch(
        "SELECT id::text FROM parts_catalog WHERE NOT is_active")
    await conn.close()

    ids = [r[0] for r in rows]
    print(f"[meili_purge] index={before:,} docs | active parts={active:,} | "
          f"inactive parts={len(ids):,} | excess={before - active:+,}", flush=True)

    if not ids:
        print("[meili_purge] nothing to purge")
        return

    tasks = []
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        res = _req(f"/indexes/{INDEX}/documents/delete-batch", "POST", chunk)
        tasks.append(res.get("taskUid"))
        print(f"[meili_purge] queued delete {i + len(chunk):,}/{len(ids):,} "
              f"(task {res.get('taskUid')})", flush=True)

    # Wait for the last enqueued task to finish so the reported count is real.
    if tasks and tasks[-1] is not None:
        import time
        for _ in range(240):          # up to ~20 min
            t = _req(f"/tasks/{tasks[-1]}")
            if t.get("status") in ("succeeded", "failed", "canceled"):
                print(f"[meili_purge] final task {t.get('status')}")
                break
            time.sleep(5)

    after = _doc_count()
    print(f"[meili_purge] DONE: {before:,} -> {after:,} docs "
          f"({after - before:+,}); active parts={active:,}, "
          f"remaining excess={after - active:+,}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
