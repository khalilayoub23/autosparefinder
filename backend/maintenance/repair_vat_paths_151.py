"""
Script: repair_vat_paths_151.py
Purpose: (#151 / #130) Correct parts_catalog cost columns written by legacy VAT paths, ONLY where the source basis is established.
Groups (each selected by exact arithmetic evidence AND a source statement; anything else is untouched and counted):
  KGM     sku 'KGM-%'  kgm.co.il states "המחירים ללא מע\"מ" (verified 2026-11-10): the listed price (online_price_ils) IS the ex-VAT
          cost. Was importer = price/1.17|1.18. -> importer = price, min = price, max = round(price*1.18, 2), base = round(price*1.45, 2).
  FOREIGN sku 'SAAB-|RA-|EBAY-%' (UK retail / RockAuto / eBay): a foreign price carries no Israeli VAT and must never become the Israeli
          reference. Was importer = price/1.18, min = price/1.17, max = price. -> importer = NULL, max = NULL, min = price (foreign
          cost stays in online_price_ils), base = round(price*1.45, 2). Parts with ANY Israeli offer are skipped.
  CHEV    sku 'CHEV-%' (description states "IL retail (incl VAT)"): importer = price/1.18 already; only min_price_ils was price/1.17
          -> min = importer.
  CADILLAC / GMC_BUICK  GM Israel (UMI) consumer price lists ("מחיר לצרכן", fetched 2026-06-11, source gm_umi_xlsx_import): re-derived at the
          in-force 18% VAT from the exact quoted price kept in the description.
  NOT touched: selected.parts (345 rows - no web VAT statement and the quoted price is not stored).
Process: dry run by default. --apply: per batch (BATCH, FOR UPDATE SKIP LOCKED) the selection is re-checked, the previous values are
  written to vat_repair_log (jsonb) first, then the row is updated and updated_at bumped (Meilisearch indexes min/base/importer, so
  the incremental sync refreshes the document). Re-running selects nothing (idempotent: the arithmetic evidence no longer holds).
  --rollback RUN_ID restores the logged values (only where the row still equals the repaired values).
Data Imported/Modified: parts_catalog importer_price_ils, min_price_ils, max_price_ils, base_price, updated_at; vat_repair_log (new).
Last Updated: 2026-11-10
"""
import argparse
import asyncio
import json
import os
import sys
import uuid

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import price_policy as pp  # noqa: E402

BATCH = 1000
LOG_DDL = """CREATE TABLE IF NOT EXISTS vat_repair_log (
  run_id text NOT NULL, grp text NOT NULL, part_id uuid NOT NULL, prev jsonb NOT NULL, new jsonb NOT NULL,
  repaired_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (run_id, part_id))"""

NO_IL_OFFER = """NOT EXISTS (SELECT 1 FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
                 WHERE sp.part_id = pc.id AND LOWER(COALESCE(s.country,'')) IN ('il','israel'))"""

