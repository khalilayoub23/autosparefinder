#!/usr/bin/env python3
"""
IL Official Importer Price List PDF Importer
============================================
Imports parts from Israeli official importer price lists (Porsche, Lexus, etc.)

PDF table structure (Hebrew RTL layout):
  מקט | תיאור פריט | סוג מוצר | אחריות | במלאי | מחיר מחירון לצרכן

Pricing rules:
  - Prices in PDF are EXCL. VAT (explicitly stated in PDF footer)
  - importer_price_ils = price (excl. VAT)
  - max_price_ils      = price * 1.18 (incl. VAT)
  - base_price         = price * 1.18 * 1.45 (customer price with 45% margin)

Usage:
  python3 il_importer_pdf_import.py --pdf /path/to/price_list.pdf --brand Porsche \
      --importer "אורכיד ספורטס קארס ישראל בע\"מ" --price-date 2025-03-01
  python3 il_importer_pdf_import.py --pdf /path/to/lexus_prices.pdf --brand Lexus \
      --importer "למוד ישראל" --price-date 2025-01-01
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from typing import Optional

import asyncpg
import pdfplumber


MARGIN = 1.45

# VAT rate embedded IN the PDF price per brand (0 = prices are EXCL. VAT).
# MANDATORY: update when adding a new brand. Never assume.
PRICES_INCL_VAT = {
    "Porsche":   0.18,   # Official IL price list incl. 18% VAT
    "Cadillac":  0.17,   # GM Israel uses 17% VAT
    "KGM":       0.17,   # kgm.co.il uses 17% VAT
    "SsangYong": 0.17,   # same source as KGM
}

INTERNATIONAL_BRANDS = {
    "Rover", "Saab", "Daewoo", "Maserati",
}


KGM_BRANDS = {"KGM", "SsangYong"}


def compute_price_triple(raw_price: float, brand: str) -> tuple[float, float, float]:
    """
    MANDATORY VAT CHECK — call before writing any price to the DB.
    Returns (importer_price_ils, max_price_ils, base_price).

    Pricing rules per source type:
      - International (Rover/Saab/Daewoo/Maserati): no IL VAT, base = raw * 1.45
      - KGM/SsangYong: actual wholesale trade price, base = il_retail * 1.45
      - IL official importers (Porsche, Lexus, Toyota, etc.): dealer retail reference,
        base = max_price_ils (no extra markup — customer sees official retail to compare)
    """
    brand_key = next((k for k in PRICES_INCL_VAT if k.lower() == brand.lower()), None)
    emb_vat = PRICES_INCL_VAT.get(brand_key, 0.0) if brand_key else 0.0

    IL_VAT = 0.18  # current Israeli VAT rate

    if brand in INTERNATIONAL_BRANDS:
        # eBay/international source — no IL VAT, apply 45% margin
        importer = round(raw_price, 2)
        max_p    = round(raw_price * (1 + IL_VAT), 2)
        base     = round(raw_price * MARGIN, 2)
    elif brand in KGM_BRANDS and emb_vat > 0:
        # Actual wholesale trade price with embedded VAT — keep importer cost, compute il_retail
        wholesale = round(raw_price / (1 + emb_vat), 2)
        max_p     = round(wholesale * (1 + IL_VAT), 2)
        importer  = wholesale
        base      = round(importer * MARGIN, 2)
    elif emb_vat > 0:
        # IL official importer PDF with VAT embedded — extract excl-VAT cost
        importer = round(raw_price / (1 + emb_vat), 2)
        max_p    = round(importer * (1 + IL_VAT), 2)
        base     = round(importer * MARGIN, 2)
    else:
        # IL official importer PDF, prices excl. VAT
        importer = round(raw_price, 2)
        max_p    = round(raw_price * (1 + IL_VAT), 2)
        base     = round(raw_price * MARGIN, 2)

    return importer, max_p, base

# Words that indicate "in stock"
IN_STOCK_WORDS = {"זמין", "yes", "available", "in stock", "במלאי"}
# Words that indicate OEM (original) parts
OEM_WORDS = {"מקורי", "original", "oem", "genuine"}


def parse_price(raw: str) -> Optional[float]:
    """Strip currency symbols and commas, return float or None. Max 200,000 ILS."""
    if not raw:
        return None
    cleaned = re.sub(r"[₪,$€\s,]", "", str(raw).strip())
    if not cleaned:
        return None
    try:
        val = float(cleaned)
        return val if 0 < val < 200000 else None
    except ValueError:
        return None


def parse_row(cells: list[str]) -> Optional[dict]:
    """
    Try to extract a valid part row from a list of cell strings.
    Handles variable column ordering by heuristic detection.
    Returns None if row looks like a header or empty row.
    """
    # Flatten to stripped non-empty values
    vals = [str(c).strip() for c in cells if str(c).strip()]
    if len(vals) < 2:
        return None

    # Skip header rows
    header_words = {"מקט", "תיאור", "פריט", "אחריות", "מחיר", "במלאי", "סוג"}
    if any(v in header_words for v in vals):
        return None

    # --- Detect price column (last numeric column) ---
    price = None
    price_idx = None
    for i in range(len(vals) - 1, -1, -1):
        p = parse_price(vals[i])
        if p is not None and p > 0:
            price = p
            price_idx = i
            break
    if price is None:
        return None

    # --- Detect part number (first column that looks like an OEM number) ---
    # OEM numbers: alphanumeric, often contain digits, may have dashes/dots
    part_num = None
    part_num_idx = None
    for i, v in enumerate(vals):
        if i == price_idx:
            continue
        # Must contain at least one digit and be a reasonable length
        if re.search(r"\d", v) and 4 <= len(v) <= 30 and not re.search(r"[^\w\-./]", v):
            part_num = v.strip()
            part_num_idx = i
            break
    if not part_num:
        return None

    # --- Detect description (longest remaining text) ---
    desc = ""
    desc_len = 0
    for i, v in enumerate(vals):
        if i in (price_idx, part_num_idx):
            continue
        # Skip known keyword-only cells
        low = v.lower()
        if any(k in low for k in ("זמין", "מקורי", "תחליפי", "חודשים", "לא זמין")):
            continue
        if len(v) > desc_len:
            desc = v
            desc_len = len(v)

    # --- Detect in-stock status ---
    all_text = " ".join(vals).lower()
    in_stock = True
    if "לא זמין" in all_text:
        in_stock = False
    elif "זמין" in all_text:
        in_stock = True

    # --- Detect part type ---
    part_type = "oem"
    if "תחליפי" in all_text or "T." in str(cells[0] if cells else ""):
        part_type = "aftermarket"

    # --- Detect warranty ---
    warranty_months = 24 if part_type == "oem" else 6
    m = re.search(r"(\d+)\s*חודשים", all_text)
    if m:
        warranty_months = int(m.group(1))

    return {
        "oem_number": part_num,
        "name": desc or part_num,
        "price": price,
        "in_stock": in_stock,
        "part_type": part_type,
        "warranty_months": warranty_months,
    }


def extract_rows_from_pdf(pdf_path: str) -> list[dict]:
    """Use pdfplumber to extract all part rows from an IL importer price list PDF."""
    rows = []
    seen_oems = set()

    with pdfplumber.open(pdf_path) as pdf:
        print(f"  PDF has {len(pdf.pages)} pages")
        for page_num, page in enumerate(pdf.pages, 1):
            # Try table extraction first
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if row:
                        parsed = parse_row(row)
                        if parsed and parsed["oem_number"] not in seen_oems:
                            parsed["page"] = page_num
                            rows.append(parsed)
                            seen_oems.add(parsed["oem_number"])

            # Fallback: line-by-line text extraction if no tables found
            if not tables:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    parts = line.split()
                    if len(parts) >= 3:
                        parsed = parse_row(parts)
                        if parsed and parsed["oem_number"] not in seen_oems:
                            parsed["page"] = page_num
                            rows.append(parsed)
                            seen_oems.add(parsed["oem_number"])

    return rows


async def import_prices(
    pdf_path: str,
    brand: str,
    importer_name: str,
    price_date: str,
    db_url: str,
    dry_run: bool = False,
):
    print(f"\n=== IL Importer Price List Import ===")
    print(f"Brand: {brand} | Importer: {importer_name} | Date: {price_date}")
    print(f"PDF: {pdf_path}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")

    print("Extracting rows from PDF...")
    rows = extract_rows_from_pdf(pdf_path)
    print(f"Extracted {len(rows)} unique part rows from PDF")

    if not rows:
        print("No rows extracted — check PDF format!")
        return

    # Show sample
    print("\nSample rows (first 5):")
    for r in rows[:5]:
        print(f"  [{r['oem_number']}] {r['name'][:50]} | {r['price']} ILS | "
              f"{'זמין' if r['in_stock'] else 'לא זמין'} | {r['part_type']}")

    if dry_run:
        print(f"\nDry run complete. Would process {len(rows)} rows.")
        return

    # Connect to DB
    pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace("asyncpg://", "postgresql://")
    conn = await asyncpg.connect(pg_url)
    print(f"\nConnected to DB. Starting upsert...")

    updated = 0
    inserted = 0
    skipped = 0
    errors = 0
    t0 = time.monotonic()

    try:
        for row in rows:
            oem = row["oem_number"]
            price_raw = row["price"]
            # MANDATORY VAT CHECK — always use compute_price_triple
            price_excl, price_incl, base = compute_price_triple(price_raw, brand)

            specs = json.dumps({
                "vat_included": PRICES_INCL_VAT.get(brand, 0.0) > 0,
                "vat_rate": 0.18,
                "warranty_months": row["warranty_months"],
                "in_stock": row["in_stock"],
                "importer": importer_name,
                "price_date": price_date,
            })

            try:
                # Try to update existing parts by oem_number
                result = await conn.execute(
                    """
                    UPDATE parts_catalog
                    SET
                        importer_price_ils = $1,
                        max_price_ils      = $2,
                        base_price         = $3,
                        is_active          = $4,
                        specifications     = specifications || $5::jsonb,
                        updated_at         = NOW()
                    WHERE oem_number = $6
                      AND manufacturer = $7
                    """,
                    price_excl, price_incl, base,
                    row["in_stock"],
                    specs,
                    oem, brand,
                )
                rows_affected = int(result.split()[-1])

                if rows_affected > 0:
                    updated += rows_affected
                else:
                    # Also try matching by sku
                    result2 = await conn.execute(
                        """
                        UPDATE parts_catalog
                        SET
                            importer_price_ils = $1,
                            max_price_ils      = $2,
                            base_price         = $3,
                            is_active          = $4,
                            specifications     = specifications || $5::jsonb,
                            oem_number         = $6,
                            updated_at         = NOW()
                        WHERE sku = $6
                          AND manufacturer = $7
                        """,
                        price_excl, price_incl, base,
                        row["in_stock"],
                        specs, oem, brand,
                    )
                    rows_affected2 = int(result2.split()[-1])
                    if rows_affected2 > 0:
                        updated += rows_affected2
                    else:
                        # Insert new part
                        await conn.execute(
                            """
                            INSERT INTO parts_catalog (
                                id, sku, oem_number, name, manufacturer,
                                category, description,
                                importer_price_ils, max_price_ils, min_price_ils, base_price,
                                part_type, specifications, is_active,
                                needs_oem_lookup, master_enriched, created_at, updated_at
                            )
                            SELECT
                                gen_random_uuid(), $1, $1, $2, $3,
                                'Parts & Accessories', $2,
                                $4, $5, $4, $6,
                                $7, $8::jsonb, $9,
                                TRUE, FALSE, NOW(), NOW()
                            WHERE NOT EXISTS (
                                SELECT 1 FROM parts_catalog WHERE sku = $1
                            )
                            """,
                            oem, row["name"], brand,
                            price_excl, price_incl, base,
                            row["part_type"], specs, row["in_stock"],
                        )
                        inserted += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  ERROR on [{oem}]: {e}")

        await conn.execute("SELECT 1")  # keep-alive flush

    finally:
        await conn.close()

    elapsed = time.monotonic() - t0
    print(f"\n=== Import Complete ===")
    print(f"  Updated existing parts : {updated:,}")
    print(f"  Inserted new parts     : {inserted:,}")
    print(f"  Skipped (errors)       : {errors:,}")
    print(f"  Total PDF rows         : {len(rows):,}")
    print(f"  Elapsed                : {elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="Import IL official importer price list PDF into AutoSpareFinder catalog")
    parser.add_argument("--pdf", required=True, help="Path to the PDF price list file")
    parser.add_argument("--brand", required=True, help="Brand name (e.g. Porsche, Lexus)")
    parser.add_argument("--importer", default="", help="Israeli importer company name (Hebrew)")
    parser.add_argument("--price-date", default="2025-01-01", help="Price list date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Parse PDF only, don't write to DB")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"ERROR: PDF not found: {args.pdf}")
        sys.exit(1)

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url and not args.dry_run:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    # Default importer names
    importer_defaults = {
        "Porsche": "אורכיד ספורטס קארס ישראל בע\"מ",
        "Lexus": "למוד ישראל",
        "Toyota": "מוטוראים",
        "Kia": "מוטוראים",
    }
    importer = args.importer or importer_defaults.get(args.brand, args.brand)

    asyncio.run(import_prices(
        pdf_path=args.pdf,
        brand=args.brand,
        importer_name=importer,
        price_date=args.price_date,
        db_url=db_url,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
