"""
Script: quarantine_motorstore_offers_129.py
Purpose: (#129) Motorstore IL's stored offers are NOT Motorstore prices: 2,321 of 2,325 carry one bulk stamp (2026-07-28 14:39 /
         18:02) and were derived from other price lists (price == catalog max_price_ils / 1.18 for most, importer cost for others);
         the other 4 are junk (e.g. 11.80 on a part whose importer cost is 9,551.90). They were listed to customers on the
         compare endpoint (no supplier_url hid them only from search). Policy: never show a fabricated supplier price.
Process: dry run by default. --apply: every Motorstore IL offer with price_ils > 0 AND is_available -> is_available = FALSE
         (rows and prices are kept, nothing deleted), previous state logged in motorstore_offer_quarantine_log.
         --rollback restores is_available from the log. Real prices must come from scrape_motorstore (catalog_scraper) - when a
         real price is written the offer is made available again by that path.
Data Imported/Modified: supplier_parts.is_available (+ log table).  Data Sources: supplier_parts, suppliers.
Missing Data Delegation: re-scrape real Motorstore prices (owner-approved follow-up).
Last Updated: 2026-11-06
"""
import argparse
import asyncio
import os

import asyncpg

LOG_DDL = """CREATE TABLE IF NOT EXISTS motorstore_offer_quarantine_log (
  supplier_part_id uuid PRIMARY KEY, prev_is_available boolean NOT NULL, price_ils numeric, reason text NOT NULL,
  quarantined_at timestamptz NOT NULL DEFAULT now())"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import offer_policy
    # Policy 1.0.0: hiding an offer is only permitted for a proven TRUE_DUPLICATE with another orderable offer - this
    # bulk quarantine is NOT, so a real run is refused (dry run only reports).
    offer_policy.gate_or_exit("maintenance/quarantine_motorstore_offers_129.py", not a.apply)
    if a.apply:
        raise SystemExit("refused by OFFER_DEDUPLICATION_POLICY: bulk deactivation is not a permitted action")
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    sid = await c.fetchval("SELECT id FROM suppliers WHERE name = 'Motorstore IL'")
    if a.rollback:
        print("rollback:", await c.execute("""UPDATE supplier_parts sp SET is_available = l.prev_is_available, updated_at = NOW()
            FROM motorstore_offer_quarantine_log l WHERE l.supplier_part_id = sp.id AND sp.is_available = FALSE"""))
        return
    n = await c.fetchval("SELECT count(*) FROM supplier_parts WHERE supplier_id = $1 AND price_ils > 0 AND is_available", sid)
    print(("APPLY" if a.apply else "DRY RUN"), "- available Motorstore IL offers to quarantine:", n)
    if not a.apply:
        return
    await c.execute(LOG_DDL)
    async with c.transaction():
        ids = [r["id"] for r in await c.fetch("""SELECT id FROM supplier_parts WHERE supplier_id = $1 AND price_ils > 0 AND is_available
                                                  FOR UPDATE SKIP LOCKED""", sid)]
        await c.execute("""INSERT INTO motorstore_offer_quarantine_log (supplier_part_id, prev_is_available, price_ils, reason)
            SELECT id, is_available, price_ils, 'derived from catalog price lists, not a Motorstore price (#129)' FROM supplier_parts
            WHERE id = ANY($1::uuid[]) ON CONFLICT DO NOTHING""", ids)
        await c.execute("UPDATE supplier_parts SET is_available = FALSE, updated_at = NOW() WHERE id = ANY($1::uuid[])", ids)
    print("quarantined:", len(ids))
    await c.close()


asyncio.run(main())
