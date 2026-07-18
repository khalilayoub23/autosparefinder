#!/usr/bin/env python3
"""
RockAuto PRICE-FILL importer (2026-07-11).

RockAuto blocks server-side/headless scraping (FlareSolverr + Playwright both
fail), so prices are harvested by the OWNER'S REAL BROWSER (valid RockAuto
cookies) — same pattern as car-parts.ie — and relayed to /api/v1/system/collect
with source=rockauto. This importer takes the harvested rows and, unlike the
car-parts.ie importer, does NOT create parts: our catalog already holds these
parts (imported from oempartsonline for OEM+fitment) but WITHOUT a price. It
MATCHES by normalized OEM and fills the price → closes the price gap.

Input: a JSON file (path as argv[1], default /app/state/rockauto_parts.json):
    [{"oem": "0986424815", "price_usd": 24.79, "brand": "Bosch", "name": "..."}]
(price may be under "price_usd" or "price"; brand/name optional.)

Pricing (international/ex-VAT, same as eBay/online parts — CLAUDE.md policy):
    price_ils         = price_usd * usd_to_ils         # our cost (ex-VAT)
    online_price_ils  = price_ils
    base_price        = price_ils * 1.45               # 45% margin
    max_price_ils     = price_ils * 1.18               # consumer ref (incl VAT)
Writes supplier_parts (RockAuto) with price + is_available, and updates
parts_catalog base_price/online_price_ils ONLY when currently unpriced (never
overwrites a real IL importer price).
"""
import asyncio
import json
import os
import re
import sys

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
STATE = os.environ.get("ROCKAUTO_PARTS_JSON", "/app/state/rockauto_parts.json")


def _norm_oem(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (s or "").upper())


async def _usd_to_ils(conn) -> float:
    """Live USD->ILS rate, from the SAME source the rest of the system uses —
    system_settings, which REX refreshes every scraper cycle (~3h) from
    exchangerate-api.com. (Do NOT hardcode; the rate moves — 3.01 on 2026-07-11.)"""
    try:
        v = await conn.fetchval(
            """
            SELECT value FROM system_settings
            WHERE key IN ('currency_exchange_rate_usd_to_ils', 'ils_per_usd')
            ORDER BY CASE WHEN key='currency_exchange_rate_usd_to_ils' THEN 0 ELSE 1 END
            LIMIT 1
            """
        )
        if v and float(v) > 0:
            return float(v)
    except Exception:
        pass
    return float(os.environ.get("USD_TO_ILS", "3.01"))


