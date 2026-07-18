#!/usr/bin/env python3
"""
MG / SAIC Israel spare parts import pipeline.
Source: מחירון-סאייק-02.22.xlsx (Lubinski importer, Feb 2022)

Columns:
  0 = model (e.g. 'MGZS', 'MG3 חדש', '550D-1.8T-AT')
  1 = stock ('כן'/'לא')
  2 = price incl. 18% VAT (ILS)
  3 = warranty text
  4 = supplier ('SAIC MOTOR INTERNATIONAL LTD')
  5 = part type ('ORIGINAL')
  6 = part description (Hebrew)
  7 = OEM part number

Runs inside autospare_backend container: python3 /app/mg_import.py
"""

import sys
import os
import uuid
import json
import logging
from decimal import Decimal, ROUND_HALF_UP

import openpyxl
import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="[mg_import] %(levelname)s %(message)s",
)
log = logging.getLogger("mg_import")

# ── Database ──────────────────────────────────────────────────────────────────
_raw_url = os.environ.get(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare",
)
# psycopg2 needs plain postgresql://, not postgresql+asyncpg://
DSN = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

# ── MG brand constants ────────────────────────────────────────────────────────
MG_BRAND_ID = "341be223-5852-4f29-bd96-085ef2c5d07b"   # verified in car_brands
MG_BRAND_NAME = "MG"
VAT_RATE = Decimal("1.18")

# ── Model → fitment mapping ───────────────────────────────────────────────────
# Derived from vehicle_market_il kinuy_mishari values + degem_nm codes.
# Each entry: (canonical model name, year_from, year_to, engine_hint, degem hint)
MODEL_FITMENT: dict[str, list[dict]] = {
    "550D-1.8T-AT": [
        {"model": "MG 550", "year_from": 2010, "year_to": 2013,
         "engine_type": "1.8T", "transmission": "AT"},
    ],
    "350-1.5T-AT": [
        {"model": "MG 350", "year_from": 2012, "year_to": 2019,
         "engine_type": "1.5T", "transmission": "AT"},
    ],
    "MG-350 חדש": [
        {"model": "MG 350", "year_from": 2018, "year_to": 2022,
         "engine_type": "1.5T", "transmission": "AT"},
    ],
    "MG3 חדש": [
        {"model": "MG3", "year_from": 2014, "year_to": 2022,
         "engine_type": "1.5", "transmission": "MT/AT"},
    ],
    "MGZS": [
        {"model": "ZS", "year_from": 2017, "year_to": 2024,
         "engine_type": "1.5/1.3T", "transmission": "AT"},
    ],
    "MGZS חשמלי": [
        {"model": "ZS EV", "year_from": 2019, "year_to": 2025,
         "engine_type": "Electric", "transmission": "AT"},
    ],
    "PHEV": [
        {"model": "ZS EV", "year_from": 2020, "year_to": 2025,
         "engine_type": "PHEV", "transmission": "AT"},
        {"model": "HS HYBRID", "year_from": 2021, "year_to": 2025,
         "engine_type": "PHEV", "transmission": "AT"},
    ],
}

