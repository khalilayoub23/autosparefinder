"""
Script: repair_brand_ids_150.py
Purpose: (#150) Correct HISTORICAL parts_catalog.manufacturer_id values that point at the wrong car_brands row (e.g. 41,227 Mazda
         parts on Jaguar's id), and the part_vehicle_fitment.manufacturer_id copies of the same wrong id. The new
         car_brand_resolver only stops NEW corruption.
Process:
  1. Candidates = active parts whose manufacturer TEXT names an active car_brands row different from manufacturer_id
     (text brands that are only labels of the right brand - Vw, Jaguar Land Rover, M-HERO 2 - are excluded).
  2. A row is VERIFIED only when two independent signals agree against the stored id: the text brand AND (the part's own
     part_vehicle_fitment rows name that brand, or the SKU prefix names it), with NO signal for the stored brand (no fitment row
     of the stored brand, SKU prefix not the stored brand). Anything else is left unchanged and counted (conflict / no evidence).
  3. Only (text brand, stored brand) groups of >= MIN_GROUP verified rows are repaired (a 1-3 row group is not a pattern).
  4. Each change is logged in brand_id_repair_log (table, row id, previous id, new id, evidence, run id) BEFORE the update; the
     update is guarded by `manufacturer_id = previous id` (idempotent, never overwrites a newer value). Batches of BATCH rows,
     FOR UPDATE SKIP LOCKED (harvesters write the same tables), updated_at is NOT touched (no Meilisearch churn: the index
     holds the brand TEXT, not the id).
  5. pvf rows of repaired parts: manufacturer_id = previous id AND the row's own manufacturer text names the new brand -> new id.
  Dry run by default. --apply to write. --rollback RUN_ID restores previous ids from the log (only where the id still equals the new one).
Data Imported/Modified: parts_catalog.manufacturer_id, part_vehicle_fitment.manufacturer_id, brand_id_repair_log (new table).
Data Sources: parts_catalog, part_vehicle_fitment, car_brands.   Missing Data Delegation: unresolved rows are only counted.
Last Updated: 2026-11-10
"""
import argparse
import asyncio
import os
import sys
import time
import uuid

import asyncpg

MIN_GROUP = 100
BATCH = 2000
ALIAS_TEXTS = ("Vw", "Jaguar Land Rover", "M-HERO 2")

LOG_DDL = """CREATE TABLE IF NOT EXISTS brand_id_repair_log (
  run_id text NOT NULL, table_name text NOT NULL, row_id uuid NOT NULL, prev_manufacturer_id uuid, new_manufacturer_id uuid NOT NULL,
  manufacturer_text text, evidence text NOT NULL, repaired_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, table_name, row_id))"""

KEY = "regexp_replace(lower({c}),'[^a-z0-9]','','g')"

