"""
Script: repair_inclusive_reference_131.py
Purpose: (#131) repair parts_catalog.max_price_ils / min_price_ils that were built from a VAT-INCLUSIVE supplier's stored cost
         x1.18 (VAT twice in the Israeli consumer reference). Root cause: db_update_agent.refresh_min_max_for_parts (and the
         cleanup agent's cost-column fill) ignored suppliers.vat_price_basis; both fixed in source (#131).
Process: DRY RUN by default (SELECT only): counts parts that have a VAT-inclusive offer and whose stored max/min equals the old
         wrong formula (offer x1.18, +-0.02). --apply [--limit N]: batches of 1000 parts, each in its own transaction: previous
         values are logged in repair_inclusive_reference_131_log, then the ONE implementation
         (db_update_agent.refresh_min_max_for_parts) recomputes the part. Idempotent: a repaired part no longer matches.
         --rollback restores the logged previous values where the part still holds the repaired value.
         Production execution needs the owner's explicit authorization - this script is not scheduled or imported.
Data Imported/Modified: parts_catalog.min_price_ils / max_price_ils (+ the log table) - only with --apply / --rollback.
Data Sources: supplier_parts, suppliers.vat_price_basis, parts_catalog.
Missing Data Delegation: parts whose stored values do not match the old formula are never touched.
Last Updated: 2026-11-06
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CAND = """SELECT pc.id FROM parts_catalog pc JOIN (
            SELECT sp.part_id, MAX(sp.price_ils) mx, MIN(sp.price_ils) mn FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
            WHERE s.vat_price_basis = 'inclusive' AND sp.price_ils > 0 AND sp.is_available GROUP BY sp.part_id) i ON i.part_id = pc.id
         WHERE (ABS(pc.max_price_ils - i.mx * 1.18) < 0.02 OR ABS(pc.min_price_ils - i.mn * 1.18) < 0.02)
"""
LOG_DDL = """CREATE TABLE IF NOT EXISTS repair_inclusive_reference_131_log (part_id uuid PRIMARY KEY, prev_min numeric, prev_max numeric,
             new_min numeric, new_max numeric, repaired_at timestamptz NOT NULL DEFAULT now())"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute("SET statement_timeout = '280s'")
    cand = CAND
    if a.rollback:
        print("rollback:", await c.execute("""UPDATE parts_catalog pc SET min_price_ils = l.prev_min, max_price_ils = l.prev_max, updated_at = NOW()
            FROM repair_inclusive_reference_131_log l WHERE l.part_id = pc.id AND pc.max_price_ils IS NOT DISTINCT FROM l.new_max
            AND pc.min_price_ils IS NOT DISTINCT FROM l.new_min"""))
        return
    n = await c.fetchval(f"SELECT count(*) FROM ({cand}) x")
    print("DRY RUN" if not a.apply else "APPLY", "- candidate parts (inclusive offer, stored value == offer x1.18):", n)
    for r in await c.fetch(f"""SELECT pc.oem_number, pc.min_price_ils, pc.max_price_ils FROM parts_catalog pc WHERE pc.id IN ({cand} LIMIT 5)"""):
        print("   sample before:", dict(r))
    if not a.apply:
        print("nothing written"); return
    import db_update_agent as dua
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory
    await c.execute(LOG_DDL)
    done = 0
    while not a.limit or done < a.limit:
        ids = [str(r["id"]) for r in await c.fetch(f"{cand} AND pc.id NOT IN (SELECT part_id FROM repair_inclusive_reference_131_log) LIMIT 1000")]
        if not ids:
            break
        await c.execute("""INSERT INTO repair_inclusive_reference_131_log (part_id, prev_min, prev_max)
            SELECT id, min_price_ils, max_price_ils FROM parts_catalog WHERE id = ANY($1::uuid[]) ON CONFLICT DO NOTHING""", ids)
        async with async_session_factory() as db:
            await dua.refresh_min_max_for_parts(db, ids, float(os.environ.get("USD_TO_ILS", "3.16")))
            await db.commit()
        await c.execute("""UPDATE repair_inclusive_reference_131_log l SET new_min = pc.min_price_ils, new_max = pc.max_price_ils
            FROM parts_catalog pc WHERE pc.id = l.part_id AND l.part_id = ANY($1::uuid[])""", ids)
        done += len(ids)
        await asyncio.sleep(0.5)
    print("repaired parts:", done)
    await c.close()


asyncio.run(main())
