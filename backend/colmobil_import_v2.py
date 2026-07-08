#!/usr/bin/env python3
"""
Colmobil IL PDF importer — all brands (Hyundai, Mitsubishi, Genesis, ORA, Smart, JAECOO).

PDFs are downloadable from https://www.colmobil.co.il/spareparts/ (no auth):
  https://prodmedia.colmobil.co.il/spare-parts/HYU.PDF
  https://prodmedia.colmobil.co.il/spare-parts/MIT.PDF
  https://prodmedia.colmobil.co.il/spare-parts/GEN.PDF
  https://prodmedia.colmobil.co.il/spare-parts/ORA.PDF
  https://prodmedia.colmobil.co.il/spare-parts/SMART.PDF
  https://prodmedia.colmobil.co.il/spare-parts/JAECOO.PDF

PDF format (all brands):
  Line: {OEM}{brand_he}          e.g. "00306ACKITיונדאי"
  Next: {description}{price}  {stock}   e.g. "קיט קומפרסור645.00        זמין"
  OR: both on same line

Prices = מחיר לצרכן (consumer retail incl. 18% VAT):
  importer_price_ils = price / 1.18
  max_price_ils      = price
  base_price         = importer_price_ils * 1.45

Usage:
  docker exec autospare_backend python3 /app/colmobil_import_v2.py
  docker exec autospare_backend python3 /app/colmobil_import_v2.py --brands hyundai,mitsubishi
"""
import asyncio, json, os, re, sys, time, uuid, asyncpg, urllib.request
import fitz
from pathlib import Path

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18
MARGIN = 0.45

PDF_DIR = Path("/app/state")
LOG_PATH = Path("/app/state/logs/colmobil_import_v2.log")
Path("/app/state/logs").mkdir(parents=True, exist_ok=True)

# Map Hebrew brand names in PDF → (English manufacturer name, importer name)
BRAND_MAP = {
    "יונדאי":    ("Hyundai",     "Colmobil - Hyundai Israel"),
    "מיצובישי":  ("Mitsubishi",  "Colmobil - Mitsubishi Israel"),
    "ג'נסיס":    ("Genesis",     "Colmobil - Genesis Israel"),
    "ג נסיס":    ("Genesis",     "Colmobil - Genesis Israel"),
    "אורה":      ("ORA",         "Colmobil - ORA Israel"),
    "ora":       ("ORA",         "Colmobil - ORA Israel"),
    "ORA":       ("ORA",         "Colmobil - ORA Israel"),
    "סמארט":     ("Smart",       "Colmobil - Smart Israel"),
    "smart":     ("Smart",       "Colmobil - Smart Israel"),
    "Smart":     ("Smart",       "Colmobil - Smart Israel"),
    "ג'אקו":     ("JAECOO",      "Colmobil - JAECOO Israel"),
    "jaecoo":    ("JAECOO",      "Colmobil - JAECOO Israel"),
    "JAECOO":    ("JAECOO",      "Colmobil - JAECOO Israel"),
    "OMODA":     ("OMODA",       "Colmobil - OMODA Israel"),
    "אומודה":    ("OMODA",       "Colmobil - OMODA Israel"),
    "מרצדס":     ("Mercedes-Benz", "Colmobil - Mercedes-Benz Israel"),
    "מרצדס-בנץ": ("Mercedes-Benz", "Colmobil - Mercedes-Benz Israel"),
}

PRICE_STOCK_RE = re.compile(r'([\d,]+\.\d{2})\s*(זמין|לא זמין)')
OEM_RE = re.compile(r'^([A-Z0-9][A-Z0-9\-./]{3,30})')

SKIP_HEADERS = {
    "מספר קטלוגי", "מותג", "תיאור החלק", "מחיר לצרכן", "זמינות מלאי",
    "catalog number", "brand", "description", "price", "availability",
}

