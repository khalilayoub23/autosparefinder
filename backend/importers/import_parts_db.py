"""
Import parts from 'parts data base.xlsx' into the parts_catalog PostgreSQL table.

Column layouts per sheet type (row 2 = headers, row 3+ = data):

Type A  – Chevrolet  (7-9 cols)
  [0] importer  [1] catalog_num  [2] name  [3] part_type
  [4] stock     [5] price        [6] warranty  [7] vehicle

Type F  – Citroen, Peugeot  (8-9 cols)
    [0] vehicle/model  [1] stock  [2] price  [3] warranty
    [4] importer       [5] part_type  [6] name  [7] catalog_num

Type B  – GEN, Hyundai, JAECOO, Mercedes, Mitsubishi, ORA, Smart  (5-254 cols)
  [0] stock  [1] qty_or_num  [2] name  [3] manufacturer  [4] catalog_num

Type C  – Porsche  (6 cols)
  [0] price  [1] stock  [2] warranty  [3] part_type  [4] name  [5] catalog_num

Type D  – Suzuki  (7 cols)
  [0] price  [1] stock  [2] catalog_num  [3] manufacturer  [4] part_type  [5] name

Type E  – Renault  (6-7 cols)
  [0] date  [1] stock  [2] cat_code  [3] part_type  [4] name  [5] catalog_num
"""

import asyncio
import hashlib
import uuid
import json
import os
from pathlib import Path
import asyncpg
import openpyxl
from datetime import datetime
from manufacturer_normalization import normalize_manufacturer_name
from workbook_normalizer import build_normalized_workbook, iter_normalized_rows, NORMALIZED_XLSX_FILE

XLSX_FILE = Path(__file__).parent.parent / "data" / "parts_database.xlsx"
_raw_url = os.getenv("DATABASE_URL", "postgresql://autospare:autospare_dev@localhost:5432/autospare")
DB_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")

SHEET_TYPES = {
    # Type A
    "Chevrolet": "A",
    # Type F
    "Citroen": "F", "Peugeot": "F",
    # Type B (single-block)
    "Hyundai": "B", "Mercedes": "B", "Mitsubishi": "B", "Smart": "B",
    # Type B-multi (horizontal multi-block layout)
    "GEN": "BM", "JAECOO": "BM", "ORA": "BM",
    # Type C
    "Porsche": "C",
    # Type D
    "Suzuki": "D",
    # Type E
    "Renault": "E",
}

# Sheets with horizontal multi-block layout (42 rows/block)
MULTIBLOCK_SHEETS = {"GEN", "JAECOO", "ORA"}


def clean(val):
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s not in ('-', 'None') else None


def make_sku(brand: str, catalog_num, row_idx: int) -> str:
    prefix = brand[:4].upper().replace(' ', '_')
    cat = clean(catalog_num)
    if cat and str(cat).strip():
        cat_clean = str(cat).replace(' ', '').replace('/', '-').strip()
        raw_sku = f"{prefix}-{cat_clean}"
        if len(raw_sku) > 100:
            h = hashlib.md5(cat_clean.encode()).hexdigest()[:12]
            raw_sku = f"{prefix}-{h}"
    else:
        raw_sku = f"{prefix}-R{row_idx}"
    return raw_sku[:100]


def parse_price(val):
    if val is None:
        return 0.0
    try:
        # Remove currency symbols
        s = str(val).replace('₪', '').replace(',', '').strip()
        p = float(s)
        return min(max(p, 0.0), 99_999_999.99)
    except (ValueError, TypeError):
        return 0.0


def parse_manufacturer_fields(raw_mfr, fallback_brand: str) -> tuple[str, int | None]:
    """Parse manufacturer display value and optional numeric manufacturer_id."""
    manufacturer_name = normalize_manufacturer_name(fallback_brand, fallback_brand)
    manufacturer_id = None
    mfr = clean(raw_mfr)
    if not mfr:
        return manufacturer_name, manufacturer_id

    s = str(mfr).strip()
    try:
        if s.isdigit():
            manufacturer_id = int(s)
            return manufacturer_name, manufacturer_id
        f = float(s)
        if f.is_integer():
            manufacturer_id = int(f)
            return manufacturer_name, manufacturer_id
    except (ValueError, TypeError):
        pass

    manufacturer_name = normalize_manufacturer_name(s, fallback_brand)
    return manufacturer_name, manufacturer_id


