"""
Script: repair_oms_foreign_max_133.py
Purpose: (#133) clear max_price_ils on parts whose value is provably a FOREIGN cost: the part has NO Israeli offer, NO importer
         cost, NO Israeli-list source label, and max_price_ils equals one of its offers' price_ils x {1, 1.05, 1.17, 1.18, 1.2}
         (the #127 foreign-writer signatures). The 274 parts audited on 2026-11-06 split 24 preserve (max == importer x1.18) /
         219 NULL / 31 unresolved (an Israeli-list label or an importer cost that does not match): ONLY the 219 are selected.
Process: DRY RUN by default (SELECT only, lists counts + samples). --apply: batches of 100, one transaction each, FOR UPDATE
         SKIP LOCKED, re-checks the predicate, writes max_price_ils = NULL and specifications.max_price_repair_127 =
         {prev, sig:'oms_foreign_133', at}. ROLLBACK: maintenance/repair_foreign_max_price_127.py --rollback (same record key)
         restores exactly these values. Needs the owner's explicit approval before --apply.
Data Imported/Modified: parts_catalog.max_price_ils (+ specifications.max_price_repair_127) - only with --apply.
Data Sources: parts_catalog, supplier_parts, suppliers.   Missing Data Delegation: unresolved parts are reported, never touched.
Last Updated: 2026-11-06
"""
import argparse, asyncio, json, os, sys
from pathlib import Path
import asyncpg
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import price_policy as pp

KEY = "max_price_repair_127"
LOCAL = pp.local_supplier_sql("s")
SIG = ("(abs(pc.max_price_ils - f.price_ils) < 0.02 OR abs(pc.max_price_ils - f.price_ils*1.05) < 0.02 OR abs(pc.max_price_ils - f.price_ils*1.17) < 0.02 "
       "OR abs(pc.max_price_ils - f.price_ils*1.18) < 0.02 OR abs(pc.max_price_ils - f.price_ils*1.2) < 0.02)")
WHERE = f"""pc.max_price_ils > 0 AND NOT (COALESCE(pc.specifications, '{{}}'::jsonb) ? '{KEY}')
  AND COALESCE(pc.importer_price_ils, 0) = 0
  AND NOT (COALESCE(pc.specifications->>'source','') ~* '(^|[^a-z])IL |importer|price list')
  AND NOT EXISTS (SELECT 1 FROM supplier_parts l JOIN suppliers s ON s.id = l.supplier_id WHERE l.part_id = pc.id AND {LOCAL})
  AND EXISTS (SELECT 1 FROM supplier_parts f WHERE f.part_id = pc.id AND f.price_ils > 0 AND {SIG})"""


async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute("SET statement_timeout = '120s'")
    n = await c.fetchval(f"SELECT count(*) FROM parts_catalog pc WHERE {WHERE}")
    lose = await c.fetchval(f"""SELECT count(*) FROM parts_catalog pc WHERE {WHERE} AND COALESCE(pc.online_price_ils,0) = 0
        AND NOT EXISTS (SELECT 1 FROM supplier_parts v JOIN suppliers s ON s.id=v.supplier_id WHERE v.part_id=pc.id AND v.price_ils>0 AND v.is_available
                        AND s.is_active AND s.name NOT IN ('Official Manufacturer Sites','Sandbox Supplier QA') AND NULLIF(BTRIM(v.supplier_url),'') IS NOT NULL)""")
    print(("APPLY" if a.apply else "DRY RUN"), "- selected parts:", n, "| left with no online price and no visible offer:", lose)
    for r in await c.fetch(f"SELECT pc.oem_number, pc.manufacturer, pc.max_price_ils, pc.online_price_ils FROM parts_catalog pc WHERE {WHERE} ORDER BY random() LIMIT 4"):
        print("   before:", dict(r), "-> max_price_ils NULL")
    if not a.apply:
        print("nothing written"); return
    done = 0
    while True:
        async with c.transaction():
            ids = [r["id"] for r in await c.fetch(f"SELECT pc.id FROM parts_catalog pc WHERE {WHERE} LIMIT 100 FOR UPDATE OF pc SKIP LOCKED")]
            if not ids:
                break
            await c.execute(f"""UPDATE parts_catalog SET specifications = jsonb_set(COALESCE(specifications,'{{}}'::jsonb), '{{{KEY}}}',
                jsonb_build_object('prev', max_price_ils, 'sig', 'oms_foreign_133', 'at', now()::text)), max_price_ils = NULL, updated_at = NOW()
                WHERE id = ANY($1::uuid[])""", ids)
        done += len(ids)
    print("cleared:", done)
asyncio.run(main())
