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
from services.suppliers.ebay_supplier import EbaySupplier

logger = logging.getLogger(__name__)

# Rotating cursor (id) for walking the unpriced-parts backlog across runs so
# every daily run prices FRESH unpriced parts instead of re-selecting the same
# ones. Resets on restart (starts from the lowest id again — harmless).
_EBAY_SYNC_LAST_ID = "00000000-0000-0000-0000-000000000000"

ebay = EbaySupplier()
USD_TO_ILS_FALLBACK = float(os.getenv("USD_TO_ILS", "3.72"))


def _compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        out[key] = value
    return out


def _normalize_image_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in urls:
        url = str(raw or "").strip()
        if not url:
            continue
        if len(url) > 500:
            url = url[:500]
        if url in seen:
            continue
        seen.add(url)
        output.append(url)
    return output


def _normalize_part_condition(raw: Optional[str]) -> Optional[str]:
    value = str(raw or "").strip().lower()
    if not value:
        return None
    if value.startswith("new"):
        return "New"
    if "used" in value or "pre-owned" in value:
        return "Used"
    if "reman" in value:
        return "Remanufactured"
    return None


async def _meili_sync_part(doc: dict[str, Any]) -> bool:
    """Push one updated part document to Meilisearch index."""
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


async def _upsert_part_images(db: AsyncSession, *, part_id: str, image_urls: list[str]) -> int:
    inserted_rows = 0
    for idx, image_url in enumerate(image_urls):
        result = await db.execute(
            text(
                """
                WITH inserted AS (
                    INSERT INTO parts_images
                        (id, part_id, url, is_primary, sort_order, embedding_generated, created_at)
                    SELECT
                        CAST(:id AS uuid),
                        CAST(:part_id AS uuid),
                        CAST(:url AS varchar(500)),
                        CASE
                            WHEN :is_primary = TRUE
                                 AND NOT EXISTS (
                                    SELECT 1
                                    FROM parts_images
                                    WHERE part_id = CAST(:part_id AS uuid)
                                      AND is_primary = TRUE
                                 )
                            THEN TRUE
                            ELSE FALSE
                        END,
                        :sort_order,
                        FALSE,
                        NOW()
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM parts_images
                        WHERE part_id = CAST(:part_id AS uuid)
                          AND url = CAST(:url AS varchar(500))
                    )
                    RETURNING id
                )
                SELECT COUNT(*)::int AS inserted_count FROM inserted
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "part_id": part_id,
                "url": image_url,
                "is_primary": idx == 0,
                "sort_order": idx,
            },
        )
        inserted_rows += int(result.scalar() or 0)
    return inserted_rows


async def _update_catalog_metadata(
    db: AsyncSession,
    *,
    part_id: str,
    spec_patch: dict[str, Any],
    part_condition: Optional[str],
    candidate_min_price_ils: Optional[float],
) -> Optional[dict[str, Any]]:
    result = await db.execute(
        text(
            """
            UPDATE parts_catalog
            SET
                specifications = COALESCE(specifications, '{}'::jsonb) || CAST(:spec_patch AS jsonb),
                part_condition = CASE
                    WHEN CAST(:part_condition AS text) IS NULL OR CAST(:part_condition AS text) = '' THEN part_condition
                    ELSE CAST(:part_condition AS text)
                END,
                min_price_ils = CASE
                    WHEN CAST(:candidate_min_price_ils AS numeric) IS NULL THEN min_price_ils
                    WHEN min_price_ils IS NULL OR min_price_ils > CAST(:candidate_min_price_ils AS numeric) THEN CAST(:candidate_min_price_ils AS numeric)
                    ELSE min_price_ils
                END,
                max_price_ils = CASE
                    WHEN CAST(:candidate_min_price_ils AS numeric) IS NULL THEN max_price_ils
                    WHEN max_price_ils IS NULL OR max_price_ils < CAST(:candidate_min_price_ils AS numeric) THEN CAST(:candidate_min_price_ils AS numeric)
                    ELSE max_price_ils
                END,
                updated_at = NOW()
            WHERE id = CAST(:part_id AS uuid)
            RETURNING
                id::text AS id,
                sku,
                name,
                name_he,
                manufacturer,
                category,
                part_type,
                oem_number,
                is_active,
                is_safety_critical,
                min_price_ils::float AS min_price_ils,
                base_price::float AS base_price
            """
        ),
        {
            "part_id": part_id,
            "spec_patch": json.dumps(spec_patch, ensure_ascii=False),
            "part_condition": part_condition,
            "candidate_min_price_ils": candidate_min_price_ils,
        },
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def _record_ebay_api_call(
    db: AsyncSession,
    *,
    query: str,
    part_number: str,
    part_id: str,
    url: str,
    http_status: int,
    success: bool,
    results_count: int,
    response_ms: int,
    error_message: Optional[str] = None,
) -> bool:
    """Write one eBay call row to scraper_api_calls for audit visibility."""
    try:
        await db.execute(
            text(
                """
                INSERT INTO scraper_api_calls
                    (id, source, query, part_number, url, http_status, success,
                     results_count, response_ms, error_message, part_id, called_at, created_at)
                VALUES
                    (CAST(:id AS uuid), 'ebay', :query, :part_number, :url, :http_status, :success,
                     :results_count, :response_ms, :error_message, CAST(:part_id AS uuid), NOW(), NOW())
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "query": (query or "")[:200],
                "part_number": (part_number or "")[:100],
                "url": (url or "")[:500],
                "http_status": int(http_status),
                "success": bool(success),
                "results_count": int(results_count),
                "response_ms": int(max(response_ms, 0)),
                "error_message": (error_message or "")[:1000] or None,
                "part_id": part_id,
            },
        )
        return True
    except Exception as exc:
        logger.warning("Failed to record eBay API call for %s: %s", part_number, exc)
        return False


