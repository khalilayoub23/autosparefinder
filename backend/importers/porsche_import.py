#!/usr/bin/env python3
"""
Porsche Import — Prices from PDF + OEM-prefix fitment for all 265K parts
=========================================================================
Part 1: Parse official אורכיד ספורטס קארס price list PDF → update prices
Part 2: Derive vehicle model from OEM number prefix → insert part_vehicle_fitment

PDF column structure (RTL, 35 pages):
  col[0] = price incl. 18% VAT (₪364.10)
  col[1] = in stock (ןימז = זמין = available)
  col[2] = warranty months
  col[3] = product type (ירוקמ = מקורי = OEM)
  col[4] = Hebrew description (reversed)
  col[5] = OEM catalog number (מקט)

Pricing: Porsche IL prices are INCL. 18% VAT
  importer_price_ils = price / 1.18   (excl. VAT = our cost reference)
  max_price_ils      = price           (incl. VAT = official retail)
  base_price         = price           (match official retail)
"""

import asyncio
import os
import re
import sys
import json
import time
import asyncpg
import pdfplumber

PDF_PATH = os.getenv("PORSCHE_PDF", "/app/porsche_prices.pdf")
IMPORTER = "אורכיד ספורטס קארס ישראל בע\"מ"
PRICE_DATE = "2025-03-01"
VAT = 0.18
BRAND = "Porsche"

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# ── OEM number prefix → Porsche model mapping ──────────────────────────────
# Key = first N chars of OEM number (try longest match first)
OEM_PREFIX_MAP = {
    # 911 generations
    "993": ["911 993"],           # 911 993 classic 1993-1998
    "996": ["911 996"],           # 911 996 1997-2004
    "997": ["911 997"],           # 911 997 2004-2012
    "991": ["911 991"],           # 911 991 2011-2019
    "992": ["911 992"],           # 911 992 2019+
    "901": ["911"],               # early 911
    "964": ["911 964"],           # 911 964 classic
    "9GT": ["911 GT3", "911 GT2RS"],  # GT variants
    "9A1": ["718 Boxster", "718 Cayman", "911 992"],  # shared platform parts
    # Boxster / Cayman
    "986": ["Boxster 986"],
    "987": ["Boxster 987", "Cayman 987"],
    "981": ["Boxster 981", "Cayman 981"],
    "982": ["718 Boxster", "718 Cayman"],
    "9J1": ["718 Boxster", "718 Cayman"],
    # Cayenne
    "955": ["Cayenne 955"],       # 1st gen 2002-2006
    "957": ["Cayenne 957"],       # 1st gen facelift 2007-2010
    "9PA": ["Cayenne 955", "Cayenne 957"],
    "958": ["Cayenne 958"],       # 2nd gen 2010-2018
    "9Y0": ["Cayenne 9YA"],       # 3rd gen 2018+
    "9Y4": ["Cayenne Coupé"],     # 3rd gen Coupé 2019+
    "4HB": ["Cayenne Coupé"],
    "4GH": ["Cayenne 9YA"],
    "4SA": ["Cayenne Turbo"],
    # Macan
    "95B": ["Macan"],             # 1st gen 2014-2022
    "95C": ["Macan"],             # Macan facelift
    "9Y3": ["Macan EV"],          # 2nd gen electric 2024+
    # Panamera
    "970": ["Panamera 970"],      # 1st gen 2009-2016
    "971": ["Panamera 971"],      # 2nd gen 2016+
    "974": ["Panamera Sport Turismo"],
    "97H": ["Panamera E-Hybrid"],
    "0PB": ["Panamera E-Hybrid"],
    "PAB": ["Panamera"],
    "PAD": ["Panamera"],
    "PAF": ["Panamera"],
    # Taycan
    "J1A": ["Taycan"],
    "Y1A": ["Taycan"],
    "9A7": ["Taycan"],
    # 918 Spyder
    "918": ["918 Spyder"],
    # Classic models
    "928": ["928"],
    "944": ["944"],
    "968": ["968"],
    "924": ["924"],
    "356": ["356"],
    # 911 generic / shared
    "911": ["911"],
    # Accessories (all models)
    "WKD": None,  # Tequipment decoration parts — skip (not model-specific)
    "WAP": None,  # Accessory packages — skip
    "9A2": ["911 992"],
    "976": ["Boxster 976", "Cayman 976"],  # 2024+ 718 successor
    # Generic ranges - assign to multiple models
    "404": ["Cayenne 9YA", "Macan"],       # body parts shared
    "405": ["Cayenne 9YA"],
    "406": ["Cayenne 9YA"],
    "980": ["Panamera 970"],
}

