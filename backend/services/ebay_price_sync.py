import os
import asyncio
import logging
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from services.suppliers.ebay_supplier import EbaySupplier

logger = logging.getLogger(__name__)

ebay = EbaySupplier()

async def sync_ebay_prices(db: AsyncSession, limit_per_run: int = int(os.getenv("EBAY_PRICE_SYNC_LIMIT", "500"))) -> dict:
    report = {
        "parts_checked": 0,
        "parts_updated": 0,
        "parts_not_found": 0,
        "errors": [],
    }

    try:
        result = await db.execute(
            text("SELECT id FROM suppliers WHERE name = 'eBay Motors' LIMIT 1")
        )
        row = result.fetchone()
        if not row:
            logger.warning("eBay Motors supplier not found in DB")
            return report

        ebay_supplier_id = str(row[0])

        parts = await db.execute(text("""
            SELECT id, oem_number, name
            FROM parts_catalog
            WHERE is_active = true
            AND oem_number IS NOT NULL
            AND oem_number != ''
            AND manufacturer IN (
                'Chevrolet',
                'Mercedes-Benz',
                'Hyundai',
                'Citroen',
                'Peugeot',
                'Mitsubishi',
                'Genesis',
                'Smart',
                'Jaecoo',
                'Nissan',
                'Honda',
                'Renault'
            )
            ORDER BY RANDOM()
            LIMIT :limit
        """), {"limit": limit_per_run})
        part_rows = parts.fetchall()

        logger.info(f"eBay price sync: checking {len(part_rows)} parts")

        for part in part_rows:
            report["parts_checked"] += 1
            try:
                results = await ebay.search_by_oem(str(part.oem_number), limit=3)
                await asyncio.sleep(0.2)  # ~5 req/s max — well within eBay Browse API rate limits

                if not results:
                    report["parts_not_found"] += 1
                    continue

                cheapest = results[0]
                price_usd = Decimal(str(cheapest.price))
                shipping_usd = Decimal(str(cheapest.shipping_cost))
                total_usd = price_usd + shipping_usd

                if total_usd <= 0:
                    continue


                await db.execute(text("""
                    WITH matched_row AS (
                        SELECT sp.id
                        FROM supplier_parts sp
                        WHERE sp.supplier_id = CAST(:supplier_id AS uuid)
                          AND (
                            sp.part_id = CAST(:part_id AS uuid)
                            OR sp.supplier_sku = :supplier_sku
                          )
                        ORDER BY CASE WHEN sp.part_id = CAST(:part_id AS uuid) THEN 0 ELSE 1 END
                        LIMIT 1
                    ),
                    updated_row AS (
                        UPDATE supplier_parts sp
                        SET
                            price_usd = :price_usd,
                            shipping_cost_usd = :shipping_cost_usd,
                            supplier_sku = :supplier_sku,
                            supplier_url = :supplier_url,
                            is_available = true,
                            availability = 'in_stock',
                            last_checked_at = NOW(),
                            updated_at = NOW()
                        FROM matched_row mr
                        WHERE sp.id = mr.id
                        RETURNING sp.id
                    )
                    INSERT INTO supplier_parts (
                        id,
                        supplier_id,
                        part_id,
                        supplier_sku,
                        price_usd,
                        shipping_cost_usd,
                        availability,
                        is_available,
                        supplier_url,
                        last_checked_at,
                        created_at,
                        updated_at
                    )
                    SELECT
                        gen_random_uuid(),
                        CAST(:supplier_id AS uuid),
                        CAST(:part_id AS uuid),
                        :supplier_sku,
                        :price_usd,
                        :shipping_cost_usd,
                        'in_stock',
                        true,
                        :supplier_url,
                        NOW(),
                        NOW(),
                        NOW()
                    WHERE NOT EXISTS (SELECT 1 FROM updated_row)
                """), {
                    "supplier_id": ebay_supplier_id,
                    "part_id": str(part.id),
                    "supplier_sku": part.oem_number,
                    "price_usd": float(price_usd),
                    "shipping_cost_usd": float(shipping_usd),
                    "supplier_url": cheapest.item_url,
                })

                report["parts_updated"] += 1
                logger.info(f"Updated eBay price for {part.oem_number}: ${total_usd}")

            except Exception as e:
                logger.error(f"eBay sync error for part {part.oem_number}: {e}")
                report["errors"].append(str(e))
                try:
                    await db.rollback()
                except Exception:
                    pass

        await db.commit()
        logger.info(f"eBay price sync complete: {report}")
        return report

    except Exception as e:
        logger.error(f"sync_ebay_prices failed: {e}", exc_info=True)
        report["errors"].append(str(e))
        return report
