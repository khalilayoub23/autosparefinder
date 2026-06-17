#!/usr/bin/env python3
"""
Lexus Official Price List PDF Importer (Union Motors / יוניון מוטורס בע"מ)
==========================================================================
Parses the official Union Motors Lexus parts price list PDF and imports:
  - ILS prices (excl. VAT) → importer_price_ils, max_price_ils, base_price
  - Vehicle fitment (מתאים לדגמים column) → part_vehicle_fitment table

PDF column structure (pdfplumber reads RTL columns left→right, text is reversed):
  col[0] = product type (ירוקמ = מקורי = OEM)
  col[1] = compatible models (מתאים לדגמים)
  col[2] = price excl. VAT (מחיר בש"ח לא כולל מע"מ)
  col[3] = in stock (האם קיים במלאי: ןכ = כן = yes)
  col[4] = Hebrew description (reversed text)
  col[5] = OEM/catalog number (מק"ט)

Pricing:
  importer_price_ils = price_excl_vat   (official Union Motors list price)
  max_price_ils      = price * 1.17     (incl. 17% VAT)
  base_price         = max_price_ils    (show official retail as reference)

Usage:
  python3 lexus_pdf_import.py [--dry-run]
  python3 lexus_pdf_import.py --pdf /app/lexus_prices.pdf
"""

import asyncio
import os
import re
import sys
import json
import time
import asyncpg
import pdfplumber

PDF_PATH = os.getenv("LEXUS_PDF", "/app/uploads/LEXUS_20260612_082412.pdf")
IMPORTER = "יוניון מוטורס בע\"מ"
PRICE_DATE = "2026-05-03"
VAT = 0.18  # 17% Israeli VAT
BRAND = "Lexus"

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# Known Lexus model patterns found in the PDF
LEXUS_MODELS = [
    "LX600", "LX570", "LX470", "LX450",
    "LS600HL", "LS600", "LS460", "LS430", "LS400",
    "GS450H", "GS350", "GS300", "GS250", "GS200T",
    "RX450H", "RX400H", "RX350", "RX300", "RX200T",
    "IS350", "IS300H", "IS300", "IS250", "IS200T",
    "NX450H", "NX300H", "NX300", "NX200T", "NX200",
    "UX300H", "UX300E", "UX250H", "UX200",
    "RC350", "RC300H", "RC300", "RC200T",
    "LC500H", "LC500",
    "ES350", "ES300H", "ES250",
    "CT200H",
    "SC430",
    "HS250H",
    "RX 350", "RX 450H",  # space variants
]

# OCR variants: map typo → canonical (O instead of 0 in numeric codes)
OCR_FIX = {
    "RX4OOH": "RX400H",
    "RX40OH": "RX400H",
    "RX4O0H": "RX400H",
    "LS6OOHL": "LS600HL",
    "LS6OO": "LS600",
    "CT2OOH": "CT200H",
    "NX3OOH": "NX300H",
    "NX2OOT": "NX200T",
    "RC2OOT": "RC200T",
    "GS45OH": "GS450H",
}
# Sort longest first for greedy matching
LEXUS_MODELS.sort(key=len, reverse=True)


def reverse_hebrew(text: str) -> str:
    """pdfplumber reverses RTL text — reverse it back."""
    return text[::-1].strip()


def parse_models(raw_text: str) -> list[str]:
    """
    Extract Lexus model names from the 'מתאים לדגמים' column.
    Raw text may be reversed and contain multiple models on separate lines.
    Handles OCR typos (O instead of 0 in model codes).
    """
    models = []
    # Normalize OCR typos in the raw text before matching
    normalized = raw_text
    for typo, canon in OCR_FIX.items():
        normalized = re.sub(re.escape(typo), canon, normalized, flags=re.IGNORECASE)

    # The cell may contain multiple entries separated by newlines
    for segment in normalized.replace("\n", " | ").split("|"):
        segment = segment.strip()
        if not segment:
            continue
        # Try reversing (RTL)
        reversed_seg = reverse_hebrew(segment)
        # Try to match Lexus model codes
        for model in LEXUS_MODELS:
            # Search both original and reversed
            for text in (segment, reversed_seg):
                if re.search(re.escape(model), text, re.IGNORECASE):
                    clean = model.strip()
                    if clean not in models:
                        models.append(clean)
                    break
    return models


