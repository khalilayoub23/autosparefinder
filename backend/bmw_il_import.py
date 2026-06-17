#!/usr/bin/env python3
"""
BMW IL Price + Fitment Import
=============================
Source: /app/bmw_parts.json (bmw.co.il official price list)
JSON fields:
  oem_number      — BMW OEM part number (11-digit numeric)
  name_he         — Hebrew part name
  model           — specific variant or 'מרובה דגמים' (multiple)
  price_ils       — price excl. VAT
  price_ils_vat   — price incl. VAT
  is_original     — bool
  source          — 'bmw.co.il'

Pricing: official BMW IL retail prices incl. VAT
  importer_price_ils = price_ils    (excl. VAT — our cost reference)
  max_price_ils      = price_ils_vat (incl. VAT — official retail)
  base_price         = price_ils_vat (match official retail per 45% margin policy)

Fitment: map variant name to series, insert part_vehicle_fitment rows
"""

import asyncio
import json
import os
import re
import sys
import time
import asyncpg

JSON_PATH = "/app/bmw_parts.json"
DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
MANUFACTURER = "BMW"
SOURCE = "BMW IL official price list (bmw.co.il)"
IMPORTER = "Champion Motors Ltd - BMW Israel"


def parse_model(raw: str) -> str | None:
    """Map BMW variant name (like '520I BUSINESS') to clean series model."""
    if not raw:
        return None
    m = raw.strip()
    if m in ("מרובה דגמים", " ", ""):
        return None

    upper = m.upper()

    # iX / iX3 / i-series (must check before X-series and numeric)
    if re.match(r"^IX3\b", upper):
        return "iX3"
    if re.match(r"^IX\b", upper):
        return "iX"
    if re.match(r"^I3\b", upper):
        return "i3"
    if re.match(r"^I4\b", upper):
        return "i4"
    if re.match(r"^I5\b", upper):
        return "i5"
    if re.match(r"^I7\b", upper):
        return "i7"
    if re.match(r"^I8\b", upper):
        return "i8"

    # XM (before X6/X7 check)
    if re.match(r"^XM\b", upper):
        return "XM"

    # X series
    for x_model in ("X7", "X6", "X5", "X4", "X3", "X2", "X1"):
        if re.match(rf"^{x_model}\b", upper):
            return x_model

    # Z series
    if re.match(r"^Z4\b", upper):
        return "Z4"
    if re.match(r"^Z8\b", upper):
        return "Z8"

    # M-prefixed series variants (M235, M340, M550, M850 etc.) → parent series
    m_series_match = re.match(r"^M([2-8])\d{2}", upper)
    if m_series_match:
        digit = int(m_series_match.group(1))
        return f"{digit} Series"

    # Pure M models (M2, M3, M4, M5, M6, M8)
    m_pure = re.match(r"^M([2-9])[\s\-]", upper)
    if m_pure:
        return f"M{m_pure.group(1)}"

    # Numeric series (1xx-8xx)
    num_match = re.match(r"^(\d{3})", upper)
    if num_match:
        num = int(num_match.group(1))
        if 100 <= num <= 129:
            return "1 Series"
        if 200 <= num <= 240:
            return "2 Series"
        if 316 <= num <= 340:
            return "3 Series"
        if 400 <= num <= 440:
            return "4 Series"
        if 500 <= num <= 545:
            return "5 Series"
        if 600 <= num <= 650:
            return "6 Series"
        if 700 <= num <= 760:
            return "7 Series"
        if 800 <= num <= 860:
            return "8 Series"

    # Motorcycles — return as-is (just normalise)
    for moto_prefix, moto_model in [
        ("R1250GS", "R 1250 GS"),
        ("R1200GS", "R 1200 GS"),
        ("R1200RT", "R 1200 RT"),
        ("S1000RR", "S 1000 RR"),
        ("S1000XR", "S 1000 XR"),
        ("K1600GT", "K 1600 GT"),
        ("K1600", "K 1600"),
        ("F800", "F 800 GS"),
        ("G 310", "G 310"),
        ("G310", "G 310"),
        ("C400", "C 400"),
    ]:
        if upper.replace(" ", "").startswith(moto_prefix.replace(" ", "")):
            return moto_model

    return None