CANDIDATES = """
WITH cbk AS (SELECT id, name, regexp_replace(lower(name),'[^a-z0-9]','','g') k, is_active FROM car_brands),
mm AS (
  SELECT pc.id, pc.manufacturer m, pc.manufacturer_id cur, tgt.id tgt, tgt.name tgtname, tgt.k tk, cur.name curname, cur.k ck,
         lower(split_part(pc.sku,'-',1)) pref
  FROM parts_catalog pc
  JOIN cbk tgt ON tgt.k = regexp_replace(lower(pc.manufacturer),'[^a-z0-9]','','g') AND tgt.is_active
  LEFT JOIN cbk cur ON cur.id = pc.manufacturer_id
  WHERE pc.manufacturer_id IS NOT NULL AND pc.manufacturer_id <> tgt.id AND pc.manufacturer <> ALL($1::text[])),
sig AS (
  SELECT mm.*,
   (length(pref)>=3 AND (left(tk,length(pref))=pref OR left(pref,length(tk))=tk)) pref_tgt,
   (length(pref)>=3 AND ck IS NOT NULL AND (left(ck,length(pref))=pref OR left(pref,length(ck))=ck)) pref_cur,
   EXISTS (SELECT 1 FROM part_vehicle_fitment f WHERE f.part_id=mm.id AND regexp_replace(lower(f.manufacturer),'[^a-z0-9]','','g')=tk) fit_tgt,
   EXISTS (SELECT 1 FROM part_vehicle_fitment f WHERE f.part_id=mm.id AND ck IS NOT NULL AND regexp_replace(lower(f.manufacturer),'[^a-z0-9]','','g')=ck) fit_cur
  FROM mm)
SELECT id, m, cur, tgt, tgtname, curname,
       CASE WHEN (fit_tgt AND NOT fit_cur AND NOT pref_cur) OR (pref_tgt AND NOT pref_cur AND NOT fit_cur) THEN 'VERIFIED'
            WHEN pref_cur OR fit_cur THEN 'CONFLICT' ELSE 'NO_EVIDENCE' END cls,
       CASE WHEN fit_tgt THEN 'text+fitment' ELSE 'text+sku_prefix' END evidence
FROM sig"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", metavar="RUN_ID")
    a = ap.parse_args()
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute("SET lock_timeout = '3s'")
    if a.rollback:
        await c.execute(LOG_DDL)
        for table in ("parts_catalog", "part_vehicle_fitment"):
            r = await c.execute(f"""UPDATE {table} t SET manufacturer_id = l.prev_manufacturer_id FROM brand_id_repair_log l
                                    WHERE l.run_id=$1 AND l.table_name='{table}' AND l.row_id=t.id AND t.manufacturer_id=l.new_manufacturer_id""",
                                a.rollback)
            print("rollback", table, r)
        return

    rows = await c.fetch(CANDIDATES, list(ALIAS_TEXTS))
    by = {}
    for r in rows:
        by.setdefault((r["m"], r["curname"], r["cls"]), []).append(r)
    verified_groups = {}
    for (m, cur, cls), lst in by.items():
        if cls == "VERIFIED":
            verified_groups[(m, cur)] = lst
    todo = [r for g in verified_groups.values() if len(g) >= MIN_GROUP for r in g]
    left = len(rows) - len(todo)
    print(("APPLY" if a.apply else "DRY RUN"), f"- mismatches (alias texts excluded): {len(rows)}; verified & in groups >= {MIN_GROUP}: {len(todo)}; left unchanged: {left}")
    for (m, cur), g in sorted(verified_groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {m!r:14} stored={cur!r:14} verified={len(g):6} {'REPAIR' if len(g) >= MIN_GROUP else 'below min group - unchanged'}")
    cls_counts = {}
    for (_, _, cls), lst in by.items():
        cls_counts[cls] = cls_counts.get(cls, 0) + len(lst)
    print("  classification:", cls_counts)
    if not a.apply:
        return

    run_id = f"brand150-{uuid.uuid4().hex[:8]}"
    await c.execute(LOG_DDL)
    done = pvf_done = 0
    t0 = time.monotonic()
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        async with c.transaction():
            ids = [r["id"] for r in chunk]
            locked = {r["id"] for r in await c.fetch("SELECT id FROM parts_catalog WHERE id = ANY($1::uuid[]) FOR UPDATE SKIP LOCKED", ids)}
            part = [r for r in chunk if r["id"] in locked]
            pid = [r["id"] for r in part]; prev = [r["cur"] for r in part]; new = [r["tgt"] for r in part]
            ev = [r["evidence"] for r in part]; mt = [r["m"] for r in part]
            await c.execute("""INSERT INTO brand_id_repair_log (run_id, table_name, row_id, prev_manufacturer_id, new_manufacturer_id, manufacturer_text, evidence)
                               SELECT $1, 'parts_catalog', x.id, x.prev, x.new, x.m, x.ev FROM unnest($2::uuid[],$3::uuid[],$4::uuid[],$5::text[],$6::text[]) AS x(id,prev,new,m,ev)
                               ON CONFLICT DO NOTHING""", run_id, pid, prev, new, mt, ev)
            res = await c.execute("""UPDATE parts_catalog pc SET manufacturer_id = x.new FROM unnest($1::uuid[],$2::uuid[],$3::uuid[]) AS x(id,prev,new)
                                     WHERE pc.id = x.id AND pc.manufacturer_id = x.prev""", pid, prev, new)
            done += int(res.split()[-1])
            # fitment rows of the same parts that carry the same wrong id and whose own text names the new brand
            frows = await c.fetch("""SELECT f.id, x.prev, x.new, f.manufacturer FROM part_vehicle_fitment f
                                     JOIN unnest($1::uuid[],$2::uuid[],$3::uuid[]) AS x(id,prev,new) ON x.id = f.part_id
                                     JOIN car_brands b ON b.id = x.new
                                     WHERE f.manufacturer_id = x.prev AND regexp_replace(lower(f.manufacturer),'[^a-z0-9]','','g') = regexp_replace(lower(b.name),'[^a-z0-9]','','g')
                                     FOR UPDATE OF f SKIP LOCKED""", pid, prev, new)
            if frows:
                fid = [r["id"] for r in frows]; fp = [r["prev"] for r in frows]; fn = [r["new"] for r in frows]; ft = [r["manufacturer"] for r in frows]
                await c.execute("""INSERT INTO brand_id_repair_log (run_id, table_name, row_id, prev_manufacturer_id, new_manufacturer_id, manufacturer_text, evidence)
                                   SELECT $1,'part_vehicle_fitment',x.id,x.prev,x.new,x.m,'fitment text names new brand'
                                   FROM unnest($2::uuid[],$3::uuid[],$4::uuid[],$5::text[]) AS x(id,prev,new,m) ON CONFLICT DO NOTHING""", run_id, fid, fp, fn, ft)
                r2 = await c.execute("""UPDATE part_vehicle_fitment f SET manufacturer_id = x.new FROM unnest($1::uuid[],$2::uuid[],$3::uuid[]) AS x(id,prev,new)
                                        WHERE f.id = x.id AND f.manufacturer_id = x.prev""", fid, fp, fn)
                pvf_done += int(r2.split()[-1])
        if (i // BATCH) % 5 == 0:
            print(f"  batch {i // BATCH + 1}: parts={done} fitment={pvf_done} elapsed={time.monotonic() - t0:.0f}s", flush=True)
        await asyncio.sleep(0.2)
    print(f"DONE run_id={run_id} parts_catalog={done} part_vehicle_fitment={pvf_done}")
    await c.close()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.run(main())
