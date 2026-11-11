#!/usr/bin/env python3
"""
Script: il_consumer_price_import.py
Purpose: Universal importer for Israeli importer price lists in the Hebrew xlsx format with a 'מחיר לצרכן' column.
Process: parse xlsx -> price_policy-based cost/reference from the price AND an EXPLICIT VAT basis -> UPDATE/INSERT
         parts_catalog with the source price + basis + evidence kept in specifications.
Data Imported/Modified: parts_catalog (importer_price_ils, max_price_ils, base_price, specifications).
Data Sources: the importer's price list (xlsx).
Missing Data Delegation: none.
Last Updated: 2026-11-11

VAT BASIS IS NEVER ASSUMED (FIXES_TRACKER #151 / #130 / 2026-11-11 closure). The column header 'מחיר לצרכן' does not say
whether VAT is included (BDV's 'recommended consumer price' EXCLUDES it; Champion/Colmobil include it). The caller must pass
--vat-basis inclusive|exclusive AND --evidence "<where the source states it>"; without both the script refuses to run.
  inclusive: cost = price / (1 + VAT_RATE); max_price_ils = price
  exclusive: cost = price;                  max_price_ils = price * (1 + VAT_RATE)
  base_price = cost * MARGIN in both cases (margin once). VAT_RATE / MARGIN come from price_policy.

Columns: 0 OEM (מספר קטלוגי) | 1 description | 2 type | 3 stock | 4 consumer price ILS | 5 warranty | 6 vehicle model

Usage:
  python3 il_consumer_price_import.py --file /app/uploads/x.xlsx --brand X --vat-basis exclusive --evidence "<quote + url>"
"""
import asyncio, os, sys, time, json, argparse
import asyncpg

# ONE category source of truth. This INSERT hardcoded 'accessories' —
# a REAL category, so the self-healing categorizer (which only revisits
# 'כללי') would never correct those parts.
from category_map import categorize_on_ingest

try:
    import openpyxl
except ImportError:
    print("pip install openpyxl"); sys.exit(1)

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
from price_policy import VAT_RATE as VAT, MARGIN


def price_fields(price, vat_basis):
    """The ONLY rule of this importer (tested): returns (cost_ex_vat, consumer_reference, base_price)."""
    if vat_basis == "inclusive":
        cost, retail = round(price / (1 + VAT), 2), round(price, 2)
    elif vat_basis == "exclusive":
        cost, retail = round(price, 2), round(price * (1 + VAT), 2)
    else:
        raise ValueError("vat_basis must be 'inclusive' or 'exclusive' - it is never assumed")
    return cost, retail, round(cost * MARGIN, 2)

def load_xlsx(path, brand, vat_basis):
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

        cost, retail, selling = price_fields(price, vat_basis)

        parts.append({
            "source_price": round(price, 2),
            "oem": oem,
            "name": name,
            "cost": cost,
            "retail": retail,
            "selling": selling,
        })

    wb.close()
    print(f"Parsed {len(parts):,} parts from {os.path.basename(path)}")
    return parts


async def run(brand, xlsx_path, vat_basis, evidence):
    parts = load_xlsx(xlsx_path, brand, vat_basis)
    if not parts:
        print("No parts parsed"); return

    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    updated = inserted = skipped = 0
    for p in parts:
        oem     = p["oem"]
        cost    = p["cost"]
        retail  = p["retail"]
        selling = p["selling"]
        spec = json.dumps({
            "importer": f"{brand} IL official (consumer price)",
            "source": os.path.basename(xlsx_path),
            "source_price_ils": p["source_price"], "vat_basis": vat_basis, "vat_basis_evidence": evidence,
            "vat_rate": VAT, "vat_included": vat_basis == "inclusive",
        }, ensure_ascii=False)

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
                        gen_random_uuid(), $1, $1, $2, $2, $3, $8,
                        $4, $5, $6, $6,
                        'original', 'new', true,
                        true, false, $7::jsonb,
                        NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                        max_price_ils      = EXCLUDED.max_price_ils,
                        base_price         = EXCLUDED.base_price,
                        specifications     = EXCLUDED.specifications,
                        updated_at         = NOW()
                """, oem, p["name"], brand, selling, cost, retail, spec,
                    categorize_on_ingest(name=p["name"]))
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
    ap.add_argument("--vat-basis", choices=("inclusive", "exclusive"), required=True)
    ap.add_argument("--evidence", required=True, help="where the source states the VAT basis (quote + URL)")
    args = ap.parse_args()
    if len(args.evidence.strip()) < 10:
        sys.exit("refusing to run: --evidence must name the source statement of the VAT basis")
    asyncio.run(run(args.brand, args.file, args.vat_basis, args.evidence.strip()))
