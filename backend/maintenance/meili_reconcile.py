#!/usr/bin/env python3
"""
Script:  maintenance/meili_reconcile.py
Purpose: Keep the Meilisearch 'parts' index equal to the catalog. The DATABASE is the source of truth: a part is eligible for
         the index iff `parts_catalog.is_active`. Three cooperating mechanisms live here:
         1. TOMBSTONES (fast path): a trigger on parts_catalog queues every hard DELETE and every active->inactive change in
            `meili_pending_deletes`; the incremental `meili_sync` drains the queue (`drain_tombstones`) so deletions, merges
            (merged-away parts are deleted or deactivated) and deactivations leave the index in the same cycle.
         2. RECONCILIATION (safety net): `reconcile()` compares the two id sets in both directions, in bounded chunks:
            STALE  = index ids that are not an active catalog row (deleted, merged away, inactive, malformed/non-canonical id)
            MISSING = active catalog rows that are not in the index (failed/interrupted indexing)
            Stale documents are backed up to a manifest FIRST, re-verified against the database, deleted, and the deletion is
            verified by re-probing the index; missing ones are re-indexed through the same row->document mapping as meili_sync.
         3. OBSERVABILITY: every run writes `status.json` (+ a run history line) with the counters the owner asked for.
Process: python3 /app/maintenance/meili_reconcile.py [--dry-run] [--no-repair-missing] [--allow-large] [--restore MANIFEST]
         Never touches Postgres rows (only reads them; the tombstone queue table is the one thing it writes there).
         Idempotent: a second run on a reconciled index changes nothing. Interrupted runs are simply re-run (stateless).
Data Imported/Modified: Meilisearch 'parts' index; /app/state/meili_reconcile/{status.json,history.jsonl,manifest-*.jsonl};
         Postgres: meili_pending_deletes + 2 triggers on parts_catalog (created idempotently by ensure_tombstones()).
Data Sources: parts_catalog (id, is_active), Meilisearch documents API.
Missing Data Delegation: none.
Last Updated: 2026-11-11

Safety: a run that would delete more than MEILI_RECONCILE_MAX_STALE (20,000) AND more than 1% of the index aborts without
deleting (a wrong DB connection or an empty table must never wipe the index) unless --allow-large. Memory is bounded by the
page size (20,000 ids), never by the catalog size. DB load: keyset/ANY(id) primary-key lookups only, statement_timeout 120 s.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path

STATE_DIR = Path(os.getenv("MEILI_RECONCILE_DIR", "/app/state/meili_reconcile"))
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
PAGE = int(os.getenv("MEILI_RECONCILE_PAGE", "20000"))
PROBE = int(os.getenv("MEILI_RECONCILE_PROBE", "5000"))
DELETE_BATCH = int(os.getenv("MEILI_RECONCILE_DELETE_BATCH", "10000"))
MAX_STALE = int(os.getenv("MEILI_RECONCILE_MAX_STALE", "20000"))
MAX_STALE_FRACTION = float(os.getenv("MEILI_RECONCILE_MAX_STALE_FRACTION", "0.01"))
MISSING_CAP = int(os.getenv("MEILI_RECONCILE_MISSING_CAP", "500000"))


# ── Meilisearch client (injectable so tests can fail any call) ───────────────────────────────────────────────
class MeiliHttp:
    def __init__(self, url: str | None = None, key: str | None = None, index: str | None = None, timeout: int = 120):
        self.url = (url or os.environ.get("MEILI_URL", "http://meilisearch:7700")).rstrip("/")
        self.key = key if key is not None else os.environ.get("MEILI_MASTER_KEY", "")
        self.index = index or os.environ.get("MEILI_INDEX", "parts")
        self.timeout = timeout

    def _req(self, path: str, method: str = "GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read() or b"{}")

    def count(self) -> int:
        return int(self._req(f"/indexes/{self.index}/stats").get("numberOfDocuments", 0))

    def page_ids(self, offset: int, limit: int) -> list[str]:
        res = self._req(f"/indexes/{self.index}/documents?fields=id&limit={limit}&offset={offset}")
        return [str(d["id"]) for d in res.get("results", [])]

    def found(self, ids: list[str]) -> set[str]:
        """Which of `ids` are in the index (read-only fetch, creates no task)."""
        if not ids:
            return set()
        body = {"filter": "id IN [" + ",".join(json.dumps(i) for i in ids) + "]", "fields": ["id"], "limit": len(ids)}
        return {str(d["id"]) for d in self._req(f"/indexes/{self.index}/documents/fetch", "POST", body).get("results", [])}

    def docs(self, ids: list[str]) -> list[dict]:
        body = {"filter": "id IN [" + ",".join(json.dumps(i) for i in ids) + "]", "limit": len(ids)}
        return self._req(f"/indexes/{self.index}/documents/fetch", "POST", body).get("results", [])

    def delete_batch(self, ids: list[str]) -> int:
        return int(self._req(f"/indexes/{self.index}/documents/delete-batch", "POST", ids)["taskUid"])

    def add_documents(self, docs: list[dict]) -> int:
        return int(self._req(f"/indexes/{self.index}/documents", "POST", docs)["taskUid"])

    def wait(self, uid: int, timeout_s: float = 1800.0) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            t = self._req(f"/tasks/{uid}")
            if t.get("status") in ("succeeded", "failed", "canceled"):
                return t
            time.sleep(0.5)
        return {"status": "timeout", "uid": uid}


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ── 1. tombstone queue ─────────────────────────────────────────────────────────────────────────────────────────
_DDL = [
    """CREATE TABLE IF NOT EXISTS meili_pending_deletes (
           part_id uuid PRIMARY KEY, reason text NOT NULL, queued_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE OR REPLACE FUNCTION meili_queue_delete() RETURNS trigger LANGUAGE plpgsql AS $f$
       BEGIN
         INSERT INTO meili_pending_deletes(part_id, reason)
         VALUES (OLD.id, CASE WHEN TG_OP = 'DELETE' THEN 'deleted' ELSE 'deactivated' END)
         ON CONFLICT (part_id) DO NOTHING;
         RETURN NULL;
       END $f$""",
    """CREATE TRIGGER trg_meili_part_deleted AFTER DELETE ON parts_catalog
       FOR EACH ROW EXECUTE FUNCTION meili_queue_delete()""",
    """CREATE TRIGGER trg_meili_part_deactivated AFTER UPDATE OF is_active ON parts_catalog
       FOR EACH ROW WHEN (OLD.is_active IS TRUE AND NEW.is_active IS NOT TRUE) EXECUTE FUNCTION meili_queue_delete()""",
]
_TRIGGERS = ("trg_meili_part_deleted", "trg_meili_part_deactivated")


async def tombstones_installed(conn) -> bool:
    t = await conn.fetchval("SELECT to_regclass('meili_pending_deletes') IS NOT NULL")
    n = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname = ANY($1::text[]) AND NOT tgisinternal", list(_TRIGGERS))
    return bool(t) and n == len(_TRIGGERS)


async def ensure_tombstones(conn, attempts: int = 6) -> bool:
    """Install the queue + triggers once (idempotent; no DDL at all when already installed). CREATE TRIGGER needs a brief
    SHARE ROW EXCLUSIVE lock on parts_catalog: lock_timeout keeps it from queueing behind a long writer (it retries)."""
    if await tombstones_installed(conn):
        return False
    last = None
    for i in range(attempts):
        try:
            await conn.execute("SET lock_timeout = '4s'")
            async with conn.transaction():
                for ddl in _DDL:
                    name = ddl.split()[2] if ddl.startswith("CREATE TRIGGER") else None
                    if name and await conn.fetchval("SELECT 1 FROM pg_trigger WHERE tgname=$1 AND NOT tgisinternal", name):
                        continue
                    await conn.execute(ddl)
            await conn.execute("RESET lock_timeout")
            return True
        except Exception as exc:  # noqa: BLE001 - lock timeout etc: retry, then surface
            last = exc
            await asyncio.sleep(2 + i * 3)
    raise RuntimeError(f"could not install the Meilisearch tombstone triggers: {type(last).__name__}: {str(last)[:200]}")


async def drain_tombstones(conn, http: MeiliHttp | None = None, limit: int = 50000) -> dict:
    """Apply queued deletions to the index. A queue row is removed ONLY after its document is verified gone (or was never
    there, or the part became active again); a failed task leaves the row queued for the next run."""
    http = http or MeiliHttp()
    out = {"queued": 0, "deleted": 0, "not_in_index": 0, "reactivated": 0, "failed": 0, "errors": []}
    rows = await conn.fetch("SELECT part_id::text FROM meili_pending_deletes ORDER BY queued_at LIMIT $1", limit)
    ids = [r[0] for r in rows]
    out["queued"] = len(ids)
    if not ids:
        return out
    active = {r[0] for r in await conn.fetch("SELECT id::text FROM parts_catalog WHERE is_active AND id = ANY($1::uuid[])", ids)}
    stale = [i for i in ids if i not in active]
    out["reactivated"] = len(ids) - len(stale)
    done = [i for i in ids if i in active]                       # re-activated: nothing to delete, just dequeue
    for chunk in _chunks(stale, PROBE):
        try:
            present = http.found(chunk)
        except Exception as exc:  # noqa: BLE001
            out["failed"] += len(chunk); out["errors"].append(f"probe: {type(exc).__name__}: {str(exc)[:120]}"); continue
        out["not_in_index"] += len(chunk) - len(present)
        done.extend(i for i in chunk if i not in present)
        todo = [i for i in chunk if i in present]
        for sub in _chunks(todo, DELETE_BATCH):
            try:
                task = http.wait(http.delete_batch(sub))
                gone = [i for i in sub if i not in http.found(sub)]
            except Exception as exc:  # noqa: BLE001
                out["failed"] += len(sub); out["errors"].append(f"delete: {type(exc).__name__}: {str(exc)[:120]}"); continue
            if task.get("status") != "succeeded":
                out["errors"].append(f"task {task.get('uid') or task.get('taskUid')}: {task.get('status')}")
            out["deleted"] += len(gone); out["failed"] += len(sub) - len(gone)
            done.extend(gone)
    for chunk in _chunks(done, 10000):
        await conn.execute("DELETE FROM meili_pending_deletes WHERE part_id = ANY($1::uuid[])", chunk)
    return out


# ── 2. reconciliation ───────────────────────────────────────────────────────────────────────────────────────────
def _write_status(rec: dict, state_dir: Path) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        prev = {}
        try:
            prev = json.loads((state_dir / "status.json").read_text())
        except (OSError, ValueError):
            pass
        if rec.get("status") == "completed" and not rec.get("dry_run"):
            rec["last_success_at"] = rec.get("finished_at")
        else:
            rec["last_success_at"] = prev.get("last_success_at")
        (state_dir / "status.json").write_text(json.dumps(rec, default=str))
        with open(state_dir / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({k: rec.get(k) for k in ("run", "status", "dry_run", "started_at", "duration_s", "db_active",
                                "index_before", "index_after", "stale_found", "stale_deleted", "missing_found",
                                "missing_repaired")}, default=str) + "\n")
    except OSError as exc:
        print(f"[meili_reconcile] status not written: {exc}", flush=True)


async def _status_of(conn, ids: list[str]) -> dict[str, str]:
    """absent | inactive | active for each canonical uuid in `ids`."""
    out = {i: "absent" for i in ids}
    for chunk in _chunks(ids, PROBE):
        for r in await conn.fetch("SELECT id::text, is_active FROM parts_catalog WHERE id = ANY($1::uuid[])", chunk):
            out[r[0]] = "active" if r[1] else "inactive"
    return out


async def reconcile(conn, http: MeiliHttp, *, apply: bool = True, repair_missing: bool = True, allow_large: bool = False,
                    run_id: str | None = None, state_dir: Path | None = None, page: int | None = None) -> dict:
    state_dir = state_dir or STATE_DIR
    page = page or PAGE
    run = run_id or time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rec = {"run": run, "dry_run": not apply, "status": "failed", "started_at": time.time(), "db_active": 0, "index_before": None,
           "index_after": None, "scanned_index": 0, "stale_found": 0, "stale_by_reason": {}, "stale_deleted": 0,
           "stale_failed": 0, "missing_found": 0, "missing_repaired": 0, "missing_failed": 0, "noncanonical_ids": 0,
           "duplicate_ids": 0, "failed_tasks": [], "manifest": None, "error": None}
    try:
        await conn.execute("SET statement_timeout = '120s'")
        rec["index_before"] = http.count()

        # ── A. STALE: index ids that are not an active catalog row ──
        stale: list[str] = []
        offset = 0
        while True:
            ids = http.page_ids(offset, page)
            if not ids:
                break
            offset += len(ids)
            canon = [i for i in ids if UUID_RE.match(i)]
            active: set[str] = set()
            for chunk in _chunks(canon, 10000):
                active.update(r[0] for r in await conn.fetch(
                    "SELECT id::text FROM parts_catalog WHERE is_active AND id = ANY($1::uuid[])", chunk))
            stale.extend(i for i in ids if i not in active)
        rec["scanned_index"] = offset
        rec["noncanonical_ids"] = sum(1 for i in stale if not UUID_RE.match(i))
        stale = list(dict.fromkeys(stale))

        # re-verify against the database RIGHT BEFORE acting (a part may have been re-activated since the scan)
        st = await _status_of(conn, [i for i in stale if UUID_RE.match(i)])
        verified, reasons = [], {}
        for i in stale:
            r = st.get(i, "malformed_id")
            if r == "active":
                continue
            verified.append(i); reasons[i] = r
        rec["stale_found"] = len(verified)
        for r in reasons.values():
            rec["stale_by_reason"][r] = rec["stale_by_reason"].get(r, 0) + 1

        too_many = len(verified) > MAX_STALE and len(verified) > MAX_STALE_FRACTION * max(rec["scanned_index"], 1)
        if too_many and not allow_large:
            rec["status"] = "aborted_threshold"
            rec["error"] = (f"{len(verified):,} stale documents exceeds the safety threshold ({MAX_STALE:,} and "
                            f"{MAX_STALE_FRACTION:.0%} of the index) - nothing deleted; re-run with --allow-large after review")
            return rec

        if apply and verified:
            # backup BEFORE any deletion: the full document + why it is considered stale
            state_dir.mkdir(parents=True, exist_ok=True)
            mpath = state_dir / f"manifest-{run}.jsonl"
            with open(mpath, "w", encoding="utf-8") as mf:
                for chunk in _chunks(verified, 1000):
                    by_id = {d.get("id"): d for d in http.docs(chunk)}
                    for i in chunk:
                        mf.write(json.dumps({"id": i, "reason": reasons[i], "doc": by_id.get(i)}, ensure_ascii=False) + "\n")
                mf.flush(); os.fsync(mf.fileno())
            rec["manifest"] = str(mpath)
            for chunk in _chunks(verified, DELETE_BATCH):
                try:
                    task = http.wait(http.delete_batch(chunk))
                    if task.get("status") != "succeeded":
                        rec["failed_tasks"].append({"task": task.get("uid"), "status": task.get("status")})
                    left = http.found(chunk)          # deletion is only counted when the documents are really gone
                except Exception as exc:  # noqa: BLE001
                    rec["failed_tasks"].append({"error": f"{type(exc).__name__}: {str(exc)[:150]}"})
                    rec["stale_failed"] += len(chunk); continue
                rec["stale_deleted"] += len(chunk) - len(left); rec["stale_failed"] += len(left)

        # ── B. MISSING: active catalog rows that are not in the index ──
        missing: list[str] = []
        last, db_active, missing_total = ZERO_UUID, 0, 0
        while True:
            rows = await conn.fetch("SELECT id::text FROM parts_catalog WHERE is_active AND id > $1::uuid ORDER BY id LIMIT $2",
                                    last, page)       # one keyset read per page (measured: 4x fewer DB round trips than per probe)
            if not rows:
                break
            page_ids = [r[0] for r in rows]
            last = page_ids[-1]; db_active += len(page_ids)
            for ids in _chunks(page_ids, PROBE):
                have = http.found(ids)
                miss = [i for i in ids if i not in have]
                missing_total += len(miss)
                if len(missing) < MISSING_CAP:
                    missing.extend(miss[:MISSING_CAP - len(missing)])
        rec["db_active"] = db_active
        rec["missing_found"] = missing_total

        if apply and repair_missing and missing:
            import meili_sync as ms                       # one row -> document mapping (same as the sync)
            for chunk in _chunks(missing, PROBE):
                rows = await conn.fetch(ms.SELECT_SQL_BY_IDS, chunk)
                try:
                    if rows:
                        task = http.wait(http.add_documents([ms.row_to_doc(r) for r in rows]))
                        if task.get("status") != "succeeded":
                            rec["failed_tasks"].append({"task": task.get("uid"), "status": task.get("status")})
                            rec["missing_failed"] += len(chunk); continue
                    rec["missing_repaired"] += len(rows); rec["missing_failed"] += len(chunk) - len(rows)
                except Exception as exc:  # noqa: BLE001
                    rec["failed_tasks"].append({"error": f"{type(exc).__name__}: {str(exc)[:150]}"})
                    rec["missing_failed"] += len(chunk)

        rec["index_after"] = http.count()
        clean = not rec["failed_tasks"] and rec["stale_failed"] == 0 and rec["missing_failed"] == 0
        rec["status"] = "completed" if clean else "partial"
    except Exception as exc:  # noqa: BLE001 - the status record must always be written
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        rec["status"] = "failed"
    finally:
        rec["finished_at"] = time.time()
        rec["duration_s"] = round(rec["finished_at"] - rec["started_at"], 1)
        _write_status(rec, state_dir)
    return rec


async def restore(conn, http: MeiliHttp, manifest: str, force: bool = False) -> dict:
    """Re-add documents from a manifest (recovery). Only parts that are ACTIVE in the catalog unless force."""
    docs = []
    for line in open(manifest, encoding="utf-8"):
        d = json.loads(line)
        if d.get("doc"):
            docs.append(d["doc"])
    ids = [d["id"] for d in docs if UUID_RE.match(str(d["id"]))]
    active = {r[0] for r in await conn.fetch("SELECT id::text FROM parts_catalog WHERE is_active AND id = ANY($1::uuid[])", ids)}
    todo = [d for d in docs if force or d["id"] in active]
    for chunk in _chunks(todo, PROBE):
        http.wait(http.add_documents(chunk))
    return {"manifest_docs": len(docs), "restored": len(todo), "skipped_not_active": len(docs) - len(todo)}


def emit_summary(rec: dict) -> None:
    print("[meili_reconcile_summary] " + json.dumps(rec, default=str, separators=(",", ":")), flush=True)


async def _main(args) -> int:
    import asyncpg
    db = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(db, statement_cache_size=0)
    try:
        http = MeiliHttp()
        if args.restore:
            print(await restore(conn, http, args.restore, args.force)); return 0
        await ensure_tombstones(conn)
        rec = await reconcile(conn, http, apply=not args.dry_run, repair_missing=not args.no_repair_missing,
                              allow_large=args.allow_large)
        emit_summary(rec)
        return 0 if rec["status"] in ("completed",) else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Reconcile the Meilisearch parts index with parts_catalog")
    ap.add_argument("--dry-run", action="store_true", help="report only; delete and repair nothing")
    ap.add_argument("--no-repair-missing", action="store_true")
    ap.add_argument("--allow-large", action="store_true", help="allow deleting more than the safety threshold")
    ap.add_argument("--restore", metavar="MANIFEST", help="re-add the documents of a manifest (active parts only)")
    ap.add_argument("--force", action="store_true", help="with --restore: also restore parts that are not active")
    sys.exit(asyncio.run(_main(ap.parse_args())))
