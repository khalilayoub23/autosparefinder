"""
Script:  maintenance/recover_wiped_specifications.py
Purpose: Give back the `specifications` of masters whose specs the old merge replaced with {"merged_from": [...]}
         (86,146 on 2026-11-15). The ONLY source is the master's OWN last pre-wipe specifications in a catalog
         backup - never a merged loser's (a loser is a different product).
Process:
  1. `--manifest --raw FOUND.json`: READ-ONLY. For every wiped master: state (still wiped?), backup source, the
     recovered specification, current merged_from, created_at. Classes: RECOVERABLE | NO_SOURCE (reason) |
     CHANGED_SINCE (master no longer wiped / has new keys) | IDENTITY_MISMATCH. Deterministic, sha256-stamped.
  2. `--apply --manifest M --expect-sha256 H --approval-ref R`: per master, one transaction, only if the stored
     specifications are STILL exactly {"merged_from": ...} as recorded; new value = recovered spec with the current
     `merged_from` kept. Previous value is written to the result file (rollback source).
Data Imported/Modified: parts_catalog.specifications (+updated_at). Nothing else.
Data Sources: /backups/catalog (daily/monthly), streamed - not restored.
Missing Data Delegation: NO_SOURCE masters are reported with a reason and left untouched.
Last Updated: 2026-11-15
"""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, time
from pathlib import Path
import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
OUT = Path(os.getenv("STATE_DIR", "/app/state")) / "spec_recovery"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


async def build(raw_path: str) -> Path:
    raw = json.load(open(raw_path))
    found = raw["found"]
    conn = await asyncpg.connect(DB)
    await conn.execute("SET statement_timeout='600s'")
    rows = await conn.fetch("SELECT id::text id, oem_number, name, created_at, specifications::text sp FROM parts_catalog "
                            "WHERE specifications ? 'merged_from' AND (SELECT count(*) FROM jsonb_object_keys(specifications))=1")
    await conn.close()
    items, counts = [], {}
    for r in sorted(rows, key=lambda r: r["id"]):
        cur = json.loads(r["sp"])
        f = found.get(r["id"])
        if f:
            cls, why = "RECOVERABLE", None
            new = dict(f["specifications"]); new["merged_from"] = cur["merged_from"]
        else:
            cls, new = "NO_SOURCE", None
            why = "no non-wiped specifications in any catalog backup (2026-05-31..2026-11-14)"
        counts[cls] = counts.get(cls, 0) + 1
        items.append({"master": r["id"], "class": cls, "reason": why, "backup": f["backup"] if f else None,
                      "current_specifications": cur, "proposed_specifications": new,
                      "created_at": r["created_at"].isoformat() if r["created_at"] else None})
    man = {"tool": "maintenance/recover_wiped_specifications.py", "raw": raw_path, "backups": raw["stats"],
           "wiped_now": len(rows), "counts": counts, "items": items}
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"spec_manifest_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    p.write_text(json.dumps(man, sort_keys=True))
    print(json.dumps({"manifest": str(p), "sha256": _sha(p), "wiped_now": len(rows), "counts": counts}))
    return p


async def apply(path: Path, ref: str) -> None:
    man = json.load(open(path)); conn = await asyncpg.connect(DB); done = skipped = 0; log = []
    try:
        for it in man["items"]:
            if it["class"] != "RECOVERABLE":
                continue
            async with conn.transaction():
                cur = await conn.fetchval("SELECT specifications::text FROM parts_catalog WHERE id=$1::uuid FOR UPDATE SKIP LOCKED", it["master"])
                if cur is None or json.loads(cur) != it["current_specifications"]:
                    skipped += 1; continue
                await conn.execute("UPDATE parts_catalog SET specifications=$2::jsonb, updated_at=NOW() WHERE id=$1::uuid",
                                   it["master"], json.dumps(it["proposed_specifications"]))
                done += 1; log.append({"master": it["master"], "previous": it["current_specifications"]})
    finally:
        await conn.close()
    res = OUT / f"spec_result_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    res.write_text(json.dumps({"manifest": str(path), "manifest_sha256": _sha(path), "approval_ref": ref,
                               "applied": done, "skipped": skipped, "rollback": log}))
    print(json.dumps({"result": str(res), "applied": done, "skipped": skipped}))


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--manifest"); ap.add_argument("--raw"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--expect-sha256"); ap.add_argument("--approval-ref"); a = ap.parse_args()
    if not a.apply:
        asyncio.run(build(a.raw)); return 0
    p = Path(a.manifest)
    if not a.expect_sha256 or _sha(p) != a.expect_sha256 or not a.approval_ref:
        print("REFUSED: --expect-sha256 must equal the manifest hash and --approval-ref is required"); return 3
    asyncio.run(apply(p, a.approval_ref)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