# ── Category guessing (keyword → slug) ───────────────────────────────────────
HE_CATEGORY_MAP: list[tuple[tuple[str, ...], str]] = [
    # engine / drivetrain
    (("מנוע", "בוכנה", "גל", "שסתום", "קמשפט", "מצנן שמן", "צינור שמן", "פולי", "רצועה"), "engine"),
    (("גיר", "תיבת הילוכים", "גירבוקס"), "gearbox"),
    (("מצמד", "דיסק", "סרן", "ציר"), "clutch-drivetrain"),
    (("בלם", "צלחת בלם", "רפידה", "מאסטר בלמים", "קליפר"), "brakes"),
    (("קפיץ", "בולם", "מיתלה", "זרוע", "ציר קדמי", "ציר אחורי", "הגה", "דחיף", "מוט"), "suspension-steering"),
    (("פנס", "פנסי", "נורה", "אורות", "פנס קדמי", "פנס אחורי", "בלינקר", "עדשה"), "lighting"),
    (("מצנן מים", "רדיאטור", "מאוורר", "תרמוסטט", "משאבת מים", "צינור מים"), "cooling"),
    (("מזגן", "קומפרסור מזגן", "צינור מזגן", "אידוי", "קבל"), "air-conditioning-heating"),
    (("פילטר", "סנן"), "filters"),
    (("פגוש", "כנף", "דלת", "מכסה מנוע", "רצפה", "גג", "אוגן", "מדרכה"), "body-exterior"),
    (("חיישן", "מחשב", "מודול", "יחידת בקרה", "חוט", "קופסה", "ממסר"), "electrical-sensors"),
    (("זנוק", "מזרק", "פחמן", "צינור דלק", "מיכל דלק", "משאבת דלק"), "fuel-air"),
    (("זרקור", "מראה", "ידית", "מושב", "שמשה", "זכוכית"), "interior-comfort"),
    (("מגב", "מגבים", "זרוע מגב"), "wipers-washers"),
    (("גלגל", "חישוק", "צמיג", "מיסב"), "wheels-bearings"),
    (("אגזוז", "קטליזטור", "פה צינור", "צינור אגזוז"), "exhaust"),
    (("רצועת תזמון", "שרשרת תזמון", "מותחן"), "belts-chains"),
    (("שמן", "נוזל", "קירור"), "fluids"),
    (("בורג", "אטם", "מהדק", "תושבת", "תומך", "פרופיל", "טבעת", "כיסוי", "מכסה", "פקק", "אום", "קישוט"), "service-general"),
    (("מצבר", "אלטרנטור", "מצת", "חוט הצתה"), "electrical-sensors"),
]

def guess_category(desc_he: str) -> str:
    if not desc_he:
        return "service-general"
    text = desc_he.strip()
    for keywords, cat in HE_CATEGORY_MAP:
        for kw in keywords:
            if kw in text:
                return cat
    # Fallback: try imported guess_category_by_text
    try:
        sys.path.insert(0, "/app")
        from categories import guess_category_by_text
        result = guess_category_by_text(text)
        if result:
            return result
    except Exception:
        pass
    return "service-general"


