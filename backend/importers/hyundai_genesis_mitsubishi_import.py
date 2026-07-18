#!/usr/bin/env python3
"""
Fitz-based importer for Hyundai, Genesis, and Mitsubishi IL price list PDFs.

PDF format (all three brands):
  Line 1: {OEM}{brand_he}                 (e.g. "00306ACKITיונדאי")
  Line 2: {description}{price}  {stock}   (e.g. "קיט קומפרסור645.00        זמין")

Prices = מחיר לצרכן (consumer retail incl. 18% VAT):
  importer_price_ils = price / 1.18
  max_price_ils      = price
  base_price         = importer_price_ils * 1.45
"""
import asyncio, json, os, re, sys, time, asyncpg
import fitz

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

BRAND_MAP = {
    "יונדאי":    "Hyundai",
    "מיצובישי":  "Mitsubishi",
    "ג'נסיס":    "Genesis",
    "ג נסיס":    "Genesis",
}

PRICE_STOCK_RE = re.compile(r'([\d,]+\.\d{2})\s+(זמין|לא זמין)')
OEM_RE = re.compile(r'^([A-Z0-9][A-Z0-9\-./]{3,30})')

HEADERS = {"מספר קטלוגי", "מותג", "תיאור החלק", "מחיר לצרכן", "זמינות מלאי"}


def extract_parts(pdf_path: str, brand_name: str) -> list:
    doc = fitz.open(pdf_path)
    parts: list = []
    seen: set = set()
    total = len(doc)

    for page_num, page in enumerate(doc, 1):
        try:
            text = page.get_text()
        except Exception as e:
            print(f"  SKIP page {page_num}: {e}")
            continue

        lines = [l.strip() for l in text.split('\n') if l.strip()]
        current_oem = None

        for line in lines:
            # Skip header lines
            if line in HEADERS or any(h in line for h in HEADERS):
                continue

            # Check if line contains price + stock — extract price
            pm = PRICE_STOCK_RE.search(line)
            if pm:
                price_str = pm.group(1).replace(',', '')
                price = float(price_str)

                # Check if OEM is on this same line (format: OEM+brand+desc+price+stock)
                if current_oem is None:
                    om = OEM_RE.match(line)
                    if om:
                        current_oem = om.group(1)

                if current_oem and current_oem not in seen and price > 0:
                    seen.add(current_oem)
                    parts.append({'oem': current_oem, 'price': price})
                current_oem = None
                continue

            # Check if this line starts with OEM followed by brand marker
            om = OEM_RE.match(line)
            if om:
                candidate_oem = om.group(1)
                rest = line[len(candidate_oem):]
                # Verify it's followed by a known brand Hebrew name (or end of line)
                is_brand_line = any(bh in rest for bh in BRAND_MAP)
                if is_brand_line:
                    current_oem = candidate_oem

        if page_num % 100 == 0 or page_num == total:
            print(f"  {brand_name} page {page_num}/{total}, parts: {len(parts):,}")

    doc.close()
    return parts


async def update_db(conn, brand: str, parts: list, importer: str) -> int:
    if not parts:
        print(f"  [{brand}] no parts to import")
        return 0
    spec = json.dumps({
        "importer": importer,
        "source": f"{brand} IL official price list",
        "vat_included": True,
        "vat_rate": VAT,
    }, ensure_ascii=False)

    updated = not_found = errors = 0
    for p in parts:
        retail = p['price']
        cost = round(retail / (1 + VAT), 2)
        selling = round(cost * 1.45, 2)
        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils = $1,
                    max_price_ils      = $2,
                    base_price         = $3,
                    specifications     = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                    updated_at         = NOW()
                WHERE oem_number = $5 AND manufacturer = $6 AND is_active = true
            """, cost, retail, selling, spec, p['oem'], brand)
            n = int(res.split()[-1])
            updated += n
            if n == 0:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  err [{p['oem']}]: {e}")

    pct = 100 * updated // max(len(parts), 1)
    print(f"  [{brand}] updated={updated:,} not_found={not_found:,} errors={errors} match={pct}%")
    return updated


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)
    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()

    IMPORTS = [
        # (pdf_path, brand_name, importer_name)
        ("/app/uploads/HYUNDAI_20260518_185642.pdf", "Hyundai", "Hyundai Israel"),
        ("/app/uploads/HYUNDAI_20260518_191141.pdf", "Hyundai", "Hyundai Israel"),
        ("/app/uploads/HYUNDAI_20260518_192208.pdf", "Hyundai", "Hyundai Israel"),
        ("/app/uploads/HYUNDAI_20260518_192233.pdf", "Hyundai", "Hyundai Israel"),
        ("/app/uploads/HYUNDAI_20260518_194204.pdf", "Hyundai", "Hyundai Israel"),
        ("/app/uploads/GENESIS_20260518_163814.pdf", "Genesis", "Hyundai Israel (Genesis)"),
        ("/app/uploads/MITSUBISHI_20260518_161229.pdf", "Mitsubishi", "Mitsubishi Israel"),
        ("/app/uploads/MITSUBISHI_20260518_161251.pdf", "Mitsubishi", "Mitsubishi Israel"),
        ("/app/uploads/MITSUBISHI_20260518_162714.pdf", "Mitsubishi", "Mitsubishi Israel"),
    ]

    brand_parts: dict = {}
    brand_seen: dict = {}

    for pdf_path, brand, importer in IMPORTS:
        if not os.path.exists(pdf_path):
            print(f"  SKIP (not found): {pdf_path}")
            continue
        print(f"\n[{brand}] {os.path.basename(pdf_path)}")
        parts = extract_parts(pdf_path, brand)
        print(f"  Extracted {len(parts):,} parts from PDF")

        if brand not in brand_parts:
            brand_parts[brand] = []
            brand_seen[brand] = set()

        added = 0
        for p in parts:
            if p['oem'] not in brand_seen[brand]:
                brand_seen[brand].add(p['oem'])
                brand_parts[brand].append(p)
                added += 1
        print(f"  Added {added:,} unique (total {len(brand_parts[brand]):,})")

    try:
        print(f"\n=== Starting DB updates ===")
        for brand, parts in brand_parts.items():
            importer_name = next(imp for _, b, imp in IMPORTS if b == brand)
            print(f"\n[{brand}] Updating {len(parts):,} unique parts...")
            await update_db(conn, brand, parts, importer_name)

        print(f"\n=== DONE ({time.monotonic()-t0:.0f}s) ===")
        for brand in brand_parts:
            r = await conn.fetchrow(
                "SELECT COUNT(*) total, COUNT(*) FILTER (WHERE importer_price_ils>0) priced "
                "FROM parts_catalog WHERE manufacturer=$1 AND is_active=true", brand)
            print(f"  {brand}: {r['priced']:,}/{r['total']:,} ({100*r['priced']//(r['total'] or 1)}%)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
