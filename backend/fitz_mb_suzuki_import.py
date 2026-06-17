#!/usr/bin/env python3
"""
Fitz-based importer for Mercedes-Benz and Suzuki PDFs (pdfplumber crashes on these).
Prices = מחיר לצרכן (consumer retail incl. 18% VAT):
  importer_price_ils = price / 1.18  (cost excl. VAT)
  max_price_ils      = price          (consumer ref)
  base_price         = cost * 1.45   (45% margin — policy)
"""
import asyncio, json, os, re, sys, time, asyncpg
import fitz

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

# ── Mercedes: same format as other fitz PDFs ({OEM}{BRAND}\n{desc}{price}{stock})
MB_OEM_RE = re.compile(r'^([A-Z0-9][A-Z0-9\-./]{2,30})', re.IGNORECASE)
PRICE_STOCK_RE = re.compile(r'([\d,]+\.\d{2})\s*(זמין|לא זמין)')

# ── Suzuki: {PRICE}{STOCK} on one line, P.{OEM} at end of next line
SUZUKI_PRICE_LINE = re.compile(r'^([\d,]+\.\d{2})(זמין|לא זמין)')
SUZUKI_OEM_RE = re.compile(r'P\.([A-Z0-9.\-]{5,30})$')


def extract_mercedes(pdf_path: str) -> list:
    doc = fitz.open(pdf_path)
    results = []
    seen = set()
    current_oem = None
    total = len(doc)
    for page_num, page in enumerate(doc, 1):
        try:
            text = page.get_text()
        except Exception as e:
            print(f"  SKIP page {page_num}: {e}"); continue
        for line in (l.strip() for l in text.split('\n') if l.strip()):
            m = MB_OEM_RE.match(line)
            if m:
                oem = m.group(1)
                if re.search(r'\d', oem) and len(oem) >= 4:
                    current_oem = oem
            pm = PRICE_STOCK_RE.search(line)
            if pm and current_oem and current_oem not in seen:
                price = float(pm.group(1).replace(',', ''))
                if price > 0:
                    seen.add(current_oem)
                    results.append({'oem': current_oem, 'price': price})
                    current_oem = None
        if page_num % 100 == 0:
            print(f"  MB page {page_num}/{total}, parts: {len(results):,}")
    doc.close()
    return results


def extract_suzuki(pdf_path: str) -> list:
    doc = fitz.open(pdf_path)
    results = []
    seen = set()
    pending_price = None
    total = len(doc)
    for page_num, page in enumerate(doc, 1):
        try:
            lines = [l.strip() for l in page.get_text().split('\n') if l.strip()]
        except Exception as e:
            print(f"  SKIP page {page_num}: {e}"); continue
        for line in lines:
            pm = SUZUKI_PRICE_LINE.match(line)
            if pm:
                pending_price = float(pm.group(1).replace(',', ''))
            elif pending_price is not None:
                om = SUZUKI_OEM_RE.search(line)
                if om:
                    oem = 'P.' + om.group(1)
                    if oem not in seen:
                        seen.add(oem)
                        results.append({'oem': oem, 'price': pending_price})
                    pending_price = None
                elif not line.startswith('מקורי') and not line.startswith('חליפי'):
                    pending_price = None  # reset if no OEM found after 2 non-type lines
        if page_num % 100 == 0:
            print(f"  Suzuki page {page_num}/{total}, parts: {len(results):,}")
    doc.close()
    return results


async def update_db(conn, brand: str, parts: list, importer: str) -> int:
    if not parts:
        print(f"  [{brand}] no parts to import"); return 0
    spec = json.dumps({"importer": importer, "source": f"{brand} IL official price list",
                       "vat_included": True, "vat_rate": VAT}, ensure_ascii=False)
    updated = not_found = errors = 0
    for p in parts:
        retail = p['price']
        cost = round(retail / (1 + VAT), 2)
        selling = round(cost * 1.45, 2)
        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils=$1, max_price_ils=$2, base_price=$3,
                    specifications=COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                    updated_at=NOW()
                WHERE oem_number=$5 AND manufacturer=$6 AND is_active=true
            """, cost, retail, selling, spec, p['oem'], brand)
            n = int(res.split()[-1])
            updated += n
            if n == 0:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3: print(f"  err [{p['oem']}]: {e}")
    pct = 100 * updated // max(len(parts), 1)
    print(f"  [{brand}] updated={updated:,} not_found={not_found:,} errors={errors} match={pct}%")
    return updated


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)
    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()
    try:
        # ── Mercedes-Benz (all PDF files)
        mb_all: list = []
        for mb_pdf in ["MERCEDES-BENZ_20260518_154959.pdf", "MERCEDES-BENZ_20260518_164659.pdf"]:
            path = f"/app/uploads/{mb_pdf}"
            if os.path.exists(path):
                print(f"\n[Mercedes-Benz] {mb_pdf}")
                parts_batch = extract_mercedes(path)
                print(f"  Extracted {len(parts_batch):,} parts")
                mb_all.extend(p for p in parts_batch if p['oem'] not in {x['oem'] for x in mb_all})
        print(f"  [Mercedes-Benz] total unique parts: {len(mb_all):,}")
        mb_updated = await update_db(conn, "Mercedes-Benz", mb_all, "Daimler Israel")

        # ── Suzuki (all PDF files)
        sz_all: list = []
        for sz_pdf in ["SUZUKI_20260518_170146.pdf", "SUZUKI_20260518_183928.pdf",
                       "SUZUKI_20260518_214247.pdf", "SUZUKI_20260518_215329.pdf",
                       "SUZUKI_20260518_220508.pdf"]:
            path = f"/app/uploads/{sz_pdf}"
            if os.path.exists(path):
                print(f"\n[Suzuki] {sz_pdf}")
                parts_batch = extract_suzuki(path)
                print(f"  Extracted {len(parts_batch):,} parts")
                sz_all.extend(p for p in parts_batch if p['oem'] not in {x['oem'] for x in sz_all})
        print(f"  [Suzuki] total unique parts: {len(sz_all):,}")
        sz_updated = await update_db(conn, "Suzuki", sz_all, "Suzuki Israel")

        print(f"\n=== DONE ({time.monotonic()-t0:.0f}s) ===")
        for brand in ["Mercedes-Benz", "Suzuki"]:
            r = await conn.fetchrow(
                "SELECT COUNT(*) total, COUNT(*) FILTER (WHERE importer_price_ils>0) priced "
                "FROM parts_catalog WHERE manufacturer=$1 AND is_active=true", brand)
            print(f"  {brand}: {r['priced']:,}/{r['total']:,} ({100*r['priced']//(r['total'] or 1)}%)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