def load_excel(path: str) -> list[dict]:
    """Parse the MG Excel file and return clean data rows."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["סאייק פב.22"]
    rows = list(ws.iter_rows(values_only=True))

    records = []
    for r in rows[7:]:   # skip header rows 0-6
        if not r or len(r) < 8:
            continue
        model_raw = str(r[0]).strip() if r[0] else ""
        price_raw = r[2]
        oem_raw   = str(r[7]).strip() if r[7] else ""
        desc_raw  = str(r[6]).strip() if r[6] else ""

        # Skip section headers / dealer directory rows
        if model_raw not in MODEL_FITMENT:
            continue
        if not oem_raw or not price_raw:
            continue

        try:
            price_vat = Decimal(str(price_raw)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        except Exception:
            continue

        if price_vat <= 0:
            continue

        price_ex_vat = (price_vat / VAT_RATE).quantize(Decimal("0.01"), ROUND_HALF_UP)

        records.append({
            "model_key": model_raw,
            "oem_number": oem_raw,
            "desc_he": desc_raw,
            "price_vat": float(price_vat),
            "price_ex_vat": float(price_ex_vat),
            "in_stock": str(r[1]).strip() == "כן" if r[1] else False,
        })

    log.info("Loaded %d valid data rows from Excel", len(records))
    return records


def build_parts(records: list[dict]) -> list[dict]:
    """Deduplicate by OEM number and build parts_catalog rows."""
    # When the same OEM appears for multiple models, keep one canonical row
    # and record all models for fitment later.
    seen: dict[str, dict] = {}   # oem_number → part dict

    for rec in records:
        oem = rec["oem_number"]
        if oem not in seen:
            cat = guess_category(rec["desc_he"])
            seen[oem] = {
                "id": str(uuid.uuid4()),
                "sku": f"MG-{oem}",
                "oem_number": oem,
                "name": rec["desc_he"] or oem,
                "name_he": rec["desc_he"],
                "category": cat,
                "manufacturer": MG_BRAND_NAME,
                "manufacturer_id": MG_BRAND_ID,
                "base_price": rec["price_ex_vat"],
                "importer_price_ils": rec["price_vat"],
                "online_price_ils": rec["price_vat"],
                "part_type": "Original",
                "part_condition": "New",
                "is_active": True,
                "is_safety_critical": False,
                "needs_oem_lookup": False,
                "master_enriched": False,
                "models": [rec["model_key"]],
                "specifications": json.dumps({
                    "supplier": "SAIC MOTOR INTERNATIONAL LTD",
                    "warranty": "12 months / 100,000 km",
                    "source_model": rec["model_key"],
                }),
            }
        else:
            # Accumulate models for fitment
            if rec["model_key"] not in seen[oem]["models"]:
                seen[oem]["models"].append(rec["model_key"])

    parts = list(seen.values())
    log.info("Unique OEM numbers (distinct parts): %d", len(parts))
    return parts


def upsert_parts(conn, parts: list[dict]) -> dict[str, str]:
    """Insert/update parts_catalog. Returns {oem_number: part_id}."""
    SQL = """
    INSERT INTO parts_catalog (
        id, sku, name, name_he, category,
        manufacturer, manufacturer_id,
        oem_number, base_price, importer_price_ils, online_price_ils,
        part_type, part_condition,
        is_active, is_safety_critical, needs_oem_lookup, master_enriched,
        specifications, updated_at
    ) VALUES (
        %(id)s, %(sku)s, %(name)s, %(name_he)s, %(category)s,
        %(manufacturer)s, %(manufacturer_id)s,
        %(oem_number)s, %(base_price)s, %(importer_price_ils)s, %(online_price_ils)s,
        %(part_type)s, %(part_condition)s,
        %(is_active)s, %(is_safety_critical)s, %(needs_oem_lookup)s, %(master_enriched)s,
        %(specifications)s::jsonb, NOW()
    )
    ON CONFLICT (sku) DO UPDATE SET
        name            = EXCLUDED.name,
        name_he         = EXCLUDED.name_he,
        category        = EXCLUDED.category,
        oem_number      = EXCLUDED.oem_number,
        base_price      = EXCLUDED.base_price,
        importer_price_ils = EXCLUDED.importer_price_ils,
        online_price_ils   = EXCLUDED.online_price_ils,
        is_active       = TRUE,
        updated_at      = NOW()
    RETURNING id, sku, oem_number
    """
    oem_to_id: dict[str, str] = {}
    inserted = updated = 0

    with conn.cursor() as cur:
        BATCH = 25
        for i in range(0, len(parts), BATCH):
            batch = parts[i:i + BATCH]
            for p in batch:
                cur.execute(SQL, p)
                row = cur.fetchone()
                if row:
                    part_id, sku, oem = row
                    oem_to_id[oem] = str(part_id)
            conn.commit()
            log.info("  parts upsert: %d/%d", min(i + BATCH, len(parts)), len(parts))

    log.info("Parts upsert done. Total inserted/updated: %d", len(oem_to_id))
    return oem_to_id


def build_fitment_rows(parts: list[dict], oem_to_id: dict[str, str]) -> list[dict]:
    """Expand each part's model list into part_vehicle_fitment rows."""
    rows = []
    for p in parts:
        part_id = oem_to_id.get(p["oem_number"])
        if not part_id:
            continue
        for model_key in p["models"]:
            fitment_list = MODEL_FITMENT.get(model_key, [])
            for fit in fitment_list:
                rows.append({
                    "part_id": part_id,
                    "manufacturer": MG_BRAND_NAME,
                    "manufacturer_id": MG_BRAND_ID,
                    "model": fit["model"],
                    "year_from": fit["year_from"],
                    "year_to": fit.get("year_to"),
                    "engine_type": fit.get("engine_type"),
                    "transmission": fit.get("transmission"),
                    "notes": f"Source: SAIC IL price list Feb 2022 ({model_key})",
                })
    log.info("Fitment rows to insert: %d", len(rows))
    return rows


