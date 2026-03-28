"""
Populate supplier_parts table — link ALL active parts to ALL suppliers.

Every part gets one row per supplier.  Re-running is safe (ON CONFLICT DO NOTHING).

Supplier pricing multipliers (relative to IL base price, or category-average if no price):
  IL   AutoParts Pro IL   ×1.00  — local stock, real catalog price, in_stock
  DE   Global Parts Hub   ×1.10  — European import, on_order
  CN   EastAuto Supply    ×0.85  — cheapest, longest transit, on_order
  US1  PartsPro USA       ×1.05  — US competitive, on_order
  US2  AutoZone Direct    ×1.15  — US retail, on_order
  KR1  Hyundai Mobis      ×0.95  — Korean OEM direct (Hyundai parts), on_order
  KR2  Kia Parts Direct   ×0.95  — Korean OEM direct (Kia parts), on_order
  DE2  Bosch Direct       ×1.00  — manufacturer direct (Bosch parts), on_order
  JP   Toyota Genuine     ×1.05  — OEM direct (Toyota parts), on_order

Manufacturer-direct suppliers (is_manufacturer=True) only get linked to parts
whose manufacturer field matches their manufacturer_name. All other suppliers
get linked to ALL parts.
"""

import asyncio
import uuid
import os
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://autospare:autospare@localhost:5432/autospare")

# Exchange rate ILS/USD used if needed (must match USD_TO_ILS in BACKEND_AI_AGENTS.py)
ILS_USD = 3.65

WARRANTY_MAP = {
    "Original": 24,
    "OEM": 24,
    "Aftermarket": 12,
    "Refurbished": 6,
}

# Category-average prices (derived from DB, used for parts with no base_price)
CATEGORY_FALLBACK_PRICE_ILS = {
    "גוף ואקסטריור":          1206,
    "כללי":                    1320,
    "מתלים והגה":              1178,
    "מנוע":                    1522,
    "חשמל":                     862,
    "בלמים":                    648,
    "מערכת דלק":                909,
    "מסננים ושמנים":            958,
    "אטמים וחומרים":            302,
    "תאורה":                   1560,
    "מיזוג ומערכת חימום":       997,
    "גלגלים וצמיגים":           889,
    "פנים הרכב":               1224,
    "תיבת הילוכים":            2398,
    "קירור":                   1027,
    "מערכת פליטה":             2365,
    "סרן והינע":                891,
    "מגבים":                    446,
    "שרשראות ורצועות":          429,
    "כלים וציוד":               350,
}
DEFAULT_FALLBACK_PRICE = 800  # ₪ if category not in map