# For OEM prefixes not in map, try to extract model from 3-char prefix
FALLBACK_PREFIX_YEARS = {
    "991": (2011, 2019), "992": (2019, None), "996": (1997, 2004), "997": (2004, 2012),
    "993": (1993, 1998), "958": (2010, 2018), "955": (2002, 2006), "970": (2009, 2016),
    "971": (2016, None), "95B": (2014, 2022), "95C": (2014, 2022), "9Y0": (2018, None),
    "9Y3": (2024, None), "918": (2013, 2015), "928": (1977, 1995), "944": (1982, 1991),
    "986": (1996, 2004), "987": (2004, 2012), "981": (2012, 2016), "982": (2016, 2024),
}


def get_fitment(oem: str) -> list[dict]:
    """Derive vehicle fitment from OEM number prefix."""
    if not oem:
        return []

    # Try longest prefix first (4-char), then 3-char, then 2-char
    for prefix_len in (4, 3, 2):
        prefix = oem[:prefix_len].upper()
        if prefix not in OEM_PREFIX_MAP:
            continue
        models = OEM_PREFIX_MAP[prefix]
        if models is None:
            return []  # explicitly excluded (e.g., WKD, WAP)
        years = FALLBACK_PREFIX_YEARS.get(prefix[:3], (2000, None))
        return [{"model": m, "year_from": years[0], "year_to": years[1]} for m in models]

    return []


def reverse_hebrew(text: str) -> str:
    return text[::-1].strip() if text else ""


def parse_price(raw: str) -> float | None:
    cleaned = re.sub(r"[₪,$€\s,]", "", str(raw).strip())
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def is_header(row: list) -> bool:
    flat = " ".join(str(c) for c in row if c)
    return any(w in flat for w in ("ןכרצל", "טקמ", "טירפ", "רואית")) and not re.search(r"₪\d", flat)


def extract_pdf_parts(pdf_path: str) -> list[dict]:
    parts = []
    seen = set()
    with pdfplumber.open(pdf_path) as pdf:
        print(f"  PDF: {len(pdf.pages)} pages")
        for page_num, page in enumerate(pdf.pages, 1):
            for table in page.extract_tables():
                for row in table:
                    if not row or len(row) < 5 or is_header(row):
                        continue
                    oem = str(row[5] or "").strip()
                    price_raw = str(row[0] or "").strip()
                    in_stock_raw = str(row[1] or "").strip()
                    desc_raw = str(row[4] or "").strip()

                    if not oem or oem in seen:
                        continue
                    price = parse_price(price_raw)
                    if not price:
                        continue
                    # Validate OEM format
                    if not re.search(r"[A-Z0-9]", oem.upper()) or len(oem) < 3:
                        continue

                    in_stock = "ןימז" in in_stock_raw and "אל" not in in_stock_raw
                    desc = reverse_hebrew(desc_raw)
                    desc = re.sub(r"-?ירוקמ$", "", desc).strip()

                    seen.add(oem)
                    parts.append({
                        "oem": oem,
                        "desc": desc or oem,
                        "price_incl_vat": price,
                        "in_stock": in_stock,
                        "page": page_num,
                    })
    return parts


async def import_pdf_prices(conn, parts: list[dict], mfr_id: str, dry_run: bool = False):
    """Update existing Porsche parts with IL prices from PDF."""
    updated = 0
    inserted = 0
    errors = 0

    for r in parts:
        oem = r["oem"]
        price_incl = r["price_incl_vat"]
        price_excl  = round(price_incl / (1 + VAT), 2)
        base_price  = round(price_excl * 1.45, 2)

        specs = json.dumps({
            "vat_included": True,
            "vat_rate": VAT,
            "importer": IMPORTER,
            "price_date": PRICE_DATE,
            "in_stock": r["in_stock"],
            "source": f"Porsche IL official price list {PRICE_DATE}",
        }, ensure_ascii=False)

        try:
            if dry_run:
                updated += 1
                continue

            res = await conn.execute("""
                UPDATE parts_catalog SET
                    importer_price_ils = $1,
                    max_price_ils      = $2,
                    base_price         = $5,
                    specifications     = COALESCE(specifications, '{}')::jsonb || $3::jsonb,
                    updated_at         = NOW()
                WHERE oem_number = $4 AND manufacturer = 'Porsche'
            """, price_excl, price_incl, specs, oem, base_price)
            n = int(res.split()[-1])

            if n == 0:
                res2 = await conn.execute("""
                    UPDATE parts_catalog SET
                        importer_price_ils = $1,
                        max_price_ils      = $2,
                        base_price         = $5,
                        oem_number         = $4,
                        specifications     = COALESCE(specifications, '{}')::jsonb || $3::jsonb,
                        updated_at         = NOW()
                    WHERE sku = $4 AND manufacturer = 'Porsche'
                """, price_excl, price_incl, specs, oem, base_price)
                n = int(res2.split()[-1])

            if n > 0:
                updated += n
            else:
                # Insert as new part
                await conn.execute("""
                    INSERT INTO parts_catalog (
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, part_condition, part_type,
                        importer_price_ils, max_price_ils, min_price_ils, base_price,
                        specifications, is_active, needs_oem_lookup, master_enriched,
                        created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $1, $2, 'Porsche', $3::uuid,
                        'Auto Parts', 'new', 'original',
                        $4, $5, $4, $7,
                        $6::jsonb, true, true, false,
                        NOW(), NOW()
                    ) ON CONFLICT (sku) DO NOTHING
                """, oem, r["desc"], mfr_id, price_excl, price_incl, specs, base_price)
                inserted += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  price error [{oem}]: {e}")

    return updated, inserted, errors