GROUPS = {
    "KGM": {
        "where": "pc.sku LIKE 'KGM-%' AND pc.online_price_ils > 0 AND (abs(pc.importer_price_ils - pc.online_price_ils/1.17) < 0.02 OR abs(pc.importer_price_ils - pc.online_price_ils/1.18) < 0.02)",
        "new": f"importer_price_ils = round(online_price_ils,2), min_price_ils = round(online_price_ils,2), max_price_ils = round(online_price_ils*(1+{pp.VAT_RATE}),2), base_price = round(online_price_ils*{pp.MARGIN},2)",
    },
    "FOREIGN": {
        "where": ("pc.sku ~ '^(SAAB|RA|EBAY)-' AND pc.online_price_ils > 0 AND abs(pc.min_price_ils - pc.online_price_ils/1.17) < 0.02 "
                  "AND abs(pc.max_price_ils - pc.online_price_ils) < 0.02 AND (abs(pc.importer_price_ils - pc.online_price_ils/1.18) < 0.02 OR abs(pc.importer_price_ils - pc.online_price_ils/1.17) < 0.02) "
                  "AND (pc.description ILIKE '%rockauto%' OR pc.description ILIKE '%ebay%' OR pc.description ILIKE '%UK retail%') AND " + NO_IL_OFFER),
        "new": f"importer_price_ils = NULL, max_price_ils = NULL, min_price_ils = round(online_price_ils,2), base_price = round(online_price_ils*{pp.MARGIN},2)",
    },
    "CHEV": {
        "where": "pc.sku LIKE 'CHEV-%' AND pc.online_price_ils > 0 AND abs(pc.min_price_ils - pc.online_price_ils/1.17) < 0.02 AND abs(pc.importer_price_ils - pc.online_price_ils/1.18) < 0.02 AND pc.description ILIKE '%incl VAT%'",
        "new": "min_price_ils = importer_price_ils",
    },
    # GM Israel (UMI) consumer price lists: description holds the quoted price "(incl. VAT): X ILS". Stored cost was X/1.17 and the
    # reference X*1.18/1.17. Israeli VAT is 18% since 2025-01-01 and the file was fetched 2026-06-11 -> cost = X/1.18, reference = X.
    "CADILLAC": {
        "where": "pc.manufacturer = 'Cadillac' AND pc.description ~ 'incl\\. VAT\\): [0-9.]+ ILS' AND abs(pc.importer_price_ils - round(substring(pc.description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric/1.17,2)) < 0.011",
        "new": "importer_price_ils = round(substring(description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric/1.18,2), min_price_ils = round(substring(description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric,2), max_price_ils = round(substring(description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric,2), base_price = round(round(substring(description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric/1.18,2)*1.45,2)",
    },
    "GMC_BUICK": {
        "where": "pc.manufacturer IN ('GMC','Buick') AND pc.description ~ 'incl\\. VAT\\): [0-9.]+ ILS' AND abs(pc.max_price_ils - round(round(substring(pc.description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric/1.17,6)*1.18,2)) < 0.011 AND abs(pc.max_price_ils - substring(pc.description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric) > 0.001",
        "new": "min_price_ils = round(substring(description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric,2), max_price_ils = round(substring(description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric,2), base_price = round(substring(description from 'incl\\. VAT\\): ([0-9.]+) ILS')::numeric,2)",
    },
}
COLS = "importer_price_ils, min_price_ils, max_price_ils, base_price"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", metavar="RUN_ID")
    a = ap.parse_args()
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute("SET lock_timeout = '3s'")
    if a.rollback:
        r = await c.execute("""UPDATE parts_catalog pc SET importer_price_ils = (l.prev->>'importer_price_ils')::numeric, min_price_ils = (l.prev->>'min_price_ils')::numeric,
                               max_price_ils = (l.prev->>'max_price_ils')::numeric, base_price = (l.prev->>'base_price')::numeric, updated_at = NOW()
                               FROM vat_repair_log l WHERE l.run_id=$1 AND l.part_id=pc.id
                               AND pc.base_price IS NOT DISTINCT FROM (l.new->>'base_price')::numeric""", a.rollback)
        print("rollback:", r)
        return
    run_id = f"vat151-{uuid.uuid4().hex[:8]}"
    for g, cfg in GROUPS.items():
        n = await c.fetchval(f"SELECT count(*) FROM parts_catalog pc WHERE {cfg['where']}")
        print(("APPLY" if a.apply else "DRY RUN"), g, "rows selected:", n)
        if not a.apply or not n:
            continue
        await c.execute(LOG_DDL)
        done = 0
        once = f"AND NOT EXISTS (SELECT 1 FROM vat_repair_log l WHERE l.run_id = '{run_id}' AND l.part_id = pc.id)"   # never twice in one run
        while True:
            async with c.transaction():
                ids = [r["id"] for r in await c.fetch(f"SELECT pc.id FROM parts_catalog pc WHERE {cfg['where']} {once} ORDER BY pc.id LIMIT {BATCH} FOR UPDATE OF pc SKIP LOCKED")]
                if not ids:
                    break
                await c.execute(f"""INSERT INTO vat_repair_log (run_id, grp, part_id, prev, new)
                    SELECT $1, $2, pc.id, to_jsonb(p.*), '{{}}'::jsonb FROM parts_catalog pc
                    CROSS JOIN LATERAL (SELECT {COLS} FROM parts_catalog x WHERE x.id = pc.id) p WHERE pc.id = ANY($3::uuid[]) ON CONFLICT DO NOTHING""", run_id, g, ids)
                await c.execute(f"UPDATE parts_catalog SET {cfg['new']}, updated_at = NOW() WHERE id = ANY($1::uuid[])", ids)
                await c.execute(f"""UPDATE vat_repair_log l SET new = to_jsonb(p.*) FROM (SELECT id, {COLS} FROM parts_catalog WHERE id = ANY($2::uuid[])) p
                                    WHERE l.run_id = $1 AND l.part_id = p.id""", run_id, ids)
                done += len(ids)
            await asyncio.sleep(0.2)
        print(f"  {g}: repaired {done}")
    if a.apply:
        print("run_id", run_id)
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
