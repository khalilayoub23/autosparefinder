#!/usr/bin/env python3
"""
Script:  maintenance/asap_msrp_quarantine.py
Purpose: ASAP Network gives MSRP/MAP, NEVER our acquisition cost (account price_level=0). The 2026-07-28 importer stored
         MSRP/1.18 as `importer_price_ils` (a LOCAL COST) and as the offer's `price_ils`, so `base_price` and the customer
         catalog ESTIMATE of ~5,000 ASAP-only parts were MSRP-derived (a BDS lift kit shown at ~12,792 ILS as if it were a
         cost + 45% + 18% VAT). Owner rule 2026-11-19/20: MSRP is not cost; never derive base_price from it.
Process: target = parts whose EVERY offer belongs to ASAP Network and whose importer_price_ils equals that offer's price_ils
         (the MSRP-derived cost proven by equality to <= 1 ILS). For each, in bounded batches, one transaction:
           - ASAP offers -> is_available = FALSE (rows, prices and history are kept; nothing is deleted),
           - parts_catalog.importer_price_ils = 0, base_price = 0, max_price_ils = 0 (MSRP-derived; no verified cost exists),
           - specifications.msrp_quarantine = {previous values, msrp_ils, at} so every row can be restored exactly.
         `--brand` limits the run. Dry run by default; `--apply` writes; `--restore` undoes from specifications.
         Offer-policy gate (offer_policy.gate_or_exit) on every write run; no offer is deleted/merged/reassigned.
Data Modified: supplier_parts.is_available, parts_catalog.importer_price_ils/base_price/specifications/updated_at.
Last Updated: 2026-11-23
"""
import argparse
import asyncio
import json
import os
import sys

import asyncpg

import offer_policy

ACTOR = "maintenance/asap_msrp_quarantine.py"
SUPPLIER = "ASAP Network"
TARGETS = """
SELECT pc.id, pc.manufacturer, pc.importer_price_ils AS imp, pc.base_price AS base, sp.price_ils AS msrp_ils
FROM parts_catalog pc
JOIN supplier_parts sp ON sp.part_id = pc.id
JOIN suppliers s ON s.id = sp.supplier_id AND s.name = $1
WHERE pc.importer_price_ils > 0
  -- 2026-11-23: the cost equals the MSRP offer price (to the rounding the importer applied, <= 1 ILS) and ASAP is the only
  -- source. Any max_price_ils on such a part is derived from the same MSRP sheet (the earlier importer wrote it; foreign
  -- sources never write it, #127), so it is cleared with the cost and kept in specifications for an exact undo.
  AND ABS(pc.importer_price_ils - sp.price_ils) <= 1
  AND NOT EXISTS (SELECT 1 FROM supplier_parts o WHERE o.part_id = pc.id AND o.supplier_id <> sp.supplier_id)
  AND NOT (COALESCE(pc.specifications, '{}'::jsonb) ? 'msrp_quarantine')  -- NULL specifications must not hide a row (NOT NULL is NULL)
  AND ($2::text IS NULL OR pc.manufacturer = $2)
"""


async def main(a) -> int:
    offer_policy.gate_or_exit(ACTOR, not (a.apply or a.restore))
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    try:
        if a.restore:
            n = await c.fetchval("""WITH r AS (
                UPDATE parts_catalog pc SET importer_price_ils = (specifications->'msrp_quarantine'->>'prev_importer')::numeric,
                       base_price = (specifications->'msrp_quarantine'->>'prev_base')::numeric,
                       max_price_ils = COALESCE((specifications->'msrp_quarantine'->>'prev_max')::numeric, max_price_ils),
                       specifications = specifications - 'msrp_quarantine', updated_at = NOW()
                WHERE specifications ? 'msrp_quarantine' AND ($1::text IS NULL OR manufacturer = $1) RETURNING id)
                SELECT count(*) FROM r""", a.brand)
            await c.execute("UPDATE supplier_parts sp SET is_available = TRUE, updated_at = NOW() FROM suppliers s "
                            "WHERE s.id = sp.supplier_id AND s.name = $1 AND NOT sp.is_available "
                            "AND sp.part_id IN (SELECT id FROM parts_catalog WHERE importer_price_ils > 0)", SUPPLIER)
            print("restored parts:", n)
            return 0
        rows = await c.fetch(TARGETS, SUPPLIER, a.brand)
        by = {}
        for r in rows:
            by[r["manufacturer"]] = by.get(r["manufacturer"], 0) + 1
        print("targets:", len(rows), by)
        if not a.apply:
            print("dry run - nothing written")
            return 0
        done = 0
        ids = [r["id"] for r in rows]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            async with c.transaction():
                await c.execute("SET LOCAL lock_timeout = '5s'")
                await c.execute("""UPDATE supplier_parts sp SET is_available = FALSE, updated_at = NOW()
                                   FROM suppliers s WHERE s.id = sp.supplier_id AND s.name = $1 AND sp.part_id = ANY($2::uuid[])""",
                                SUPPLIER, chunk)
                await c.execute("""UPDATE parts_catalog pc SET
                        specifications = COALESCE(pc.specifications, '{}'::jsonb) || jsonb_build_object('msrp_quarantine',
                            jsonb_build_object('prev_importer', pc.importer_price_ils, 'prev_base', pc.base_price, 'prev_max', pc.max_price_ils,
                                               'msrp_ils', pc.importer_price_ils, 'at', to_char(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS'),
                                               'reason', 'ASAP gives MSRP not cost (price_level=0)')),
                        importer_price_ils = 0, base_price = 0, max_price_ils = 0, updated_at = NOW()
                    WHERE pc.id = ANY($1::uuid[])""", chunk)
            done += len(chunk)
        print("applied parts:", done)
        return 0
    finally:
        await c.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--brand")
    sys.exit(asyncio.run(main(p.parse_args())))