async def add_oem_prefix_fitment(conn, mfr_id: str, dry_run: bool = False) -> int:
    """
    For ALL Porsche parts without fitment, derive model from OEM number prefix
    and insert part_vehicle_fitment rows.
    """
    print("\n[OEM-Prefix Fitment] Loading Porsche parts without fitment...")
    parts = await conn.fetch("""
        SELECT pc.id, pc.oem_number
        FROM parts_catalog pc
        WHERE pc.manufacturer = 'Porsche'
          AND pc.is_active = true
          AND pc.oem_number IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM part_vehicle_fitment pvf WHERE pvf.part_id = pc.id
          )
    """)
    print(f"  Parts without fitment: {len(parts):,}")

    inserted = 0
    skipped_no_map = 0

    for i, part in enumerate(parts):
        pid = str(part['id'])
        oem = str(part['oem_number'] or "")
        fitment_entries = get_fitment(oem)

        if not fitment_entries:
            skipped_no_map += 1
            continue

        for entry in fitment_entries:
            try:
                if not dry_run:
                    await conn.execute("""
                        INSERT INTO part_vehicle_fitment (
                            id, part_id, manufacturer, manufacturer_id,
                            model, year_from, year_to, notes,
                            created_at, updated_at
                        ) VALUES (
                            gen_random_uuid(), $1::uuid, 'Porsche', $2::uuid,
                            $3, $4, $5,
                            'OEM prefix fitment mapping',
                            NOW(), NOW()
                        ) ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                    """, pid, mfr_id, entry["model"],
                        entry["year_from"], entry.get("year_to"))
                inserted += 1
            except Exception as e:
                if inserted < 3:
                    print(f"  fitment error [{oem}]: {e}")

        if (i + 1) % 5000 == 0:
            print(f"  {i+1:,}/{len(parts):,} processed, fitment_rows={inserted:,}")

    print(f"  OEM-prefix fitment: {inserted:,} rows, skipped_no_map={skipped_no_map:,}")
    return inserted


async def run(dry_run: bool = False):
    if not DB_URL and not dry_run:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()

    try:
        mfr = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)='porsche' LIMIT 1")
        if not mfr:
            print("ERROR: Porsche not in car_brands")
            return
        mfr_id = str(mfr["id"])

        # ─── Part 1: PDF prices ────────────────────────────────────────────
        print(f"\n=== PORSCHE PDF PRICE IMPORT ===")
        pdf_parts = extract_pdf_parts(PDF_PATH)
        print(f"Extracted {len(pdf_parts)} parts from PDF")
        if pdf_parts:
            print("Sample:")
            for p in pdf_parts[:5]:
                print(f"  [{p['oem']}] {p['desc'][:50]} ₪{p['price_incl_vat']}")

        if not dry_run:
            u, ins, err = await import_pdf_prices(conn, pdf_parts, mfr_id)
            print(f"  Prices: updated={u} inserted={ins} errors={err}")
        else:
            print(f"  [DRY RUN] Would process {len(pdf_parts)} price records")

        # ─── Part 2: OEM-prefix fitment ────────────────────────────────────
        print(f"\n=== PORSCHE OEM-PREFIX FITMENT ===")
        before = await conn.fetchval(
            "SELECT COUNT(DISTINCT part_id) FROM part_vehicle_fitment pvf "
            "JOIN parts_catalog pc ON pvf.part_id=pc.id WHERE pc.manufacturer='Porsche'"
        )
        fitment_rows = await add_oem_prefix_fitment(conn, mfr_id, dry_run=dry_run)
        after = await conn.fetchval(
            "SELECT COUNT(DISTINCT part_id) FROM part_vehicle_fitment pvf "
            "JOIN parts_catalog pc ON pvf.part_id=pc.id WHERE pc.manufacturer='Porsche'"
        )

        elapsed = time.monotonic() - t0
        print(f"\n=== DONE ({elapsed:.1f}s) ===")
        print(f"  PDF parts: {len(pdf_parts)}")
        print(f"  Fitment rows added: {fitment_rows:,}")
        print(f"  Porsche parts with fitment: {before:,} → {after:,}")

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
    print(f"Mode: {'DRY RUN' if dry else 'LIVE'}")
    asyncio.run(run(dry_run=dry))