PDF_CONFIGS = [
    ("HYU.PDF",    "Hyundai"),
    ("MIT.PDF",    "Mitsubishi"),
    ("GEN.PDF",    "Genesis"),
    ("ORA.PDF",    "ORA"),
    ("SMART.PDF",  "Smart"),
    ("JAECOO.PDF", "JAECOO"),
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE_URL = "https://prodmedia.colmobil.co.il/spare-parts/"


def log(msg: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def download_pdf(filename: str) -> Path:
    dest = PDF_DIR / filename
    if dest.exists() and dest.stat().st_size > 1_000_000:
        log(f"  {filename}: already downloaded ({dest.stat().st_size//1024//1024}MB)")
        return dest
    url = BASE_URL + filename
    log(f"  Downloading {url}...")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        downloaded = 0
        while True:
            chunk = r.read(2 * 1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
    log(f"  {filename}: {downloaded//1024//1024}MB downloaded")
    return dest


def _normalize_oem(oem: str) -> str:
    """Remove spaces, dashes, dots for fuzzy matching."""
    return re.sub(r'[\s\-./]', '', oem).upper()


def extract_parts(pdf_path: Path, expected_brand_en: str) -> list[dict]:
    """Extract (oem, price) from a Colmobil PDF.

    Handles two row formats found across brands:
    - HYU/MIT: '{OEM}{brand_he}' then '{desc}{price} {stock}' (or all on one line)
    - ORA/JAECOO/Smart: '{OEM}' alone, then '{BrandLatin}{desc}{price} {stock}'
      (sometimes with brand name on its own intermediate line)
    """
    doc = fitz.open(str(pdf_path))
    parts: list = []
    seen: set = set()
    total = len(doc)

    for page_num, page in enumerate(doc, 1):
        try:
            text = page.get_text()
        except Exception as e:
            log(f"  SKIP page {page_num}: {e}")
            continue

        lines = [l.strip() for l in text.split('\n') if l.strip()]
        pending_oem: str | None = None

        for line in lines:
            if any(h in line for h in SKIP_HEADERS):
                continue

            pm = PRICE_STOCK_RE.search(line)
            om = OEM_RE.match(line)

            if pm:
                price = float(pm.group(1).replace(',', ''))
                oem_here = None
                if pending_oem:
                    oem_here = pending_oem
                elif om:
                    # OEM + price on same line
                    oem_here = om.group(1)
                if oem_here and oem_here not in seen and price > 0:
                    seen.add(oem_here)
                    parts.append({'oem': oem_here, 'price': price})
                pending_oem = None

            elif om:
                # OEM line (no price yet) — set as pending
                pending_oem = om.group(1)
            # else: description text or brand-name-only line — keep pending_oem

        if page_num % 200 == 0 or page_num == total:
            log(f"  {expected_brand_en} page {page_num}/{total}, parts: {len(parts):,}")

    doc.close()
    return parts


async def ensure_supplier(conn, importer_name: str) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", importer_name)
    if row:
        return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,country,website,is_active,created_at,updated_at) "
        "VALUES($1,$2,'IL','https://www.colmobil.co.il/',true,NOW(),NOW())",
        sid, importer_name,
    )
    return sid


async def get_manufacturer_id(conn, manufacturer: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name)=LOWER($1) LIMIT 1", manufacturer
    )
    return str(row["id"]) if row else None


