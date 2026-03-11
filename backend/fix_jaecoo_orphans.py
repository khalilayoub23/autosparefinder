"""Fix Jaecoo parts that have no supplier_parts by using base_price from catalog."""
import asyncio
import uuid
from datetime import datetime

import asyncpg
from dotenv import load_dotenv
import os

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://autospare:autospare_dev@localhost:5432/autospare")
DB_URL = DB_URL.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
ILS_TO_USD = 1 / 3.65


async def main():
    conn = await asyncpg.connect(DB_URL)
    supplier_id = await conn.fetchval("SELECT id FROM suppliers WHERE name='AutoParts Pro IL'")
    print(f"supplier_id: {supplier_id}")

    orphans = await conn.fetch("""
        SELECT pc.id, pc.sku, pc.name, pc.base_price
        FROM parts_catalog pc
        WHERE pc.manufacturer='Jaecoo'
        AND NOT EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id=pc.id)
    """)
    print(f"Orphans: {len(orphans)}")

    now = datetime.utcnow()
    inserted = errors = 0

    for p in orphans:
        price = float(p['base_price']) if p['base_price'] else 100.0
        price_usd = round(price * ILS_TO_USD, 2)
        orig_sku = p['sku'][5:] if p['sku'] and p['sku'].startswith('JAEC-') else (p['sku'] or 'UNK')

        try:
            await conn.execute("""
                INSERT INTO supplier_parts (
                    id, supplier_id, part_id, supplier_sku,
                    price_ils, price_usd,
                    is_available, availability,
                    warranty_months, estimated_delivery_days,
                    stock_quantity, min_order_qty,
                    last_checked_at, created_at
                ) VALUES (
                    $1, $2, $3, $4,
                    $5, $6,
                    $7, $8,
                    $9, $10,
                    $11, $12,
                    $13, $13
                )
            """,
                uuid.uuid4(), supplier_id, p['id'], orig_sku,
                price, price_usd,
                False, 'on_order',
                12, 14,
                0, 1,
                now
            )
            await conn.execute("""
                UPDATE parts_catalog SET part_type='OEM',
                    importer_price_ils=$1, online_price_ils=$2, updated_at=NOW()
                WHERE id=$3
            """, price, price, p['id'])
            inserted += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  ERR part={p['sku']}: {e}")

    print(f"Inserted: {inserted}, Errors: {errors}")

    final = await conn.fetchval("""
        SELECT COUNT(*) FROM supplier_parts sp
        JOIN parts_catalog pc ON sp.part_id=pc.id
        WHERE pc.manufacturer='Jaecoo'
    """)
    print(f"Total Jaecoo supplier_parts: {final}")

    in_stock = await conn.fetchval("""
        SELECT COUNT(*) FROM supplier_parts sp
        JOIN parts_catalog pc ON sp.part_id=pc.id
        WHERE pc.manufacturer='Jaecoo' AND sp.is_available=true
    """)
    print(f"In-stock: {in_stock}, On-order: {final - in_stock}")
    await conn.close()


asyncio.run(main())
