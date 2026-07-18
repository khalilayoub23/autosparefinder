#!/usr/bin/env python3
"""Import Isuzu parts from official Israeli distributor Excel (isuzu-dmax.co.il)."""
import asyncio
import json
import re
import uuid

import asyncpg
import openpyxl

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)
XLSX_PATH = "/tmp/isuzuFile.xlsx"
MANUFACTURER = "Isuzu"
MANUFACTURER_ID = "a5f0f44e-814d-4fa2-b6b6-dd1b3175d855"
SOURCE = "isuzu-dmax.co.il"

SKIP_VEHICLES = {
    "כל הדגמים", "שמוש כללי איסוזו", "ALL MAKES Universali",
    "חלקי משאיות ALL MAKES", "ACDelco  ALL MAKES  IL",
    "56/75 Oil Dilution הסכם פשר", "Service campaign EGR FIL",
    "AVIS", "דגם מנוע",
}


def parse_vehicle(raw: str) -> list[dict]:
    v = (raw or "").strip()
    if not v or v in SKIP_VEHICLES:
        return []

    entry: dict = {"manufacturer": "Isuzu", "source": SOURCE}

    years = re.findall(r'\b(19\d{2}|20\d{2})\b', v)
    if years:
        y = int(years[0])
        entry["year_from"] = y
        entry["year_to"] = y

    vl = v.lower()

    if any(k in vl for k in ("d-max", "dmax", "di-max", "rg01", "rg12", "rg14", "rg", "rt-88", "rt-66", "rt-93")) or "די-מקס" in v:
        entry["model"] = "D-MAX"
    elif "טרופר" in v or "trooper" in vl:
        entry["model"] = "Trooper"
    elif "rodeo" in vl or "רודאו" in v:
        entry["model"] = "Rodeo"
    elif "frontera" in vl:
        entry["model"] = "Frontera"
    elif "איפון" in v or "ippon" in vl or "טנדר" in v:
        entry["model"] = "Pickup"
    elif "fargo" in vl:
        entry["model"] = "Fargo"
    elif any(k in vl for k in ("elf", "npr", "nkr", "nqr", "frr", "ftr", "fsr", "ldt", "mdt")) or "משאית" in v:
        entry["model"] = "N-Series"
    elif "savana" in vl:
        entry["model"] = "Savana"
        entry["manufacturer"] = "GMC"
    else:
        entry["model"] = v[:60]

    return [entry]


def build_sku(oem: str) -> str:
    clean = re.sub(r'[^\w\-]', '-', oem.strip()).upper()
    clean = re.sub(r'-+', '-', clean).strip('-')
    return f"ISUZU-{clean}"


async def main() -> None:
    wb = openpyxl.load_workbook(XLSX_PATH)
    ws = wb.active

    # Collect rows, merging compatible_vehicles for duplicate OEM numbers
    seen: dict[str, dict] = {}

    for row in ws.iter_rows(min_row=3, values_only=True):
        _, catalog_num, name_he, _, stock, price, _, vehicle = row
        if not catalog_num:
            continue
        oem = str(catalog_num).strip()
        if not oem:
            continue

        sku = build_sku(oem)
        compat = parse_vehicle(vehicle or "")

        if sku in seen:
            existing = seen[sku]["compatible_vehicles"]
            for nv in compat:
                if nv not in existing:
                    existing.append(nv)
        else:
            # Prices from isuzu-dmax.co.il are consumer retail (IL importer reference)
            raw_price = float(price) if price else None
            il_retail = raw_price  # treat as IL retail incl. VAT
            seen[sku] = {
                "id": str(uuid.uuid4()),
                "sku": sku,
                "oem_number": oem,
                "name": str(name_he or oem).strip(),
                "name_he": str(name_he or "").strip() or None,
                "manufacturer": MANUFACTURER,
                "manufacturer_id": MANUFACTURER_ID,
                "part_type": "original",
                "part_condition": "new",
                "base_price": round(il_retail / 1.18 * 1.45, 2) if il_retail else 0.0,
                "importer_price_ils": round(il_retail / 1.18, 2) if il_retail else 0.0,
                "online_price_ils": None,
                "max_price_ils": il_retail,
                "min_price_ils": il_retail,
                "is_active": True,
                "compatible_vehicles": compat,
                "is_safety_critical": False,
                "needs_oem_lookup": False,
                "master_enriched": False,
            }

    rows = list(seen.values())
    print(f"Unique parts to import: {len(rows)}")

    conn = await asyncpg.connect(DB_DSN)
    inserted = updated = errors = 0

    try:
        for r in rows:
            try:
                result = await conn.execute(
                    """
                    INSERT INTO parts_catalog (
                        id, sku, oem_number, name, name_he, manufacturer, manufacturer_id,
                        part_type, part_condition, base_price, importer_price_ils,
                        max_price_ils, min_price_ils,
                        is_active, compatible_vehicles, is_safety_critical, needs_oem_lookup,
                        master_enriched
                    ) VALUES (
                        $1::uuid, $2, $3, $4, $5, $6, $7::uuid,
                        $8, $9, $10, $11, $12, $12,
                        $13, $14::jsonb, $15, $16, $17
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        oem_number         = EXCLUDED.oem_number,
                        name_he            = COALESCE(EXCLUDED.name_he, parts_catalog.name_he),
                        manufacturer_id    = EXCLUDED.manufacturer_id,
                        base_price         = EXCLUDED.base_price,
                        importer_price_ils = EXCLUDED.importer_price_ils,
                        max_price_ils      = EXCLUDED.max_price_ils,
                        min_price_ils      = EXCLUDED.min_price_ils,
                        is_active          = TRUE,
                        compatible_vehicles = CASE
                            WHEN parts_catalog.compatible_vehicles IS NULL THEN EXCLUDED.compatible_vehicles
                            ELSE parts_catalog.compatible_vehicles
                        END,
                        updated_at         = NOW()
                    """,
                    r["id"], r["sku"], r["oem_number"], r["name"], r["name_he"],
                    r["manufacturer"], r["manufacturer_id"],
                    r["part_type"], r["part_condition"],
                    r["base_price"], r["importer_price_ils"], r["max_price_ils"],
                    r["is_active"], json.dumps(r["compatible_vehicles"]),
                    r["is_safety_critical"], r["needs_oem_lookup"], r["master_enriched"],
                )
                if "INSERT 0 1" in result:
                    inserted += 1
                else:
                    updated += 1
            except Exception as e:
                print(f"  Error [{r['sku']}]: {e}")
                errors += 1
    finally:
        await conn.close()

    print(f"\nDone: inserted={inserted}  updated={updated}  errors={errors}")
    print(f"Total Isuzu parts in DB now: running query...")

    conn2 = await asyncpg.connect(DB_DSN)
    total = await conn2.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer = 'Isuzu'")
    await conn2.close()
    print(f"  Isuzu parts total: {total}")


if __name__ == "__main__":
    asyncio.run(main())
