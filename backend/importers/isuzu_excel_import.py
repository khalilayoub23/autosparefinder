#!/usr/bin/env python3
"""Import Isuzu parts from official Israeli distributor Excel (isuzu-dmax.co.il)."""
import asyncio
import json
import re
import uuid

import asyncpg
import openpyxl

# ONE category source of truth — categorize at INGEST so parts never land
# with a NULL category and depend on the self-healing task to find them.
from category_map import categorize_on_ingest
from warranty_policy import resolve as _warranty_resolve

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)
XLSX_PATH = "/tmp/isuzuFile.xlsx"
MANUFACTURER = "Isuzu"
MANUFACTURER_ID = "a5f0f44e-814d-4fa2-b6b6-dd1b3175d855"
SOURCE = "isuzu-dmax.co.il"
SUPPLIER_NAME = "Isuzu Israel - isuzu-dmax.co.il"
SUPPLIER_URL = "https://isuzu-dmax.co.il"
DELIVERY_DAYS = 3

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


async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.90,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL,
    )
    return sid


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
            _wmonths, _wsource = _warranty_resolve(None)  # Isuzu Excel has no warranty column
            _name_he = str(name_he or "").strip()
            seen[sku] = {
                "id": str(uuid.uuid4()),
                "sku": sku,
                "oem_number": oem,
                "name": str(name_he or oem).strip(),
                "name_he": _name_he or None,
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
                "warranty_months": _wmonths,
                "warranty_source": _wsource,
                "specs": json.dumps({
                    "source": SOURCE,
                    "source_url": "https://isuzu-dmax.co.il",
                    "importer": "Isuzu Israel",
                    "currency": "ILS",
                    "vat_included": True,
                    "vat_rate": 0.18,
                    "warranty_months": _wmonths,
                    "category_hint": "original",
                    "name_he": _name_he,
                    "vehicle_context": str(vehicle or "").strip(),
                }),
            }

    rows = list(seen.values())
    print(f"Unique parts to import: {len(rows)}")

    conn = await asyncpg.connect(DB_DSN)
    supplier_id = await ensure_supplier(conn)
    inserted = updated = errors = 0
    fitment_rows = 0
    supplier_rows = 0

    try:
        for r in rows:
            try:
                async with conn.transaction():
                    part_id = await conn.fetchval(
                        """
                        INSERT INTO parts_catalog (
                            id, sku, oem_number, name, name_he, manufacturer, manufacturer_id,
                            part_type, part_condition, base_price, importer_price_ils,
                            max_price_ils, min_price_ils,
                            is_active, compatible_vehicles, is_safety_critical, needs_oem_lookup,
                            master_enriched, category, specifications
                        ) VALUES (
                            $1::uuid, $2, $3, $4, $5, $6, $7::uuid,
                            $8, $9, $10, $11, $12, $12,
                            $13, $14::jsonb, $15, $16, $17, $18, $19::jsonb
                        )
                        ON CONFLICT (sku) DO UPDATE SET
                            oem_number         = EXCLUDED.oem_number,
                            name_he            = COALESCE(EXCLUDED.name_he, parts_catalog.name_he),
                            manufacturer_id    = EXCLUDED.manufacturer_id,
                            base_price         = EXCLUDED.base_price,
                            importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                            max_price_ils      = EXCLUDED.max_price_ils,
                            min_price_ils      = EXCLUDED.min_price_ils,
                            is_active          = TRUE,
                            compatible_vehicles = CASE
                                WHEN parts_catalog.compatible_vehicles IS NULL THEN EXCLUDED.compatible_vehicles
                                ELSE parts_catalog.compatible_vehicles
                            END,
                            specifications     = COALESCE(parts_catalog.specifications, '{}'::jsonb) || EXCLUDED.specifications,
                            updated_at         = NOW()
                        RETURNING id
                        """,
                        r["id"], r["sku"], r["oem_number"], r["name"], r["name_he"],
                        r["manufacturer"], r["manufacturer_id"],
                        r["part_type"], r["part_condition"],
                        r["base_price"], r["importer_price_ils"], r["max_price_ils"],
                        r["is_active"], json.dumps(r["compatible_vehicles"]),
                        r["is_safety_critical"], r["needs_oem_lookup"], r["master_enriched"],
                        categorize_on_ingest(name=r["name"], name_he=r["name_he"]),
                        r["specs"],
                    )
                    if part_id:
                        inserted += 1
                    else:
                        part_id = await conn.fetchval(
                            "SELECT id FROM parts_catalog WHERE sku=$1", r["sku"]
                        )
                        updated += 1

                    if part_id:
                        # RULE (fitment): the parsed vehicles must reach the TABLE the
                        # fitment-first search joins — a compatible_vehicles JSONB blob
                        # is not a substitute.
                        for v in r["compatible_vehicles"]:
                            if not v.get("model"):
                                continue
                            try:
                                await conn.execute(
                                    """
                                    INSERT INTO part_vehicle_fitment
                                        (id, part_id, manufacturer, model,
                                         year_from, year_to, created_at)
                                    VALUES (gen_random_uuid(), $1::uuid, $2::varchar,
                                            $3::varchar, $4::int, $5::int, NOW())
                                    ON CONFLICT (part_id, manufacturer, model, year_from)
                                    DO UPDATE SET year_to = GREATEST(
                                        COALESCE(part_vehicle_fitment.year_to, 0),
                                        COALESCE(EXCLUDED.year_to, 0))
                                    """,
                                    part_id,
                                    (v.get("manufacturer") or MANUFACTURER)[:100],
                                    v["model"][:150],
                                    int(v.get("year_from") or 1990),
                                    int(v["year_to"]) if v.get("year_to") else None,
                                )
                                fitment_rows += 1
                            except Exception as fe:
                                print(f"  [fitment] {r['sku']}: {str(fe)[:90]}")

                        # supplier_parts upsert
                        avail = "in_stock" if (r["importer_price_ils"] or 0) > 0 else "out_of_stock"
                        await conn.execute(
                            """
                            INSERT INTO supplier_parts(
                                id, supplier_id, part_id, supplier_sku,
                                price_ils, price_usd, availability, is_available,
                                warranty_months, warranty_source,
                                estimated_delivery_days, supplier_url,
                                created_at, updated_at)
                            VALUES(gen_random_uuid(),$1::uuid,$2::uuid,$3,
                                   $4,0.0,$5,$6,$7,$8,$9,$10,NOW(),NOW())
                            ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
                                price_ils        = EXCLUDED.price_ils,
                                is_available     = EXCLUDED.is_available,
                                availability     = EXCLUDED.availability,
                                warranty_months  = EXCLUDED.warranty_months,
                                warranty_source  = EXCLUDED.warranty_source,
                                updated_at       = NOW()
                            """,
                            supplier_id, str(part_id), r["sku"],
                            r["importer_price_ils"] or 0.0,
                            avail, (r["importer_price_ils"] or 0) > 0,
                            r["warranty_months"], r["warranty_source"],
                            DELIVERY_DAYS, SUPPLIER_URL,
                        )
                        supplier_rows += 1

            except Exception as e:
                print(f"  Error [{r['sku']}]: {e}")
                errors += 1
    finally:
        await conn.close()

    print(f"\nDone: inserted={inserted}  updated={updated}  supplier_rows={supplier_rows}  fitment={fitment_rows}  errors={errors}")
    print(f"Total Isuzu parts in DB now: running query...")

    conn2 = await asyncpg.connect(DB_DSN)
    total = await conn2.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer = 'Isuzu'")
    await conn2.close()
    print(f"  Isuzu parts total: {total}")


if __name__ == "__main__":
    asyncio.run(main())
