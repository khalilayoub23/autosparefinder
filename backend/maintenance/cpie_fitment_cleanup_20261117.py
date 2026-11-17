"""
Script:  maintenance/cpie_fitment_cleanup_20261117.py
Purpose: Remove the 246 page-wide-block fitment rows (10 small BYD/Geely/Maxus/Wey listings, audit 2026-11-17) AND the matching
         elements of parts_catalog.compatible_vehicles (vehicle search reads that column too). Consultant-reviewed manifest.
Process:  --plan  (read-only) builds the manifest (every row DELETE, per-row evidence) + backups of the pvf rows and of the
                  compatible_vehicles of the affected parts, prints their sha256.
          --apply --expect-manifest-sha256 H --expect-backup-sha256 H2  one transaction: re-verify every row still equals the
                  backup, strip the scoped compatible_vehicles elements (optimistic check), delete the pvf rows, verify counts.
          Rollback: restore.pvf_rows() = INSERT ... ON CONFLICT DO NOTHING from the backup; compat restore = SET to the backed-up JSON.
Data Imported/Modified: part_vehicle_fitment (DELETE 246), parts_catalog.compatible_vehicles (scoped element removal).
Last Updated: 2026-11-17
"""
import argparse, asyncio, hashlib, json, os, sys
from pathlib import Path
import asyncpg

DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
SRC = Path("/app/state/relay/../p2_rows2.json")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


async def plan(rows_file, out):
    rows = json.load(open(rows_file))
    c = await asyncpg.connect(DB); await c.execute("SET default_transaction_read_only=on")
    ids = [r["fid"] for r in rows]
    pvf = {str(r["id"]): {k: (str(v) if v is not None else None) for k, v in dict(r).items()}
           for r in await c.fetch("SELECT * FROM part_vehicle_fitment WHERE id = ANY($1::uuid[])", ids)}
    assert len(pvf) == len(ids) > 0
    pids = sorted({r["pid"] for r in rows})
    compat = {str(r["id"]): r["compatible_vehicles"] for r in await c.fetch(
        "SELECT id, compatible_vehicles::text FROM parts_catalog WHERE id = ANY($1::uuid[])", pids)}
    manifest, strips = [], {}
    for r in rows:
        f = pvf[r["fid"]]
        manifest.append({"fitment_id": r["fid"], "part_id": r["pid"], "part_sku": r["sku"], "part_name": r["name"],
                         "manufacturer": f["manufacturer"], "model": f["model"], "action": "DELETE_FITMENT_ROW",
                         "evidence": f"'{r['name']}' came from the page-wide popular-parts block of the {len(rows) and r['model']} listing "
                                     f"(payload <100 items; listing page '{r.get('slug')}', name category '{r['name_cat']}'); no vehicle-specific evidence"})
        strips.setdefault(r["pid"], []).append((f["manufacturer"], f["model"]))
    compat_plan = {}
    for pid, vs in strips.items():
        old = json.loads(compat[pid]) if compat[pid] else []
        new = [e for e in old if not (isinstance(e, dict) and (e.get("manufacturer"), e.get("model")) in set(vs))]
        compat_plan[pid] = {"old": old, "new": new, "removed": len(old) - len(new)}
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (out / "backup_pvf.json").write_text(json.dumps(pvf, ensure_ascii=False))
    (out / "backup_compat.json").write_text(json.dumps(compat_plan, ensure_ascii=False))
    print("manifest", sha((out / "manifest.json").read_bytes()))
    print("backup  ", sha((out / "backup_pvf.json").read_bytes()), sha((out / "backup_compat.json").read_bytes()))
    print("parts", len(compat_plan), "compat elements to remove", sum(v["removed"] for v in compat_plan.values()))


async def apply(out, exp_manifest, exp_backup):
    out = Path(out)
    assert sha((out / "manifest.json").read_bytes()) == exp_manifest, "manifest hash mismatch"
    assert sha((out / "backup_pvf.json").read_bytes()) == exp_backup, "backup hash mismatch"
    pvf = json.loads((out / "backup_pvf.json").read_text()); compat = json.loads((out / "backup_compat.json").read_text())
    c = await asyncpg.connect(DB)
    async with c.transaction():
        live = {str(r["id"]): r for r in await c.fetch("SELECT id, part_id::text pid, manufacturer, model FROM part_vehicle_fitment WHERE id = ANY($1::uuid[])", list(pvf))}
        assert len(live) == len(pvf) and all(live[i]["pid"] == pvf[i]["part_id"] and live[i]["model"] == pvf[i]["model"] for i in pvf), "rows changed since the backup"
        n_c = 0
        for pid, v in compat.items():
            if v["removed"]:
                tag = await c.execute("UPDATE parts_catalog SET compatible_vehicles=$2::jsonb WHERE id=$1::uuid AND compatible_vehicles=$3::jsonb",
                                      pid, json.dumps(v["new"]), json.dumps(v["old"]))
                assert tag.endswith(" 1"), f"compatible_vehicles changed under us for {pid}"
                n_c += 1
        tag = await c.execute("DELETE FROM part_vehicle_fitment WHERE id = ANY($1::uuid[])", list(pvf))
        assert tag == f"DELETE {len(pvf)}", tag
    left = await c.fetchval("SELECT count(*) FROM part_vehicle_fitment WHERE id = ANY($1::uuid[])", list(pvf))
    print("APPLIED: pvf deleted", len(pvf), ", parts compat updated", n_c, "remaining of manifest:", left)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", metavar="ROWS_JSON"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="/app/state/cpie_fitment_cleanup_20261117")
    ap.add_argument("--expect-manifest-sha256"); ap.add_argument("--expect-backup-sha256")
    a = ap.parse_args()
    if a.plan:
        asyncio.run(plan(a.plan, a.out))
    elif a.apply:
        asyncio.run(apply(a.out, a.expect_manifest_sha256, a.expect_backup_sha256))