async def import_brand(conn, brand_en: str, parts: list[dict], supplier_id: str, manufacturer_id: str | None):
    importer_name = next(
        (v[1] for v in BRAND_MAP.values() if v[0] == brand_en),
        f"Colmobil - {brand_en} Israel"
    )
    spec_base = json.dumps({
        "importer": importer_name,
        "source": f"{brand_en} IL official price list",
        "source_url": "https://www.colmobil.co.il/spareparts/",
        "vat_included": True,
        "vat_rate": VAT,
    }, ensure_ascii=False)

    updated = inserted = errors = 0

    for p in parts:
        retail = p["price"]
        cost = round(retail / (1 + VAT), 2)
        selling = round(cost * MARGIN + cost, 2)
        oem = p["oem"]

        try:
            async with conn.transaction():
                # Exact OEM + manufacturer match
                row = await conn.fetchrow(
                    "SELECT id FROM parts_catalog WHERE oem_number=$1 AND LOWER(manufacturer)=LOWER($2) AND is_active LIMIT 1",
                    oem, brand_en,
                )

                # Normalized match (strips dashes/spaces from both sides)
                if not row:
                    norm = _normalize_oem(oem)
                    row = await conn.fetchrow(
                        "SELECT id FROM parts_catalog "
                        "WHERE REPLACE(REPLACE(UPPER(oem_number),' ',''),'-','')=$1 "
                        "AND LOWER(manufacturer)=LOWER($2) AND is_active LIMIT 1",
                        norm, brand_en,
                    )

                if row:
                    part_id = str(row["id"])
                    await conn.execute("""
                        UPDATE parts_catalog SET
                            importer_price_ils = CASE WHEN $1>0 THEN $1 ELSE importer_price_ils END,
                            max_price_ils      = CASE WHEN $2>0 THEN $2 ELSE max_price_ils END,
                            base_price         = CASE WHEN $3>0 THEN $3 ELSE base_price END,
                            specifications     = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                            updated_at         = NOW()
                        WHERE id=$5
                    """, cost, retail, selling, spec_base, part_id)
                    updated += 1
                else:
                    # INSERT new part — for ORA/Smart/JAECOO or OEM not yet in catalog
                    sku = f"{brand_en.upper().replace('-','').replace(' ','')}-{re.sub(r'[^A-Za-z0-9]','',oem)[:40]}"
                    part_id = await conn.fetchval("""
                        INSERT INTO parts_catalog(
                            id, sku, oem_number, name, name_he, manufacturer, manufacturer_id,
                            part_type, part_condition, importer_price_ils, max_price_ils,
                            base_price, is_active, specifications
                        ) VALUES(
                            gen_random_uuid(), $1, $2, $2, $2, $3, $4,
                            'oem', 'new', $5, $6, $7, true, $8::jsonb
                        )
                        ON CONFLICT (sku) DO UPDATE SET
                            importer_price_ils=CASE WHEN EXCLUDED.importer_price_ils>0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                            max_price_ils=CASE WHEN EXCLUDED.max_price_ils>0 THEN EXCLUDED.max_price_ils ELSE parts_catalog.max_price_ils END,
                            base_price=CASE WHEN EXCLUDED.base_price>0 THEN EXCLUDED.base_price ELSE parts_catalog.base_price END,
                            updated_at=NOW()
                        RETURNING id
                    """, sku, oem, brand_en, manufacturer_id, cost, retail, selling, spec_base)
                    inserted += 1

                if part_id and supplier_id:
                    await conn.execute("""
                        INSERT INTO supplier_parts(id,supplier_id,part_id,supplier_sku,price_ils,price_usd,
                            availability,is_available,supplier_url,created_at,updated_at)
                        VALUES(gen_random_uuid(),$1,$2::uuid,$3,$4,0,'in_stock',true,$5,NOW(),NOW())
                        ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
                            price_ils=EXCLUDED.price_ils, is_available=true, updated_at=NOW()
                    """, supplier_id, part_id, oem, retail, "https://www.colmobil.co.il/spareparts/")

        except Exception as e:
            errors += 1
            if errors <= 5:
                log(f"  ERR {oem}: {e}")

    log(f"  [{brand_en}] updated={updated:,} inserted={inserted:,} errors={errors}")


async def main():
    # Parse --brands flag
    brands_filter = None
    for i, a in enumerate(sys.argv):
        if a == "--brands" and i + 1 < len(sys.argv):
            brands_filter = [b.strip().lower() for b in sys.argv[i+1].split(",")]

    if not DB_URL:
        log("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()
    log("=== Colmobil PDF Importer v2 ===")

    try:
        for filename, brand_en in PDF_CONFIGS:
            if brands_filter and brand_en.lower() not in brands_filter:
                continue

            pdf_path = PDF_DIR / filename
            if not pdf_path.exists():
                log(f"SKIP {filename}: not downloaded yet — run downloads first")
                continue

            log(f"\n[{brand_en}] Processing {filename} ({pdf_path.stat().st_size//1024//1024}MB)")
            parts = extract_parts(pdf_path, brand_en)
            log(f"  Extracted {len(parts):,} unique parts from PDF")
            if not parts:
                log(f"  WARNING: 0 parts extracted — PDF format may differ. Check manually.")
                continue

            manufacturer_id = await get_manufacturer_id(conn, brand_en)
            log(f"  manufacturer_id={manufacturer_id}")

            importer_name = f"Colmobil - {brand_en} Israel"
            supplier_id = await ensure_supplier(conn, importer_name)

            await import_brand(conn, brand_en, parts, supplier_id, manufacturer_id)

            # Post-import coverage report
            r = await conn.fetchrow(
                "SELECT COUNT(*) t, COUNT(*) FILTER(WHERE importer_price_ils>0) p "
                "FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1) AND is_active", brand_en
            )
            pct = r["p"] / r["t"] * 100 if r["t"] else 0
            log(f"  [{brand_en}] COVERAGE: {r['p']:,}/{r['t']:,} ({pct:.1f}%)")

        log(f"\n=== DONE ({time.monotonic()-t0:.0f}s) ===")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
