#!/usr/bin/env python3
"""
Renault IL price list PDF importer (fitz-based, Karaso Motors format).
PDF format (3 or 2 lines per part):
  Line A: dd/mm/yyyy HH:MM:SS + stock status + type
  Line B: Hebrew description + price (incl. 18% VAT)
  Line C: OEM number (starts with RE, alone on line)
  — sometimes Line A+B are merged into one line —

Prices = מחיר לצרכן (consumer retail incl. 18% VAT):
  importer_price_ils = price / 1.18  (cost excl. VAT)
  max_price_ils      = price          (consumer ref)
  base_price         = cost * 1.45   (45% margin — policy)
"""
import asyncio, json, os, re, sys, time, asyncpg
import fitz

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

# OEM: starts with RE followed by 6-20 alphanumeric chars, alone on line
OEM_RE = re.compile(r'^(RE[A-Z0-9]{6,20})$', re.IGNORECASE)
# Price: last decimal number at end of the description+price line
PRICE_RE = re.compile(r'(\d{1,7}(?:,\d{3})*\.\d{2})\s*$')

SKIP_HEADERS = {'מחיר', 'פריט', 'תיאור', 'סוג מוצר'}


def extract_renault(pdf_path: str) -> list:
    doc = fitz.open(pdf_path)
    parts: list = []
    seen: set = set()
    total = len(doc)
    prev_line = None

    for page_num, page in enumerate(doc, 1):
        try:
            text = page.get_text()
        except Exception as e:
            print(f"  SKIP page {page_num}: {e}")
            continue

        for raw_line in text.split('\n'):
            line = raw_line.strip()
            if not line:
                continue

            # Check if this is an OEM line (RE + digits only, nothing else)
            om = OEM_RE.match(line)
            if om:
                oem = om.group(1).upper()
                if prev_line and oem not in seen:
                    pm = PRICE_RE.search(prev_line)
                    if pm:
                        price_str = pm.group(1).replace(',', '')
                        try:
                            price = float(price_str)
                        except ValueError:
                            price = 0
                        if price > 0:
                            seen.add(oem)
                            parts.append({'oem': oem, 'price': price})
                # Reset prev after consuming OEM
                prev_line = None
                continue

            # Skip pure header/label lines
            if line in SKIP_HEADERS:
                continue

            prev_line = line

        if page_num % 50 == 0 or page_num == total:
            print(f"  page {page_num}/{total}, extracted: {len(parts):,}")

    doc.close()
    return parts


async def update_db(conn, parts: list) -> int:
    spec = json.dumps({
        "importer": "Karaso Motors",
        "source": "Renault IL official price list",
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
                WHERE oem_number = $5 AND manufacturer = 'Renault' AND is_active = true
            """, cost, retail, selling, spec, p['oem'])
            n = int(res.split()[-1])
            updated += n
            if n == 0:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  err [{p['oem']}]: {e}")

    pct = 100 * updated // max(len(parts), 1)
    print(f"  [Renault] updated={updated:,} not_found={not_found:,} errors={errors} match={pct}%")
    return updated


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()

    PDFS = ["/app/uploads/RENAULT_20260518_223327.pdf"]

    all_parts: dict = {}
    for pdf_path in PDFS:
        if not os.path.exists(pdf_path):
            print(f"  SKIP (not found): {pdf_path}")
            continue
        print(f"\n[Renault] {os.path.basename(pdf_path)}")
        extracted = extract_renault(pdf_path)
        print(f"  Extracted {len(extracted):,} parts")
        for p in extracted:
            all_parts[p['oem']] = p

    unique_parts = list(all_parts.values())
    print(f"\n[Renault] Total unique OEMs: {len(unique_parts):,}")

    if unique_parts:
        print("[Renault] Updating DB...")
        await update_db(conn, unique_parts)

    r = await conn.fetchrow(
        "SELECT COUNT(*) total, COUNT(*) FILTER (WHERE importer_price_ils>0) priced "
        "FROM parts_catalog WHERE manufacturer='Renault' AND is_active"
    )
    pct = 100 * r['priced'] // max(r['total'], 1)
    print(f"\n[Renault] Coverage: {r['priced']:,}/{r['total']:,} ({pct}%)")
    print(f"=== DONE ({time.monotonic()-t0:.0f}s) ===")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
