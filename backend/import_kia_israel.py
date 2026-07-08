"""
import_kia_israel.py — Import Kia Israel official price list into parts_catalog.

Source: kia_israel_parts.json (output from kia_israel_harvester.py)
Pricing: Prices are EX-VAT (מחיר ללא מע"מ)
  importer_price_ils = price_no_vat   (already the ex-VAT cost)
  base_price         = price × 1.45   (45% margin)
  max_price_ils      = price × 1.18   (consumer reference price incl. VAT)

Strategy:
  1. If an existing Kia part with the same OEM number already exists → UPDATE its prices
  2. If no existing record → INSERT new record (SKU = raw OEM, matching kia_import.py format)
  This avoids creating duplicate records for the 97%+ of OEM numbers that kia_import.py already imported.

Run inside backend container:
  docker exec autospare_backend python3 /app/import_kia_israel.py
"""
import asyncio
import json
import os
import re
import sys
import uuid
import urllib.parse as up
from pathlib import Path

import asyncpg

INPUT_FILE = os.getenv("KIA_JSON", "/app/state/kia_israel_parts.json")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
)
BATCH_SIZE = 50
# Use raw OEM as SKU to match kia_import.py format and avoid duplicates
# same as: sku = oem.upper() (no prefix)

KIA_PRICE_URL = "https://kia-israel.co.il/%D7%9E%D7%97%D7%99%D7%A8%D7%95%D7%9F-%D7%97%D7%9C%D7%A4%D7%99%D7%9D"
OEM_RE = re.compile(r"^[A-Z0-9][\w\-./]{3,49}$", re.IGNORECASE)


def make_sku(oem: str) -> str:
    # Match kia_import.py format: raw OEM number (no prefix) to update existing records
    return re.sub(r"\s+", "", oem).upper()


