"""
import_from_excel.py
--------------------
Imports supplier pricing and availability into supplier_parts.

This importer now consumes the curated normalized workbook so supplier sync and
catalog sync use the same parsed source rows.

Supplier: AutoParts Pro IL (official Israeli importer)

Run:
    python3 import_from_excel.py              # all sheets
    python3 import_from_excel.py JAECOO       # just JAECOO
    python3 import_from_excel.py JAECOO --dry-run
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime

import asyncpg
from dotenv import load_dotenv

from manufacturer_normalization import normalize_manufacturer_name
from workbook_normalizer import build_normalized_workbook, iter_normalized_rows

load_dotenv()

_raw_url = os.getenv("DATABASE_URL", "")
if not _raw_url:
    raise RuntimeError("DATABASE_URL environment variable is required")
DB_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")

SUPPLIER_NAME = "AutoParts Pro IL"
ILS_TO_USD = 1 / 3.65

SHEET_CONFIG = {
    "JAECOO": "Jaecoo",
    "ORA": "ORA",
    "GEN": "ג'נסיס",
    "Hyundai": "Hyundai",
    "Mercedes": "Mercedes",
    "Mitsubishi": "Mitsubishi",
    "Smart": "Smart",
    "Porsche": "Porsche",
    "Suzuki": "Suzuki",
    "Renault": "Renault",
    "Chevrolet": "Chevrolet",
    "Citroen": "Citroen",
    "Peugeot": "Peugeot",
}

def clean(val):
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s not in ("-", "None", "nan") else None


def parse_avail(val):
    if val is None:
        return None, None
    s = str(val).strip()
    if s in ("זמין", "in_stock", "available", "yes", "כן", "זמין במלאי"):
        return True, "in_stock"
    if s in ("לא זמין", "out_of_stock", "no", "לא", "אזל", "אין במלאי"):
        return False, "on_order"
    return None, None


def parse_specs(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def parse_price(value):
    try:
        price = float(value)
        return round(price, 2) if price > 0 else None
    except (TypeError, ValueError):
        return None


def load_normalized_records(brands=None):
    grouped = {sheet_name: [] for sheet_name in SHEET_CONFIG}
    seen = {sheet_name: set() for sheet_name in SHEET_CONFIG}

    for record in iter_normalized_rows(selected_sheets=brands):
        sheet_name = record.get("source_sheet")
        if sheet_name not in SHEET_CONFIG:
            continue

        catalog = clean(record.get("catalog_num") or record.get("oem_number"))
        name = clean(record.get("name"))
        sku = clean(record.get("sku"))
        if not catalog or not name:
            continue

        dedupe_key = catalog.upper()
        if dedupe_key in seen[sheet_name]:
            continue
        seen[sheet_name].add(dedupe_key)

        specs = parse_specs(record.get("specifications"))
        is_av, av_code = parse_avail(specs.get("stock_status"))
        grouped[sheet_name].append({
            "catalog": catalog,
            "sku": sku,
            "name": name,
            "price": parse_price(record.get("base_price")),
            "part_type": clean(record.get("part_type")) or "unknown",
            "is_available": is_av,
            "availability": av_code,
        })

    return grouped


async def get_supplier_parts_columns(conn):
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'supplier_parts'
        ORDER BY ordinal_position
        """
    )
    return {row["column_name"] for row in rows}


def build_supplier_insert_sql(column_names):
    placeholders = ", ".join(f"${index}" for index in range(1, len(column_names) + 1))
    columns_sql = ", ".join(column_names)
    return f"""
        INSERT INTO supplier_parts
            ({columns_sql})
        VALUES ({placeholders})
        ON CONFLICT DO NOTHING
    """


def build_supplier_insert_payload(column_names, supplier_id, part_id, rec, now, price, price_usd, is_av, av_code):
    values = {
        "id": uuid.uuid4(),
        "supplier_id": supplier_id,
        "part_id": uuid.UUID(part_id),
        "supplier_sku": rec["catalog"],
        "price_ils": price,
        "price_usd": price_usd,
        "shipping_cost_usd": 0.0,
        "shipping_cost_ils": 0.0,
        "is_available": is_av,
        "availability": av_code,
        "warranty_months": 12,
        "estimated_delivery_days": 7 if is_av else 14,
        "last_checked_at": now,
        "stock_quantity": 10 if is_av else 0,
        "min_order_qty": 1,
        "supplier_url": None,
        "last_in_stock_at": now if is_av else None,
        "express_available": False,
        "express_price_ils": None,
        "express_delivery_days": None,
        "express_cutoff_time": None,
        "express_last_checked": now,
        "created_at": now,
        "updated_at": now,
        "part_type": rec["part_type"] if rec["part_type"] != "unknown" else "OEM",
    }
    return [values[column_name] for column_name in column_names]