def parse_price(raw: str) -> float | None:
    if not raw:
        return None
    cleaned = re.sub(r"[₪,$€\s,]", "", str(raw).strip())
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def is_header_row(row: list) -> bool:
    header_words = {"ט", "קמ", 'ט"קמ', "רואית", "טירפ", "יאלמב", "ריחמ", "םימגד", "גוויס", "רצומ"}
    flat = " ".join(str(c) for c in row if c)
    return any(w in flat for w in header_words) and not re.search(r"\d{6,}", flat)


def extract_rows(pdf_path: str) -> list[dict]:
    rows_out = []
    seen_oems = set()

    with pdfplumber.open(pdf_path) as pdf:
        print(f"  PDF: {len(pdf.pages)} pages")
        for page_num, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 5:
                        continue
                    if is_header_row(row):
                        continue

                    # Column mapping (RTL layout from pdfplumber)
                    oem_num = str(row[5] or "").strip() if len(row) > 5 else ""
                    desc_raw = str(row[4] or "").strip() if len(row) > 4 else ""
                    in_stock_raw = str(row[3] or "").strip() if len(row) > 3 else ""
                    price_raw = str(row[2] or "").strip() if len(row) > 2 else ""
                    models_raw = str(row[1] or "").strip() if len(row) > 1 else ""
                    # col[0] = product type (ירוקמ = מקורי)

                    oem_num = oem_num.strip()
                    if not oem_num or oem_num in seen_oems:
                        continue
                    # Must look like an OEM number (contains digits, 6-20 chars)
                    if not re.search(r"\d", oem_num) or not (4 <= len(oem_num) <= 25):
                        continue

                    price = parse_price(price_raw)
                    if price is None:
                        continue

                    in_stock = "ןכ" in in_stock_raw or "yes" in in_stock_raw.lower()

                    # Description — try to reverse it (RTL)
                    desc = reverse_hebrew(desc_raw) if desc_raw else oem_num
                    # Clean "-ירוקמ" suffix (= "-מקורי" = "-original")
                    desc = re.sub(r"-?ירוקמ$", "", desc).strip()
                    desc = re.sub(r"^ירוקמ-?", "", desc).strip()

                    models = parse_models(models_raw)

                    seen_oems.add(oem_num)
                    rows_out.append({
                        "oem": oem_num,
                        "desc": desc or oem_num,
                        "price": price,
                        "in_stock": in_stock,
                        "models": models,
                        "page": page_num,
                    })

    return rows_out