async def run_import():
    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        database=p.path.lstrip("/"), user=p.username, password=p.password, timeout=30,
    )
    print(f"[DB] Connected to {p.hostname}/{p.path.lstrip('/')}")

    data_path = Path(INPUT_FILE)
    if not data_path.exists():
        print(f"[ERROR] Not found: {INPUT_FILE}")
        sys.exit(1)

    raw = json.loads(data_path.read_text(encoding="utf-8"))
    parts_raw = raw.get("parts", raw) if isinstance(raw, dict) else raw
    print(f"[Import] {len(parts_raw)} raw Kia parts from {INPUT_FILE}")

    if not parts_raw:
        print("[ERROR] File empty — run kia_israel_harvester.py first")
        sys.exit(1)

    # Get Kia manufacturer_id
    kia_row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) = 'kia' LIMIT 1"
    )
    if not kia_row:
        print("[ERROR] Brand 'Kia' not found in car_brands")
        sys.exit(1)
    kia_id = str(kia_row["id"])

    # Get or create supplier
    sup = await conn.fetchrow("SELECT id FROM suppliers WHERE name='Kia Israel' LIMIT 1")
    if not sup:
        sup_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO suppliers(id,name,country,is_active,created_at) "
            "VALUES($1,'Kia Israel','IL',TRUE,NOW()) ON CONFLICT DO NOTHING",
            sup_id
        )
        sup = await conn.fetchrow("SELECT id FROM suppliers WHERE name='Kia Israel' LIMIT 1")
    supplier_id = str(sup["id"])
    print(f"[DB] Supplier Kia Israel: {supplier_id}")

    stats = {"updated": 0, "inserted": 0, "skipped": 0, "errors": 0, "sp_upserted": 0}

    async def process_part(raw_part):
        oem = (raw_part.get("oem_number") or "").strip()
        if not oem or not OEM_RE.match(oem):
            stats["skipped"] += 1
            return

        price_no_vat = float(raw_part.get("price_no_vat") or 0)
        if price_no_vat <= 0:
            stats["skipped"] += 1
            return

        name_he = (raw_part.get("name_he") or oem).strip()
        in_stock = bool(raw_part.get("in_stock", True))
        suffix = (raw_part.get("oem_suffix") or "").strip()

        # Pricing formula — prices already ex-VAT
        importer_price_ils = round(price_no_vat, 2)
        base_price = round(price_no_vat * 1.45, 2)
        max_price_ils = round(price_no_vat * 1.18, 2)

        sku = make_sku(oem)
        specs = json.dumps({
            "source": "Kia Israel official price list",
            "source_url": KIA_PRICE_URL,
            "importer": "Kia Israel (Albar Group)",
            "currency": "ILS",
            "price_no_vat": price_no_vat,
            "vat_included": False,
            "vat_rate": 0.18,
            "oem_suffix": suffix,
            "in_stock": in_stock,
        }, ensure_ascii=False)

        try:
            async with conn.transaction():
                # Try to find existing part by OEM number + Kia brand
                existing = await conn.fetchrow(
                    "SELECT id, sku FROM parts_catalog "
                    "WHERE oem_number=$1 AND manufacturer_id=$2::uuid AND is_active "
                    "LIMIT 1",
                    oem, kia_id
                )

                if existing:
                    # UPDATE existing record with fresh IL price
                    await conn.execute("""
                        UPDATE parts_catalog SET
                            importer_price_ils = CASE WHEN $1 > 0 THEN $1 ELSE importer_price_ils END,
                            base_price = CASE WHEN $2 > 0 THEN $2 ELSE base_price END,
                            max_price_ils = CASE WHEN $3 > 0 THEN $3 ELSE max_price_ils END,
                            specifications = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                            updated_at = NOW()
                        WHERE id = $5::uuid
                    """, importer_price_ils, base_price, max_price_ils, specs, str(existing["id"]))
                    part_id = str(existing["id"])
                    stats["updated"] += 1
                else:
                    # INSERT new record with raw OEM as SKU
                    new_id = str(uuid.uuid4())
                    await conn.execute("""
                        INSERT INTO parts_catalog(
                            id, sku, name, name_he, manufacturer, manufacturer_id,
                            oem_number, category, part_type, part_condition,
                            base_price, importer_price_ils, min_price_ils, max_price_ils,
                            is_active, specifications,
                            needs_oem_lookup, master_enriched, updated_at
                        ) VALUES(
                            $1,$2,$3,$4,'Kia',$5,$6,'General Parts','oem','new',
                            $7,$8,$8,$9,
                            TRUE,$10::jsonb,FALSE,FALSE,NOW()
                        )
                    """,
                        new_id, sku, name_he, name_he, kia_id, oem,
                        base_price, importer_price_ils, max_price_ils, specs
                    )
                    part_id = new_id
                    stats["inserted"] += 1

                # Upsert supplier_parts
                await conn.execute("""
                    INSERT INTO supplier_parts(
                        id, supplier_id, part_id, supplier_sku,
                        price_ils, price_usd, availability, is_available,
                        warranty_months, estimated_delivery_days, supplier_url,
                        created_at, updated_at)
                    VALUES(gen_random_uuid(),$1::uuid,$2::uuid,$3,
                           $4,0.0,'in_stock',$5,12,7,$6,NOW(),NOW())
                    -- price_ils = max_price_ils (consumer reference), consistent with other IL importers
                    ON CONFLICT(supplier_id,supplier_sku) DO UPDATE SET
                        price_ils=EXCLUDED.price_ils,
                        is_available=EXCLUDED.is_available,
                        updated_at=NOW()
                """,
                    supplier_id, part_id, oem,
                    max_price_ils, in_stock, KIA_PRICE_URL
                )
                stats["sp_upserted"] += 1

        except Exception as exc:
            print(f"  [ERR] {oem}: {exc}")
            stats["errors"] += 1

    # Process in batches
    batch_count = 0
    for raw_part in parts_raw:
        await process_part(raw_part)
        batch_count += 1
        if batch_count % 1000 == 0:
            pct = 100 * batch_count / len(parts_raw)
            print(f"  {batch_count}/{len(parts_raw)} ({pct:.0f}%) — updated={stats['updated']} inserted={stats['inserted']}")

    await conn.close()

    print(f"\n[DONE] updated={stats['updated']}  inserted={stats['inserted']}  skipped={stats['skipped']}  errors={stats['errors']}")
    print(f"       supplier_parts upserted: {stats['sp_upserted']}")
    return stats


async def verify():
    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        database=p.path.lstrip("/"), user=p.username, password=p.password
    )
    row = await conn.fetchrow(
        "SELECT COUNT(*) as n, COUNT(*) FILTER (WHERE importer_price_ils > 0) as priced "
        "FROM parts_catalog "
        "WHERE manufacturer_id=(SELECT id FROM car_brands WHERE LOWER(name)='kia' LIMIT 1) AND is_active"
    )
    sp_row = await conn.fetchrow(
        "SELECT COUNT(*) as n FROM supplier_parts "
        "WHERE supplier_id=(SELECT id FROM suppliers WHERE name='Kia Israel' LIMIT 1)"
    )
    await conn.close()
    print(f"\n[Verify] Kia parts total: {row['n']}, with IL price: {row['priced']}")
    print(f"         Kia Israel supplier_parts: {sp_row['n']}")
    return row["n"]


if __name__ == "__main__":
    asyncio.run(run_import())
    asyncio.run(verify())
