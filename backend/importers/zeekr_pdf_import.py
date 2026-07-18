#!/usr/bin/env python3
"""
Zeekr IL price list PDF importer (fitz-based).
PDF column: מחיר לא כולל מע"מ (price excl. 18% VAT).
  importer_price_ils = price         (cost, already excl. VAT)
  max_price_ils      = price * 1.18  (consumer ref incl. VAT)
  base_price         = price * 1.45  (45% margin — policy)
"""
import asyncio, json, os, re, sys, time, asyncpg
import fitz

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

HEADERS = {
    'ט"מק', 'תיאור פריט', 'האם קיים במלאי',
    'מחיר', 'מתאים לדגמים', 'סיווג מוצר', 'מע"מ',
    'מ"ח לא כולל', 'מחיר בש',
}
OEM_RE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9\-./]{4,30})(?:\s|$)')
PRICE_RE = re.compile(r'^(כן|לא)\s*([\d,]+\.?\d*)')


def normalize_line(raw: str) -> str:
    line = raw.replace('\xa0', ' ')
    # "ZE - xxxx" → "ZE-xxxx" (PDF sometimes adds spaces around hyphen in ZE- prefix)
    line = re.sub(r'\bZE\s+\-\s+', 'ZE-', line)
    return line.strip()


def extract_zeekr(pdf_path: str) -> list:
    doc = fitz.open(pdf_path)
    parts: list = []
    seen: set = set()
    total = len(doc)
    current_oem = None

    for page_num, page in enumerate(doc, 1):
        try:
            text = page.get_text()
        except Exception as e:
            print(f"  SKIP page {page_num}: {e}")
            continue

        for raw_line in text.split('\n'):
            line = normalize_line(raw_line)
            if not line:
                continue

            # Skip header rows
            if any(h in line for h in HEADERS):
                current_oem = None
                continue

            # Price/stock line: starts with כן or לא followed by numeric price
            pm = PRICE_RE.match(line)
            if pm:
                try:
                    price = float(pm.group(2).replace(',', ''))
                except ValueError:
                    current_oem = None
                    continue
                if current_oem and current_oem not in seen and price > 0:
                    seen.add(current_oem)
                    parts.append({'oem': current_oem, 'price': price})
                current_oem = None
                continue

            # OEM line: starts with alphanumeric code, must contain at least one digit
            om = OEM_RE.match(line)
            if om:
                candidate = om.group(1)
                if re.search(r'\d', candidate):
                    current_oem = candidate

        if page_num % 50 == 0 or page_num == total:
            print(f"  page {page_num}/{total}, extracted: {len(parts):,}")

    doc.close()
    return parts


async def build_oem_lookup(conn) -> dict:
    """Return normalized→canonical mapping for all Zeekr OEMs in DB."""
    rows = await conn.fetch(
        "SELECT DISTINCT oem_number FROM parts_catalog WHERE manufacturer='Zeekr' AND is_active"
    )
    lookup: dict = {}
    for r in rows:
        db_oem = r['oem_number']
        lookup[db_oem.upper()] = db_oem
        # Hyphen-stripped form for fuzzy matching
        norm = db_oem.upper().replace('-', '').replace(' ', '')
        if norm not in lookup:
            lookup[norm] = db_oem
    return lookup


def find_db_oem(pdf_oem: str, lookup: dict):
    upper = pdf_oem.upper().replace(' ', '')
    norm = upper.replace('-', '')
    for key in (upper, norm, 'ZE' + norm,
                norm[2:] if norm.startswith('ZE') else None):
        if key and key in lookup:
            return lookup[key]
    return None


async def update_db(conn, parts: list, oem_lookup: dict) -> int:
    spec = json.dumps({
        "importer": "Zeekr Israel",
        "source": "Zeekr IL official price list",
        "vat_included": False,
        "vat_rate": VAT,
    }, ensure_ascii=False)

    updated = no_db_match = not_found = errors = 0
    for p in parts:
        db_oem = find_db_oem(p['oem'], oem_lookup)
        if not db_oem:
            no_db_match += 1
            continue

        cost = p['price']
        max_price = round(cost * (1 + VAT), 2)
        selling = round(cost * 1.45, 2)

        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils = $1,
                    max_price_ils      = $2,
                    base_price         = $3,
                    specifications     = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                    updated_at         = NOW()
                WHERE oem_number = $5 AND manufacturer = 'Zeekr' AND is_active = true
            """, cost, max_price, selling, spec, db_oem)
            n = int(res.split()[-1])
            updated += n
            if n == 0:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  err [{db_oem}]: {e}")

    pct = 100 * updated // max(len(parts), 1)
    print(f"  [Zeekr] updated={updated:,} no_db_match={no_db_match:,} not_found={not_found:,} errors={errors} match={pct}%")
    return updated


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()

    PDFS = [
        "/app/uploads/ZEEKER001_20260519_131930.pdf",
        "/app/uploads/ZEEKER001_20260519_160740.pdf",
        "/app/uploads/ZEEKER001_20260526_103011.pdf",
        "/app/uploads/ZEEKER001_20260526_103745.pdf",
    ]

    all_parts: dict = {}
    for pdf_path in PDFS:
        if not os.path.exists(pdf_path):
            print(f"  SKIP (not found): {pdf_path}")
            continue
        print(f"\n[Zeekr] {os.path.basename(pdf_path)}")
        extracted = extract_zeekr(pdf_path)
        print(f"  Extracted {len(extracted):,} from this PDF")
        for p in extracted:
            all_parts[p['oem']] = p  # latest price wins on duplicate OEM

    unique_parts = list(all_parts.values())
    print(f"\n[Zeekr] Total unique OEMs across all PDFs: {len(unique_parts):,}")

    print("[Zeekr] Loading DB OEM lookup...")
    oem_lookup = await build_oem_lookup(conn)
    print(f"  {len(oem_lookup):,} lookup entries")

    print("[Zeekr] Updating DB...")
    await update_db(conn, unique_parts, oem_lookup)

    r = await conn.fetchrow(
        "SELECT COUNT(*) total, COUNT(*) FILTER (WHERE importer_price_ils>0) priced "
        "FROM parts_catalog WHERE manufacturer='Zeekr' AND is_active"
    )
    pct = 100 * r['priced'] // max(r['total'], 1)
    print(f"\n[Zeekr] Coverage: {r['priced']:,}/{r['total']:,} ({pct}%)")
    print(f"=== DONE ({time.monotonic()-t0:.0f}s) ===")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
