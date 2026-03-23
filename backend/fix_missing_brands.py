"""
fix_missing_brands.py
---------------------
Fixes brands that were missing or mis-imported:

1. Citroen  – parts_catalog was wrong (only 6 bad entries). Deletes them and
               re-imports ~6,000+ rows from Excel, then loads supplier_parts.
2. Peugeot  – same situation as Citroen (~4,000+ rows).
3. Renault  – parts_catalog is correct (161,466 row-index SKUs "RENA-R{n}").
               Only supplier_parts were missing; fixed by matching same row-index key.

Run:
  python3 fix_missing_brands.py              # all three
  python3 fix_missing_brands.py Renault      # just Renault
  python3 fix_missing_brands.py Citroen Peugeot
  python3 fix_missing_brands.py --dry-run    # dry run all three
"""
import asyncio, sys, uuid, hashlib, json, os
from pathlib import Path
from datetime import datetime
import asyncpg, openpyxl
from dotenv import load_dotenv

load_dotenv()

XLSX_FILE = Path(__file__).parent.parent / "parts data base.xlsx"
_raw_url = os.getenv("DATABASE_URL", "")
if not _raw_url:
    raise RuntimeError("DATABASE_URL environment variable is required")
DB_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")

SUPPLIER_NAME = "AutoParts Pro IL"
ILS_TO_USD = 1 / 3.65

# ── helpers ──────────────────────────────────────────────────────────────────

def clean(val):
    if val is None: return None
    s = str(val).strip()
    return s if s and s not in ('-', 'None', 'nan') else None

def parse_price(val):
    if val is None: return None
    try:
        p = float(str(val).replace('₪','').replace(',','').strip())
        return round(p, 2) if p > 0 else None
    except: return None

def parse_avail(val):
    if val is None: return None, None
    s = str(val).strip()
    if s in ('זמין','in_stock','available','yes','כן','זמין במלאי'): return True, 'in_stock'
    if s in ('לא זמין','out_of_stock','no','לא','אזל','אין במלאי'): return False, 'on_order'
    return None, None

def make_sku(prefix, catalog, row_idx):
    cat = clean(catalog)
    if cat:
        raw = f"{prefix}{cat}".replace(' ','').replace('/','-')[:100]
        if len(raw) > 100:
            raw = f"{prefix}{hashlib.md5(cat.encode()).hexdigest()[:12]}"
    else:
        raw = f"{prefix}R{row_idx}"
    return raw[:100]

# ── Citroen / Peugeot ────────────────────────────────────────────────────────
# Format F:  7 header rows + 1 column-header row → data at openpyxl row 8+
# col[0]=model  col[1]=avail(כן/לא)  col[2]=price  col[3]=warranty
# col[4]=mfr    col[5]=type          col[6]=name   col[7]=catalog_num

def parse_cit_peug_row(row):
    row = list(row)
    if len(row) < 8: return None
    cat  = clean(row[7])
    name = clean(row[6])
    if not cat or not name: return None
    is_av, av_code = parse_avail(row[1])
    return {"catalog": cat, "name": name, "price": parse_price(row[2]),
            "is_available": is_av, "availability": av_code}

# ── Renault ──────────────────────────────────────────────────────────────────
# Format E (start_row=3 to mirror import_parts_db.py row-index numbering):
# col[0]=date  col[1]=avail  col[2]=cat_code  col[3]=part_type
# col[4]=name  col[6]=real_catalog_num
# IMPORTANT: DB SKUs are RENA-R{row_idx} (row-index fallback from original import)

def parse_renault_row(row, row_idx):
    row = list(row)
    name = clean(row[4]) if len(row) > 4 else None
    if not name: return None
    is_av, av_code = parse_avail(row[1] if len(row) > 1 else None)
    # Mirror original row-index key so by_sku lookup hits "RENA-R{row_idx}"→ key "R{row_idx}"
    return {"catalog": f"R{row_idx}", "name": name, "price": None,
            "is_available": is_av, "availability": av_code}

# ── Catalog UPSERT for Citroen/Peugeot ───────────────────────────────────────

