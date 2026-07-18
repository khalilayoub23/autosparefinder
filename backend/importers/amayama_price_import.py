#!/usr/bin/env python3
"""
Amayama PRICE-FILL importer (2026-07-12).

Amayama (amayama.com) is a genuine Japanese/Asian OEM parts catalog that ships to
Israel (verified on the owner's account). Its pages are Cloudflare-protected
server-side (403), so prices are harvested by the OWNER'S LOGGED-IN BROWSER — same
pattern as RockAuto — and relayed to /api/v1/system/collect with brand='amayama'.

Unlike a fuzzy marketplace, Amayama is **OEM-precise**: searching an OEM lands on the
exact part page, which lists offers with BOTH a part price AND a shipping price to the
account's Israel address. So we get real price + real IL shipping per offer.

Input JSON (argv[1]): a list of offer rows, each:
    {"oem": "90915-YZZE1", "name": "FILTER, OIL", "brand": "Toyota",
     "part_num": "9091510009", "warehouse": "Japan (Osaka)",
     "price_usd": 9.86, "shipping_usd": 28.68, "part_type": "genuine"}

Matching: normalized OEM → parts_catalog (fills price only if currently unpriced,
never overwrites a real IL importer price). Groups every offer per OEM, stores the
CHEAPEST-LANDED (price+shipping) as the Amayama supplier offer with its real shipping,
and keeps ALL offers in parts_catalog.specifications->'amayama_options'.

Pricing (international/ex-VAT, CLAUDE.md policy):
    price_ils        = price_usd * usd_to_ils      # our cost (ex-VAT)
    shipping_cost_ils= shipping_usd * usd_to_ils    # REAL per-offer IL shipping
    base_price       = price_ils * 1.45             # 45% margin  (only if unpriced)
    max_price_ils    = price_ils * 1.18             # consumer ref (only if unpriced)
"""
import asyncio
import json
import os
import re
import sys
from collections import defaultdict

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
STATE = os.environ.get("AMAYAMA_PARTS_JSON", "/app/state/amayama_parts.json")


def _norm_oem(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (s or "").upper())


async def _usd_to_ils(conn) -> float:
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


def _f(v) -> float:
    try:
        m = re.search(r"[\d]+\.[\d]+|[\d]+", str(v).replace(",", ""))
        return float(m.group()) if m else 0.0
    except Exception:
        return 0.0


async def main(path: str) -> None:
    raw = json.load(open(path, encoding="utf-8"))
    parts = raw if isinstance(raw, list) else raw.get("parts", [])
    if not parts:
        print("[amayama_import] no parts in", path)
        return

    conn = await asyncpg.connect(DB)
    try:
        supplier_id = await conn.fetchval("SELECT id FROM suppliers WHERE name='Amayama' LIMIT 1")
        if not supplier_id:
            supplier_id = await conn.fetchval(
                "INSERT INTO suppliers (id,name,country,is_active,created_at,updated_at) "
                "VALUES (gen_random_uuid(),'Amayama','JP',true,NOW(),NOW()) RETURNING id"
            )
        rate = await _usd_to_ils(conn)

        by_oem = defaultdict(list)
        skipped = 0
        for p in parts:
            oem = p.get("oem") or p.get("oem_number")
            price_usd = _f(p.get("price_usd") or p.get("price"))
            norm = _norm_oem(oem)
            if not norm or price_usd <= 0:
                skipped += 1
                continue
            ship_usd = _f(p.get("shipping_usd") or p.get("shipping"))
            # 0/missing shipping means the parser didn't capture it — store None
            # (unknown), NOT 0, so the customer is never shown false "free shipping".
            ship_ils = round(ship_usd * rate, 2) if ship_usd > 0 else None
            by_oem[norm].append({
                "brand": str(p.get("brand") or "").strip(),
                "part_num": str(p.get("part_num") or "").strip(),
                "name": str(p.get("name") or "").strip(),
                "warehouse": str(p.get("warehouse") or "").strip(),
                "part_type": str(p.get("part_type") or "").strip(),
                "price_usd": round(price_usd, 2),
                "shipping_usd": round(ship_usd, 2) if ship_usd > 0 else None,
                "price_ils": round(price_usd * rate, 2),
                "shipping_ils": ship_ils,
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
            # cheapest by LANDED cost (part + known shipping; unknown shipping sorts
            # as 0 so we don't over-penalise an offer whose shipping we failed to read)
            opts.sort(key=lambda o: o["price_ils"] + (o["shipping_ils"] or 0))
            best = opts[0]
            cp_ils, ship_ils = best["price_ils"], best["shipping_ils"]  # ship_ils may be None → NULL

            await conn.execute(
                """
                INSERT INTO supplier_parts
                    (id, supplier_id, part_id, supplier_sku, price_ils, price_usd,
                     availability, is_available, part_type, shipping_cost_ils, created_at, updated_at)
                VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, 'in_stock', true, 'oem', $6, NOW(), NOW())
                ON CONFLICT (part_id, supplier_id) DO UPDATE SET
                    supplier_sku=EXCLUDED.supplier_sku, price_ils=EXCLUDED.price_ils,
                    price_usd=EXCLUDED.price_usd, is_available=true, availability='in_stock',
                    shipping_cost_ils=EXCLUDED.shipping_cost_ils, updated_at=NOW()
                """,
                supplier_id, part_id, f"AMY-{norm}", cp_ils, best["price_usd"], ship_ils,
            )
            supplier_offers += 1

            opts_json = json.dumps(opts, ensure_ascii=False)
            upd = await conn.execute(
                """
                UPDATE parts_catalog
                SET specifications = jsonb_set(COALESCE(specifications, '{}'::jsonb),
                                               '{amayama_options}', $2::jsonb, true),
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

        print(f"[amayama_import] input_rows={len(parts)} unique_oems={len(by_oem)} "
              f"matched={matched} price_filled={filled} supplier_offers={supplier_offers} "
              f"unmatched={unmatched} skipped={skipped} rate={rate}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else STATE))
