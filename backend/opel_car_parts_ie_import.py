#!/usr/bin/env python3
"""
Script: opel_car_parts_ie_import.py
Purpose: Import Opel parts extracted from car-parts.ie into catalog tables.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import asyncpg

DB_DSN = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
OPEL_MANUFACTURER_ID = "86106424-41ba-434b-b107-4b6db23523b7"
SUPPLIER_NAME = "Car-Parts.ie"
SUPPLIER_URL = "https://www.car-parts.ie"


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _extract_years(model_text: str, engine_text: str) -> tuple[int | None, int | None]:
    txt = f"{model_text} {engine_text}"
    m = re.search(r"(\d{2})\.(\d{4})\s*-\s*(\d{2})\.(\d{4}|\.\.\.)", txt)
    if m:
        y1 = int(m.group(2))
        y2 = int(m.group(4)) if m.group(4).isdigit() else 2099
        return y1, y2
    years = [int(y) for y in re.findall(r"(19\d{2}|20\d{2})", txt)]
    if len(years) >= 2:
        return years[0], years[1]
    if years:
        return years[0], years[0]
    return None, None


def _extract_model_name(model_text: str) -> str:
    model = re.sub(r"\(\d{2}\.\d{4}\s*-\s*(?:\d{2}\.\d{4}|\.\.\.)\)", "", model_text)
    return _clean(model)


def _infer_sku(product: dict[str, Any]) -> str:
    for key in ("article_number", "inferred_sku"):
        val = _clean(str(product.get(key) or ""))
        if val:
            return val
    name = _clean(str(product.get("name") or ""))
    m = re.search(r"\b([A-Z0-9][A-Z0-9\-]{2,})\b", name)
    if m:
        return m.group(1)
    url = _clean(str(product.get("product_url") or ""))
    return "CP-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12].upper()


def _category_from_name(name: str) -> str:
    n = name.lower()
    if "wiper" in n:
        return "wipers-washers"
    if "valve" in n or "coolant" in n or "heater" in n:
        return "cooling"
    return "service-general"


async def _ensure_supplier(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])

    sid = await conn.fetchval(
        """
        INSERT INTO suppliers (
            id, name, country, website,
            reliability_score, is_active, priority,
            supports_express, rate_limit_per_minute,
            is_manufacturer, manufacturer_name, manufacturer_id,
            created_at, updated_at
        ) VALUES (
            gen_random_uuid(), $1, 'IE', $2,
            0.85, TRUE, 5,
            FALSE, 30,
            FALSE, NULL, NULL,
            NOW(), NOW()
        ) RETURNING id
        """,
        SUPPLIER_NAME,
        SUPPLIER_URL,
    )
    return str(sid)


async def import_file(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text())
    products = payload.get("products") or []
    maker = _clean(payload.get("maker") or "OPEL")
    model_text = _clean(payload.get("model") or "")
    engine_text = _clean(payload.get("engine") or "")
    model_name = _extract_model_name(model_text)
    year_from, year_to = _extract_years(model_text, engine_text)

    conn = await asyncpg.connect(DB_DSN)
    try:
        supplier_id = await _ensure_supplier(conn)

        inserted = 0
        updated = 0
        fitment_rows = 0
        supplier_rows = 0

        for product in products:
            sku_raw = _infer_sku(product)
            sku = f"OPE-{sku_raw}"
            name = _clean(product.get("name") or sku_raw)
            category = _category_from_name(name)
            url = _clean(product.get("product_url") or "")

            compatible_vehicles = []
            if model_name and year_from:
                compatible_vehicles.append(
                    {
                        "manufacturer": "Opel",
                        "model": model_name,
                        "year_from": year_from,
                        "year_to": year_to or year_from,
                        "engine": engine_text or None,
                    }
                )

            row = await conn.fetchrow(
                """
                INSERT INTO parts_catalog (
                    id, sku, oem_number, name, name_he,
                    manufacturer, manufacturer_id,
                    category, description, specifications, compatible_vehicles,
                    part_type, aftermarket_tier,
                    importer_price_ils, online_price_ils, min_price_ils, max_price_ils,
                    is_safety_critical, needs_oem_lookup, master_enriched,
                    is_active, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1, $2, $3, $3,
                    'Opel', $4::uuid,
                    $5, NULL, $6::jsonb, $7::jsonb,
                    'aftermarket', 'OE_equivalent',
                    NULL, NULL, NULL, NULL,
                    FALSE, FALSE, FALSE,
                    TRUE, NOW(), NOW()
                )
                ON CONFLICT (sku) DO UPDATE SET
                    name = EXCLUDED.name,
                    name_he = EXCLUDED.name_he,
                    category = EXCLUDED.category,
                    compatible_vehicles = COALESCE(parts_catalog.compatible_vehicles, EXCLUDED.compatible_vehicles),
                    updated_at = NOW()
                RETURNING id, xmax = 0 AS inserted
                """,
                sku,
                sku_raw,
                name,
                OPEL_MANUFACTURER_ID,
                category,
                json.dumps({"source": "car-parts.ie", "product_url": url}),
                json.dumps(compatible_vehicles),
            )
            part_id = str(row["id"])
            if row["inserted"]:
                inserted += 1
            else:
                updated += 1

            if model_name and year_from:
                await conn.execute(
                    """
                    INSERT INTO part_vehicle_fitment (
                        id, part_id, manufacturer, manufacturer_id,
                        model, year_from, year_to, engine_type, notes,
                        created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1::uuid, $2, $3::uuid,
                        $4, $5, $6, NULL, $7,
                        NOW(), NOW()
                    )
                    ON CONFLICT (part_id, manufacturer, model, year_from) DO UPDATE SET
                        year_to = EXCLUDED.year_to,
                        notes = EXCLUDED.notes,
                        updated_at = NOW()
                    """,
                    part_id,
                    maker.title(),
                    OPEL_MANUFACTURER_ID,
                    model_name,
                    year_from,
                    year_to or year_from,
                    engine_text,
                )
                fitment_rows += 1

            await conn.execute(
                """
                INSERT INTO supplier_parts (
                    id, supplier_id, part_id, supplier_sku,
                    price_usd, price_ils,
                    availability, is_available,
                    estimated_delivery_days, warranty_months,
                    supplier_url, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1::uuid, $2::uuid, $3,
                    0.0, NULL,
                    'in_stock', TRUE,
                    10, 12,
                    $4, NOW(), NOW()
                )
                ON CONFLICT (supplier_id, supplier_sku) DO UPDATE SET
                    supplier_url = EXCLUDED.supplier_url,
                    updated_at = NOW()
                """,
                supplier_id,
                part_id,
                sku_raw,
                url,
            )
            supplier_rows += 1

        return {
            "products_scanned": len(products),
            "parts_inserted": inserted,
            "parts_updated": updated,
            "fitment_rows": fitment_rows,
            "supplier_rows": supplier_rows,
        }
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Path to extracted Opel JSON")
    args = ap.parse_args()
    report = asyncio.run(import_file(Path(args.file)))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
