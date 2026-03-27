"""
populate_eastauto.py
────────────────────
Adds EastAuto Supply (China) as a 3rd-tier supplier for high-value parts.

Targets:
  - Parts in expensive categories: מנוע, תיבת הילוכים, מערכת פליטה, תאורה
  - Any part with Global Parts Hub price > ₪2000 cost (supplier cost, not customer price)

Pricing:
  - EastAuto price = Global Parts Hub price × 0.55  (Chinese OEM-grade discount)
  - Delivery = 21 days (air freight China → Israel)
  - Shipping cost = $24.50 USD flat (~₪91)
  - is_available = False (ordered from China factory — not stocked locally)
  - Warranty = 12 months (standard Chinese aftermarket)

SKU format:  CN-{part_uuid}

Run:  python3 populate_eastauto.py
Idempotent: uses ON CONFLICT DO NOTHING on (supplier_id, supplier_sku).
"""

import asyncio
import uuid
from datetime import datetime

import asyncpg

DB_URL = "postgresql://autospare:autospare@localhost:5432/autospare"
ILS_PER_USD = 3.72

# Expensive categories worth offering a Chinese alternative for
TARGET_CATEGORIES = [
    "מנוע",
    "תיבת הילוכים",
    "מערכת פליטה",
    "תאורה",
    "מיזוג ומערכת חימום",
    "מתלים והגה",
]

# Also include any part with Global Parts Hub cost > this USD amount
HIGH_VALUE_USD_THRESHOLD = 2000 / ILS_PER_USD   # ~537 USD

CHINA_DISCOUNT = 0.55   # EastAuto price = Global × 0.55


async def main():
    conn = await asyncpg.connect(DB_URL)
    try:
        # Fetch EastAuto and Global Parts Hub IDs
        suppliers = await conn.fetch("SELECT id, name FROM suppliers")
        eastauto_id = None
        global_id = None
        for s in suppliers:
            if "EastAuto" in s["name"]:
                eastauto_id = s["id"]
            if "Global Parts Hub" in s["name"]:
                global_id = s["id"]

        if not eastauto_id or not global_id:
            print("ERROR: could not find EastAuto / Global Parts Hub in suppliers table")
            return

        print(f"EastAuto supplier id : {eastauto_id}")
        print(f"Global Parts Hub id  : {global_id}")

        # Fetch target supplier_parts from Global Parts Hub
        rows = await conn.fetch(
            """
            SELECT sp.id, sp.part_id, sp.price_usd, sp.warranty_months, pc.category
            FROM supplier_parts sp
            JOIN parts_catalog pc ON sp.part_id = pc.id
            WHERE sp.supplier_id = $1
              AND pc.is_active = true
              AND (
                pc.category = ANY($2::text[])
                OR sp.price_usd > $3
              )
            """,
            global_id,
            TARGET_CATEGORIES,
            HIGH_VALUE_USD_THRESHOLD,
        )

        total_source = len(rows)
        print(f"\nSource records (Global Parts Hub target parts): {total_source:,}")

        # Prepare batch inserts
        inserted = 0
        batch_size = 1000
        batch = []

        for row in rows:
            eastauto_price = float(row["price_usd"]) * CHINA_DISCOUNT
            sku = f"CN-{str(row['part_id'])}"

            batch.append((
                str(uuid.uuid4()),          # id
                str(eastauto_id),           # supplier_id
                str(row["part_id"]),        # part_id
                sku,                        # supplier_sku
                round(eastauto_price, 4),   # price_usd
                24.46,                      # shipping_cost_usd (~₪91)
                False,                      # is_available
                21,                         # estimated_delivery_days
                12,                         # warranty_months
                datetime.utcnow(),          # created_at
            ))

            if len(batch) >= batch_size:
                await _insert_batch(conn, batch)
                inserted += len(batch)
                batch = []
                print(f"  ... {inserted:,} inserted so far")

        if batch:
            await _insert_batch(conn, batch)
            inserted += len(batch)

        print(f"\n✓ EastAuto links inserted: {inserted:,}")
        print(f"  (Skipped duplicates via ON CONFLICT DO NOTHING)")

        # Verify
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM supplier_parts WHERE supplier_id = $1", eastauto_id
        )
        print(f"  EastAuto total supplier_parts rows: {count:,}")

        # Category breakdown
        cat_rows = await conn.fetch(
            """
            SELECT pc.category, COUNT(*) as cnt, AVG(sp.price_usd) as avg_usd
            FROM supplier_parts sp
            JOIN parts_catalog pc ON sp.part_id = pc.id
            WHERE sp.supplier_id = $1
            GROUP BY pc.category ORDER BY cnt DESC LIMIT 12
            """,
            eastauto_id,
        )
        print("\n  Category breakdown:")
        for r in cat_rows:
            print(f"    {r['category']:30s} {r['cnt']:6,}  avg ${r['avg_usd']:.2f}  ≈ ₪{r['avg_usd']*ILS_PER_USD:.0f}")

    finally:
        await conn.close()


async def _insert_batch(conn, batch):
    await conn.executemany(
        """
        INSERT INTO supplier_parts
            (id, supplier_id, part_id, supplier_sku, price_usd, shipping_cost_usd,
             is_available, estimated_delivery_days, warranty_months, created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (supplier_id, supplier_sku) DO NOTHING
        """,
        batch,
    )


if __name__ == "__main__":
    asyncio.run(main())