async def run(brands=None, dry_run=False):
    print(f"{'[DRY RUN] ' if dry_run else ''}Connecting to DB...")
    conn = await asyncpg.connect(DB_URL)
    supplier = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if not supplier:
        print(f"ERROR: Supplier '{SUPPLIER_NAME}' not found!")
        await conn.close(); return
    supplier_id = supplier['id']
    print(f"Supplier id: {supplier_id}")

    supplier_part_columns = await get_supplier_parts_columns(conn)
    insert_column_order = [
        "id",
        "supplier_id",
        "part_id",
        "supplier_sku",
        "price_ils",
        "price_usd",
        "shipping_cost_usd",
        "shipping_cost_ils",
        "is_available",
        "availability",
        "warranty_months",
        "estimated_delivery_days",
        "last_checked_at",
        "stock_quantity",
        "min_order_qty",
        "supplier_url",
        "last_in_stock_at",
        "express_available",
        "express_price_ils",
        "express_delivery_days",
        "express_cutoff_time",
        "express_last_checked",
        "created_at",
        "updated_at",
        "part_type",
    ]
    insert_column_order = [column for column in insert_column_order if column in supplier_part_columns]
    supplier_insert_sql = build_supplier_insert_sql(insert_column_order)

    normalized_path = build_normalized_workbook(selected_sheets=brands)
    normalized_records = load_normalized_records(brands=brands)
    print(f"Opened normalized source: {normalized_path}\n")

    all_stats = []
    for sheet_name, manufacturer in SHEET_CONFIG.items():
        if brands and sheet_name.upper() not in [b.upper() for b in brands]:
            continue

        manufacturer = normalize_manufacturer_name(manufacturer, manufacturer)

        print(f"=== {sheet_name} ({manufacturer}) ===")
        stats = dict(sheet=sheet_name, parsed=0, matched=0, not_found=0,
                     sp_ins=0, cat_fixed=0, errors=0)

        # Build SKU/name lookup: key → (part_id_str, base_price)
        mfr_variants = sorted({
            manufacturer,
            normalize_manufacturer_name(manufacturer, manufacturer),
            sheet_name,
        })
        db_parts = await conn.fetch(
            """
            SELECT id, sku, oem_number, name, base_price
            FROM parts_catalog
            WHERE LOWER(manufacturer) = ANY($1::text[])
            """,
            [m.lower() for m in mfr_variants if m],
        )
        by_sku, by_oem, by_name = {}, {}, {}
        for p in db_parts:
            val = (str(p['id']), float(p['base_price']) if p['base_price'] else None)
            if p['sku']:
                by_sku[p['sku'].strip().upper()] = val
            if p['oem_number']:
                by_oem[str(p['oem_number']).strip().upper()] = val
            if p['name']:
                by_name[p['name'].strip()] = val
        print(f"  DB: {len(db_parts)} parts")

        # Delete old supplier_parts
        if not dry_run:
            dr = await conn.execute("""
                DELETE FROM supplier_parts WHERE supplier_id=$1
                AND part_id IN (
                    SELECT id
                    FROM parts_catalog
                    WHERE LOWER(manufacturer) = ANY($2::text[])
                )
            """, supplier_id, [m.lower() for m in mfr_variants if m])
            print(f"  Deleted: {dr}")

        unique = normalized_records.get(sheet_name, [])
        stats["parsed"] = len(unique)
        print(f"  Normalized rows: {len(unique)}")

        now = datetime.utcnow()
        for rec in unique:
            lookup = (
                by_sku.get((rec.get("sku") or "").upper())
                or by_oem.get(rec["catalog"].upper())
                or by_name.get(rec["name"])
            )
            if not lookup:
                stats["not_found"] += 1
                continue
            part_id, base_price_db = lookup
            stats["matched"] += 1
            if dry_run:
                stats["sp_ins"] += 1
                continue

            # Use normalized workbook price; fallback to catalog base_price; last resort 100.
            price = rec["price"] or base_price_db or 100.0
            price_usd = round(price * ILS_TO_USD, 2)
            is_av = rec["is_available"] if rec["is_available"] is not None else False
            av_code = rec["availability"] or "on_order"
            try:
                await conn.execute("""
                    UPDATE parts_catalog
                    SET part_type = CASE
                            WHEN $1::text IS NOT NULL AND $1::text <> 'unknown' THEN LEFT($1::text, 50)
                            ELSE COALESCE(NULLIF(part_type, ''), 'OEM')
                        END,
                        base_price = CASE WHEN $2 > 0 THEN $2 ELSE base_price END,
                        max_price_ils = CASE WHEN $2 > 0 THEN $2 ELSE max_price_ils END,
                        importer_price_ils = CASE WHEN importer_price_ils > 0 THEN importer_price_ils ELSE 0 END,
                        online_price_ils=0, updated_at=NOW()
                    WHERE id=$3
                """, rec["part_type"], price, uuid.UUID(part_id))
                stats["cat_fixed"] += 1
                payload = build_supplier_insert_payload(
                    insert_column_order,
                    supplier_id,
                    part_id,
                    rec,
                    now,
                    price,
                    price_usd,
                    is_av,
                    av_code,
                )
                await conn.execute(supplier_insert_sql, *payload)
                stats["sp_ins"] += 1
            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 3:
                    print(f"  ERR: {e}")

        all_stats.append(stats)
        print(f"  Result: matched={stats['matched']}, not_found={stats['not_found']}, "
              f"sp_ins={stats['sp_ins']}, cat_fixed={stats['cat_fixed']}, err={stats['errors']}\n")

    await conn.close()
    print("="*55)
    print("SUMMARY")
    print("="*55)
    for s in all_stats:
        print(f"  {s['sheet']:15s} parsed={s['parsed']:5d} matched={s['matched']:5d} "
              f"not_found={s['not_found']:5d} sp_ins={s['sp_ins']:5d} err={s['errors']}")
    print()

if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry-run" in args
    br = [a for a in args if not a.startswith("-")] or None
    asyncio.run(run(brands=br, dry_run=dry))
