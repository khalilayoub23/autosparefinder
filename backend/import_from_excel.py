"""
import_from_excel.py
--------------------
Imports REAL supplier data from 'parts data base.xlsx' into supplier_parts.

Uses the same multi-block parsing logic as import_parts_db.py.
Also fixes parts_catalog.part_type and sets importer_price_ils.

Supplier: AutoParts Pro IL (official Israeli importer)

Run:
  python3 import_from_excel.py              # all sheets
  python3 import_from_excel.py JAECOO       # just JAECOO
  python3 import_from_excel.py JAECOO --dry-run
"""
import asyncio, sys, uuid, hashlib, json
from datetime import datetime
from pathlib import Path
import asyncpg, openpyxl
from dotenv import load_dotenv
import os

load_dotenv()

XLSX_FILE = Path(__file__).parent.parent / "parts data base.xlsx"
_raw_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://autospare:autospare_dev@localhost:5432/autospare")
DB_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")

SUPPLIER_NAME = "AutoParts Pro IL"
ILS_TO_USD = 1 / 3.65

SHEET_CONFIG = {
    "JAECOO":     ("Jaecoo",    "JAEC-", "BM"),
    "ORA":        ("ORA",       "ORA-",  "BM"),
    "GEN":        ("ג'נסיס",   "GEN-",  "BM"),
    "Hyundai":    ("Hyundai",   "HYUN-", "B"),
    "Mercedes":   ("Mercedes",  "MERC-", "B"),
    "Mitsubishi": ("Mitsubishi","MITS-", "B"),
    "Smart":      ("Smart",     "SMAR-", "B"),
    "Porsche":    ("Porsche",   "PORS-", "C"),
    "Suzuki":     ("Suzuki",    "SUZU-", "D"),
    "Renault":    ("Renault",   "RENA-", "E"),
    "Chevrolet":  ("Chevrolet", "CHEV-", "A"),
    "Citroen":    ("Citroen",   "CITR-", "F"),
    "Peugeot":    ("Peugeot",   "PEUG-", "F"),
}

_HEADER_VALS = {'זמינות מלאי','מחיר לצרכן','תיאור החלק','מותג','מספר קטלוגי'}

def clean(val):
    if val is None: return None
    s = str(val).strip()
    return s if s and s not in ('-','None','nan') else None

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

def parse_multiblock(sheet_name, row, row_idx):
    row = list(row); mc = len(row)
    def g(i): return row[i] if i < mc else None
    results = []

    def add(avail_v, price_v, name_v, mfr_v, cat_v):
        cat = clean(cat_v)
        if not cat or cat in _HEADER_VALS: return
        name = clean(name_v) or "(ללא שם)"
        if name in _HEADER_VALS: return
        is_av, av_code = parse_avail(avail_v)
        results.append({"catalog": cat, "name": name, "price": parse_price(price_v),
                        "is_available": is_av, "availability": av_code})

    if any(g(i) is not None for i in range(5)):
        add(g(0), g(1), g(2), g(3), g(4))
    if mc > 5 and any(g(i) is not None for i in range(5, 10)):
        if g(9) is not None or g(7) is not None:
            add(g(5), g(6), g(7), g(8), g(9))
    avail_c, mfr_c = g(5), g(8)
    for x in range(10, mc - 1, 3):
        pv, nv = g(x), g(x+1)
        cv = g(x+2) if x+2 < mc else None
        if any(v is not None for v in (pv, nv, cv)):
            add(avail_c, pv, nv, mfr_c, cv)
    return results

def parse_single_block(sheet_name, row, row_idx):
    row = list(row)
    if len(row) < 5: return None
    def g(i): return row[i] if i < len(row) else None
    cat = clean(g(4))
    if not cat or cat in _HEADER_VALS: return None
    name = clean(g(2))
    if not name or name in _HEADER_VALS: return None
    is_av, av_code = parse_avail(g(0))
    return {"catalog": cat, "name": name, "price": parse_price(g(1)),
            "is_available": is_av, "availability": av_code}

def parse_porsche(row, row_idx):
    row = list(row)
    if len(row) < 6: return None
    cat, name = clean(row[5]), clean(row[4])
    if not cat or not name: return None
    is_av, av_code = parse_avail(row[1])
    return {"catalog": cat, "name": name, "price": parse_price(row[0]),
            "is_available": is_av, "availability": av_code}

def parse_suzuki(row, row_idx):
    row = list(row)
    if len(row) < 6: return None
    cat = clean(row[2]); name = clean(row[5]) or clean(row[4])
    if not cat or not name: return None
    is_av, av_code = parse_avail(row[1])
    return {"catalog": cat, "name": name, "price": parse_price(row[0]),
            "is_available": is_av, "availability": av_code}

def parse_renault(row, row_idx):
    # Renault: col[4]=name, col[1]=availability.
    # The original import_parts_db.py used row-index SKUs (RENA-R{ri})
    # because col[5] catalog was None. We mirror that so by_sku matching works.
    row = list(row)
    name = clean(row[4]) if len(row) > 4 else None
    if not name: return None
    is_av, av_code = parse_avail(row[1] if len(row) > 1 else None)
    # Use same row_idx-based key as the original importer (RENA-R{ri} → strip prefix → R{ri})
    return {"catalog": f"R{row_idx}", "name": name, "price": None,
            "is_available": is_av, "availability": av_code}

