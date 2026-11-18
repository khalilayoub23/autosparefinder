#!/usr/bin/env python3
"""
Import IL prices from MIXED CARS BRANDS.xlsx (Renault, Nissan, Xpeng, etc.)
Row format: [date, availability, code, part_type, description_he, price_ils_consumer, oem_number]
OEM prefix → manufacturer: RE=Renault, NI/NF/NV=Nissan, XP=Xpeng, CH=Chevrolet, JM=Jaecoo

Pricing: price_ils_consumer is CONSUMER PRICE incl. 17% VAT
  importer_price_ils = price / 1.17
  max_price_ils      = price
  base_price         = NOT written here (base_price_rule.apply_to_parts)

Usage:
  python3 mixed_brands_import.py --file /app/uploads/MIXED\ CARS\ BRANDS.xlsx
"""
import asyncio, os, sys, time, json, argparse
import asyncpg
import base_price_rule

# ONE category source of truth. This INSERT used to hardcode 'accessories'
# positionally, which is a REAL category — so the self-healing categorizer
# (which only re-processes 'כללי') would never revisit those parts.
from category_map import categorize_on_ingest

try:
    import openpyxl
except ImportError:
    print("pip install openpyxl"); sys.exit(1)

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

# OEM prefix → (manufacturer, strip_len)
# strip_len = chars to try stripping for normalized matching
PREFIX_MAP = {
    "RE": "Renault",
    "NI": "Nissan",
    "NF": "Nissan",
    "NV": "Nissan",
    "XP": "Xpeng",
    "CH": "Chevrolet",
    "JM": "Jaecoo",
}


def normalize_oem(oem: str) -> list[str]:
    """Return OEM variants to try for matching."""
    oem = oem.strip()
    variants = [oem]
    # Also try without internal spaces (after prefix)
    no_space = oem.replace(" ", "")
    if no_space != oem:
        variants.append(no_space)
    # Try with normalized hyphens
    no_hyphen = oem.replace("-", "")
    if no_hyphen != oem and no_hyphen not in variants:
        variants.append(no_hyphen)
    return variants


def load_xlsx(path: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    parts = []
    skipped_header = skipped_no_oem = skipped_no_price = skipped_unknown = 0

    for row in ws.iter_rows(values_only=True):
        if not row or not row[6]:
            skipped_no_oem += 1
            continue

        oem = str(row[6]).strip()
        # Skip header rows
        if oem in ("פריט", "OEM", "SKU", ""):
            skipped_header += 1
            continue

        pfx = oem[:2].upper()
        brand = PREFIX_MAP.get(pfx)
        if not brand:
            skipped_unknown += 1
            continue

        price_raw = row[5]
        if price_raw is None:
            skipped_no_price += 1
            continue
        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            skipped_no_price += 1
            continue

        if price <= 0:
            skipped_no_price += 1
            continue

        name_he = str(row[4] or "").strip()
        avail = str(row[1] or "").strip()
        is_avail = "זמין" in avail and "לא זמין" not in avail

        cost    = round(price / (1 + VAT), 2)
        retail  = round(price, 2)

        parts.append({
            "oem": oem,
            "brand": brand,
            "name_he": name_he,
            "cost": cost,
            "retail": retail,
            "available": is_avail,
        })

    wb.close()
    print(f"Parsed {len(parts):,} parts | skipped: header={skipped_header} unknown={skipped_unknown} no_price={skipped_no_price}")
    return parts


async def run(xlsx_path: str):
    parts = load_xlsx(xlsx_path)
    if not parts:
        print("No parts parsed"); return

    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()

    updated = inserted = skipped = 0
    touched_part_ids = []
    brand_stats: dict[str, dict] = {}

    for p in parts:
        brand = p["brand"]
        oem = p["oem"]
        cost = p["cost"]
        retail = p["retail"]

        spec = json.dumps({
            "importer": f"{brand} IL (mixed brands catalog)",
            "source": "mixed_brands_xlsx",
            "vat_rate": VAT, "vat_included": True,
            "consumer_price_ils": retail,
            "available": p["available"],
        }, ensure_ascii=False)

        # Try multiple OEM variants for matching
        matched = False
        for oem_variant in normalize_oem(oem):
            res = await conn.fetch("""
                UPDATE parts_catalog SET
                    importer_price_ils = CASE WHEN importer_price_ils IS NULL OR importer_price_ils = 0
                        THEN $1 ELSE importer_price_ils END,
                    max_price_ils      = CASE WHEN max_price_ils IS NULL OR max_price_ils = 0
                        THEN $2 ELSE max_price_ils END,
                    specifications     = COALESCE(specifications,'{}')::jsonb || $3::jsonb,
                    updated_at         = NOW()
                WHERE oem_number = $4 AND manufacturer = $5 AND is_active = true
                RETURNING id::text
            """, cost, retail, spec, oem_variant, brand)
            n = len(res)
            if n > 0:
                updated += n
                touched_part_ids.extend(r_[0] for r_ in res)
                matched = True
                break

        if not matched:
            # Insert new part
            try:
                new_id = await conn.fetchval("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, name_he, manufacturer, category,
                        base_price, importer_price_ils, max_price_ils, min_price_ils,
                        part_type, part_condition, is_active,
                        needs_oem_lookup, master_enriched, specifications,
                        created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(), $1, $1, $2, $2, $3, $8,
                        0.0, $4, $5, $4,
                        'original', 'new', $6,
                        true, false, $7::jsonb,
                        NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        importer_price_ils = CASE WHEN parts_catalog.importer_price_ils IS NULL OR parts_catalog.importer_price_ils = 0
                            THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                        max_price_ils      = CASE WHEN parts_catalog.max_price_ils IS NULL OR parts_catalog.max_price_ils = 0
                            THEN EXCLUDED.max_price_ils ELSE parts_catalog.max_price_ils END,
                        updated_at = NOW()
                    RETURNING id::text
                """, oem, p["name_he"] or oem, brand, cost, retail, True, spec,
                    categorize_on_ingest(name_he=p["name_he"] or oem))
                inserted += 1
                if new_id:
                    touched_part_ids.append(new_id)
            except Exception:
                skipped += 1

        bs = brand_stats.setdefault(brand, {"updated": 0, "inserted": 0})
        if matched:
            bs["updated"] += 1
        elif skipped == 0:
            bs["inserted"] += 1

    await base_price_rule.apply_and_report(conn, touched_part_ids, "mixed_brands_import")
    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.0f}s: updated={updated:,} inserted={inserted:,} skipped={skipped:,}")
    print("\nPer-brand breakdown:")
    for brand, s in sorted(brand_stats.items()):
        r = await conn.fetchrow(
            "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
            "FROM parts_catalog WHERE manufacturer=$1 AND is_active=true", brand)
        print(f"  {brand:<15} upd={s['updated']:>6,} ins={s['inserted']:>5,} | coverage={r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")

    await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="/app/uploads/MIXED CARS BRANDS.xlsx")
    args = ap.parse_args()
    asyncio.run(run(args.file))