async def main(path: str) -> None:
    raw = json.load(open(path, encoding="utf-8"))
    parts = raw if isinstance(raw, list) else raw.get("parts", [])
    if not parts:
        print("[rockauto_import] no parts in", path)
        return

    conn = await asyncpg.connect(DB)
    try:
        supplier_id = await conn.fetchval("SELECT id FROM suppliers WHERE name='RockAuto' LIMIT 1")
        if not supplier_id:
            supplier_id = await conn.fetchval(
                "INSERT INTO suppliers (id,name,country,is_active,created_at,updated_at) "
                "VALUES (gen_random_uuid(),'RockAuto','US',true,NOW(),NOW()) RETURNING id"
            )
        rate = await _usd_to_ils(conn)
        # RockAuto international shipping to Israel — MEASURED live 2026-07-11 in
        # the RockAuto cart (OEM 04152-YZZA1 → Tel Aviv): cheapest option $57.99
        # (Economy). Per-ORDER floor, weight-scales above it. Converted at the
        # live rate so it tracks currency (was a fabricated flat ₪110 before).
        RA_SHIP_USD = float(os.environ.get("ROCKAUTO_SHIP_USD", "57.99"))
        ship_ils = round(RA_SHIP_USD * rate, 2)

        # Group EVERY brand option RockAuto returns per OEM (owner directive
        # 2026-07-11: import all prices, not just the cheapest). supplier_parts
        # allows only ONE row per (part_id, supplier_id) — so we store the
        # CHEAPEST option as the RockAuto buy-offer (headline, compared against
        # other suppliers) AND record the FULL options list in the part's
        # specifications JSONB ('rockauto_options') so all brands+prices are kept.
        from collections import defaultdict
        by_oem = defaultdict(list)
        skipped = 0
        for p in parts:
            oem = p.get("oem") or p.get("oem_number")
            try:
                price_usd = float(p.get("price_usd") or p.get("price") or 0)
            except Exception:
                price_usd = 0.0
            norm = _norm_oem(oem)
            if not norm or price_usd <= 0:
                skipped += 1
                continue
            by_oem[norm].append({
                "brand": str(p.get("brand") or "").strip(),
                "partnum": str(p.get("partnum") or "").strip(),
                "name": str(p.get("name") or "").strip(),
                "price_usd": round(price_usd, 2),
                "price_ils": round(price_usd * rate, 2),
            })

        matched = filled = supplier_offers = unmatched = 0
        for norm, opts in by_oem.items():
            part_id = await conn.fetchval(
                """
                SELECT id FROM parts_catalog
                WHERE is_active
                  AND REPLACE(REPLACE(UPPER(oem_number),' ',''),'-','') = $1
                ORDER BY (CASE WHEN base_price IS NULL OR base_price=0 THEN 0 ELSE 1 END)
                LIMIT 1
                """,
                norm,
            )
            if not part_id:
                unmatched += 1
                continue
            matched += 1
            opts.sort(key=lambda o: o["price_ils"])
            cheapest = opts[0]
            cp_ils = cheapest["price_ils"]

            # One RockAuto offer = cheapest (ON CONFLICT on the part+supplier index).
            await conn.execute(
                """
                INSERT INTO supplier_parts
                    (id, supplier_id, part_id, supplier_sku, price_ils, price_usd,
                     availability, is_available, part_type, shipping_cost_ils, created_at, updated_at)
                VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, 'in_stock', true, 'aftermarket', $6, NOW(), NOW())
                ON CONFLICT (part_id, supplier_id) DO UPDATE SET
                    supplier_sku=EXCLUDED.supplier_sku, price_ils=EXCLUDED.price_ils,
                    price_usd=EXCLUDED.price_usd, is_available=true,
                    availability='in_stock', shipping_cost_ils=EXCLUDED.shipping_cost_ils,
                    updated_at=NOW()
                """,
                supplier_id, part_id, f"RA-{norm}", cp_ils, cheapest["price_usd"], ship_ils,
            )
            supplier_offers += 1

            # Full options list + headline price (fill catalog price only if unpriced).
            opts_json = json.dumps(opts, ensure_ascii=False)
            upd = await conn.execute(
                """
                UPDATE parts_catalog
                SET specifications = jsonb_set(COALESCE(specifications, '{}'::jsonb),
                                               '{rockauto_options}', $2::jsonb, true),
                    online_price_ils = CASE WHEN (base_price IS NULL OR base_price=0) THEN $3 ELSE online_price_ils END,
                    base_price       = CASE WHEN (base_price IS NULL OR base_price=0) THEN $4 ELSE base_price END,
                    max_price_ils    = CASE WHEN (base_price IS NULL OR base_price=0) THEN COALESCE(NULLIF(max_price_ils,0), $5) ELSE max_price_ils END,
                    min_price_ils    = CASE WHEN (base_price IS NULL OR base_price=0) THEN $3 ELSE min_price_ils END,
                    updated_at       = NOW()
                WHERE id = $1
                RETURNING (base_price = $4) AS was_filled
                """,
                part_id, opts_json, cp_ils, round(cp_ils * 1.45, 2), round(cp_ils * 1.18, 2),
            )
            if upd.endswith(" 1"):
                filled += 1

        print(f"[rockauto_import] input_rows={len(parts)} unique_oems={len(by_oem)} "
              f"matched={matched} price_filled={filled} supplier_offers={supplier_offers} "
              f"unmatched={unmatched} skipped={skipped} rate={rate}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else STATE))
