"""
Script: remove_placeholder_offers_149.py
Purpose: (#149, owner decision 2026-11-10: "if it is a placeholder with no real source and no real data, remove it - only true values")
         AutoParts Pro IL is a seed sentinel (no website, no credentials, no business found). Its 115,116 offers were bulk-created by
         import_from_excel with a price fallback chain (workbook price -> catalog base_price -> literal 100.0); none was ever updated by
         a supplier, none has a price_history row, and the supplier's VAT basis is unknowable. Nothing about them is a verified
         supplier fact, so they are REMOVED - after being archived in full.
Process: dry run by default (counts per reason). --apply: per batch (SKIP LOCKED), copy the rows into supplier_parts_removed_149
         (full row as jsonb + reason + run id), then delete them. PROTECTED (never removed): offers referenced by an order/cart item
         (checked against the PII database) and offers that carry a real supplier_url. --restore RUN_ID re-inserts the archived rows.
         Reasons: placeholder_100 (price exactly 100.00) | catalog_derived (price equals the part's own base/importer/max price, i.e. the
         fallback or a copy of it) | unsourced_workbook (any other price: no source file, no URL, no sync, unverifiable VAT).
Policy: OFFER_DEDUPLICATION_POLICY gate is called (fail closed when the policy is unavailable). This is an explicit OWNER override of
         the policy's "never hide/deactivate unresolved offers" for provably fabricated, never-synced placeholder rows; rows are archived,
         not lost. Recorded in FIXES_TRACKER #149.
Data Imported/Modified: supplier_parts (DELETE), supplier_parts_removed_149 (new archive table).
Last Updated: 2026-11-10
"""
import argparse
import asyncio
import os
import sys
import uuid

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import offer_policy  # noqa: E402

SUPPLIER = "AutoParts Pro IL"
BATCH = 2000
ARCHIVE_DDL = """CREATE TABLE IF NOT EXISTS supplier_parts_removed_149 (
  run_id text NOT NULL, reason text NOT NULL, supplier_part_id uuid NOT NULL, row_json jsonb NOT NULL,
  removed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (run_id, supplier_part_id))"""

REASON_SQL = """CASE WHEN sp.price_ils = 100 THEN 'placeholder_100'
       WHEN abs(sp.price_ils - pc.base_price) < 0.015 OR abs(sp.price_ils - pc.importer_price_ils) < 0.015
            OR abs(sp.price_ils - pc.max_price_ils) < 0.015 THEN 'catalog_derived'
       ELSE 'unsourced_workbook' END"""


async def protected_ids() -> list:
    """Offers referenced by orders / carts (PII database) - history must be preserved."""
    url = os.environ.get("DATABASE_PII_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    pii = await asyncpg.connect(url)
    try:
        ids = set()
        for t in ("order_items", "cart_items"):
            ids |= {r[0] for r in await pii.fetch(f"SELECT DISTINCT supplier_part_id FROM {t} WHERE supplier_part_id IS NOT NULL")}
        return list(ids)
    finally:
        await pii.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", metavar="RUN_ID")
    a = ap.parse_args()
    offer_policy.gate_or_exit("maintenance/remove_placeholder_offers_149.py", not a.apply and not a.restore)
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute("SET lock_timeout = '3s'")
    if a.restore:
        await c.execute(ARCHIVE_DDL)
        r = await c.execute("""INSERT INTO supplier_parts SELECT (jsonb_populate_record(NULL::supplier_parts, row_json)).*
                               FROM supplier_parts_removed_149 WHERE run_id = $1 ON CONFLICT DO NOTHING""", a.restore)
        print("restore:", r)
        return
    sid = await c.fetchval("SELECT id FROM suppliers WHERE name = $1", SUPPLIER)
    prot = await protected_ids()
    where = ("sp.supplier_id = $1 AND NULLIF(BTRIM(sp.supplier_url),'') IS NULL AND NOT (sp.id = ANY($2::uuid[]))")
    rows = await c.fetch(f"SELECT {REASON_SQL} reason, count(*) n, count(*) FILTER (WHERE sp.is_available) avail "
                         f"FROM supplier_parts sp JOIN parts_catalog pc ON pc.id = sp.part_id WHERE {where} GROUP BY 1 ORDER BY 2 DESC", sid, prot)
    total = await c.fetchval("SELECT count(*) FROM supplier_parts WHERE supplier_id = $1", sid)
    print(("APPLY" if a.apply else "DRY RUN"), f"- {SUPPLIER}: {total} offers; protected (orders/carts) {len(prot)}; will remove:")
    for r in rows:
        print(f"  {r['reason']:20} {r['n']:7}  (available {r['avail']})")
    if not a.apply:
        return
    run_id = f"offers149-{uuid.uuid4().hex[:8]}"
    await c.execute(ARCHIVE_DDL)
    removed = 0
    while True:
        async with c.transaction():
            got = await c.fetch(f"""SELECT sp.id, {REASON_SQL} reason FROM supplier_parts sp JOIN parts_catalog pc ON pc.id = sp.part_id
                                    WHERE {where} AND NOT EXISTS (SELECT 1 FROM price_history ph WHERE ph.supplier_part_id = sp.id)
                                    ORDER BY sp.id LIMIT {BATCH} FOR UPDATE OF sp SKIP LOCKED""", sid, prot)
            if not got:
                break
            ids = [r["id"] for r in got]; reasons = [r["reason"] for r in got]
            await c.execute("""INSERT INTO supplier_parts_removed_149 (run_id, reason, supplier_part_id, row_json)
                               SELECT $1, x.reason, sp.id, to_jsonb(sp.*) FROM supplier_parts sp JOIN unnest($2::uuid[], $3::text[]) AS x(id, reason) ON x.id = sp.id
                               ON CONFLICT DO NOTHING""", run_id, ids, reasons)
            res = await c.execute("DELETE FROM supplier_parts WHERE id = ANY($1::uuid[]) AND id IN (SELECT supplier_part_id FROM supplier_parts_removed_149 WHERE run_id = $2)", ids, run_id)
            removed += int(res.split()[-1])
        if (removed // BATCH) % 5 == 0:
            print(f"  removed {removed}", flush=True)
        await asyncio.sleep(0.2)
    print(f"DONE run_id={run_id} removed={removed}; remaining {SUPPLIER} offers:", await c.fetchval("SELECT count(*) FROM supplier_parts WHERE supplier_id = $1", sid))
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
