"""
aliexpress_price_sync.py — AliExpress DS price sync worker

Mirrors ebay_price_sync.py structure.
Queries active parts, searches AliExpress DS, writes supplier_parts + price_history,
updates parts_catalog.min_price_ils, syncs to Meilisearch per part.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from currency_rate import get_usd_to_ils_rate
from services.suppliers.aliexpress_supplier import AliExpressSupplier

logger = logging.getLogger(__name__)

# Rotating id cursor over the unpriced backlog (see ebay_price_sync for rationale).
_ALIEXPRESS_SYNC_LAST_ID = "00000000-0000-0000-0000-000000000000"

aliexpress = AliExpressSupplier()
USD_TO_ILS_FALLBACK = float(os.getenv("USD_TO_ILS", "3.72"))


def _normalize_image_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in urls:
        url = str(raw or "").strip()
        if not url or len(url) > 500:
            continue
        if url in seen:
            continue
        seen.add(url)
        output.append(url)
    return output


async def _meili_sync_part(doc: dict[str, Any]) -> bool:
    meili_url = os.getenv("MEILI_URL", "").strip()
    if not meili_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.put(
                f"{meili_url}/indexes/parts/documents",
                headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                json=[doc],
            )
        return resp.status_code < 300
    except Exception as exc:
        logger.warning("Meili per-part sync failed for %s: %s", doc.get("id"), exc)
        return False


async def _update_catalog_min_price(
    db: AsyncSession,
    *,
    part_id: str,
    candidate_min_price_ils: float,
) -> Optional[dict]:
    """Update min_price_ils only if candidate is lower; return doc for Meili sync."""
    result = await db.execute(
        text("""
            UPDATE parts_catalog
            SET
                min_price_ils = CASE
                    WHEN min_price_ils IS NULL OR :candidate < min_price_ils
                    THEN :candidate
                    ELSE min_price_ils
                END,
                updated_at = NOW()
            WHERE id = CAST(:part_id AS uuid)
              AND is_active = TRUE
            RETURNING id, sku, name, name_he, oem_number, manufacturer,
                      category, part_type, base_price, min_price_ils, is_active
        """),
        {"part_id": part_id, "candidate": candidate_min_price_ils},
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "id": str(row.id),
        "sku": row.sku,
        "name": row.name,
        "name_he": row.name_he,
        "oem_number": row.oem_number,
        "manufacturer": row.manufacturer,
        "category": row.category,
        "part_type": row.part_type,
        "base_price": float(row.base_price or 0),
        "min_price_ils": float(row.min_price_ils or 0),
        "is_active": row.is_active,
    }


async def sync_aliexpress_prices(
    db: AsyncSession,
    limit_per_run: int = int(os.getenv("ALIEXPRESS_PRICE_SYNC_LIMIT", "200")),
) -> dict:
    report = {
        "parts_checked": 0,
        "parts_updated": 0,
        "parts_not_found": 0,
        "price_history_rows": 0,
        "catalog_rows_updated": 0,
        "parts_images_added": 0,
        "parts_indexed": 0,
        "index_failures": 0,
        "errors": [],
    }

    pending_writes = 0

    try:
        result = await db.execute(
            text("SELECT id FROM suppliers WHERE name = 'AliExpress' LIMIT 1")
        )
        row = result.fetchone()
        if not row:
            # Auto-create the supplier row
            new_id = str(uuid.uuid4())
            await db.execute(
                text("""
                    INSERT INTO suppliers (id, name, country, is_active, created_at, updated_at)
                    VALUES (CAST(:id AS uuid), 'AliExpress', 'CN', TRUE, NOW(), NOW())
                    ON CONFLICT DO NOTHING
                """),
                {"id": new_id},
            )
            await db.commit()
            result = await db.execute(
                text("SELECT id FROM suppliers WHERE name = 'AliExpress' LIMIT 1")
            )
            row = result.fetchone()
            if not row:
                logger.error("Could not create AliExpress supplier row")
                return report

        aliexpress_supplier_id = str(row[0])
        ils_per_usd_rate = await get_usd_to_ils_rate(db, fallback=USD_TO_ILS_FALLBACK)
        report["ils_per_usd_rate"] = float(ils_per_usd_rate)
        report["run_started_at"] = datetime.utcnow().isoformat() + "Z"

        # Target UNPRICED parts via a rotating id cursor so every run closes the
        # gap instead of re-pricing already-priced rows (fixed 2026-07-11).
        global _ALIEXPRESS_SYNC_LAST_ID
        parts = await db.execute(
            text("""
                SELECT id, oem_number, name
                FROM parts_catalog
                WHERE is_active = TRUE
                  AND oem_number IS NOT NULL
                  AND oem_number != ''
                  AND (base_price IS NULL OR base_price = 0)
                  AND id > CAST(:last_id AS uuid)
                ORDER BY id
                LIMIT :limit
            """),
            {"limit": limit_per_run, "last_id": _ALIEXPRESS_SYNC_LAST_ID},
        )
        part_rows_pre = parts.fetchall()
        if len(part_rows_pre) < limit_per_run:
            _ALIEXPRESS_SYNC_LAST_ID = "00000000-0000-0000-0000-000000000000"
        elif part_rows_pre:
            _ALIEXPRESS_SYNC_LAST_ID = str(part_rows_pre[-1].id)
        part_rows = part_rows_pre
        logger.info("AliExpress price sync: checking %d parts", len(part_rows))

        for part in part_rows:
            report["parts_checked"] += 1
            part_number = str(part.oem_number or "")

            results = []
            try:
                results = await aliexpress.search_by_oem(part_number, limit=3)
            except Exception as exc:
                logger.error("AliExpress sync search error for %s: %s", part_number, exc)
                report["errors"].append(f"search:{part_number}:{exc}")

            # Throttle to respect rate limits
            await asyncio.sleep(0.5)

            if not results:
                report["parts_not_found"] += 1
                if pending_writes >= 25:
                    await db.commit()
                    pending_writes = 0
                continue

            cheapest = min(results, key=lambda r: float(getattr(r, "total_cost", 0) or 0))
            detail = None
            if getattr(cheapest, "item_id", None):
                try:
                    detail = await aliexpress.get_part_details(str(cheapest.item_id))
                except Exception as exc:
                    report["errors"].append(f"details:{part_number}:{exc}")

            selected = detail or cheapest

            price_usd = Decimal(str(getattr(selected, "price", None) or getattr(cheapest, "price", 0) or 0))
            if price_usd <= 0:
                report["parts_not_found"] += 1
                continue

            price_ils = (price_usd * Decimal(str(ils_per_usd_rate))).quantize(Decimal("0.01"))
            candidate_min_price_ils = float(price_ils)

            image_urls = _normalize_image_urls(
                list(getattr(selected, "image_urls", None) or [])
                + ([str(getattr(selected, "image_url", "") or "")] if getattr(selected, "image_url", None) else [])
            )

            selected_item_url = str(getattr(selected, "item_url", "") or "")[:1000]
            supplier_sku = part_number

            existing_res = await db.execute(
                text("""
                    SELECT id, price_usd, price_ils
                    FROM supplier_parts
                    WHERE supplier_id = CAST(:supplier_id AS uuid)
                      AND part_id = CAST(:part_id AS uuid)
                    LIMIT 1
                """),
                {"supplier_id": aliexpress_supplier_id, "part_id": str(part.id)},
            )
            existing = existing_res.fetchone()

            old_price_usd: Optional[Decimal] = None
            old_price_ils: Optional[Decimal] = None
            supplier_part_id: str

            if existing:
                supplier_part_id = str(existing.id)
                old_price_usd = Decimal(str(existing.price_usd)) if existing.price_usd is not None else None
                old_price_ils = Decimal(str(existing.price_ils)) if existing.price_ils is not None else None
                await db.execute(
                    text("""
                        UPDATE supplier_parts
                        SET price_usd = :price_usd,
                            price_ils = :price_ils,
                            supplier_sku = :supplier_sku,
                            supplier_url = :supplier_url,
                            is_available = TRUE,
                            availability = 'in_stock',
                            last_checked_at = NOW(),
                            updated_at = NOW()
                        WHERE id = CAST(:supplier_part_id AS uuid)
                    """),
                    {
                        "supplier_part_id": supplier_part_id,
                        "price_usd": float(price_usd),
                        "price_ils": float(price_ils),
                        "supplier_sku": supplier_sku,
                        "supplier_url": selected_item_url,
                    },
                )
            else:
                supplier_part_id = str(uuid.uuid4())
                await db.execute(
                    text("""
                        INSERT INTO supplier_parts (
                            id, supplier_id, part_id, supplier_sku,
                            price_usd, price_ils,
                            availability, is_available, supplier_url,
                            last_checked_at, created_at, updated_at
                        ) VALUES (
                            CAST(:supplier_part_id AS uuid),
                            CAST(:supplier_id AS uuid),
                            CAST(:part_id AS uuid),
                            :supplier_sku,
                            :price_usd, :price_ils,
                            'in_stock', TRUE, :supplier_url,
                            NOW(), NOW(), NOW()
                        )
                    """),
                    {
                        "supplier_part_id": supplier_part_id,
                        "supplier_id": aliexpress_supplier_id,
                        "part_id": str(part.id),
                        "supplier_sku": supplier_sku,
                        "price_usd": float(price_usd),
                        "price_ils": float(price_ils),
                        "supplier_url": selected_item_url,
                    },
                )

            report["parts_updated"] += 1
            pending_writes += 1

            catalog_doc = await _update_catalog_min_price(
                db,
                part_id=str(part.id),
                candidate_min_price_ils=candidate_min_price_ils,
            )
            if catalog_doc:
                report["catalog_rows_updated"] += 1
                pending_writes += 1

            # Upsert images
            for idx, image_url in enumerate(image_urls[:5]):
                try:
                    await db.execute(
                        text("""
                            INSERT INTO parts_images
                                (id, part_id, url, is_primary, sort_order, embedding_generated, created_at)
                            VALUES (
                                gen_random_uuid(),
                                CAST(:part_id AS uuid),
                                :url, :is_primary, :sort_order, FALSE, NOW()
                            )
                            ON CONFLICT (part_id, url) DO NOTHING
                        """),
                        {
                            "part_id": str(part.id),
                            "url": image_url,
                            "is_primary": idx == 0,
                            "sort_order": idx,
                        },
                    )
                    report["parts_images_added"] += 1
                    pending_writes += 1
                except Exception:
                    pass

            if catalog_doc:
                index_ok = await _meili_sync_part(catalog_doc)
                if index_ok:
                    report["parts_indexed"] += 1
                else:
                    report["index_failures"] += 1

            # Record price history only on change
            price_changed = (
                old_price_usd is None
                or old_price_ils is None
                or old_price_usd != price_usd
                or old_price_ils != price_ils
            )
            if price_changed:
                change_pct = None
                if old_price_ils is not None and old_price_ils > 0:
                    change_pct = float(
                        ((price_ils - old_price_ils) / old_price_ils * Decimal("100"))
                        .quantize(Decimal("0.0001"))
                    )
                    # Cap to NUMERIC(7,4) max ±999.9999 (see ebay_price_sync).
                    change_pct = max(-999.9999, min(999.9999, change_pct))
                await db.execute(
                    text("""
                        INSERT INTO price_history (
                            id, supplier_part_id,
                            old_price_ils, new_price_ils,
                            old_price_usd, new_price_usd,
                            change_pct, source, ils_per_usd_rate, created_at
                        ) VALUES (
                            CAST(:id AS uuid), CAST(:supplier_part_id AS uuid),
                            :old_price_ils, :new_price_ils,
                            :old_price_usd, :new_price_usd,
                            :change_pct, 'aliexpress_sync', :ils_per_usd_rate, NOW()
                        )
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "supplier_part_id": supplier_part_id,
                        "old_price_ils": float(old_price_ils) if old_price_ils is not None else None,
                        "new_price_ils": float(price_ils),
                        "old_price_usd": float(old_price_usd) if old_price_usd is not None else None,
                        "new_price_usd": float(price_usd),
                        "change_pct": change_pct,
                        "ils_per_usd_rate": float(ils_per_usd_rate),
                    },
                )
                report["price_history_rows"] += 1
                pending_writes += 1

            logger.info(
                "AliExpress updated part %s: usd=%s ils=%s",
                part.oem_number, float(price_usd), float(price_ils),
            )

            if pending_writes >= 25:
                await db.commit()
                pending_writes = 0

        if pending_writes:
            await db.commit()

        logger.info("AliExpress price sync complete: %s", report)
        return report

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.error("sync_aliexpress_prices failed: %s", exc, exc_info=True)
        report["errors"].append(str(exc))
        return report
