"""
Script:  maintenance/cpie_base_price_repair.py
Purpose: Evidence-backed repair of parts_catalog.base_price for Car-Parts.ie-sourced parts that DIVERGE from the ONE ownership rule
         (base_price_rule.py == db_update_agent.apply_base_price_rule). NOT a blanket formula: the manifest holds only parts whose
         stored value differs from the rule's target, with the offer/estimate inputs that produce the target. Root cause (2026-11-16):
         the importer wrote its own `price_ils x 1.45`, and legacy rows carry importer_price_ils = offer/1.18, so a foreign (0% VAT)
         offer's base_price was understated by 18% (sample: 41% of 6,054 parts diverged; 59% were already correct).
Process:  --build      read-only keyset scan -> sha256-pinned manifest (jsonl.gz) of divergent parts + counts of every other class
          --apply      per batch ONE transaction: lock rows (FOR UPDATE SKIP LOCKED), compare-and-swap on the manifest's `current`,
                       recompute the rule for the locked parts and require it to still equal the manifest `target` (else skip: inputs
                       moved since the manifest), write base_price (+ updated_at, the Meilisearch incremental key), journal each row
                       BEFORE commit (fsync); resumable (already-journaled parts are skipped); stop-file + running-job gates.
          --rollback   per journal row, only if base_price still equals the recorded `after`; a newer legitimate write is skipped.
Data Imported/Modified: parts_catalog.base_price / updated_at of manifest parts ONLY. No offer, no price_ils, no visibility.
Data Sources: parts_catalog, supplier_parts, suppliers, system_settings (rate).
Missing Data Delegation: parts with no basis (no_basis / other_class_only) are never written, only counted.
Last Updated: 2026-11-16
"""
from __future__ import annotations

import argparse, asyncio, gzip, hashlib, json, os, sys, time, uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import base_price_rule as bpr

DSN = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
CPIE = os.getenv("CPIE_SUPPLIER_ID", "88f6ce24-ab5f-4f99-8b41-03cf6828cf3d")
STATE = Path(__file__).resolve().parent.parent / "state" / "cpie_ownership" / "base_price_repair"
STOP_FILE = STATE / "STOP"
BUSY_JOBS = ("run_all_tasks", "night_pipeline")


def classify(row, rate):
    """Pure. -> (cls, target, source). cls: correct | divergent | no_target."""
    tgt, src = bpr.target(row, rate)
    if tgt is None:
        return "no_target", None, src
    cur = None if row.base_price is None else round(float(row.base_price), 2)
    return ("correct" if cur == tgt else "divergent"), tgt, src


async def _connect(write: bool):
    c = await asyncpg.connect(DSN)
    if not write:
        await c.execute("SET default_transaction_read_only=on")
    await c.execute("SET statement_timeout='120s'")
    return c