async def sync_ebay_prices(
    db: AsyncSession,
    limit_per_run: int = int(os.getenv("EBAY_PRICE_SYNC_LIMIT", "500")),
) -> dict:
    report = {
        "parts_checked": 0,
        "parts_updated": 0,
        "parts_not_found": 0,
        "blocked_non_il_shipping": 0,
        "price_history_rows": 0,
        "api_calls_logged": 0,
        "catalog_rows_updated": 0,
        "parts_images_added": 0,
        "warranty_details_saved": 0,
        "parts_indexed": 0,
        "index_failures": 0,
        "errors": [],
    }

    pending_writes = 0

    try:
        result = await db.execute(
            text("SELECT id FROM suppliers WHERE name = 'eBay Motors' LIMIT 1")
        )
        row = result.fetchone()
        if not row:
            logger.warning("eBay Motors supplier not found in DB")
            return report

        ebay_supplier_id = str(row[0])
        ils_per_usd_rate = await get_usd_to_ils_rate(db, fallback=USD_TO_ILS_FALLBACK)
        report["ils_per_usd_rate"] = float(ils_per_usd_rate)
        report["run_started_at"] = datetime.utcnow().isoformat() + "Z"

        # Brands to price from eBay. Default now covers the LARGEST unpriced
        # blocks too (Kia/Toyota/Porsche/Lexus/Volvo/Audi/Subaru/… were missing
        # from the old 24-brand list, so 70% of the 2.24M unpriced parts could
        # NEVER get an eBay price). Override with EBAY_PRICE_SYNC_BRANDS (comma
        # list) or set it to 'ALL' to price every brand by OEM number.
        _DEFAULT_BRANDS = [
            "Chevrolet", "Mercedes-Benz", "Hyundai", "Citroen", "Peugeot",
            "Mitsubishi", "Genesis", "Smart", "Jaecoo", "Nissan", "Honda",
            "Renault", "BMW", "Jaguar", "MINI", "Ford", "Mazda", "MG", "Maxus",
            "Jetour", "BYD", "Chery", "GWM", "Omoda",
            # added 2026-07-11 — the big unpriced brands:
            "Kia", "Toyota", "Porsche", "Lexus", "Volvo", "Infiniti", "Chrysler",
            "Land Rover", "Audi", "Subaru", "Acura", "WEY", "Volkswagen", "Skoda",
            "SEAT", "Suzuki", "Opel", "Fiat", "Alfa Romeo", "Dacia", "Lancia",
            "Cadillac", "GMC", "Buick", "Dodge", "Jeep", "RAM", "Lincoln",
        ]
        _brands_env = (os.getenv("EBAY_PRICE_SYNC_BRANDS", "") or "").strip()
        _all_brands = _brands_env.upper() == "ALL"
        _brands = [b.strip() for b in _brands_env.split(",") if b.strip()] or _DEFAULT_BRANDS

        # Target ONLY unpriced parts (base_price 0/NULL) so every run CLOSES the
        # gap instead of re-pricing already-priced rows (the old ORDER BY RANDOM()
        # over millions wasted its budget on priced parts AND took 48s). Walk the
        # unpriced backlog in id order via a rotating cursor (`_EBAY_SYNC_LAST_ID`)
        # — cheap (pkey index, ~1.8s) and makes deterministic forward progress;
        # wraps to the start when it reaches the end. Priced parts still get
        # market-drift refresh from the synthetic pass in sync_prices.
        global _EBAY_SYNC_LAST_ID
        _brand_filter = "" if _all_brands else "AND manufacturer = ANY(:brands)"
        _q_params = {"limit": limit_per_run, "last_id": _EBAY_SYNC_LAST_ID}
        if not _all_brands:
            _q_params["brands"] = _brands
        parts = await db.execute(
            text(
                f"""
                SELECT id, oem_number, name
                FROM parts_catalog
                WHERE is_active = true
                  AND oem_number IS NOT NULL
                  AND oem_number != ''
                  AND (base_price IS NULL OR base_price = 0)
                  AND id > CAST(:last_id AS uuid)
                  {_brand_filter}
                ORDER BY id
                LIMIT :limit
                """
            ),
            _q_params,
        )
        part_rows = parts.fetchall()
        # Advance / wrap the cursor.
        if len(part_rows) < limit_per_run:
            _EBAY_SYNC_LAST_ID = "00000000-0000-0000-0000-000000000000"  # reached end → wrap
        elif part_rows:
            _EBAY_SYNC_LAST_ID = str(part_rows[-1].id)

        logger.info("eBay price sync: checking %d parts", len(part_rows))
        search_url_base = f"{getattr(ebay, '_base_url', 'https://api.ebay.com')}/buy/browse/v1/item_summary/search"

        for part in part_rows:
            report["parts_checked"] += 1
            part_number = str(part.oem_number or "")
            query = part_number
            call_started = time.monotonic()
            results = []
            call_success = True
            call_http_status = 200
            call_error: Optional[str] = None

            try:
                results = await ebay.search_by_oem(part_number, limit=3)
            except Exception as exc:
                call_success = False
                call_http_status = 500
                call_error = str(exc)
                logger.error("eBay sync search error for part %s: %s", part.oem_number, exc)
                report["errors"].append(f"search:{part_number}:{exc}")

            response_ms = int((time.monotonic() - call_started) * 1000)
            api_logged = await _record_ebay_api_call(
                db,
                query=query,
                part_number=part_number,
                part_id=str(part.id),
                url=f"{search_url_base}?q={part_number}",
                http_status=call_http_status,
                success=call_success,
                results_count=len(results),
                response_ms=response_ms,
                error_message=call_error,
            )
            if api_logged:
                report["api_calls_logged"] += 1
                pending_writes += 1

            # Keep eBay request pace conservative.
            await asyncio.sleep(0.2)

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
                    detail = await ebay.get_part_details(str(cheapest.item_id))
                except Exception as exc:
                    report["errors"].append(f"details:{part_number}:{exc}")

            selected = detail or cheapest

            ships_to_israel = bool(getattr(selected, "ships_to_israel", False))
            if not ships_to_israel:
                await db.execute(
                    text(
                        """
                        UPDATE supplier_parts
                        SET
                            is_available = FALSE,
                            availability = 'on_order',
                            last_checked_at = NOW(),
                            updated_at = NOW()
                        WHERE supplier_id = CAST(:supplier_id AS uuid)
                          AND part_id = CAST(:part_id AS uuid)
                        """
                    ),
                    {
                        "supplier_id": ebay_supplier_id,
                        "part_id": str(part.id),
                    },
                )
                report["blocked_non_il_shipping"] += 1
                pending_writes += 1
                if pending_writes >= 25:
                    await db.commit()
                    pending_writes = 0
                continue

            price_usd = Decimal(str(getattr(selected, "price", None) or getattr(cheapest, "price", 0) or 0))
            shipping_usd = Decimal(str(getattr(selected, "shipping_cost", 0) or 0))
            if price_usd <= 0:
                report["parts_not_found"] += 1
                continue

            price_ils = (price_usd * Decimal(str(ils_per_usd_rate))).quantize(Decimal("0.01"))
            shipping_ils = (shipping_usd * Decimal(str(ils_per_usd_rate))).quantize(Decimal("0.01"))
            candidate_min_price_ils = float((price_ils + shipping_ils).quantize(Decimal("0.01")))

            tech_specs = dict(getattr(selected, "tech_specs", None) or {})
            warranty_text = str(getattr(selected, "warranty_text", "") or "").strip() or None
            warranty_months_raw = getattr(selected, "warranty_months", None)
            try:
                warranty_months = int(warranty_months_raw) if warranty_months_raw is not None else None
                if warranty_months is not None and warranty_months <= 0:
                    warranty_months = None
            except Exception:
                warranty_months = None

            image_urls = _normalize_image_urls(
                list(getattr(selected, "image_urls", None) or [])
                + ([str(getattr(selected, "image_url", "") or "")] if getattr(selected, "image_url", None) else [])
                + ([str(getattr(cheapest, "image_url", "") or "")] if getattr(cheapest, "image_url", None) else [])
            )

            ebay_meta = _compact_dict(
                {
                    "last_sync_at": datetime.utcnow().isoformat() + "Z",
                    "item_id": str(getattr(selected, "item_id", "") or "")[:120],
                    "item_url": str(getattr(selected, "item_url", "") or "")[:1000],
                    "seller": str(getattr(selected, "seller", "") or "")[:255],
                    "seller_rating": getattr(selected, "seller_rating", None),
                    "condition": str(getattr(selected, "condition", "") or "")[:120],
                    "location": str(getattr(selected, "location", "") or "")[:255],
                      "ships_to_israel": ships_to_israel,
                      "shipping_country_context": "IL",
                    "warranty_text": (warranty_text or "")[:500] if warranty_text else None,
                    "warranty_months": warranty_months,
                    "tech_specs": tech_specs,
                    "image_urls": image_urls,
                }
            )
            spec_patch = {"ebay": ebay_meta}

            existing_res = await db.execute(
                text(
                    """
                    SELECT id, price_usd, price_ils
                    FROM supplier_parts
                    WHERE supplier_id = CAST(:supplier_id AS uuid)
                      AND (
                        part_id = CAST(:part_id AS uuid)
                        OR supplier_sku = :supplier_sku
                      )
                    ORDER BY CASE WHEN part_id = CAST(:part_id AS uuid) THEN 0 ELSE 1 END
                    LIMIT 1
                    """
                ),
                {
                    "supplier_id": ebay_supplier_id,
                    "part_id": str(part.id),
                    "supplier_sku": part_number,
                },
            )
            existing = existing_res.fetchone()

            old_price_usd: Optional[Decimal] = None
            old_price_ils: Optional[Decimal] = None

            selected_item_url = str(getattr(selected, "item_url", "") or getattr(cheapest, "item_url", ""))[:1000]

            if existing:
                supplier_part_id = str(existing.id)
                old_price_usd = Decimal(str(existing.price_usd)) if existing.price_usd is not None else None
                old_price_ils = Decimal(str(existing.price_ils)) if existing.price_ils is not None else None

                await db.execute(
                    text(
                        """
                        UPDATE supplier_parts
                        SET
                            price_usd = :price_usd,
                            price_ils = :price_ils,
                            shipping_cost_usd = :shipping_cost_usd,
                            shipping_cost_ils = :shipping_cost_ils,
                            warranty_months = COALESCE(:warranty_months, warranty_months),
                            supplier_sku = :supplier_sku,
                            supplier_url = :supplier_url,
                            is_available = true,
                            availability = 'in_stock',
                            last_checked_at = NOW(),
                            updated_at = NOW()
                        WHERE id = CAST(:supplier_part_id AS uuid)
                        """
                    ),
                    {
                        "supplier_part_id": supplier_part_id,
                        "price_usd": float(price_usd),
                        "price_ils": float(price_ils),
                        "shipping_cost_usd": float(shipping_usd),
                        "shipping_cost_ils": float(shipping_ils),
                        "warranty_months": warranty_months,
                        "supplier_sku": part_number,
                        "supplier_url": selected_item_url,
                    },
                )
            else:
                supplier_part_id = str(uuid.uuid4())
                await db.execute(
                    text(
                        """
                        INSERT INTO supplier_parts (
                            id,
                            supplier_id,
                            part_id,
                            supplier_sku,
                            price_usd,
                            price_ils,
                            shipping_cost_usd,
                            shipping_cost_ils,
                            warranty_months,
                            availability,
                            is_available,
                            supplier_url,
                            last_checked_at,
                            created_at,
                            updated_at
                        )
                        VALUES (
                            CAST(:supplier_part_id AS uuid),
                            CAST(:supplier_id AS uuid),
                            CAST(:part_id AS uuid),
                            :supplier_sku,
                            :price_usd,
                            :price_ils,
                            :shipping_cost_usd,
                            :shipping_cost_ils,
                            COALESCE(:warranty_months, 12),
                            'in_stock',
                            true,
                            :supplier_url,
                            NOW(),
                            NOW(),
                            NOW()
                        )
                        """
                    ),
                    {
                        "supplier_part_id": supplier_part_id,
                        "supplier_id": ebay_supplier_id,
                        "part_id": str(part.id),
                        "supplier_sku": part_number,
                        "price_usd": float(price_usd),
                        "price_ils": float(price_ils),
                        "shipping_cost_usd": float(shipping_usd),
                        "shipping_cost_ils": float(shipping_ils),
                        "warranty_months": warranty_months,
                        "supplier_url": selected_item_url,
                    },
                )

            report["parts_updated"] += 1
            pending_writes += 1

            catalog_doc = await _update_catalog_metadata(
                db,
                part_id=str(part.id),
                spec_patch=spec_patch,
                part_condition=_normalize_part_condition(getattr(selected, "condition", None)),
                candidate_min_price_ils=candidate_min_price_ils,
            )
            if catalog_doc:
                report["catalog_rows_updated"] += 1
                pending_writes += 1

            if warranty_text or warranty_months is not None:
                report["warranty_details_saved"] += 1

            if image_urls:
                added_images = await _upsert_part_images(db, part_id=str(part.id), image_urls=image_urls)
                if added_images > 0:
                    report["parts_images_added"] += int(added_images)
                    pending_writes += int(added_images)

            if catalog_doc:
                index_ok = await _meili_sync_part(catalog_doc)
                if index_ok:
                    report["parts_indexed"] += 1
                else:
                    report["index_failures"] += 1

            price_changed = (
                old_price_usd is None
                or old_price_ils is None
                or old_price_usd != price_usd
                or old_price_ils != price_ils
            )
            if price_changed:
                change_pct = None
                if old_price_ils is not None and old_price_ils > 0:
                    change_pct = float(((price_ils - old_price_ils) / old_price_ils * Decimal("100")).quantize(Decimal("0.0001")))
                    # price_history.change_pct is NUMERIC(7,4) (max ±999.9999). A
                    # part re-priced from a stale near-zero old price can compute
                    # a huge % (e.g. +4405%) that overflowed and ABORTED the whole
                    # sync run partway (fixed 2026-07-11). Cap it — it's just a
                    # change indicator, not used for money.
                    change_pct = max(-999.9999, min(999.9999, change_pct))

                await db.execute(
                    text(
                        """
                        INSERT INTO price_history (
                            id,
                            supplier_part_id,
                            old_price_ils,
                            new_price_ils,
                            old_price_usd,
                            new_price_usd,
                            change_pct,
                            source,
                            ils_per_usd_rate,
                            created_at
                        )
                        VALUES (
                            CAST(:id AS uuid),
                            CAST(:supplier_part_id AS uuid),
                            :old_price_ils,
                            :new_price_ils,
                            :old_price_usd,
                            :new_price_usd,
                            :change_pct,
                            'ebay_sync',
                            :ils_per_usd_rate,
                            NOW()
                        )
                        """
                    ),
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
                "Updated eBay part %s: usd=%s ils=%s indexed=%s",
                part.oem_number,
                float(price_usd),
                float(price_ils),
                "yes" if catalog_doc else "no",
            )

            if pending_writes >= 25:
                await db.commit()
                pending_writes = 0

        if pending_writes:
            await db.commit()

        logger.info("eBay price sync complete: %s", report)
        return report

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.error("sync_ebay_prices failed: %s", exc, exc_info=True)
        report["errors"].append(str(exc))
        return report
