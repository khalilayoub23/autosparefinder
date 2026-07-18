#!/usr/bin/env python3
"""
IL Official Importer Price List — Batch PDF Importer
=====================================================
Processes all brand PDFs from /app/uploads/ that follow the standard
Hebrew RTL format:
  [ןימז/ןימז אל] [price] [description] [brand_he] [OEM_NUMBER]

Pricing policy: prices are excl. VAT
  importer_price_ils = price          (excl. VAT — official cost reference)
  max_price_ils      = price * 1.17   (incl. 17% VAT — Israeli official retail)
  base_price         = price * 1.17   (match official retail, no extra markup)

Usage: python3 il_pdf_batch_import.py [--brand BRAND] [--dry-run]
  Without --brand: processes all configured brands
"""

import asyncio
import gc
import os
import re
import sys
import time
import asyncpg
import pdfplumber

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18   # Israeli standard VAT

# (brand_in_db, pdf_path, importer_name)
# Duplicate PDFs: take only the first unique-content file
BRAND_PDFS = [
    ("Hyundai",       "/app/uploads/HYUNDAI_20260518_185642.pdf",       "Autocenter / Colmobil - Hyundai Israel"),
    ("Mercedes-Benz", "/app/uploads/MERCEDES-BENZ_20260518_154959.pdf", "Daimler Israel"),
    ("Mitsubishi",    "/app/uploads/MITSUBISHI_20260518_161229.pdf",     "Mitsubishi Israel"),
    ("Suzuki",        "/app/uploads/SUZUKI_20260518_170146.pdf",         "Suzuki Israel"),
    ("Peugeot",       "/app/uploads/PEUGEOT_20260518_231928.pdf",        "PSA Israel - Peugeot"),
    ("Renault",       "/app/uploads/RENAULT_20260518_223327.pdf",        "Renault Israel"),
    ("Smart",         "/app/uploads/SMART_20260518_153424.pdf",          "Smart Israel"),
    ("ORA",           "/app/uploads/ORA_20260518_145353.pdf",            "Great Wall Motors Israel - ORA"),
    ("Genesis",       "/app/uploads/GENESIS_20260518_163814.pdf",        "Genesis Israel"),
]

# OEM number: alphanumeric (letters + digits), 3-30 chars, no spaces
OEM_RE = re.compile(r'^[A-Z0-9][A-Z0-9\-./]{2,29}$', re.IGNORECASE)
# Price: digits with optional comma thousands separator and decimal
PRICE_RE = re.compile(r'^[\d,]+\.\d{2}$')


def parse_price(raw: str) -> float | None:
    cleaned = re.sub(r'[,\s]', '', raw)
    try:
        v = float(cleaned)
        return v if v > 0 else None
    except ValueError:
        return None