def parse_citroen_peugeot(row, row_idx):
    # Format F: 7-row header then data.
    # col[0]=model col[1]=avail(כן/לא) col[2]=price(incl VAT)
    # col[3]=warranty col[4]=mfr col[5]=type col[6]=name col[7]=catalog_num
    row = list(row)
    if len(row) < 8: return None
    cat = clean(row[7])
    name = clean(row[6])
    if not cat or not name: return None
    is_av, av_code = parse_avail(row[1])
    return {"catalog": cat, "name": name, "price": parse_price(row[2]),
            "is_available": is_av, "availability": av_code}

def parse_chevrolet(row, row_idx):
    row = list(row)
    if len(row) < 2: return None
    cat = clean(row[1]); name = clean(row[2]) if len(row) > 2 else None
    if not cat or not name: return None
    price = parse_price(row[5]) if len(row) > 5 else None
    stock = clean(row[4]) if len(row) > 4 else None
    is_av, av_code = parse_avail(stock)
    return {"catalog": cat, "name": name, "price": price,
            "is_available": is_av, "availability": av_code}


async def run(brands=None, dry_run=False):
    print(f"{'[DRY RUN] ' if dry_run else ''}Connecting to DB...")
    conn = await asyncpg.connect(DB_URL)
    supplier = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if not supplier:
        print(f"ERROR: Supplier '{SUPPLIER_NAME}' not found!")
        await conn.close(); return
    supplier_id = supplier['id']
    print(f"Supplier id: {supplier_id}")

    wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
    print(f"Opened: {XLSX_FILE}\n")

    all_stats = []
    for sheet_name, (manufacturer, sku_prefix, stype) in SHEET_CONFIG.items():
        if brands and sheet_name.upper() not in [b.upper() for b in brands]:
            continue
        if sheet_name not in wb.sheetnames:
            print(f"  [{sheet_name}] not in workbook, skip"); continue

        print(f"=== {sheet_name} ===")
        stats = dict(sheet=sheet_name, parsed=0, matched=0, not_found=0,
                     sp_ins=0, cat_fixed=0, errors=0)

        # Build SKU/name lookup: key → (part_id_str, base_price)
        db_parts = await conn.fetch(
            "SELECT id, sku, name, base_price FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)",
            manufacturer)
        by_sku, by_name = {}, {}
        for p in db_parts:
            full = p['sku'] or ""
            orig = full[len(sku_prefix):] if full.startswith(sku_prefix) else full
            val = (str(p['id']), float(p['base_price']) if p['base_price'] else None)
            if orig: by_sku[orig.upper()] = val
            if p['name']: by_name[p['name'].strip()] = val
        print(f"  DB: {len(db_parts)} parts")

        # Delete old supplier_parts
        if not dry_run:
            dr = await conn.execute("""
                DELETE FROM supplier_parts WHERE supplier_id=$1
                AND part_id IN (SELECT id FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($2))
            """, supplier_id, manufacturer)
            print(f"  Deleted: {dr}")

        # Parse Excel
        ws = wb[sheet_name]
        # BM/B: start_row=1 (no separate header rows)
        # F (Citroen/Peugeot): start_row=8 (7 metadata rows + 1 column-header row)
        # Others (A/C/D/E): start_row=3 (2-row header, matching import_parts_db.py)
        if stype in ("BM", "B"): start_row = 1
        elif stype == "F": start_row = 8
        else: start_row = 3
        recs = []
        for ri, row in enumerate(ws.iter_rows(min_row=start_row, values_only=True), 1):
            if stype == "BM": recs.extend(parse_multiblock(sheet_name, row, ri))
            elif stype == "B":
                r = parse_single_block(sheet_name, row, ri)
                if r: recs.append(r)
            elif stype == "C":
                r = parse_porsche(row, ri)
                if r: recs.append(r)
            elif stype == "D":
                r = parse_suzuki(row, ri)
                if r: recs.append(r)
            elif stype == "E":
                r = parse_renault(row, ri)
                if r: recs.append(r)
            elif stype == "A":
                r = parse_chevrolet(row, ri)
                if r: recs.append(r)
            elif stype == "F":
                r = parse_citroen_peugeot(row, ri)
                if r: recs.append(r)

        # Deduplicate
        seen, unique = set(), []
        for r in recs:
            k = r["catalog"].upper()
            if k not in seen:
                seen.add(k); unique.append(r)

        stats["parsed"] = len(unique)
        print(f"  Excel: {len(recs)} raw / {len(unique)} unique")

        now = datetime.utcnow()
        for rec in unique:
            lookup = by_sku.get(rec["catalog"].upper()) or by_name.get(rec["name"])
            if not lookup:
                stats["not_found"] += 1; continue
            part_id, base_price_db = lookup
            stats["matched"] += 1
            if dry_run:
                stats["sp_ins"] += 1; continue

            # Use Excel price; fallback to catalog base_price; last resort 100
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
                stats["cat_fixed"] += 1
                await conn.execute("""
                    INSERT INTO supplier_parts
                        (id,supplier_id,part_id,supplier_sku,price_ils,price_usd,
                         is_available,availability,warranty_months,estimated_delivery_days,
                         stock_quantity,min_order_qty,last_checked_at,created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13)
                """, uuid.uuid4(), supplier_id, uuid.UUID(part_id), rec["catalog"],
                    price, price_usd, is_av, av_code,
                    12, 7 if is_av else 14, 10 if is_av else 0, 1, now)
                stats["sp_ins"] += 1
            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 3: print(f"  ERR: {e}")

        all_stats.append(stats)
        print(f"  Result: matched={stats['matched']}, not_found={stats['not_found']}, "
              f"sp_ins={stats['sp_ins']}, cat_fixed={stats['cat_fixed']}, err={stats['errors']}\n")

    wb.close(); await conn.close()
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