async def run():
    if not DB_URL:
        print("ERROR: DATABASE_URL not set"); sys.exit(1)

    with open(JSON_PATH) as f:
        data = json.load(f)
    parts_json = data if isinstance(data, list) else data.get("parts", [])
    print(f"Loaded {len(parts_json):,} parts from JSON")

    conn = await asyncpg.connect(DB_URL)
    t0 = time.monotonic()

    try:
        # Get BMW manufacturer_id
        mfr = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)='bmw' LIMIT 1")
        if not mfr:
            print("ERROR: BMW not found in car_brands"); return
        mfr_id = str(mfr["id"])
        print(f"BMW manufacturer_id: {mfr_id}")

        # Deduplicate by OEM number (take highest price where duplicated)
        deduped = {}
        for p in parts_json:
            oem = str(p.get("oem_number", "")).strip()
            if not oem or not p.get("price_ils"):
                continue
            existing = deduped.get(oem)
            if not existing or (p.get("price_ils", 0) > existing.get("price_ils", 0)):
                deduped[oem] = p
        print(f"Unique OEM numbers with price: {len(deduped):,}")

        # ─── Phase 1: Price update ───────────────────────────────────────────
        print("\n=== PHASE 1: PRICE UPDATE ===")
        price_updated = 0
        price_not_found = 0
        price_errors = 0

        for oem, part in deduped.items():
            cost = float(part["price_ils"])
            retail = float(part["price_ils_vat"])

            spec_patch = json.dumps({
                "source": SOURCE, "importer": IMPORTER, "vat_included": True
            })
            try:
                res = await conn.execute("""
                    UPDATE parts_catalog SET
                        importer_price_ils = $1,
                        max_price_ils      = $2,
                        base_price         = $2,
                        specifications = COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                        updated_at = NOW()
                    WHERE oem_number = $4
                      AND manufacturer = 'BMW'
                      AND is_active = true
                """, cost, retail, spec_patch, oem)
                n = int(res.split()[-1])
                if n > 0:
                    price_updated += n
                else:
                    price_not_found += 1
            except Exception as e:
                price_errors += 1
                if price_errors <= 3:
                    print(f"  price error [{oem}]: {e}")

            if (price_updated + price_not_found) % 5000 == 0 and price_updated + price_not_found > 0:
                print(f"  progress: updated={price_updated:,} not_found={price_not_found:,}")

        print(f"  Prices updated: {price_updated:,}, not found: {price_not_found:,}, errors: {price_errors}")

        # ─── Phase 2: Fitment from model name ────────────────────────────────
        print("\n=== PHASE 2: FITMENT FROM MODEL NAMES ===")
        fitment_inserted = 0
        fitment_skipped_no_model = 0
        fitment_errors = 0
        already_has_fitment = 0

        for oem, part in deduped.items():
            raw_model = str(part.get("model", "") or "")
            clean_model = parse_model(raw_model)
            if not clean_model:
                fitment_skipped_no_model += 1
                continue

            # Get part IDs for this OEM
            part_ids = await conn.fetch("""
                SELECT id FROM parts_catalog
                WHERE oem_number = $1 AND manufacturer = 'BMW' AND is_active = true
            """, oem)

            for row in part_ids:
                pid = str(row["id"])
                # Check if fitment already exists for this model
                exists = await conn.fetchval("""
                    SELECT 1 FROM part_vehicle_fitment
                    WHERE part_id = $1::uuid AND manufacturer = 'BMW' AND model = $2
                """, pid, clean_model)
                if exists:
                    already_has_fitment += 1
                    continue

                try:
                    await conn.execute("""
                        INSERT INTO part_vehicle_fitment (
                            id, part_id, manufacturer, manufacturer_id,
                            model, year_from, year_to, notes,
                            created_at, updated_at
                        ) VALUES (
                            gen_random_uuid(), $1::uuid, 'BMW', $2::uuid,
                            $3, 2000, NULL,
                            'Fitment from BMW IL official price list variant name',
                            NOW(), NOW()
                        ) ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                    """, pid, mfr_id, clean_model)
                    fitment_inserted += 1
                except Exception as e:
                    fitment_errors += 1
                    if fitment_errors <= 3:
                        print(f"  fitment error [{oem}]: {e}")

        print(f"  Fitment rows inserted: {fitment_inserted:,}")
        print(f"  Already had fitment: {already_has_fitment:,}")
        print(f"  Skipped (multi-model/empty): {fitment_skipped_no_model:,}")
        print(f"  Errors: {fitment_errors}")

        # ─── Summary ─────────────────────────────────────────────────────────
        elapsed = time.monotonic() - t0
        with_price = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='BMW' AND is_active=true AND importer_price_ils>0"
        )
        with_fitment = await conn.fetchval("""
            SELECT COUNT(DISTINCT pc.id) FROM parts_catalog pc
            JOIN part_vehicle_fitment pvf ON pvf.part_id=pc.id
            WHERE pc.manufacturer='BMW'
        """)
        total = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='BMW' AND is_active=true")

        print(f"\n=== BMW IMPORT DONE ({elapsed:.1f}s) ===")
        print(f"  Total BMW parts: {total:,}")
        print(f"  With IL price now: {with_price:,}")
        print(f"  With fitment now: {with_fitment:,}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
