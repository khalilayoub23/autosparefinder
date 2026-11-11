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
  6. --restore-text (second defect, same incident): db_update_agent.normalize_imported_manufacturers used to REWRITE parts_catalog.manufacturer
     from the first two letters of oem_number (HO->Honda, CH->Chery, PO->Polaris, RE->Renault, MB/MD/MN/MR->Mitsubishi, NF/NV/NI->Nissan,
     JM->JAC, XP, YQ, FQ, IL->Hyundai) for every active part, so ~24,900 parts of other brands carry a wrong TEXT while their id (set by the
     importer) is right. A part is restored (manufacturer text := the stored id's brand name) only when the current text IS exactly what that
     prefix rule produces AND the stored id's brand is supported by an independent signal (SKU prefix, specifications.importer, or
     compatible_vehicles manufacturer). Its part_vehicle_fitment rows carrying the old wrong text with the stored id get the brand name too
     (skipped when that would collide with an existing fitment key). updated_at is bumped (Meilisearch indexes the text).
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
  WHERE pc.is_active AND pc.manufacturer_id IS NOT NULL AND pc.manufacturer_id <> tgt.id AND pc.manufacturer <> ALL($1::text[])),
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


PREFIX_RULE = {"NI": "Nissan", "NF": "Nissan", "NV": "Nissan", "CH": "Chery", "XP": "Xpeng", "HO": "Honda", "JM": "JAC", "PO": "Polaris",
               "MR": "Mitsubishi", "MN": "Mitsubishi", "MD": "Mitsubishi", "MB": "Mitsubishi", "RE": "Renault", "YQ": "Citroen",
               "FQ": "Jaecoo", "IL": "Hyundai"}      # the REMOVED db_update_agent rule, kept here only to recognise its damage

TEXT_CANDIDATES = """
WITH cbk AS (SELECT id, name, regexp_replace(lower(name),'[^a-z0-9]','','g') k FROM car_brands),
pm AS (SELECT * FROM unnest($1::text[], $2::text[]) AS t(op, br)),
c AS (
  SELECT pc.id, pc.manufacturer old_text, cur.id cur_id, cur.name cur_name, cur.k ck, lower(split_part(pc.sku,'-',1)) pref,
         pc.specifications->>'importer' imp, pc.compatible_vehicles cvj
  FROM parts_catalog pc JOIN pm ON pm.op = upper(left(pc.oem_number,2)) AND pm.br = pc.manufacturer
  JOIN cbk cur ON cur.id = pc.manufacturer_id
  WHERE pc.manufacturer_id IS NOT NULL AND cur.k <> regexp_replace(lower(pc.manufacturer),'[^a-z0-9]','','g')
    AND pc.manufacturer <> ALL($3::text[]))
SELECT id, old_text, cur_id, cur_name FROM c
WHERE (length(pref)>=3 AND (left(ck,length(pref))=pref OR left(pref,length(ck))=ck))
   OR (imp IS NOT NULL AND imp ILIKE '%'||cur_name||'%')
   OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(cvj)='array' THEN cvj ELSE '[]'::jsonb END) cv
              WHERE regexp_replace(lower(cv->>'manufacturer'),'[^a-z0-9]','','g') = ck)"""