# Known header cell values to skip in multi-block sheets
_HEADER_VALS = {'זמינות מלאי', 'מחיר לצרכן', 'תיאור החלק', 'מותג', 'מספר קטלוגי'}


def _make_part_record(brand: str, avail, price, name, mfr, catalog, row_idx: int) -> dict | None:
    """Helper: build a part dict from extracted fields, or return None to skip."""
    catalog = clean(catalog)
    if not catalog or str(catalog) in _HEADER_VALS:
        return None
    name = clean(name) or "(ללא שם)"
    if name in _HEADER_VALS:
        return None
    stock = clean(avail)
    manufacturer, manufacturer_id = parse_manufacturer_fields(mfr, brand)
    specs = {}
    if stock:
        specs["stock_status"] = stock
    specs["manufacturer_name"] = manufacturer
    if manufacturer_id is not None:
        specs["manufacturer_id"] = manufacturer_id
    sku = make_sku(brand, catalog, row_idx)
    return {
        "sku": sku,
        "name": name[:255],
        "category": brand[:100],
        "manufacturer": manufacturer[:100],
        "manufacturer_id": manufacturer_id,
        "part_type": "unknown",
        "description": name[:500],
        "specifications": json.dumps(specs, ensure_ascii=False),
        "compatible_vehicles": "[]",
        "base_price": parse_price(price),
    }


def parse_multiblock_row(sheet_name: str, row: tuple, row_idx: int) -> list:
    """
    Parse a row from a multi-block sheet (GEN/JAECOO/ORA).

    Layout:
      Block 0 (cols 0-4):  avail, price, name, mfr, catalog
      Block 1 (cols 5-9):  avail, price, name, mfr, catalog
      Block 2+ (cols 5,8,X,X+1,X+2 for X=10,13,16,...): avail=col5, mfr=col8,
          then price/name/catalog rotate every 3 columns starting at col 10.
    Each 42-row chunk of data belongs to exactly one block.
    """
    brand = sheet_name.strip()
    row = list(row)
    max_col = len(row)

    def g(idx):
        return row[idx] if idx < max_col else None

    results = []

    # Block 0: cols 0-4
    if any(g(i) is not None for i in range(5)):
        r = _make_part_record(brand, g(0), g(1), g(2), g(3), g(4), row_idx)
        if r:
            results.append(r)

    # Block 1: cols 5-9 (full 5-col record)
    if any(g(i) is not None for i in range(5, 10)):
        # Validate: needs at least name or catalog
        if g(9) is not None or g(7) is not None:
            r = _make_part_record(brand, g(5), g(6), g(7), g(8), g(9), row_idx)
            if r:
                results.append(r)

    # Blocks 2+: avail shared at col 5, mfr shared at col 8,
    # then (price, name, catalog) in groups of 3 starting at col 10
    avail_common = g(5)
    mfr_common = g(8)
    for x in range(10, max_col - 1, 3):
        price_v = g(x)
        name_v = g(x + 1)
        catalog_v = g(x + 2) if x + 2 < max_col else None
        if any(v is not None for v in (price_v, name_v, catalog_v)):
            r = _make_part_record(brand, avail_common, price_v, name_v, mfr_common, catalog_v, row_idx)
            if r:
                results.append(r)

    return results


