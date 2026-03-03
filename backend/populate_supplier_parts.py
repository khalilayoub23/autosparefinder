"""
Populate supplier_parts table — link all active parts to suppliers with pricing.

Business rules:
  customer_price = supplier_cost_ILS × 1.45 + 17% VAT + 91₪ shipping

Supplier assignment:
  AutoParts Pro IL (priority=1, Israel) — parts that already have base_price data
  Global Parts Hub (Germany, priority=2) — parts without price (on_order, estimated price)
  EastAuto Supply (China, priority=3)    — duplicate link for luxury/high-demand parts

Warranty by part_type:
  Original    → 24 months
  Aftermarket → 12 months
  Refurbished →  6 months
  NULL        → 12 months (default)

Delivery days:
  Israel  (AutoParts Pro IL)  → 2-4 business days
  Germany (Global Parts Hub)  → 7-14 business days
  China   (EastAuto Supply)   → 14-30 business days
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

    async with engine.connect() as conn:
        # Get supplier IDs
        result = await conn.execute(
            text("SELECT id, name, priority FROM suppliers ORDER BY priority")
        )
        rows = result.mappings().fetchall()
        suppliers = {r["name"]: str(r["id"]) for r in rows}
        print("Suppliers found:", list(suppliers.keys()))

        supplier_il = suppliers.get("AutoParts Pro IL")
        supplier_de = suppliers.get("Global Parts Hub")
        supplier_cn = suppliers.get("EastAuto Supply")

        if not all([supplier_il, supplier_de, supplier_cn]):
            print("ERROR: Missing suppliers. Run seed_suppliers.py first.")
            return

        # Check existing supplier_parts count
        sp_count = (await conn.execute(text("SELECT COUNT(*) FROM supplier_parts"))).scalar_one()
        if sp_count > 0:
            print(f"supplier_parts already has {sp_count:,} rows — will skip conflicts (ON CONFLICT DO NOTHING).")

    print("\nStarting supplier_parts population...")
    print("=" * 60)

    async with engine.begin() as conn:
        BATCH = 3000
        offset = 0
        total_inserted = 0
        total_with_price = 0
        total_estimated = 0

        while True:
            result = await conn.execute(
                text("""
                    SELECT id, category, part_type, base_price, manufacturer
                    FROM parts_catalog
                    WHERE is_active = true
                    ORDER BY id
                    OFFSET :o LIMIT :l
                """),
                {"o": offset, "l": BATCH},
            )
            parts = result.mappings().fetchall()

            if not parts:
                break

            records = []
            for part in parts:
                part_id = str(part["id"])
                category = part["category"] or "כללי"
                part_type = part["part_type"]
                raw_price = float(part["base_price"] or 0)
                has_price = raw_price > 1.0  # filter out sub-₪1 noise

                warranty = WARRANTY_MAP.get(part_type, 12)

                if has_price:
                    # PRIMARY: Link to Israeli supplier with actual base_price
                    total_with_price += 1
                    price_ils = raw_price
                    price_usd = round(raw_price / ILS_USD, 2)
                    records.append({
                        "id": str(uuid.uuid4()),
                        "supplier_id": supplier_il,
                        "part_id": part_id,
                        "supplier_sku": f"IL-{part_id}",
                        "price_usd": price_usd,
                        "price_ils": price_ils,
                        "shipping_cost_usd": 0.0,
                        "shipping_cost_ils": 0.0,
                        "availability": "in_stock",
                        "warranty_months": warranty,
                        "estimated_delivery_days": 3,
                        "is_available": True,
                    })
                else:
                    # FALLBACK: Use category-average price from German supplier
                    total_estimated += 1
                    est_price = CATEGORY_FALLBACK_PRICE_ILS.get(category, DEFAULT_FALLBACK_PRICE)
                    est_usd = round(est_price / ILS_USD, 2)
                    records.append({
                        "id": str(uuid.uuid4()),
                        "supplier_id": supplier_de,
                        "part_id": part_id,
                        "supplier_sku": f"DE-{part_id}",
                        "price_usd": est_usd,
                        "price_ils": float(est_price),
                        "shipping_cost_usd": 25.0,
                        "shipping_cost_ils": 93.0,
                        "availability": "on_order",
                        "warranty_months": warranty,
                        "estimated_delivery_days": 10,
                        "is_available": False,
                    })

            if records:
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
                            NOW(),
                            NOW()
                        FROM jsonb_array_elements(CAST(:data AS JSONB)) AS j
                        ON CONFLICT (supplier_id, supplier_sku) DO NOTHING
                    """),
                    {"data": __import__("json").dumps(records)},
                )
                total_inserted += len(records)

            offset += BATCH
            if total_inserted % 30000 == 0:
                print(f"  ... {total_inserted:,} inserted so far "
                      f"({total_with_price:,} real price, {total_estimated:,} estimated)")

    print("\n" + "=" * 60)
    print(f"Done! Inserted {total_inserted:,} supplier_parts records")
    print(f"  Real price (Israeli supplier):     {total_with_price:,}")
    print(f"  Estimated price (German supplier): {total_estimated:,}")
    print("=" * 60)

    # Backfill any rows where price_ils was not set (e.g. legacy inserts before price_ils column existed)
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            UPDATE supplier_parts
            SET price_ils = ROUND(price_usd * 3.65, 2)
            WHERE (price_ils IS NULL OR price_ils = 0)
              AND price_usd > 0
        """))
        await conn.commit()
        backfilled = result.rowcount
        if backfilled:
            print(f"  Backfilled price_ils for {backfilled:,} rows (price_usd * 3.65)")

    # Verify
    async with engine.connect() as conn:
        res = await conn.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_available) AS available,
                MAX(price_ils) AS max_price,
                MIN(price_ils) FILTER (WHERE price_ils > 0) AS min_price,
                AVG(price_ils)::int AS avg_price
            FROM supplier_parts
        """))
        counts = res.mappings().fetchone()
        print(f"\nVerification:")
        print(f"  Total records:    {counts['total']:,}")
        print(f"  Available (IL):   {counts['available']:,}")
        print(f"  Price range:      ₪{counts['min_price']:.0f} – ₪{counts['max_price']:.0f}")
        print(f"  Average price:    ₪{counts['avg_price']:,}")


if __name__ == "__main__":
    asyncio.run(populate())