async def restore_text(c, apply):
    ops = list(PREFIX_RULE); brs = [PREFIX_RULE[o] for o in ops]
    rows = await c.fetch(TEXT_CANDIDATES, ops, brs, list(ALIAS_TEXTS))
    print(("APPLY" if apply else "DRY RUN"), f"- restore-text: {len(rows)} parts whose text was rewritten by the OEM-prefix rule and whose stored brand is independently supported")
    grp = {}
    for r in rows:
        grp[(r["old_text"], r["cur_name"])] = grp.get((r["old_text"], r["cur_name"]), 0) + 1
    for (o, n), k in sorted(grp.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {o!r:12} -> {n!r:14} {k}")
    if not apply:
        return
    run_id = f"brand150t-{uuid.uuid4().hex[:8]}"
    await c.execute(LOG_DDL)
    done = pvf = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        async with c.transaction():
            ids = [r["id"] for r in chunk]; olds = [r["old_text"] for r in chunk]; news = [r["cur_name"] for r in chunk]; curs = [r["cur_id"] for r in chunk]
            locked = {r["id"] for r in await c.fetch("SELECT id FROM parts_catalog WHERE id = ANY($1::uuid[]) FOR UPDATE SKIP LOCKED", ids)}
            sel = [k for k, pid in enumerate(ids) if pid in locked]
            ids = [ids[k] for k in sel]; olds = [olds[k] for k in sel]; news = [news[k] for k in sel]; curs = [curs[k] for k in sel]
            await c.execute("""INSERT INTO brand_id_repair_log (run_id, table_name, row_id, prev_manufacturer_id, new_manufacturer_id, manufacturer_text, evidence)
                               SELECT $1,'parts_catalog.manufacturer',x.id,x.cur,x.cur,x.old,'text rewritten by OEM-prefix rule; stored brand supported -> '||x.new
                               FROM unnest($2::uuid[],$3::uuid[],$4::text[],$5::text[]) AS x(id,cur,old,new) ON CONFLICT DO NOTHING""", run_id, ids, curs, olds, news)
            res = await c.execute("""UPDATE parts_catalog pc SET manufacturer = x.new, updated_at = NOW()
                                     FROM unnest($1::uuid[],$2::text[],$3::text[]) AS x(id,old,new) WHERE pc.id = x.id AND pc.manufacturer = x.old""", ids, olds, news)
            done += int(res.split()[-1])
            r2 = await c.execute("""UPDATE part_vehicle_fitment f SET manufacturer = x.new
                                    FROM unnest($1::uuid[],$2::text[],$3::text[],$4::uuid[]) AS x(id,old,new,cur)
                                    WHERE f.part_id = x.id AND f.manufacturer = x.old AND f.manufacturer_id = x.cur
                                      AND NOT EXISTS (SELECT 1 FROM part_vehicle_fitment g WHERE g.part_id = f.part_id AND g.manufacturer = x.new
                                                      AND g.model IS NOT DISTINCT FROM f.model AND g.year_from IS NOT DISTINCT FROM f.year_from)""", ids, olds, news, curs)
            pvf += int(r2.split()[-1])
        if (i // BATCH) % 5 == 0:
            print(f"  batch {i // BATCH + 1}: parts={done} fitment={pvf}", flush=True)
        await asyncio.sleep(0.2)
    print(f"DONE run_id={run_id} parts_catalog.manufacturer={done} part_vehicle_fitment.manufacturer={pvf}")


async def resolve_rest(c, apply):
    """Third pass (owner: "why left, we are in debug mode"). For the parts whose text and id still disagree, find the TRUE brand from signals
    that do not depend on the (corrupted) text or the (possibly wrong) id:
      * compatible_vehicles manufacturer (written by the car-parts.ie importer from the vehicle page) resolved through car_brand_resolver
        (vw -> Volkswagen ...), and the SKU prefix token resolved the same way;
      * 'fix'    both agree (or the SKU prefix alone, >= 4 letters, which the importer built from the brand text AT IMPORT) -> id AND text := that brand;
      * 'polaris' PO-prefix family: text Polaris in the 2026-05-31 dump and in 25 other PO parts that carry the Polaris id, nothing for Renault;
      * 'isolate' nothing independent: is_active = FALSE (hidden from search/compatibility, nothing deleted), specifications.brand_unresolved keeps the
        original text and id. --rollback restores all three kinds.
    Fitment rows of fixed parts that carry the old wrong id and whose own text names the new brand get the new id."""
    import json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import car_brand_resolver as cbr
    brands, aliases = await cbr._load_maps(c)
    polaris = await c.fetchrow("SELECT id::text id, name FROM car_brands WHERE name = 'Polaris' AND is_active")
    rows = await c.fetch("""WITH cbk AS (SELECT id, regexp_replace(lower(name),'[^a-z0-9]','','g') k FROM car_brands WHERE is_active)
        SELECT pc.id, pc.sku, pc.manufacturer m, pc.manufacturer_id::text cur, cur.name curname, pc.compatible_vehicles cv, upper(left(pc.oem_number,2)) op
        FROM parts_catalog pc JOIN cbk tgt ON tgt.k = regexp_replace(lower(pc.manufacturer),'[^a-z0-9]','','g')
        LEFT JOIN car_brands cur ON cur.id = pc.manufacturer_id
        WHERE pc.is_active AND pc.manufacturer_id IS NOT NULL AND pc.manufacturer_id <> tgt.id AND pc.manufacturer <> ALL($1::text[])""", list(ALIAS_TEXTS))
    fix, isolate = [], []
    def pick(key):
        pl = cbr.plan_resolution(key, brands, aliases, False)
        return (pl["id"], pl["name"]) if pl["action"] in ("existing", "alias") else None
    for r in rows:
        cv = r["cv"]; cv = json.loads(cv) if isinstance(cv, str) else cv
        cvb = None
        if isinstance(cv, list) and cv:
            names = {cbr.brand_key(x.get("manufacturer")) for x in cv if isinstance(x, dict) and x.get("manufacturer")}
            if len(names) == 1:
                cvb = pick(next(iter(names)))
        pref = (r["sku"] or "").split("-")[0]
        skub = pick(cbr.brand_key(pref)) if len(pref) >= 4 else None
        tgt, ev = None, None
        if cvb and skub and cvb[0] == skub[0]:
            tgt, ev = cvb, "compatible_vehicles + sku prefix agree"
        elif cvb and cvb[0] == r["cur"]:
            tgt, ev = cvb, "compatible_vehicles names the stored brand (text was overwritten)"
        elif skub and not cvb:
            tgt, ev = skub, "sku prefix (built from the brand at import)"
        elif r["m"] == "Polaris" and r["op"] == "PO" and polaris and not cvb and not skub:
            tgt, ev = (polaris["id"], polaris["name"]), "PO-prefix Polaris family (text in 2026-05-31 dump, 25 PO parts on the Polaris id)"
        if tgt:
            fix.append((r, tgt, ev))
        else:
            isolate.append(r)
    print(("APPLY" if apply else "DRY RUN"), f"- resolve-rest: fix {len(fix)} (id+text), isolate {len(isolate)} (is_active=false)")
    grp = {}
    for r, t, ev in fix:
        grp[(r["m"], r["curname"], t[1], ev)] = grp.get((r["m"], r["curname"], t[1], ev), 0) + 1
    for k, v in sorted(grp.items(), key=lambda kv: -kv[1])[:12]:
        print("  fix", v, k)
    if not apply:
        return
    run_id = f"brand150r-{uuid.uuid4().hex[:8]}"
    await c.execute(LOG_DDL)
    async with c.transaction():
        for r, t, ev in fix:
            await c.execute("""INSERT INTO brand_id_repair_log (run_id, table_name, row_id, prev_manufacturer_id, new_manufacturer_id, manufacturer_text, evidence)
                               VALUES ($1,'parts_catalog.brand',$2,$3::uuid,$4::uuid,$5,$6) ON CONFLICT DO NOTHING""", run_id, r["id"], r["cur"], t[0], r["m"], ev)
            await c.execute("UPDATE parts_catalog SET manufacturer_id = $2::uuid, manufacturer = $3, updated_at = NOW() WHERE id = $1 AND manufacturer_id = $4::uuid",
                            r["id"], t[0], t[1], r["cur"])
            await c.execute("""UPDATE part_vehicle_fitment f SET manufacturer_id = $2::uuid WHERE f.part_id = $1 AND f.manufacturer_id = $3::uuid
                               AND regexp_replace(lower(f.manufacturer),'[^a-z0-9]','','g') = regexp_replace(lower($4),'[^a-z0-9]','','g')""", r["id"], t[0], r["cur"], t[1])
        for r in isolate:
            await c.execute("""INSERT INTO brand_id_repair_log (run_id, table_name, row_id, prev_manufacturer_id, new_manufacturer_id, manufacturer_text, evidence)
                               VALUES ($1,'parts_catalog.isolate',$2,$3::uuid,$3::uuid,$4,'no independent evidence of the brand: text and id disagree; hidden, not deleted') ON CONFLICT DO NOTHING""", run_id, r["id"], r["cur"], r["m"])
            await c.execute("""UPDATE parts_catalog SET is_active = FALSE, updated_at = NOW(),
                               specifications = COALESCE(specifications,'{}'::jsonb) || jsonb_build_object('brand_unresolved', jsonb_build_object('run', $2::text, 'text', $3::text, 'id', $4::text))
                               WHERE id = $1""", r["id"], run_id, r["m"], r["cur"])
    print("DONE", run_id)


async def prune_fitment(c, apply):
    """Fitment rows that name MAZDA (stored with Jaguar's id) on parts that are provably JAGUAR parts (text, id and SKU prefix all Jaguar):
    a Jaguar oil/lock-nut/windscreen part 'fitting a Mazda 6 / BT-50 / MX-6' is a false compatibility claim left by the old
    mazda->Jaguar id dictionary. Archived in pvf_removed_150 (full row as jsonb, run id) and removed; --rollback re-inserts them."""
    await c.execute("""CREATE TABLE IF NOT EXISTS pvf_removed_150 (run_id text NOT NULL, row_id uuid NOT NULL, row_json jsonb NOT NULL,
                       removed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (run_id, row_id))""")
    where = """f.manufacturer = 'Mazda' AND f.manufacturer_id = (SELECT id FROM car_brands WHERE name = 'Jaguar')
               AND EXISTS (SELECT 1 FROM parts_catalog pc WHERE pc.id = f.part_id AND pc.manufacturer = 'Jaguar'
                           AND pc.manufacturer_id = f.manufacturer_id AND upper(pc.sku) ~ '^(JAG|JAGUA)')"""
    n = await c.fetchval(f"SELECT count(*) FROM part_vehicle_fitment f WHERE {where}")
    print(("APPLY" if apply else "DRY RUN"), f"- prune-fitment: {n} Mazda-fitment rows on provably Jaguar parts")
    if not apply:
        return
    run_id = f"brand150f-{uuid.uuid4().hex[:8]}"
    async with c.transaction():
        await c.execute(f"INSERT INTO pvf_removed_150 (run_id, row_id, row_json) SELECT $1, f.id, to_jsonb(f.*) FROM part_vehicle_fitment f WHERE {where}", run_id)
        r = await c.execute("DELETE FROM part_vehicle_fitment WHERE id IN (SELECT row_id FROM pvf_removed_150 WHERE run_id = $1)", run_id)
    print("DONE", run_id, r, "rollback: INSERT INTO part_vehicle_fitment SELECT (jsonb_populate_record(NULL::part_vehicle_fitment, row_json)).* FROM pvf_removed_150 WHERE run_id = '%s'" % run_id)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", metavar="RUN_ID")
    ap.add_argument("--restore-text", action="store_true")
    ap.add_argument("--resolve-rest", action="store_true")
    ap.add_argument("--prune-fitment", action="store_true")
    a = ap.parse_args()
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute("SET lock_timeout = '3s'")
    if a.rollback:
        await c.execute(LOG_DDL)
        r = await c.execute("""UPDATE parts_catalog pc SET manufacturer = l.manufacturer_text, updated_at = NOW() FROM brand_id_repair_log l
                               WHERE l.run_id=$1 AND l.table_name='parts_catalog.manufacturer' AND l.row_id=pc.id""", a.rollback)
        print("rollback text", r)
        r = await c.execute("""UPDATE parts_catalog pc SET manufacturer_id = l.prev_manufacturer_id, manufacturer = l.manufacturer_text, updated_at = NOW() FROM brand_id_repair_log l
                               WHERE l.run_id=$1 AND l.table_name='parts_catalog.brand' AND l.row_id=pc.id""", a.rollback)
        print("rollback brand", r)
        r = await c.execute("""UPDATE parts_catalog pc SET is_active = TRUE, specifications = pc.specifications - 'brand_unresolved', updated_at = NOW() FROM brand_id_repair_log l
                               WHERE l.run_id=$1 AND l.table_name='parts_catalog.isolate' AND l.row_id=pc.id""", a.rollback)
        print("rollback isolate", r)
        for table in ("parts_catalog", "part_vehicle_fitment"):
            r = await c.execute(f"""UPDATE {table} t SET manufacturer_id = l.prev_manufacturer_id FROM brand_id_repair_log l
                                    WHERE l.run_id=$1 AND l.table_name='{table}' AND l.row_id=t.id AND t.manufacturer_id=l.new_manufacturer_id""",
                                a.rollback)
            print("rollback", table, r)
        return

    if a.restore_text:
        await restore_text(c, a.apply)
        return
    if a.resolve_rest:
        await resolve_rest(c, a.apply)
        return
    if a.prune_fitment:
        await prune_fitment(c, a.apply)
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