async def populate():
    engine = create_async_engine(DATABASE_URL, pool_size=5)
    import json

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT id, name, priority, is_manufacturer, manufacturer_name FROM suppliers WHERE is_active=true ORDER BY priority")
        )
        rows = result.mappings().fetchall()
        suppliers = {r["name"]: {**dict(r), "id": str(r["id"])} for r in rows}
        print("Suppliers found:", list(suppliers.keys()))

        required = ["AutoParts Pro IL", "Global Parts Hub", "EastAuto Supply", "PartsPro USA", "AutoZone Direct"]
        missing = [n for n in required if n not in suppliers]
        if missing:
            print(f"ERROR: Missing suppliers: {missing}. Run seed_data.py first.")
            return

        sp_count = (await conn.execute(text("SELECT COUNT(*) FROM supplier_parts"))).scalar_one()
        print(f"supplier_parts currently has {sp_count:,} rows — will add missing rows (ON CONFLICT DO NOTHING).")

    # (supplier_name, sku_prefix, price_multiplier, ship_ils, ship_usd, days, avail, is_avail)
    # manufacturer_name=None  → link to ALL parts
    # manufacturer_name=X     → link only to parts WHERE manufacturer = X
    UNIVERSAL_SUPPLIERS = [
        ("AutoParts Pro IL", "IL",  1.00, 0.0,   0.0,  3,  "in_stock", True),
        ("Global Parts Hub", "DE",  1.10, 93.0,  25.0, 10, "on_order", False),
        ("EastAuto Supply",  "CN",  0.85, 130.0, 35.0, 21, "on_order", False),
        ("PartsPro USA",     "US1", 1.05, 110.0, 30.0, 12, "on_order", False),
        ("AutoZone Direct",  "US2", 1.15, 120.0, 33.0, 14, "on_order", False),
    ]
    MANUFACTURER_SUPPLIERS = [
        # (supplier_name, sku_prefix, multiplier, ship_ils, ship_usd, days, avail, is_avail)
        ("Hyundai Mobis",    "KR1", 0.95, 95.0, 26.0,  8, "on_order", False),
        ("Kia Parts Direct", "KR2", 0.95, 95.0, 26.0,  8, "on_order", False),
        ("Bosch Direct",     "DE2", 1.00, 80.0, 22.0,  7, "on_order", False),
        ("Toyota Genuine",   "JP",  1.05, 99.0, 27.0, 10, "on_order", False),
    ]

    # Build lookup: supplier_name → manufacturer_name filter (None = all parts)
    mfr_filter = {}
    for s_name, s_data in suppliers.items():
        if s_data.get("is_manufacturer") and s_data.get("manufacturer_name"):
            mfr_filter[s_name] = s_data["manufacturer_name"]

    print("\nStarting population (per-batch commits)...")
    print("=" * 60)

    async def _insert_batch(records: list) -> int:
        """Insert a batch and commit immediately. Returns rows inserted."""
        if not records:
            return 0
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO supplier_parts (
                        id, supplier_id, part_id, supplier_sku,
                        price_usd, price_ils, shipping_cost_usd, shipping_cost_ils,
                        availability, warranty_months, estimated_delivery_days,
                        is_available, last_checked_at, created_at
                    )
                    SELECT
                        CAST(j->>'id' AS UUID),
                        CAST(j->>'supplier_id' AS UUID),
                        CAST(j->>'part_id' AS UUID),
                        j->>'supplier_sku',
                        CAST(j->>'price_usd' AS NUMERIC),
                        CAST(j->>'price_ils' AS NUMERIC),
                        CAST(j->>'shipping_cost_usd' AS NUMERIC),
                        CAST(j->>'shipping_cost_ils' AS NUMERIC),
                        j->>'availability',
                        CAST(j->>'warranty_months' AS INT),
                        CAST(j->>'estimated_delivery_days' AS INT),
                        CAST(j->>'is_available' AS BOOLEAN),
                        NOW(), NOW()
                    FROM jsonb_array_elements(CAST(:data AS JSONB)) AS j
                    ON CONFLICT (supplier_id, supplier_sku) DO NOTHING
                """),
                {"data": json.dumps(records)},
            )
        return len(records)

    total_inserted = 0
    BATCH = 1000  # parts per batch; × num_suppliers = actual rows

    # ── PASS 1: Universal suppliers (all parts) ──────────────────────────────
    print("Pass 1: Universal suppliers (all parts)...")
    offset = 0
    while True:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT id, category, part_type, base_price FROM parts_catalog WHERE is_active=true ORDER BY id OFFSET :o LIMIT :l"),
                {"o": offset, "l": BATCH},
            )
            parts = result.mappings().fetchall()
        if not parts:
            break

        records = []
        for part in parts:
            part_id = str(part["id"])
            base_ils = float(part["base_price"] or 0)
            if base_ils <= 1.0:
                base_ils = float(CATEGORY_FALLBACK_PRICE_ILS.get(part["category"] or "כללי", DEFAULT_FALLBACK_PRICE))
            warranty = WARRANTY_MAP.get(part["part_type"], 12)

            for (sup_name, prefix, mult, ship_ils, ship_usd, days, avail, is_avail) in UNIVERSAL_SUPPLIERS:
                if sup_name not in suppliers:
                    continue
                price_ils = round(base_ils * mult, 2)
                records.append({
                    "id": str(uuid.uuid4()),
                    "supplier_id": suppliers[sup_name]["id"],
                    "part_id": part_id,
                    "supplier_sku": f"{prefix}-{part_id}",
                    "price_usd": round(price_ils / ILS_USD, 2),
                    "price_ils": price_ils,
                    "shipping_cost_usd": ship_usd,
                    "shipping_cost_ils": ship_ils,
                    "availability": avail,
                    "warranty_months": warranty,
                    "estimated_delivery_days": days,
                    "is_available": is_avail,
                })

        total_inserted += await _insert_batch(records)
        offset += BATCH
        if total_inserted % 50000 == 0 and total_inserted > 0:
            print(f"  ... {total_inserted:,} rows inserted")

    # ── PASS 2: Manufacturer-direct suppliers (filtered by manufacturer) ──────
    print("Pass 2: Manufacturer-direct suppliers (filtered by manufacturer)...")
    for (sup_name, prefix, mult, ship_ils, ship_usd, days, avail, is_avail) in MANUFACTURER_SUPPLIERS:
        if sup_name not in suppliers:
            print(f"  Skipping {sup_name} — not in DB yet")
            continue
        mfr = mfr_filter.get(sup_name)
        where_clause = "AND LOWER(manufacturer) = LOWER(:mfr)" if mfr else ""
        params_base = {"mfr": mfr} if mfr else {}
        print(f"  {sup_name} → manufacturer filter: '{mfr}'")

        offset = 0
        sup_inserted = 0
        while True:
            params = {**params_base, "o": offset, "l": BATCH}
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(f"SELECT id, category, part_type, base_price FROM parts_catalog WHERE is_active=true {where_clause} ORDER BY id OFFSET :o LIMIT :l"),
                    params,
                )
                parts = result.mappings().fetchall()
            if not parts:
                break

            records = []
            for part in parts:
                part_id = str(part["id"])
                base_ils = float(part["base_price"] or 0)
                if base_ils <= 1.0:
                    base_ils = float(CATEGORY_FALLBACK_PRICE_ILS.get(part["category"] or "כללי", DEFAULT_FALLBACK_PRICE))
                warranty = WARRANTY_MAP.get(part["part_type"], 12)
                price_ils = round(base_ils * mult, 2)
                records.append({
                    "id": str(uuid.uuid4()),
                    "supplier_id": suppliers[sup_name]["id"],
                    "part_id": part_id,
                    "supplier_sku": f"{prefix}-{part_id}",
                    "price_usd": round(price_ils / ILS_USD, 2),
                    "price_ils": price_ils,
                    "shipping_cost_usd": ship_usd,
                    "shipping_cost_ils": ship_ils,
                    "availability": avail,
                    "warranty_months": warranty,
                    "estimated_delivery_days": days,
                    "is_available": is_avail,
                })

            n = await _insert_batch(records)
            total_inserted += n
            sup_inserted += n
            offset += BATCH

        print(f"    → {sup_inserted:,} rows for {sup_name}")

    print("\n" + "=" * 60)
    print(f"Done! {total_inserted:,} new rows inserted total")
    print("=" * 60)

    # Final verification
    async with engine.connect() as conn:
        res = await conn.execute(text("""
            SELECT
                s.name,
                COUNT(sp.id)                                    AS total_rows,
                COUNT(sp.id) FILTER (WHERE sp.is_available)    AS available,
                s.is_manufacturer,
                s.manufacturer_name
            FROM supplier_parts sp
            JOIN suppliers s ON s.id = sp.supplier_id
            GROUP BY s.name, s.priority, s.is_manufacturer, s.manufacturer_name
            ORDER BY s.priority
        """))
        print(f"\n{'Supplier':<20} {'Rows':>8} {'Avail':>7} {'Mfr?':>5} {'Mfr Name'}")
        print("-" * 60)
        for r in res.mappings():
            mfr = r["manufacturer_name"] or "-"
            print(f"{r['name']:<20} {r['total_rows']:>8,} {r['available']:>7,} {'✓' if r['is_manufacturer'] else ' ':>5}   {mfr}")

        parts_with_5plus = (await conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT part_id FROM supplier_parts GROUP BY part_id HAVING COUNT(DISTINCT supplier_id) >= 5
            ) t
        """))).scalar_one()
        total = (await conn.execute(text("SELECT COUNT(*) FROM supplier_parts"))).scalar_one()
        print(f"\nTotal supplier_parts rows   : {total:,}")
        print(f"Parts with 5+ supplier links: {parts_with_5plus:,}")


if __name__ == "__main__":
    asyncio.run(populate())
