#!/usr/bin/env python3
"""
Fitz (PyMuPDF) based PDF importer for brands whose PDFs have missing MediaBox
and crash pdfplumber. Format: {OEM}{BRAND_HE}\n{description}{PRICE}    {STOCK}
Prices in these PDFs are retail ILS (incl. 17% VAT = מחיר לצרכן).
  importer_price_ils = price / 1.17
  max_price_ils = price
  base_price = price
"""
import asyncio, json, os, re, sys, time, asyncpg
import fitz  # PyMuPDF

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# Prices in these PDFs are consumer retail (incl. VAT)
VAT = 0.18

BRAND_PDFS = [
    ("Mitsubishi", "/app/uploads/MITSUBISHI_20260518_161229.pdf",  "Mitsubishi Israel"),
    ("ORA",        "/app/uploads/ORA_20260518_145353.pdf",         "Great Wall Motors Israel - ORA"),
    ("Smart",      "/app/uploads/SMART_20260518_153424.pdf",       "Smart Israel"),
    ("Genesis",    "/app/uploads/GENESIS_20260518_163814.pdf",     "Genesis Israel"),
    ("Peugeot",    "/app/uploads/PEUGEOT_20260518_231928.pdf",     "PSA Israel - Peugeot"),
]

OEM_RE = re.compile(r'^([A-Z0-9][A-Z0-9\-./]{2,25})', re.IGNORECASE)
PRICE_RE = re.compile(r'([\d,]+\.\d{2})\s+(זמין|לא זמין)')


def extract_parts_fitz(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    results = []
    seen = set()
    current_oem = None
    total = len(doc)
    for page_num, page in enumerate(doc, 1):
        try:
            text = page.get_text()
        except Exception as e:
            print(f"  SKIP page {page_num}: {e}")
            continue
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for line in lines:
            m = OEM_RE.match(line)
            if m:
                oem = m.group(1)
                if re.search(r'\d', oem) and len(oem) >= 4:
                    current_oem = oem
            pm = PRICE_RE.search(line)
            if pm and current_oem and current_oem not in seen:
                price = float(pm.group(1).replace(',', ''))
                if price > 0:
                    seen.add(current_oem)
                    results.append({'oem': current_oem, 'price': price,
                                    'in_stock': pm.group(2) == 'זמין'})
                    current_oem = None
        if page_num % 50 == 0:
            print(f"  Page {page_num}/{total}, parts so far: {len(results):,}")
    doc.close()
    return results


async def import_brand(conn, brand: str, pdf_path: str, importer: str) -> dict:
    print(f"\n[{brand}] {os.path.basename(pdf_path)}")
    if not os.path.exists(pdf_path):
        print(f"  SKIP: file not found"); return {}

    t0 = time.monotonic()
    parts = extract_parts_fitz(pdf_path)
    print(f"  Extracted {len(parts):,} unique parts")
    if not parts:
        return {"brand": brand, "parsed": 0, "updated": 0}

    spec = json.dumps({"importer": importer, "source": f"{brand} IL official price list (fitz)",
                       "vat_included": True, "vat_rate": VAT}, ensure_ascii=False)
    updated = not_found = errors = 0
    for p in parts:
        oem = p['oem']
        retail = p['price']               # incl. VAT
        cost = round(retail / (1 + VAT), 2)
        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2, base_price=$2,
                    specifications=COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                    updated_at=NOW()
                WHERE oem_number=$4 AND manufacturer=$5 AND is_active=true
            """, cost, retail, spec, oem, brand)
            n = int(res.split()[-1])
            if n > 0:
                updated += n
            else:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  err [{oem}]: {e}")

    elapsed = time.monotonic() - t0
    pct = 100 * updated // max(len(parts), 1)
    print(f"  [{brand}] updated={updated:,} not_found={not_found:,} errors={errors} "
          f"match={pct}% ({elapsed:.1f}s)")
    return {"brand": brand, "parsed": len(parts), "updated": updated}


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)
    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()
    results = []
    try:
        for brand, pdf_path, importer in BRAND_PDFS:
            r = await import_brand(conn, brand, pdf_path, importer)
            results.append(r)

        print(f"\n{'='*55}")
        print(f"FITZ PDF IMPORT SUMMARY — {time.monotonic()-t0:.0f}s")
        print(f"{'='*55}")
        total_p = total_u = 0
        for r in results:
            if r:
                print(f"  {r.get('brand',''):<15} parsed={r.get('parsed',0):>6,}  updated={r.get('updated',0):>6,}")
                total_p += r.get('parsed', 0)
                total_u += r.get('updated', 0)
        print(f"  {'TOTAL':<15} parsed={total_p:>6,}  updated={total_u:>6,}")

        # Post-run coverage
        row = await conn.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE is_active AND importer_price_ils>0) il FROM parts_catalog")
        print(f"\n  Global IL priced: {row['il']:,}")
        for brand, _, _ in BRAND_PDFS:
            r2 = await conn.fetchrow(
                "SELECT COUNT(*) total, COUNT(*) FILTER (WHERE importer_price_ils>0) priced "
                "FROM parts_catalog WHERE manufacturer=$1 AND is_active=true", brand)
            if r2 and r2['total'] > 0:
                print(f"  {brand:<15}: {r2['priced']:,}/{r2['total']:,} ({100*r2['priced']//(r2['total'] or 1)}%)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