def parse_row(sheet_name: str, row: tuple, row_idx: int) -> dict | None:
    """Parse a row based on the sheet type. Returns a dict or None to skip."""
    if not any(row):
        return None

    stype = SHEET_TYPES.get(sheet_name, "A")
    brand = sheet_name.strip()

    def g(idx):
        return row[idx] if idx < len(row) else None

    if stype == "A":
        catalog_num = g(1)
        name = clean(g(2)) or "(ללא שם)"
        part_type = (clean(g(3)) or "unknown")[:50]
        stock = clean(g(4))
        price = parse_price(g(5))
        warranty = clean(g(6))
        vehicle = clean(g(7))
        importer = clean(g(0))
        specs = {k: v for k, v in {"stock_status": stock, "warranty": warranty, "importer": importer}.items() if v}
        compat = [{"make": brand, "model_year": vehicle}] if vehicle else []

    elif stype == "F":
        vehicle = clean(g(0))
        stock = clean(g(1))
        price = parse_price(g(2))
        warranty = clean(g(3))
        importer = clean(g(4))
        part_type = (clean(g(5)) or "unknown")[:50]
        name = clean(g(6)) or "(ללא שם)"
        catalog_num = g(7)
        specs = {k: v for k, v in {"stock_status": stock, "warranty": warranty, "importer": importer}.items() if v}
        compat = [{"manufacturer": brand, "model_year": vehicle, "source": "parts_database.xlsx"}] if vehicle else []

    elif stype in ("B", "BM"):
        catalog_num = g(4)
        name = clean(g(2)) or "(ללא שם)"
        part_type = "unknown"
        stock = clean(g(0))
        price = 0.0
        manufacturer_override = g(3)
        manufacturer, manufacturer_id = parse_manufacturer_fields(manufacturer_override, brand)
        specs = {k: v for k, v in {"stock_status": stock}.items() if v}
        specs["manufacturer_name"] = manufacturer
        if manufacturer_id is not None:
            specs["manufacturer_id"] = manufacturer_id
        compat = []

    elif stype == "C":
        catalog_num = g(5)
        name = clean(g(4)) or "(ללא שם)"
        part_type = (clean(g(3)) or "unknown")[:50]
        stock = clean(g(1))
        price = parse_price(g(0))
        warranty = clean(g(2))
        specs = {k: v for k, v in {"stock_status": stock, "warranty": warranty}.items() if v}
        compat = []

    elif stype == "D":
        catalog_num = g(2)
        name = clean(g(5)) or clean(g(4)) or "(ללא שם)"
        part_type = (clean(g(4)) or "unknown")[:50]
        stock = clean(g(1))
        price = parse_price(g(0))
        manufacturer, manufacturer_id = parse_manufacturer_fields(g(3), brand)
        specs = {k: v for k, v in {"stock_status": stock, "manufacturer_name": manufacturer}.items() if v}
        if manufacturer_id is not None:
            specs["manufacturer_id"] = manufacturer_id
        compat = []

    elif stype == "E":
        catalog_num = g(5)
        name = clean(g(4)) or "(ללא שם)"
        part_type = (clean(g(3)) or "unknown")[:50]
        stock = clean(g(1))
        price = 0.0
        category_code = clean(g(2))
        specs = {k: v for k, v in {"stock_status": stock, "category_code": category_code}.items() if v}
        compat = []
    else:
        return None

    sku = make_sku(brand, catalog_num, row_idx)

    manufacturer = locals().get("manufacturer", brand)
    manufacturer_id = locals().get("manufacturer_id", None)

    return {
        "sku": sku,
        "name": name[:255],
        "category": brand[:100],
        "manufacturer": manufacturer[:100],
        "manufacturer_id": manufacturer_id,
        "part_type": part_type[:50],
        "description": name[:500],
        "specifications": json.dumps(specs, ensure_ascii=False),
        "compatible_vehicles": json.dumps(compat, ensure_ascii=False),
        "base_price": price,
    }


UPSERT_SQL_BASE = """
INSERT INTO parts_catalog
    (id, sku, name, category, manufacturer, part_type,
    description, specifications, compatible_vehicles, oem_number,
     base_price, part_condition, is_safety_critical, needs_oem_lookup,
     master_enriched, is_active, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11,
        'new', false, false, false, true, NOW(), NOW())
ON CONFLICT (sku) DO UPDATE SET
    name = EXCLUDED.name,
    category = EXCLUDED.category,
    manufacturer = EXCLUDED.manufacturer,
    part_type = EXCLUDED.part_type,
    description = EXCLUDED.description,
    specifications = EXCLUDED.specifications,
    compatible_vehicles = EXCLUDED.compatible_vehicles,
    oem_number = EXCLUDED.oem_number,
    base_price = EXCLUDED.base_price,
    is_active = true,
    updated_at = NOW()
"""

UPSERT_SQL_WITH_MANUFACTURER_ID = """
INSERT INTO parts_catalog
    (id, sku, name, category, manufacturer, manufacturer_id, part_type,
     description, specifications, compatible_vehicles, oem_number,
     base_price, part_condition, is_safety_critical, needs_oem_lookup,
     master_enriched, is_active, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, $12,
        'new', false, false, false, true, NOW(), NOW())
ON CONFLICT (sku) DO UPDATE SET
    name = EXCLUDED.name,
    category = EXCLUDED.category,
    manufacturer = EXCLUDED.manufacturer,
    manufacturer_id = EXCLUDED.manufacturer_id,
    part_type = EXCLUDED.part_type,
    description = EXCLUDED.description,
    specifications = EXCLUDED.specifications,
    compatible_vehicles = EXCLUDED.compatible_vehicles,
    oem_number = EXCLUDED.oem_number,
    base_price = EXCLUDED.base_price,
    is_active = true,
    updated_at = NOW()
"""

