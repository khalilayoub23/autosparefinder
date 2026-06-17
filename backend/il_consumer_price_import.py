#!/usr/bin/env python3
"""
Universal Israeli importer price import for Hebrew-format xlsx files.
Handles IL importer catalog exports with 'מחיר לצרכן' (consumer price incl. VAT).

Column format (auto-detected by position or header):
  Col 0: OEM number (מספר קטלוגי)
  Col 1: Hebrew description (תאור)
  Col 2: Part type (מקורי/aftermarket)
  Col 3: Stock status
  Col 4: Consumer price ILS incl. VAT (מחיר לצרכן)
  Col 5: Warranty
  Col 6: Vehicle model

Pricing: consumer_price is incl. 17% VAT
  importer_price_ils = consumer_price / 1.17
  max_price_ils      = consumer_price
  base_price         = (consumer_price / 1.17) * 1.45

Usage:
  python3 il_consumer_price_import.py --file /app/uploads/chevrolet.xlsx --brand Chevrolet
  python3 il_consumer_price_import.py --file /app/uploads/citroen.xlsx --brand Citroen
"""
import asyncio, os, sys, time, json, argparse
import asyncpg

try:
    import openpyxl
except ImportError:
    print("pip install openpyxl"); sys.exit(1)

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

def load_xlsx(path, brand):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    # Find the header row (scan first 15 rows for price/OEM indicators)
    header_row = 0
    data_start = 1
    for i, row in enumerate(rows[:15]):
        row_str = " ".join(str(c or "") for c in row)
        non_null = sum(1 for c in row if c is not None and str(c).strip())
        # Row is a header if it has multiple filled cells AND contains known column name terms
        if non_null >= 3 and (
            "קטלוגי" in row_str or "oem" in row_str.lower() or
            ('מק"ט' in row_str and "מחיר" in row_str) or
            ('מחיר' in row_str and 'מצאי' in row_str)
        ):
            header_row = i
            data_start = i + 1
            break

    headers = [str(h or "").strip() for h in rows[header_row]]
    hl = [h.lower() for h in headers]

    def idx(*names, exclude=None):
        # Prefer shorter/more-exact matches first
        for n in names:
            for i, h in enumerate(hl):
                if n in h:
                    if exclude and any(ex in h for ex in exclude):
                        continue
                    return i
        return -1

    # Try to find columns by header text (Hebrew + English)
    # For OEM: look for catalog/OEM terms but exclude description columns
    i_oem   = idx("קטלוגי", "oem", "sku", "מספר", exclude=["תיאור", "description"])
    # "מק\"ט" appears in both description and OEM columns; pick the one without "תיאור"
    if i_oem < 0:
        i_oem = idx('מק"ט', "מקִט", exclude=["תיאור"])
    i_name  = idx("תיאור", "תאור", "שם", "name")
    i_price = idx("לצרכן", "כולל מע", "מחיר", "price")
    i_type  = idx("סוג", "type")

    # Fallback to positional if headers not found
    if i_oem < 0:  i_oem = 0
    if i_name < 0: i_name = 1
    if i_price < 0: i_price = 4  # standard position for consumer price

    print(f"Columns: OEM={i_oem} NAME={i_name} PRICE={i_price} TYPE={i_type}")

    parts = []
    for row in rows[data_start:]:
        def cell(i):
            return row[i] if 0 <= i < len(row) and row[i] is not None else None

        oem   = str(cell(i_oem) or "").strip().rstrip()
        name  = str(cell(i_name) or "").strip()
        price_raw = cell(i_price)

        if not oem or oem == "-" or oem == "":
            continue

        price = None
        if price_raw is not None:
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                pass

        if price is None or price <= 0:
            continue

        # Consumer price is incl. VAT — extract importer cost
        cost    = round(price / (1 + VAT), 2)
        retail  = round(price, 2)          # consumer price = max_price_ils
        selling = round(cost * 1.45, 2)

        parts.append({
            "oem": oem,
            "name": name,
            "cost": cost,
            "retail": retail,
            "selling": selling,
        })

    wb.close()
    print(f"Parsed {len(parts):,} parts from {os.path.basename(path)}")
    return parts


async def run(brand, xlsx_path):
    parts = load_xlsx(xlsx_path, brand)
    if not parts:
        print("No parts parsed"); return

    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    spec = json.dumps({
        "importer": f"{brand} IL official (consumer price)",
        "source": os.path.basename(xlsx_path),
        "vat_rate": VAT, "vat_included": False,
        "note": "price excl. VAT derived from consumer price"
    }, ensure_ascii=False)

    updated = inserted = skipped = 0
    for p in parts:
        oem     = p["oem"]
        cost    = p["cost"]
        retail  = p["retail"]
        selling = p["selling"]

        res = await conn.execute("""
            UPDATE parts_catalog SET
                importer_price_ils = $1,
                max_price_ils      = $2,
                base_price         = $3,
                specifications     = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                updated_at         = NOW()
            WHERE oem_number = $5 AND manufacturer = $6 AND is_active = true
        """, cost, retail, selling, spec, oem, brand)
        n = int(res.split()[-1])
        if n > 0:
            updated += n
        else:
            try:
                await conn.execute("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, name_he, manufacturer, category,
                        base_price, importer_price_ils, max_price_ils, min_price_ils,
                        part_type, part_condition, is_active,
                        needs_oem_lookup, master_enriched, specifications,
                        created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(), $1, $1, $2, $2, $3, 'accessories',
                        $4, $5, $6, $6,
                        'Original', 'new', true,
                        true, false, $7::jsonb,
                        NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        importer_price_ils = EXCLUDED.importer_price_ils,
                        max_price_ils      = EXCLUDED.max_price_ils,
                        base_price         = EXCLUDED.base_price,
                        specifications     = EXCLUDED.specifications,
                        updated_at         = NOW()
                """, oem, p["name"], brand, selling, cost, retail, spec)
                inserted += 1
            except Exception:
                skipped += 1

    elapsed = time.monotonic() - t0
    print(f"[{brand}] Done {elapsed:.0f}s: updated={updated:,} inserted={inserted:,} skipped={skipped:,}")

    r = await conn.fetchrow(
        "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
        "FROM parts_catalog WHERE manufacturer=$1 AND is_active=true", brand)
    print(f"[{brand}] Coverage: {r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")
    await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--brand", required=True)
    args = ap.parse_args()
    asyncio.run(run(args.brand, args.file))