def extract_lines_from_pdf(pdf_path: str) -> list[dict]:
    """
    Parse PDF text: each line has format (RTL extraction):
      [stock_status] [price] [description...] [OEM_NUMBER]
    OEM is always at the END of the line (leftmost col in RTL = last in LTR extraction).
    """
    results = []
    seen_oems = set()

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        print(f"  PDF: {total_pages} pages")
        for page_num, page in enumerate(pdf.pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                print(f"  SKIP page {page_num}: {e}")
                continue

            for line in text.split('\n'):
                line = line.strip()
                if not line:
                    continue

                tokens = line.split()
                if len(tokens) < 3:
                    continue

                # OEM is the last token
                oem_candidate = tokens[-1]
                if not OEM_RE.match(oem_candidate):
                    # Try second-to-last
                    if len(tokens) >= 4 and OEM_RE.match(tokens[-2]):
                        oem_candidate = tokens[-2]
                    else:
                        continue

                # Must contain at least one digit to be a real OEM
                if not re.search(r'\d', oem_candidate):
                    continue

                if oem_candidate in seen_oems:
                    continue

                # Find price in remaining tokens (first float-like token)
                price = None
                price_idx = None
                for i, t in enumerate(tokens[:-1]):
                    if PRICE_RE.match(t):
                        price = parse_price(t)
                        price_idx = i
                        break

                if price is None or price <= 0:
                    continue

                # Stock status: first token
                in_stock = tokens[0] in ("ןימז", "Available", "Yes", "available")
                if tokens[0] == "ןימז" and len(tokens) > 1 and tokens[1] == "אל":
                    in_stock = False

                seen_oems.add(oem_candidate)
                results.append({
                    "oem": oem_candidate,
                    "price": price,
                    "in_stock": in_stock,
                    "page": page_num,
                })

            if page_num % 100 == 0:
                print(f"  Parsed page {page_num}/{total_pages}, parts={len(results):,}")
                gc.collect()

    return results


async def import_brand_pdf(conn, brand: str, pdf_path: str, importer: str,
                            mfr_id: str, dry_run: bool = False) -> dict:
    print(f"\n[{brand}] Processing {os.path.basename(pdf_path)}")
    t0 = time.monotonic()

    try:
        rows = extract_lines_from_pdf(pdf_path)
    except Exception as e:
        print(f"  ERROR reading PDF: {e}")
        return {"brand": brand, "parsed": 0, "updated": 0, "errors": 1}

    print(f"  Extracted {len(rows):,} unique parts from PDF")
    if not rows:
        return {"brand": brand, "parsed": 0, "updated": 0, "errors": 0}

    updated = 0
    not_found = 0
    errors = 0
    spec_patch = __import__('json').dumps({
        "source": f"{brand} IL official importer price list",
        "importer": importer,
        "vat_rate": VAT,
        "vat_included": False,
    }, ensure_ascii=False)

    for r in rows:
        oem = r["oem"]
        price = r["price"]
        retail = round(price * (1 + VAT), 2)

        if dry_run:
            updated += 1
            continue

        try:
            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils = $1,
                    max_price_ils      = $2,
                    base_price         = $2,
                    specifications     = COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                    updated_at         = NOW()
                WHERE oem_number = $4
                  AND manufacturer = $5
                  AND is_active = true
            """, price, retail, spec_patch, oem, brand)
            n = int(res.split()[-1])
            if n > 0:
                updated += n
            else:
                not_found += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  error [{oem}]: {e}")

    elapsed = time.monotonic() - t0
    pct = 100 * updated // max(len(rows), 1)
    print(f"  [{brand}] updated={updated:,} not_found={not_found:,} errors={errors} ({elapsed:.1f}s) match={pct}%")
    return {"brand": brand, "parsed": len(rows), "updated": updated, "not_found": not_found, "errors": errors}


async def run(filter_brand: str = None, dry_run: bool = False):
    if not DB_URL:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)

    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()

    try:
        results = []
        for brand, pdf_path, importer in BRAND_PDFS:
            if filter_brand and filter_brand.lower() != brand.lower():
                continue
            if not os.path.exists(pdf_path):
                print(f"[{brand}] SKIP: PDF not found: {pdf_path}")
                continue

            mfr = await conn.fetchrow(
                "SELECT id FROM car_brands WHERE LOWER(name) = LOWER($1) LIMIT 1", brand
            )
            if not mfr:
                print(f"[{brand}] SKIP: not in car_brands")
                continue
            mfr_id = str(mfr["id"])

            result = await import_brand_pdf(conn, brand, pdf_path, importer, mfr_id, dry_run)
            results.append(result)

        print(f"\n{'='*60}")
        print(f"BATCH PDF IMPORT SUMMARY ({'DRY RUN' if dry_run else 'LIVE'}) — {time.monotonic()-t0:.0f}s")
        print(f"{'='*60}")
        total_parsed = total_updated = 0
        for r in results:
            print(f"  {r['brand']:<20} parsed={r.get('parsed',0):>6,} updated={r.get('updated',0):>6,}")
            total_parsed += r.get('parsed', 0)
            total_updated += r.get('updated', 0)
        print(f"  {'TOTAL':<20} parsed={total_parsed:>6,} updated={total_updated:>6,}")

        # Final catalog check
        if not dry_run:
            row = await conn.fetchrow("""
                SELECT COUNT(*) FILTER (WHERE is_active AND importer_price_ils > 0) as with_il
                FROM parts_catalog
            """)
            print(f"\n  Global parts with IL importer price now: {row['with_il']:,}")

    finally:
        await conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    brand_arg = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--brand"), None)
    asyncio.run(run(filter_brand=brand_arg, dry_run=dry))