BATCH_SIZE = 500


async def import_parts(selected_sheets: list[str] | None = None):
    print(f"[{datetime.now():%H:%M:%S}] Building normalized workbook…")
    normalized_path = build_normalized_workbook(selected_sheets=selected_sheets)
    print(f"Normalized workbook: {normalized_path}")
    print("Sheets: ['parts_catalog_import']")

    conn = await asyncpg.connect(DB_URL)
    print("Connected to PostgreSQL\n")

    has_manufacturer_id_col = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'parts_catalog'
              AND column_name = 'manufacturer_id'
        )
    """)
    upsert_sql = UPSERT_SQL_WITH_MANUFACTURER_ID if has_manufacturer_id_col else UPSERT_SQL_BASE
    print(f"parts_catalog.manufacturer_id column detected: {bool(has_manufacturer_id_col)}")

    total_upserted = total_errors = 0

    wanted = {s.lower() for s in (selected_sheets or [])}

    sheet_stats: dict[str, dict[str, int]] = {}
    batch: list[tuple[str, tuple]] = []

    async def flush_batch() -> None:
        nonlocal batch, total_upserted, total_errors
        if not batch:
            return

        params_only = [params for _, params in batch]
        try:
            await conn.executemany(upsert_sql, params_only)
            for source_sheet, _params in batch:
                sheet_stats.setdefault(source_sheet, {"rows": 0, "errors": 0})
                sheet_stats[source_sheet]["rows"] += 1
                total_upserted += 1
        except Exception:
            for source_sheet, params in batch:
                try:
                    await conn.execute(upsert_sql, *params)
                    sheet_stats.setdefault(source_sheet, {"rows": 0, "errors": 0})
                    sheet_stats[source_sheet]["rows"] += 1
                    total_upserted += 1
                except Exception:
                    sheet_stats.setdefault(source_sheet, {"rows": 0, "errors": 0})
                    sheet_stats[source_sheet]["errors"] += 1
                    total_errors += 1
        batch = []

    for record in iter_normalized_rows(selected_sheets=selected_sheets):
        sheet_name = str(record.get("source_sheet", "") or "")
        if wanted and sheet_name.lower() not in wanted:
            continue

        sheet_stats.setdefault(sheet_name, {"rows": 0, "errors": 0})
        manufacturer_id = record.get("manufacturer_id")
        if manufacturer_id in ("", None):
            manufacturer_id = None

        if has_manufacturer_id_col:
            params = (
                uuid.uuid4(),
                str(record.get("sku", "")),
                str(record.get("name", "")),
                str(record.get("category", "")),
                str(record.get("manufacturer", "")),
                manufacturer_id,
                str(record.get("part_type", "unknown")),
                str(record.get("description", "")),
                str(record.get("specifications", "{}")),
                str(record.get("compatible_vehicles", "[]")),
                str(record.get("oem_number", "")) or None,
                float(record.get("base_price", 0.0) or 0.0),
            )
        else:
            params = (
                uuid.uuid4(),
                str(record.get("sku", "")),
                str(record.get("name", "")),
                str(record.get("category", "")),
                str(record.get("manufacturer", "")),
                str(record.get("part_type", "unknown")),
                str(record.get("description", "")),
                str(record.get("specifications", "{}")),
                str(record.get("compatible_vehicles", "[]")),
                str(record.get("oem_number", "")) or None,
                float(record.get("base_price", 0.0) or 0.0),
            )

        batch.append((sheet_name, params))

        if len(batch) >= BATCH_SIZE:
            await flush_batch()

    await flush_batch()

    for sheet_name in sorted(sheet_stats):
        stats = sheet_stats[sheet_name]
        print(f"  → {sheet_name} (normalized)")
        print(f"     ✓ {stats['rows']:,} rows  ({stats['errors']} errors)")

    final_count = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog")
    await conn.close()

    print(f"\n{'='*50}")
    print(f"Import complete:")
    print(f"  Rows upserted : {total_upserted:,}")
    print(f"  Errors        : {total_errors:,}")
    print(f"  DB total now  : {final_count:,}")
    print(f"{'='*50}")


if __name__ == "__main__":
    import sys
    sheets = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    asyncio.run(import_parts(sheets or None))