async def build(a):
    c = await _connect(False)
    rate = await bpr.get_rate(c)
    sql = bpr._asyncpg_sql(bpr.inputs_sql(True))
    counts, rows, last, scanned = {}, [], "00000000-0000-0000-0000-000000000000", 0
    try:
        while True:
            # ORDER BY the uuid column itself (pc.id), NOT the text alias: sorting by `id::text` forced a parallel seq scan + sort of
            # ~1.2M rows per batch (consultant EXPLAIN 2026-11-16); the PK index serves this keyset scan.
            ids = [str(r["id"]) for r in await c.fetch(
                "SELECT pc.id FROM parts_catalog pc WHERE pc.is_active AND pc.id > $1::uuid AND pc.specifications->>'source'="
                "(SELECT name FROM suppliers WHERE id=$2::uuid) ORDER BY pc.id LIMIT $3", last, CPIE, a.batch)]
            if not ids:
                break
            last = ids[-1]
            for r in await c.fetch(sql, ids, rate):
                row = SimpleNamespace(**dict(r))
                cls, tgt, src = classify(row, rate)
                k = f"{cls}:{src}" if cls != "correct" else "correct"
                counts[k] = counts.get(k, 0) + 1
                scanned += 1
                if cls == "divergent":
                    rows.append({"part_id": row.id, "current": None if row.base_price is None else str(row.base_price), "target": f"{tgt:.2f}",
                                 "source": src, "offer_ils": None if row.offer_ils is None else str(row.offer_ils), "offer_supplier": row.offer_supplier,
                                 "importer_price_ils": None if row.importer_price_ils is None else str(row.importer_price_ils),
                                 "online_price_ils": None if row.online_price_ils is None else str(row.online_price_ils)})
            if a.limit and scanned >= a.limit:
                break
            if scanned % (a.batch * 50) < a.batch:
                print(f"scanned={scanned} divergent={len(rows)}", flush=True)
    finally:
        await c.close()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(a.out, "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    sha = hashlib.sha256(open(a.out, "rb").read()).hexdigest()
    Path(a.out + ".sha256").write_text(sha + "\n")
    summary = {"manifest": a.out, "manifest_sha256": sha, "rows": len(rows), "scanned": scanned, "counts": counts, "rate": rate,
               "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    Path(a.out + ".summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


def _load_manifest(path, expect_sha, expect_count):
    raw = open(path, "rb").read()
    if hashlib.sha256(raw).hexdigest() != expect_sha:
        raise SystemExit("REFUSED: manifest sha256 mismatch")
    rows = [json.loads(l) for l in gzip.open(path, "rt")]
    if expect_count is not None and len(rows) != expect_count:
        raise SystemExit(f"REFUSED: manifest has {len(rows)} rows, expected {expect_count}")
    return rows


async def _busy(c):
    r = await c.fetch("SELECT job_id FROM job_registry WHERE status='running' AND last_heartbeat_at > NOW() - INTERVAL '10 minutes'")
    return [x["job_id"] for x in r if any(b in x["job_id"] for b in BUSY_JOBS)]


async def apply(a):
    if not a.approval_ref:
        raise SystemExit("REFUSED: --approval-ref required")
    rows = _load_manifest(a.manifest, a.expect_sha256, a.expect_count)
    STATE.mkdir(parents=True, exist_ok=True)
    journal = Path(a.journal or STATE / f"journal_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl")
    done = set()
    if journal.exists():
        done = {json.loads(l)["part_id"] for l in open(journal) if '"committed"' in l}
    todo = [r for r in rows if r["part_id"] not in done]
    c = await _connect(True)
    rate = await bpr.get_rate(c)
    sql = bpr._asyncpg_sql(bpr.inputs_sql(True))
    tot = {"applied": 0, "skipped_changed": 0, "skipped_rule_moved": 0, "skipped_locked": 0, "batches": 0}
    jf = open(journal, "a")
    try:
        for i in range(0, len(todo), a.batch):
            if STOP_FILE.exists():
                print("STOP file present - halting (resumable)"); break
            if a.max_batches and tot["batches"] >= a.max_batches:
                break
            busy = await _busy(c)
            if busy:
                print(f"HALT: {busy} running - writes would contend (resumable)"); break
            chunk = todo[i:i + a.batch]
            ids = [r["part_id"] for r in chunk]
            async with c.transaction():
                locked = {r["id"]: r["bp"] for r in await c.fetch(
                    "SELECT id::text id, base_price bp FROM parts_catalog WHERE id = ANY($1::uuid[]) FOR UPDATE SKIP LOCKED", ids)}
                tot["skipped_locked"] += len(ids) - len(locked)
                cands = []
                for r in chunk:
                    if r["part_id"] not in locked:
                        continue
                    live = locked[r["part_id"]]
                    if (None if live is None else Decimal(live)) != (None if r["current"] is None else Decimal(r["current"])):
                        tot["skipped_changed"] += 1          # a newer legitimate write: never overwritten
                        continue
                    cands.append(r)
                fresh = {}
                if cands:
                    for x in await c.fetch(sql, [r["part_id"] for r in cands], rate):
                        row = SimpleNamespace(**dict(x))
                        fresh[row.id] = bpr.target(row, rate)[0]
                good = []
                for r in cands:
                    t = fresh.get(r["part_id"])
                    if t is None or Decimal(f"{t:.2f}") != Decimal(r["target"]):
                        tot["skipped_rule_moved"] += 1       # offers/estimate inputs moved since the manifest
                    else:
                        good.append(r)
                if good:
                    for r in good:                          # journal BEFORE commit, fsynced
                        jf.write(json.dumps({"state": "intent", "part_id": r["part_id"], "before": r["current"], "after": r["target"],
                                             "approval_ref": a.approval_ref, "ts": time.time()}) + "\n")
                    jf.flush(); os.fsync(jf.fileno())
                    n = await c.fetchval(
                        """WITH u AS (UPDATE parts_catalog pc SET base_price=t.v, updated_at=NOW()
                                      FROM unnest($1::uuid[], $2::numeric[]) AS t(id, v) WHERE pc.id=t.id RETURNING 1) SELECT count(*) FROM u""",
                        [r["part_id"] for r in good], [Decimal(r["target"]) for r in good])
                    assert n == len(good), (n, len(good))
                    tot["applied"] += n
            for r in good:
                jf.write(json.dumps({"state": "committed", "part_id": r["part_id"], "before": r["current"], "after": r["target"]}) + "\n")
            jf.flush(); os.fsync(jf.fileno())
            tot["batches"] += 1
            if a.sleep:
                await asyncio.sleep(a.sleep)
    finally:
        jf.close(); await c.close()
    print(json.dumps({"journal": str(journal), **tot, "remaining": len(todo) - tot["applied"] - tot["skipped_changed"] - tot["skipped_rule_moved"] - tot["skipped_locked"]}))


async def rollback(a):
    if not a.approval_ref:
        raise SystemExit("REFUSED: --approval-ref required")
    last = {}
    for l in open(a.journal):
        d = json.loads(l)
        if d["state"] in ("intent", "committed"):      # a crash between commit and the committed line must still be undoable (CAS on `after`)
            last[d["part_id"]] = d
    c = await _connect(True)
    out = {"restored": 0, "skipped_newer": 0}
    try:
        items = list(last.values())
        for i in range(0, len(items), a.batch):
            ch = items[i:i + a.batch]
            async with c.transaction():
                n = await c.fetchval(
                    """WITH u AS (UPDATE parts_catalog pc SET base_price=t.b, updated_at=NOW()
                                  FROM unnest($1::uuid[], $2::numeric[], $3::numeric[]) AS t(id, b, a)
                                  WHERE pc.id=t.id AND pc.base_price=t.a RETURNING 1) SELECT count(*) FROM u""",
                    [d["part_id"] for d in ch], [Decimal(d["before"]) if d["before"] is not None else None for d in ch],
                    [Decimal(d["after"]) for d in ch])
                out["restored"] += n; out["skipped_newer"] += len(ch) - n
    finally:
        await c.close()
    print(json.dumps(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["build", "apply", "rollback"])
    ap.add_argument("--out"); ap.add_argument("--manifest"); ap.add_argument("--expect-sha256"); ap.add_argument("--expect-count", type=int)
    ap.add_argument("--approval-ref"); ap.add_argument("--journal")
    ap.add_argument("--batch", type=int, default=500); ap.add_argument("--max-batches", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--sleep", type=float, default=0.5)
    a = ap.parse_args()
    if a.mode == "apply" and not (a.manifest and a.expect_sha256):
        raise SystemExit("REFUSED: --manifest and --expect-sha256 required")
    asyncio.run({"build": build, "apply": apply, "rollback": rollback}[a.mode](a))


if __name__ == "__main__":
    main()