CATALOG_UPSERT = """
INSERT INTO parts_catalog
    (id, sku, name, category, manufacturer, part_type,
     description, specifications, compatible_vehicles,
     base_price, is_active, created_at, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,true,NOW(),NOW())
ON CONFLICT (sku) DO UPDATE SET
    name=EXCLUDED.name, category=EXCLUDED.category,
    manufacturer=EXCLUDED.manufacturer, part_type=EXCLUDED.part_type,
    description=EXCLUDED.description, base_price=EXCLUDED.base_price,
    is_active=true, updated_at=NOW()
"""

async def repair_catalog(conn, sheet_name, brand, sku_prefix, dry_run):
    """Delete bad catalog entries for brand and re-import from Excel."""
    wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
    ws = wb[sheet_name]

    # Count bad entries
    bad_count = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)", brand)
    print(f"  Existing catalog entries: {bad_count} (will delete + re-import)")

    if not dry_run:
        # Delete supplier_parts for this brand first (FK constraint)
        sid = await conn.fetchval("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
        if sid:
            await conn.execute("""
                DELETE FROM supplier_parts WHERE supplier_id=$1
                AND part_id IN (SELECT id FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($2))
            """, sid, brand)
        await conn.execute(
            "DELETE FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)", brand)

    # Parse Excel (start_row=8 for format F)
    inserted = errors = 0
    batch = []
    for ri, row in enumerate(ws.iter_rows(min_row=8, values_only=True), 1):
        rec = parse_cit_peug_row(row)
        if not rec: continue
        sku = make_sku(sku_prefix, rec["catalog"], ri)
        price = rec["price"] or 0.0
        row_data = (
            uuid.uuid4(), sku, rec["name"][:255],
            brand, brand, "OEM",
            rec["name"][:500],
            json.dumps({"availability": rec["availability"]}, ensure_ascii=False),
            json.dumps([{"make": brand}], ensure_ascii=False),
            price,
        )
        batch.append(row_data)
        if len(batch) >= 500 and not dry_run:
            try:
                await conn.executemany(CATALOG_UPSERT, batch)
                inserted += len(batch)
            except Exception as e:
                for r in batch:
                    try:
                        await conn.execute(CATALOG_UPSERT, *r)
                        inserted += 1
                    except: errors += 1
            batch = []

    if batch and not dry_run:
        try:
            await conn.executemany(CATALOG_UPSERT, batch)
            inserted += len(batch)
        except Exception:
            for r in batch:
                try:
                    await conn.execute(CATALOG_UPSERT, *r)
                    inserted += 1
                except: errors += 1
    elif dry_run:
        inserted = sum(1 for _ in ws.iter_rows(min_row=8, values_only=True)
                      if parse_cit_peug_row(_))

    print(f"  Catalog: inserted={inserted}, errors={errors}")
    wb.close()
    return inserted

# ── Supplier Parts import ─────────────────────────────────────────────────────

SP_INSERT = """
INSERT INTO supplier_parts
    (id,supplier_id,part_id,supplier_sku,price_ils,price_usd,
     is_available,availability,warranty_months,estimated_delivery_days,
     stock_quantity,min_order_qty,last_checked_at,created_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13)
ON CONFLICT DO NOTHING
"""

async def import_supplier_parts(conn, supplier_id, sheet_name, brand, sku_prefix,
                                 stype, dry_run):
    wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
    ws = wb[sheet_name]

    # Build lookup: stripped_sku_upper → (part_id_str, base_price)
    db_parts = await conn.fetch(
        "SELECT id, sku, name, base_price FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)",
        brand)
    by_sku, by_name = {}, {}
    for p in db_parts:
        full = p['sku'] or ""
        orig = full[len(sku_prefix):] if full.startswith(sku_prefix) else full
        val = (str(p['id']), float(p['base_price']) if p['base_price'] else None)
        if orig: by_sku[orig.upper()] = val
        nm = (p['name'] or "").strip()
        if nm: by_name[nm] = val
    print(f"  DB catalog: {len(db_parts)} parts | by_sku keys: {len(by_sku)}")

    # Delete old supplier_parts for this brand
    if not dry_run:
        dr = await conn.execute("""
            DELETE FROM supplier_parts WHERE supplier_id=$1
            AND part_id IN (SELECT id FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($2))
        """, supplier_id, brand)
        print(f"  Deleted old supplier_parts: {dr}")

    # Parse Excel
    start_row = 8 if stype == "F" else 3
    matched = not_found = sp_ins = errors = 0
    now = datetime.utcnow()

    for ri, row in enumerate(ws.iter_rows(min_row=start_row, values_only=True), 1):
        if stype == "F":
            rec = parse_cit_peug_row(row)
        else:  # E = Renault
            rec = parse_renault_row(row, ri)
        if not rec: continue

        lookup = by_sku.get(rec["catalog"].upper()) or by_name.get(rec["name"])
        if not lookup:
            not_found += 1
            continue
        part_id, base_price_db = lookup
        matched += 1
        if dry_run:
            sp_ins += 1; continue

        price = rec["price"] or base_price_db or 100.0
        price_usd = round(price * ILS_TO_USD, 2)
        is_av = rec["is_available"] if rec["is_available"] is not None else False
        av_code = rec["availability"] or "on_order"
        try:
            await conn.execute("""
                UPDATE parts_catalog SET part_type='OEM',
                    importer_price_ils=$1, online_price_ils=$2, updated_at=NOW()
                WHERE id=$3
            """, price, price, uuid.UUID(part_id))
            await conn.execute(SP_INSERT,
                uuid.uuid4(), supplier_id, uuid.UUID(part_id), rec["catalog"],
                price, price_usd, is_av, av_code,
                12, 7 if is_av else 21, 10 if is_av else 0, 1, now)
            sp_ins += 1
        except Exception as e:
            errors += 1

    wb.close()
    print(f"  Matched: {matched} | Not found: {not_found} | Inserted: {sp_ins} | Errors: {errors}")
    return sp_ins

# ── Main ──────────────────────────────────────────────────────────────────────

TARGETS = {
    "Citroen": ("Citroen", "CITR-", "F"),
    "Peugeot": ("Peugeot", "PEUG-", "F"),
    "Renault": ("Renault", "RENA-", "E"),
}

async def run(brands=None, dry_run=False):
    print(f"{'[DRY RUN] ' if dry_run else ''}Connecting...")
    conn = await asyncpg.connect(DB_URL)
    supplier = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if not supplier:
        print(f"ERROR: Supplier '{SUPPLIER_NAME}' not found!"); await conn.close(); return
    supplier_id = supplier['id']
    print(f"Supplier: {SUPPLIER_NAME} ({supplier_id})\n")

    wb_check = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
    available_sheets = wb_check.sheetnames
    wb_check.close()

    for sheet_name, (brand, sku_prefix, stype) in TARGETS.items():
        if brands and sheet_name.upper() not in [b.upper() for b in brands]:
            continue
        if sheet_name not in available_sheets:
            print(f"[{sheet_name}] not in workbook, skip\n"); continue

        print(f"{'='*55}")
        print(f"  {sheet_name}  (type={stype})")
        print(f"{'='*55}")

        # For Citroen/Peugeot: fix catalog first
        if stype == "F":
            print("[1] Repairing parts_catalog...")
            await repair_catalog(conn, sheet_name, brand, sku_prefix, dry_run)
            print("[2] Importing supplier_parts...")
        else:
            print("[1] Importing supplier_parts (catalog already OK)...")

        await import_supplier_parts(conn, supplier_id, sheet_name, brand, sku_prefix, stype, dry_run)

        # Summary
        if not dry_run:
            sp = await conn.fetchval("""
                SELECT COUNT(*) FROM supplier_parts sp
                JOIN parts_catalog pc ON pc.id=sp.part_id
                WHERE LOWER(pc.manufacturer)=LOWER($1)
            """, brand)
            ins = await conn.fetchval("""
                SELECT COUNT(*) FROM supplier_parts sp
                JOIN parts_catalog pc ON pc.id=sp.part_id
                WHERE LOWER(pc.manufacturer)=LOWER($1) AND sp.availability='in_stock'
            """, brand)
            print(f"  → {sp} total supplier_parts ({ins} in_stock)\n")

    await conn.close()
    print("Done.")

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry  = "--dry-run" in sys.argv
    asyncio.run(run(brands=args if args else None, dry_run=dry))
