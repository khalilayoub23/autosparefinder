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
  2. PROBE the index (read-only `documents/fetch`, filter `id IN [...]`) and keep only
     the ids that are still there. Creates no Meilisearch task.
  3. Delete just those in batches (Meilisearch delete-batch).
  4. Report before/after document counts.

Why step 2 exists (found 2026-11-03, FIXES_TRACKER #118):
    The first version re-sent ALL ~495k inactive ids after every 2 h sync (50
    `documentDeletion` tasks x 10,000 ids, ~600/day) although the documents were
    long gone (`deletedDocuments: 0`). Meilisearch stores every id list in its task
    store, which grew to 11 GB. Deleting only ids that are present makes a repeat
    run enqueue nothing. A probe that fails is NEVER answered by a blind delete: the
    chunk is skipped, the run exits 1, and the next run re-probes it.

Run record (added 2026-11-03):
    Every run ends with ONE machine-readable line on stdout,
    `[meili_purge_summary] {json}` (start/end, duration, checked, present, submitted,
    skipped, chunks + per-chunk results, probe failures, status
    completed|partial|failed). The sync loop captures it (`purge_run_log.py`) instead of
    discarding the output. Logging only: deletion scope and fail-closed behaviour are unchanged.

Data Modified: Meilisearch 'parts' index only. Never touches Postgres.
Last Updated:  2026-11-03
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
MEILI_URL = os.environ.get("MEILI_URL", "http://meilisearch:7700").rstrip("/")
MEILI_KEY = os.environ.get("MEILI_MASTER_KEY", "")
INDEX = os.environ.get("MEILI_INDEX", "parts")
BATCH = int(os.getenv("MEILI_PURGE_BATCH", "10000"))
PROBE_BATCH = int(os.getenv("MEILI_PURGE_PROBE_BATCH", "5000"))
PROBE_RETRIES = int(os.getenv("MEILI_PURGE_PROBE_RETRIES", "3"))


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


def present_ids(ids: list[str], req=_req, sleep=time.sleep, chunks: list | None = None) -> tuple[list[str], list[str]]:
    """Return (ids still in the index, ids whose probe failed after retries).

    Read-only: `documents/fetch` enqueues no task. `id` is a filterable attribute.
    When `chunks` is given, one result dict per probe chunk is appended to it.
    """
    present: list[str] = []
    failed: list[str] = []
    for i in range(0, len(ids), PROBE_BATCH):
        chunk = ids[i:i + PROBE_BATCH]
        body = {"filter": "id IN [" + ",".join(json.dumps(x) for x in chunk) + "]",
                "fields": ["id"], "limit": len(chunk)}
        for attempt in range(PROBE_RETRIES):
            try:
                res = req(f"/indexes/{INDEX}/documents/fetch", "POST", body)
                found = [d["id"] for d in res.get("results", [])]
                present.extend(found)
                if chunks is not None:
                    chunks.append({"kind": "probe", "n": len(chunk), "present": len(found), "ok": True})
                break
            except Exception as exc:  # noqa: BLE001 - any transport/API error = retry, then skip
                if attempt == PROBE_RETRIES - 1:
                    print(f"[meili_purge] probe failed for {len(chunk):,} ids: {type(exc).__name__}: {str(exc)[:200]}",
                          flush=True)
                    failed.extend(chunk)
                    if chunks is not None:
                        chunks.append({"kind": "probe", "n": len(chunk), "ok": False,
                                       "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                else:
                    sleep(2 ** attempt)
    return present, failed


def purge_with_stats(ids: list[str], req=_req, sleep=time.sleep) -> dict:
    """Delete from the index only the ids that are still there; return the run statistics."""
    chunks: list[dict] = []
    present, failed = present_ids(ids, req, sleep, chunks)
    print(f"[meili_purge] still in index: {len(present):,} of {len(ids):,} inactive "
          f"(probe failures: {len(failed):,})", flush=True)
    tasks = []
    delete_errors = 0
    submitted = 0
    for i in range(0, len(present), BATCH):
        chunk = present[i:i + BATCH]
        try:
            res = req(f"/indexes/{INDEX}/documents/delete-batch", "POST", chunk)
        except Exception as exc:  # noqa: BLE001 - keep going; the next run re-probes
            print(f"[meili_purge] delete batch failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            delete_errors += 1
            chunks.append({"kind": "delete", "n": len(chunk), "ok": False,
                           "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue
        tasks.append(res.get("taskUid"))
        submitted += len(chunk)
        chunks.append({"kind": "delete", "n": len(chunk), "ok": True, "task": res.get("taskUid")})
        print(f"[meili_purge] queued delete {i + len(chunk):,}/{len(present):,} "
              f"(task {res.get('taskUid')})", flush=True)
    return {"tasks": tasks, "errors": len(failed) + delete_errors, "checked": len(ids),
            "present": len(present), "submitted": submitted,
            "skipped": len(failed) + (len(present) - submitted), "probe_failed": len(failed),
            "delete_errors": delete_errors, "chunks": chunks}


def purge(ids: list[str], req=_req, sleep=time.sleep) -> tuple[list, int]:
    """Delete from the index only the ids that are still there. Returns (task uids, failures)."""
    stats = purge_with_stats(ids, req, sleep)
    return stats["tasks"], stats["errors"]


def _status(errors: int, submitted: int, present: int, checked: int, probe_failed: int) -> str:
    """completed = no error; partial = some chunks done, some skipped; failed = nothing could be checked/deleted."""
    if not errors:
        return "completed"
    if probe_failed >= checked and checked:
        return "failed"
    return "partial"


def emit_summary(record: dict) -> None:
    """One bounded, credential-free JSON line (per-chunk detail capped at 50 entries)."""
    out = dict(record)
    chunks = out.get("chunks") or []
    if len(chunks) > 50:
        out["chunks"] = chunks[:50]
        out["chunks_truncated"] = len(chunks) - 50
    print("[meili_purge_summary] " + json.dumps(out, default=str, separators=(",", ":")), flush=True)


async def main() -> int:
    started = time.time()
    record: dict = {"started_at": started, "status": "failed", "checked": 0, "present": 0,
                    "submitted": 0, "skipped": 0, "chunks": []}
    rc = 1
    try:
        rc = await _run(record)
    except Exception as exc:  # noqa: BLE001 - the summary must still be written
        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(f"[meili_purge] FAILED: {record['error']}", flush=True)
        rc = 1
    finally:
        record["finished_at"] = time.time()
        record["duration_s"] = round(record["finished_at"] - started, 1)
        record["exit_code"] = rc
        emit_summary(record)
    return rc


async def _run(record: dict) -> int:
    before = _doc_count()
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout = '300s'")

    active = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active")
    rows = await conn.fetch(
        "SELECT id::text FROM parts_catalog WHERE NOT is_active")
    await conn.close()

    ids = [r[0] for r in rows]
    record.update(docs_before=before, active_parts=active, checked=len(ids))
    print(f"[meili_purge] index={before:,} docs | active parts={active:,} | "
          f"inactive parts={len(ids):,} | excess={before - active:+,}", flush=True)

    if not ids:
        print("[meili_purge] nothing to purge")
        record["status"] = "completed"
        return 0

    stats = purge_with_stats(ids)
    tasks, errors = stats.pop("tasks"), stats["errors"]
    record.update(stats)
    record["tasks"] = tasks[:20]

    # Wait for the last enqueued task to finish so the reported count is real.
    if tasks and tasks[-1] is not None:
        for _ in range(240):          # up to ~20 min
            t = _req(f"/tasks/{tasks[-1]}")
            if t.get("status") in ("succeeded", "failed", "canceled"):
                print(f"[meili_purge] final task {t.get('status')}")
                record["final_task_status"] = t.get("status")
                break
            time.sleep(5)

    after = _doc_count()
    record["docs_after"] = after
    print(f"[meili_purge] DONE: {before:,} -> {after:,} docs "
          f"({after - before:+,}); active parts={active:,}, "
          f"remaining excess={after - active:+,}", flush=True)
    record["status"] = _status(errors, record["submitted"], record["present"],
                               record["checked"], record["probe_failed"])
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