async def run(dry_run: bool = False):
    rows = extract_rows(PDF_PATH)
    print(f"\nExtracted {len(rows)} unique parts from PDF")

    if not rows:
        print("ERROR: No parts extracted — check PDF path/format")
        sys.exit(1)

    # Preview
    print("\nSample parts:")
    for r in rows[:8]:
        print(f"  [{r['oem']}] {r['desc'][:55]} | ₪{r['price']:,.1f} | models: {r['models']}")

    total_with_fitment = sum(1 for r in rows if r["models"])
    print(f"\nParts with fitment data: {total_with_fitment}/{len(rows)}")

    if dry_run:
        print("\nDry run — not writing to DB")
        return

    if not DB_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()

    try:
        # Get Lexus manufacturer_id
        mfr = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)='lexus' LIMIT 1")
        if not mfr:
            print("ERROR: Lexus not found in car_brands")
            return
        mfr_id = str(mfr["id"])
        print(f"\nLexus manufacturer_id: {mfr_id}")

        # Existing counts
        before_price = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Lexus' AND importer_price_ils > 0"
        )
        before_fitment = await conn.fetchval(
            "SELECT COUNT(DISTINCT part_id) FROM part_vehicle_fitment pvf "
            "JOIN parts_catalog pc ON pvf.part_id=pc.id WHERE pc.manufacturer='Lexus'"
        )
        print(f"Before: {before_price} parts with IL price, {before_fitment} parts with fitment")

        updated_price = 0
        inserted_new = 0
        inserted_fitment = 0
        errors = 0

        for i, r in enumerate(rows):
            oem = r["oem"]
            price_excl = r["price"]
            price_incl = round(price_excl * (1 + VAT), 2)  # incl. 17% VAT
            base_price = round(price_excl * 1.45, 2)       # 45% margin over cost

            specs = json.dumps({
                "vat_included": False,
                "vat_rate": VAT,
                "importer": IMPORTER,
                "price_date": PRICE_DATE,
                "in_stock": r["in_stock"],
                "part_type": "original",
                "source": "Union Motors official Lexus price list 2026-05-03",
            }, ensure_ascii=False)

            try:
                async with conn.transaction():
                    # Try update by oem_number match
                    res = await conn.execute("""
                        UPDATE parts_catalog SET
                            importer_price_ils = $1,
                            max_price_ils      = $2,
                            base_price         = $3,
                            is_active          = true,
                            specifications     = COALESCE(specifications, '{}')::jsonb || $4::jsonb,
                            updated_at         = NOW()
                        WHERE oem_number = $5 AND manufacturer = 'Lexus'
                    """, price_excl, price_incl, base_price, specs, oem)
                    n = int(res.split()[-1])

                    if n == 0:
                        # Try matching by sku
                        res2 = await conn.execute("""
                            UPDATE parts_catalog SET
                                importer_price_ils = $1,
                                max_price_ils      = $2,
                                base_price         = $3,
                                oem_number         = $5,
                                is_active          = true,
                                specifications     = COALESCE(specifications, '{}')::jsonb || $4::jsonb,
                                updated_at         = NOW()
                            WHERE sku = $5 AND manufacturer = 'Lexus'
                        """, price_excl, price_incl, base_price, specs, oem)
                        n = int(res2.split()[-1])

                    if n > 0:
                        updated_price += n
                    else:
                        # Insert new part
                        await conn.execute("""
                            INSERT INTO parts_catalog (
                                id, sku, oem_number, name, name_he,
                                manufacturer, manufacturer_id,
                                category, part_condition, part_type,
                                importer_price_ils, max_price_ils, min_price_ils, base_price,
                                specifications, is_active,
                                needs_oem_lookup, master_enriched,
                                created_at, updated_at
                            ) VALUES (
                                gen_random_uuid(), $1, $1, $2, $2,
                                'Lexus', $3::uuid,
                                'Auto Parts', 'new', 'original',
                                $4, $5, $4, $7,
                                $6::jsonb, true,
                                true, false,
                                NOW(), NOW()
                            ) ON CONFLICT (sku) DO NOTHING
                        """, oem, r["desc"], mfr_id,
                            price_excl, price_incl, specs, base_price)
                        inserted_new += 1

                    # Now insert fitment for matched parts
                    if r["models"]:
                        part_id = await conn.fetchval(
                            "SELECT id FROM parts_catalog WHERE oem_number=$1 AND manufacturer='Lexus' LIMIT 1",
                            oem
                        )
                        if not part_id:
                            part_id = await conn.fetchval(
                                "SELECT id FROM parts_catalog WHERE sku=$1 AND manufacturer='Lexus' LIMIT 1",
                                oem
                            )
                        if part_id:
                            for model in r["models"]:
                                try:
                                    await conn.execute("""
                                        INSERT INTO part_vehicle_fitment (
                                            id, part_id, manufacturer, manufacturer_id,
                                            model, year_from, year_to, notes,
                                            created_at, updated_at
                                        ) VALUES (
                                            gen_random_uuid(), $1::uuid, 'Lexus', $2::uuid,
                                            $3, 2000, NULL,
                                            'Fitment from Union Motors official price list 2026-05-03',
                                            NOW(), NOW()
                                        ) ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                                    """, str(part_id), mfr_id, model)
                                    inserted_fitment += 1
                                except Exception:
                                    pass

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  ERROR [{oem}]: {e}")

            if (i + 1) % 200 == 0:
                elapsed = time.monotonic() - t0
                print(f"  {i+1}/{len(rows)} processed | price_upd={updated_price} ins={inserted_new} "
                      f"fitment={inserted_fitment} err={errors} [{elapsed:.0f}s]")

        # Final counts
        after_price = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Lexus' AND importer_price_ils > 0"
        )
        after_fitment = await conn.fetchval(
            "SELECT COUNT(DISTINCT part_id) FROM part_vehicle_fitment pvf "
            "JOIN parts_catalog pc ON pvf.part_id=pc.id WHERE pc.manufacturer='Lexus'"
        )
        elapsed = time.monotonic() - t0
        print(f"\n=== LEXUS IMPORT COMPLETE ({elapsed:.1f}s) ===")
        print(f"  PDF rows processed : {len(rows)}")
        print(f"  Prices updated     : {updated_price}")
        print(f"  New parts inserted : {inserted_new}")
        print(f"  Fitment rows added : {inserted_fitment}")
        print(f"  Errors             : {errors}")
        print(f"  After: {after_price} parts with IL price (was {before_price})")
        print(f"  After: {after_fitment} parts with fitment (was {before_fitment})")

    finally:
        await conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    pdf_arg = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--pdf"), None)
    if pdf_arg:
        PDF_PATH = pdf_arg
    if not os.path.exists(PDF_PATH):
        print(f"ERROR: PDF not found: {PDF_PATH}")
        sys.exit(1)
    asyncio.run(run(dry_run=dry))