def upsert_fitment(conn, fitment_rows: list[dict]) -> int:
    SQL = """
    INSERT INTO part_vehicle_fitment (
        part_id, manufacturer, manufacturer_id, model,
        year_from, year_to, engine_type, transmission, notes
    ) VALUES (
        %(part_id)s, %(manufacturer)s, %(manufacturer_id)s, %(model)s,
        %(year_from)s, %(year_to)s, %(engine_type)s, %(transmission)s, %(notes)s
    )
    ON CONFLICT (part_id, manufacturer, model, year_from) DO UPDATE SET
        year_to      = EXCLUDED.year_to,
        engine_type  = EXCLUDED.engine_type,
        transmission = EXCLUDED.transmission,
        updated_at   = NOW()
    """
    count = 0
    with conn.cursor() as cur:
        BATCH = 25
        for i in range(0, len(fitment_rows), BATCH):
            batch = fitment_rows[i:i + BATCH]
            psycopg2.extras.execute_batch(cur, SQL, batch, page_size=BATCH)
            conn.commit()
            count += len(batch)
            log.info("  fitment upsert: %d/%d", count, len(fitment_rows))
    log.info("Fitment upsert done: %d rows", count)
    return count


def verify(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE manufacturer='MG') parts_count,
                COUNT(DISTINCT category) FILTER (WHERE manufacturer='MG') categories,
                MIN(base_price) FILTER (WHERE manufacturer='MG') min_price,
                MAX(base_price) FILTER (WHERE manufacturer='MG') max_price
            FROM parts_catalog WHERE is_active=TRUE AND manufacturer='MG'
        """)
        row = cur.fetchone()
        log.info("VERIFY parts_catalog: count=%s  categories=%s  price_range=₪%.2f–₪%.2f",
                 row[0], row[1], row[2] or 0, row[3] or 0)

        cur.execute("""
            SELECT model, COUNT(*) cnt, MIN(year_from), MAX(year_to)
            FROM part_vehicle_fitment
            WHERE manufacturer='MG'
            GROUP BY model ORDER BY model
        """)
        log.info("VERIFY fitment:")
        for r in cur.fetchall():
            log.info("  %-20s  %5d parts  %s–%s", r[0], r[1], r[2], r[3])


def main():
    xlsx_path = "/tmp/mg_parts.xlsx"
    if not os.path.exists(xlsx_path):
        log.error("Excel not found: %s", xlsx_path)
        sys.exit(1)

    log.info("=== MG Import Pipeline START ===")

    # Step 1 — parse Excel
    records = load_excel(xlsx_path)
    if not records:
        log.error("No records parsed from Excel")
        sys.exit(1)

    # Step 2 — deduplicate & build parts list
    parts = build_parts(records)

    # Step 3 — connect DB
    conn = psycopg2.connect(DSN)
    conn.autocommit = False

    try:
        # Step 4 — upsert parts_catalog
        oem_to_id = upsert_parts(conn, parts)

        # Step 5 — build & upsert fitment
        fitment_rows = build_fitment_rows(parts, oem_to_id)
        upsert_fitment(conn, fitment_rows)

        # Step 6 — verify
        verify(conn)

        log.info("=== MG Import Pipeline DONE ===")
        log.info("Summary: %d parts | %d fitment rows", len(oem_to_id), len(fitment_rows))

    except Exception as e:
        conn.rollback()
        log.error("Import failed: %s", e)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
