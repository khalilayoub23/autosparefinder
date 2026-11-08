"""
Script: repair_double_margin_129.py
Purpose: (#129) repair Israeli-importer offers whose supplier_parts.price_ils holds the MARGIN price instead of the
         ex-VAT cost, so the central price policy (cost x1.45 + 18% VAT) applied the margin twice.
Root cause: importers/samelet_import_v2.py and importers/import_champion_motors.py wrote parts_catalog.base_price
         (cost x1.45) into supplier_parts.price_ils (both fixed in the same change to write importer_price_ils).
Process: an offer is repaired ONLY when all of these hold:
           base_price == 1.45 x importer_price_ils (+-0.03)   (the catalog row carries the importer's own cost)
           rule A: price_ils / importer_price_ils in [1.40, 1.49]   (margin price, incl. drift from an older price list)
           rule B (Lexus, Subaru-Samelet only): ratio in [1.68, 1.74]  (margin price x1.18)
         -> price_ils := importer_price_ils (the ex-VAT cost). Verified 2026-11-05 against the live Samelet API: for
         Subaru 30098AA280 the source is 515.6 ex-VAT / 608.41 incl.; the catalog importer cost is the ex-VAT cost and
         the offer held cost x1.45. (max_price_ils is NOT evidence: healers derive it from price_ils.)
         Dry run by default. --apply [--limit N]: batches of 2000, FOR UPDATE SKIP LOCKED, each change logged in
         supplier_price_repairs_129 (prev/new/rule). Idempotent (a repaired row no longer matches).
         --rollback restores prev where the row still holds the repaired value.
Data Imported/Modified: supplier_parts.price_ils (+ supplier_price_repairs_129 log table, created by --apply).
Data Sources: parts_catalog.importer_price_ils / base_price.
Missing Data Delegation: rows that do not match are only counted per supplier, never guessed.
Last Updated: 2026-11-05
"""
import argparse
import asyncio
import os

import asyncpg

SUPPLIERS = (
    "Abarth Official Importer Israel", "Alfa Romeo Official Importer Israel", "BYD IL (Shlomo Motors)",
    "Champion Motors", "Fiat Official Importer Israel", "Jeep Official Importer Israel",
    "RAM Official Importer Israel", "Subaru Official Importer Israel", "WEY Official Importer Israel",
    "Subaru Israel - Samelet Motors", "Lexus Official Importer Israel",
)
RULE_B_SUPPLIERS = ("Subaru Israel - Samelet Motors", "Lexus Official Importer Israel")
# $1 = supplier id, $2 = uuid[] of the rule-B suppliers
BASE_OK = "pc.importer_price_ils > 0 AND ABS(pc.base_price - 1.45 * pc.importer_price_ils) <= 0.03"
RULE_SQL = (f"(CASE WHEN {BASE_OK} AND sp.price_ils / NULLIF(pc.importer_price_ils, 0) BETWEEN 1.40 AND 1.49 THEN 'A' "
            f"WHEN {BASE_OK} AND sp.price_ils / NULLIF(pc.importer_price_ils, 0) BETWEEN 1.68 AND 1.74 "
            f"AND sp.supplier_id = ANY($2::uuid[]) THEN 'B' END)")
LOG_DDL = """CREATE TABLE IF NOT EXISTS supplier_price_repairs_129 (
  supplier_part_id uuid PRIMARY KEY, prev_price_ils numeric NOT NULL, new_price_ils numeric NOT NULL,
  rule text NOT NULL, repaired_at timestamptz NOT NULL DEFAULT now())"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="max rows to repair this run (0 = all)")
    a = ap.parse_args()
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute("SET statement_timeout = '120s'")
    if a.rollback:
        print("rollback:", await c.execute(
            "UPDATE supplier_parts sp SET price_ils = l.prev_price_ils, updated_at = NOW() "
            "FROM supplier_price_repairs_129 l WHERE l.supplier_part_id = sp.id AND sp.price_ils = l.new_price_ils"))
        return
    ids = {r["name"]: r["id"] for r in await c.fetch("SELECT id, name FROM suppliers WHERE name = ANY($1)", list(SUPPLIERS))}
    b_ids = [ids[n] for n in RULE_B_SUPPLIERS]
    print(f"{'supplier':38} {'priced':>7} {'ruleA':>7} {'ruleB':>7} {'unmatched':>9}")
    tot = {"A": 0, "B": 0}
    for name in SUPPLIERS:
        r = await c.fetchrow(f"""SELECT count(*) n, count(*) FILTER (WHERE rule='A') a, count(*) FILTER (WHERE rule='B') b FROM (
            SELECT {RULE_SQL} rule FROM supplier_parts sp JOIN parts_catalog pc ON pc.id = sp.part_id
            WHERE sp.supplier_id = $1 AND sp.price_ils > 0) x""", ids[name], b_ids)
        tot["A"] += r["a"]; tot["B"] += r["b"]
        print(f"{name[:38]:38} {r['n']:7} {r['a']:7} {r['b']:7} {r['n'] - r['a'] - r['b']:9}")
    print("TOTAL eligible:", tot)
    if not a.apply:
        print("dry run - nothing written"); return
    await c.execute(LOG_DDL)
    done = 0
    for name in SUPPLIERS:
        while not a.limit or done < a.limit:
            take = 2000 if not a.limit else min(2000, a.limit - done)
            n = await c.fetchval(f"""WITH t AS (
                SELECT sp.id, sp.price_ils prev, pc.importer_price_ils new, {RULE_SQL} rule
                FROM supplier_parts sp JOIN parts_catalog pc ON pc.id = sp.part_id
                WHERE sp.supplier_id = $1 AND sp.price_ils > 0 AND {RULE_SQL} IS NOT NULL
                LIMIT $3 FOR UPDATE OF sp SKIP LOCKED),
              l AS (INSERT INTO supplier_price_repairs_129 (supplier_part_id, prev_price_ils, new_price_ils, rule)
                    SELECT id, prev, new, rule FROM t ON CONFLICT (supplier_part_id) DO NOTHING RETURNING 1),
              u AS (UPDATE supplier_parts sp SET price_ils = t.new, updated_at = NOW() FROM t WHERE sp.id = t.id RETURNING 1)
              SELECT count(*) FROM u""", ids[name], b_ids, take)
            done += n
            if not n:
                break
            await asyncio.sleep(0.3)
        print(f"  {name}: cumulative repaired {done}")
    print("repaired:", done)
    await c.close()


asyncio.run(main())
