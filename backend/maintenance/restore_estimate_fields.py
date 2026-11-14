"""
Script:  maintenance/restore_estimate_fields.py
Purpose: Put back the catalogue estimate fields (importer_price_ils / max_price_ils / online_price_ils / min_price_ils)
         that the old merge_master_parts copied from a separated part onto its master (it kept the group's max/min).
Process:
  1. `--manifest IN.json`: READ-ONLY. IN.json is the evidence file built from the catalog backups (per master+field the
     value in every monthly backup + the loser's current value). Only tier A rows are eligible: the value was stable
     in every backup before the merge, then equals the separated part's current value. Writes a manifest with sha256.
  2. `--apply --manifest FILE --expect-sha256 H --approval-ref REF`: one transaction per row; the row is changed only if
     the column STILL holds the recorded contaminated value (anything else = skipped, reported). The previous value is
     written to the result file (rollback source). Never recomputes a value, never touches an offer.
Data Imported/Modified: parts_catalog.<field> (+ updated_at so meili re-syncs). base_price is NOT touched here: run
         db_update_agent.apply_base_price_rule for the restored parts afterwards (see FIXES_TRACKER).
Data Sources: catalog backups (/backups/catalog/monthly), evidence file produced by the CORE repair.
Missing Data Delegation: tier B/C rows (value unstable or no prior value) are reported, not restored.
Last Updated: 2026-11-14
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
OUT_DIR = Path(os.getenv("STATE_DIR", "/app/state")) / "unmerge_distinct"
COLUMN = {"importer": "importer_price_ils", "max": "max_price_ils", "online": "online_price_ils", "min": "min_price_ils"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build(evidence: str) -> Path:
    rows = [r for r in json.load(open(evidence)) if r["tier"].startswith("A") and r["field"] in COLUMN]
    held = [r for r in json.load(open(evidence)) if not r["tier"].startswith("A")]
    man = {"tool": "maintenance/restore_estimate_fields.py", "evidence": evidence,
           "rows": [{"master": r["master"], "field": r["field"], "expect_current": r["current"],
                     "restore_to": r["restore_to"], "customer_visible": r["customer_visible"],
                     "series": r["series"]} for r in rows],
           "held": [{"master": r["master"], "field": r["field"], "tier": r["tier"], "series": r["series"]} for r in held]}
    p = OUT_DIR / f"estimate_manifest_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    p.write_text(json.dumps(man, indent=1))
    print(json.dumps({"manifest": str(p), "sha256": _sha(p), "rows": len(man["rows"]), "held": len(held)}))
    return p


async def apply(path: Path, approval_ref: str) -> None:
    man = json.load(open(path))
    conn = await asyncpg.connect(DB)
    done, skipped = [], []
    try:
        for r in man["rows"]:
            col = COLUMN[r["field"]]
            async with conn.transaction():
                row = await conn.fetchrow(f"SELECT {col} AS v FROM parts_catalog WHERE id=$1::uuid FOR UPDATE SKIP LOCKED",
                                          r["master"])
                if row is None or row["v"] is None or abs(float(row["v"]) - float(r["expect_current"])) > 1e-9:
                    skipped.append({**{k: r[k] for k in ("master", "field")}, "reason": "state changed or locked",
                                    "now": None if row is None else (None if row["v"] is None else float(row["v"]))})
                    continue
                await conn.execute(f"UPDATE parts_catalog SET {col}=$2, updated_at=NOW() WHERE id=$1::uuid",
                                   r["master"], r["restore_to"])
                done.append({"master": r["master"], "field": r["field"], "previous": r["expect_current"],
                             "restored": r["restore_to"]})
    finally:
        await conn.close()
    res = OUT_DIR / f"estimate_result_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    res.write_text(json.dumps({"manifest": str(path), "manifest_sha256": _sha(path), "approval_ref": approval_ref,
                               "applied": len(done), "skipped": len(skipped), "done": done, "skipped_rows": skipped}, indent=1))
    print(json.dumps({"result": str(res), "applied": len(done), "skipped": len(skipped)}))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest")
    ap.add_argument("--evidence")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--expect-sha256")
    ap.add_argument("--approval-ref")
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not a.apply:
        build(a.evidence or a.manifest)
        return 0
    p = Path(a.manifest)
    if not a.expect_sha256 or _sha(p) != a.expect_sha256 or not a.approval_ref:
        print("REFUSED: --expect-sha256 must equal the manifest hash and --approval-ref is required")
        return 3
    asyncio.run(apply(p, a.approval_ref))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
