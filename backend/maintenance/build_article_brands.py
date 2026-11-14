"""
Script:  maintenance/build_article_brands.py
Purpose: Build backend/data/article_brands.json - the vocabulary of KNOWN part brands that part_identity uses as brand
         evidence. A brand is a short text that many catalogue titles carry directly in front of the row's own part
         number ('TOPRAN 118 702 Gear selector cable' -> TOPRAN).
Process: 1. READ-ONLY scan of parts_catalog: for every row whose name contains its own number, take the text in front
            of it (first 40 characters at most) and count how many titles share it.
         2. Keep a prefix when at least --min-rows titles share it (default 30), it has at most 3 words, carries no
            bracket and is not a known non-brand (quantity prefixes such as '2X BRAKE DISC', generic words).
         3. Collect the vehicle makes and their words (car_brands, brand_aliases, parts_catalog.manufacturer) as
            `vehicle_words`: never a brand at the END of a vehicle-first title ('Brake disc for Suzuki 51234').
         4. Write the JSON file (--out, default: print only). Review the diff before committing: a wrong entry makes two
            unrelated rows look like one brand.
Data Imported/Modified: none in the database; writes the JSON file given by --out.
Data Sources: parts_catalog (name, oem_number).
Missing Data Delegation: a brand with fewer than --min-rows titles is NOT known -> its rows are UNCERTAIN in
         part_identity and are never merged automatically (reported by the weekly gate for review).
Last Updated: 2026-11-14
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
NOT_A_BRAND = {"CAR", "LTD", "MAX", "S/S TO", "TECH", "FOR", "SET", "KIT", "NEW", "GENUINE", "ORIGINAL"}
SQL = r"""
    WITH x AS (SELECT name, btrim(regexp_replace(oem_number, '^\s*article\s*(№|no\.?|#)?\s*:?\s*', '', 'i')) AS num
               FROM parts_catalog WHERE oem_number IS NOT NULL AND name IS NOT NULL),
         y AS (SELECT name, strpos(upper(name), upper(num)) AS pos FROM x WHERE num <> '')
    SELECT upper(btrim(regexp_replace(left(name, pos - 1), '\s+', ' ', 'g'), ' ,-–|(')) AS pfx, count(*) AS n
    FROM y WHERE pos > 1 AND pos <= 40 GROUP BY 1 HAVING count(*) >= $1
"""


def keep(prefix: str) -> bool:
    return bool(prefix) and len(prefix.split()) <= 3 and "(" not in prefix and ")" not in prefix \
        and prefix not in NOT_A_BRAND and not re.match(r"^\d+\s*X\b", prefix) and not prefix.isdigit()


async def build(min_rows: int) -> dict:
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    try:
        await conn.execute("SET default_transaction_read_only = on")
        await conn.execute("SET statement_timeout = '600s'")
        rows = await conn.fetch(SQL, min_rows)
        # vehicle makes / model-family words: part_identity never accepts them as the brand ENDING a vehicle-first title
        makes = await conn.fetch("SELECT name FROM car_brands UNION SELECT alias FROM brand_aliases "
                                 "UNION SELECT DISTINCT manufacturer FROM parts_catalog WHERE manufacturer IS NOT NULL")
        vehicle_words = set()
        for m in makes:
            name = (m[0] or "").strip().upper()
            if name:
                vehicle_words.add(name)
                vehicle_words.update(t for t in re.split(r"[\s\-/]+", name) if len(t) >= 2)
    finally:
        await conn.close()
    brands = {r["pfx"]: int(r["n"]) for r in rows if keep(r["pfx"])}
    if not vehicle_words:
        raise SystemExit("no vehicle makes found - refusing to write a vocabulary without vehicle_words")
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "min_rows": min_rows,
            "source": "parts_catalog titles (maintenance/build_article_brands.py)",
            "brands": dict(sorted(brands.items())), "vehicle_words": sorted(vehicle_words)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-rows", type=int, default=30)
    ap.add_argument("--out", help="write the vocabulary here (omit to print the summary only)")
    a = ap.parse_args()
    doc = asyncio.run(build(a.min_rows))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=0)
    print(json.dumps({"brands": len(doc["brands"]), "min_rows": a.min_rows, "out": a.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
