"""
Script: subaru_il_importer.py
Purpose: Harvest ALL Subaru OEM parts from Samelet Motors API and import to DB.

Process:
  1. Sweep samelet.com/api (site=subaru) with Hebrew letters + common parts terms
  2. Deduplicate by Material (OEM number)
  3. Save raw JSON to /app/subaru_il_parts.json
  4. Import to parts_catalog, supplier_parts, part_vehicle_fitment
  5. Run Meilisearch scoped sync for Subaru

Data Imported / Modified:
  - parts_catalog: sku, oem_number, name, name_he, manufacturer, manufacturer_id,
                   category, importer_price_ils, min_price_ils, max_price_ils,
                   specifications (vat_included, vat_rate, currency, source),
                   part_condition, aftermarket_tier, is_active, needs_oem_lookup,
                   master_enriched
  - part_vehicle_fitment: extracted from MatDescHe model mentions — queued to REX
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, availability,
                    warranty_months, estimated_delivery_days, supplier_url

Data Sources / Web Links:
  - Samelet Motors Subaru price list: https://samelet.com/form/parts-prices/subaru
  - API endpoint: https://samelet.com/api (POST, site=subaru)

Missing Data Delegation:
  - Fitment: parsed from Hebrew description; REX todo queued for gaps
  - Hebrew names: already present from API (MatDescHe)
  - English names: not provided by API — ai_catalog_builder fills later

Author: AutoSpareFinder Agent
Last Updated: 2026-06-02
"""

import asyncio
import asyncpg
import json
import time
import urllib.request
import urllib.parse
import re
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
DSN = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
MANUFACTURER = "Subaru"
MANUFACTURER_ID = "88a04aee-d7d5-45ff-8308-4c6b50c67c0e"
SUPPLIER_NAME = "Subaru Israel - Samelet Motors"
SUPPLIER_URL = "https://www.subaru.co.il"
SAMELET_TOKEN = "4b533b024047e159f4723a4406d06bd8"
SAMELET_SITE = "subaru"
SAMELET_API = "https://samelet.com/api"
VAT_RATE = 0.18
WARRANTY_MONTHS = 24
DELIVERY_DAYS = 3
OUTPUT_FILE = "/app/subaru_il_parts.json"

# Hebrew letters
HEBREW = list("אבגדהוזחטיכלמנסעפצקרשת")

# Common Israeli auto parts terms in Hebrew + Subaru OEM prefixes
TERMS_DESC = HEBREW + [
    "בלם", "שמן", "מסנן", "משאבה", "חיישן", "חגורה", "מנוע", "גיר",
    "זרוע", "קפיץ", "בולם", "גלגל", "דיסק", "רפידה", "פנס", "מראה",
    "מגב", "מצנן", "מאוורר", "מדחס", "מצמד", "תיבה", "גל", "נושא",
    "מסב", "ידית", "מנעול", "חלון", "פגוש", "מכסה", "קמר", "צינור",
    "כבל", "חוט", "ממסר", "נתיך", "מודול", "יחידה", "מצבר", "גנרטור",
    "מזנק", "אחיזה", "טבעת", "אטם", "שפה", "מושב", "חגורה", "קיט",
    "הגה", "בורג", "אום", "כיסוי", "צלחת", "ציר",
]

# OEM prefix seeds for part-number search (mode=1)
# Subaru OEM patterns: 2-digit numeric prefix + 3-letter alpha + 5+ digits
OEM_PREFIXES = [
    "260", "261", "262", "263", "264", "265", "266", "267", "268", "269",
    "310", "311", "312", "313", "314", "315", "316", "317", "318", "319",
    "320", "321", "322",
    "160", "161", "162", "163", "164", "165", "166",
    "940", "941", "942", "943", "944", "960", "961",
    "680", "681", "682",
    "720", "721", "722", "723",
    "800", "801", "802",
    "900", "901", "902",
    "610", "611", "612",
    "560", "561", "562",
    "440", "441",
    "450", "451",
    "280", "281", "282",
    "200", "201", "202",
    "100", "101",
    "SU", "PZ",
]


def call_api(term: str, mode: int) -> list:
    """Call samelet.com API and return list of parts."""
    data = urllib.parse.urlencode({
        "site": SAMELET_SITE,
        "tag": "parts-prices",
        "page_name": "מחירון חלפים",
        "token": SAMELET_TOKEN,
        "campaign": "",
        "agency": "",
        "source": "",
        "part_search": term,
        "part_search_options": str(mode),
    }).encode("utf-8")

    req = urllib.request.Request(
        SAMELET_API,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://samelet.com/form/parts-prices/{SAMELET_SITE}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
            parts = d.get("parts", [])
            if isinstance(parts, dict):
                return [parts] if parts else []
            return parts if isinstance(parts, list) else []
    except Exception as e:
        print(f"  WARN term={term!r} mode={mode}: {e}")
        return []


def harvest_all() -> list:
    """Sweep all terms and collect unique parts by Material (OEM number)."""
    seen: set = set()
    all_parts: list = []

    all_terms = [(t, 2) for t in TERMS_DESC] + [(p, 1) for p in OEM_PREFIXES]
    total = len(all_terms)
    print(f"Sweeping {total} terms...")

    for i, (term, mode) in enumerate(all_terms):
        parts = call_api(term, mode)
        new_count = 0
        for p in parts:
            mat = (p.get("Material") or "").strip()
            if mat and mat not in seen:
                seen.add(mat)
                all_parts.append(p)
                new_count += 1
        if parts:
            print(f"  [{i+1}/{total}] '{term}' mode={mode}: {len(parts)} → {new_count} new (total: {len(all_parts)})")
        time.sleep(0.3)

    print(f"\nHarvest complete: {len(all_parts)} unique Subaru parts")
    Path(OUTPUT_FILE).write_text(json.dumps(all_parts, ensure_ascii=False, indent=2))
    print(f"Saved: {OUTPUT_FILE}")
    return all_parts


# ── Category mapping from Hebrew description ─────────────────────────────────
CATEGORY_KEYWORDS = {
    "בלם": "Brakes",
    "רפידה": "Brakes",
    "דיסק בלם": "Brakes",
    "קליפר": "Brakes",
    "מנוע": "Engine",
    "מנוף": "Engine",
    "בוכנה": "Engine",
    "גל ארכובה": "Engine",
    "שמן מנוע": "Engine",
    "מסנן שמן": "Engine",
    "מסנן אויר": "Engine",
    "מסנן": "Filters",
    "גיר": "Transmission",
    "תיבת הילוכים": "Transmission",
    "CVT": "Transmission",
    "מצמד": "Transmission",
    "הגה": "Steering",
    "גל הגה": "Steering",
    "משאבת הגה": "Steering",
    "קפיץ": "Suspension",
    "בולם": "Suspension",
    "זרוע": "Suspension",
    "חיישן": "Sensors & Electrical",
    "ממסר": "Sensors & Electrical",
    "נתיך": "Sensors & Electrical",
    "מודול": "Sensors & Electrical",
    "יחידת שליטה": "Sensors & Electrical",
    "מצנן": "Cooling",
    "מאוורר": "Cooling",
    "מדחס": "Air Conditioning",
    "מזגן": "Air Conditioning",
    "פנס": "Lighting",
    "נורה": "Lighting",
    "מגב": "Wipers & Washers",
    "משאבת מים": "Cooling",
    "רדיאטור": "Cooling",
    "שרשרת": "Engine",
    "חגורה": "Engine",
    "אטם": "Engine",
    "מצבר": "Battery",
    "גנרטור": "Electrical",
    "גלגל": "Wheels & Tires",
    "מראה": "Body & Trim",
    "פגוש": "Body & Trim",
    "מכסה": "Body & Trim",
    "דלת": "Body & Trim",
    "חלון": "Glass & Seals",
    "שמשה": "Glass & Seals",
    "צינור": "Fuel & Exhaust",
    "מאבזר": "Accessories",
    "אביזר": "Accessories",
}


def infer_category(name_he: str) -> str:
    name_lower = name_he.lower()
    for kw, cat in CATEGORY_KEYWORDS.items():
        if kw in name_he:
            return cat
    return "Other Parts"


# ── Subaru model names for fitment extraction ─────────────────────────────────
SUBARU_MODELS = [
    ("FORESTER", "Forester"),
    ("OUTBACK", "Outback"),
    ("IMPREZA", "Impreza"),
    ("LEGACY", "Legacy"),
    ("CROSSTREK", "Crosstrek"),
    ("XV", "XV"),
    ("WRX STI", "WRX STI"),
    ("WRX", "WRX"),
    ("BRZ", "BRZ"),
    ("LEVORG", "Levorg"),
    ("ASCENT", "Ascent"),
    ("SOLTERRA", "Solterra"),
    ("BAJA", "Baja"),
    ("TRIBECA", "Tribeca"),
    ("FOR ", "Forester"),   # abbreviated in descriptions
    ("IMP ", "Impreza"),
    ("OUT ", "Outback"),
]

YEAR_RE = re.compile(r"MY(\d{2,4})", re.IGNORECASE)


def parse_fitment(name_he: str):
    """Extract model + year range from Hebrew part description."""
    results = []
    upper = name_he.upper()
    years = [int(m) for m in YEAR_RE.findall(upper)]
    # Normalize 2-digit years
    years = [2000 + y if y < 100 else y for y in years]

    for pattern, model_name in SUBARU_MODELS:
        if pattern in upper:
            year_from = min(years) if years else 2000
            year_to = max(years) if len(years) > 1 else (None if not years else years[0])
            if year_to == year_from:
                year_to = None
            results.append({
                "model": model_name,
                "year_from": year_from,
                "year_to": year_to,
            })
    return results


async def ensure_supplier(conn) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    import uuid
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.90,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL,
    )
    print(f"  Created supplier: {SUPPLIER_NAME} ({sid})")
    return sid


async def import_parts(parts: list) -> dict:
    import uuid

    conn = await asyncpg.connect(DSN)
    supplier_id = await ensure_supplier(conn)

    inserted = 0
    updated = 0
    fitment_rows = 0
    errors = []
    t0 = time.time()

    print(f"\nImporting {len(parts)} parts into DB...")

    for idx, p in enumerate(parts):
        material = (p.get("Material") or "").strip()
        if not material:
            continue

        name_he = (p.get("MatDescHe") or "").strip()
        mat_type_desc = (p.get("MatTypeDesc") or "").strip()  # מקורי / חליפי
        price_no_vat = float(p.get("PriceNoVat") or 0)
        price_with_vat = float(p.get("PriceWithVat") or 0)
        status_code = str(p.get("StatusCode") or "")
        in_stock = bool(p.get("StockExist", "").strip())

        # Skip zero-price error rows (StatusCode=2)
        if status_code == "2" or price_no_vat == 0:
            continue

        is_original = mat_type_desc == "מקורי"
        part_type = "original" if is_original else "oe_equivalent"
        aftermarket_tier = None if is_original else "OE_equivalent"

        sku = f"SUBARU-{material}"
        category = infer_category(name_he)
        il_retail = round(price_with_vat, 2)   # consumer retail incl. VAT = market reference
        max_price = il_retail
        min_price = il_retail

        specs = {
            "vat_included": False,
            "vat_rate": VAT_RATE,
            "currency": "ILS",
            "source": "samelet.com",
            "importer": SUPPLIER_NAME,
            "warranty_months": WARRANTY_MONTHS,
        }

        fitments = parse_fitment(name_he)

        try:
            async with conn.transaction():
                # Upsert to parts_catalog
                existing = await conn.fetchrow(
                    "SELECT id, importer_price_ils FROM parts_catalog WHERE sku=$1", sku
                )
                if existing:
                    part_id = str(existing["id"])
                    await conn.execute(
                        """UPDATE parts_catalog SET
                           name_he=$2, base_price=$3, min_price_ils=$3, max_price_ils=$3,
                           importer_price_ils=CASE WHEN parts_catalog.importer_price_ils > 0 THEN parts_catalog.importer_price_ils ELSE ROUND($3::numeric / 1.18, 2) END,
                           specifications=$4, updated_at=NOW()
                           WHERE id=$1""",
                        part_id, name_he, il_retail,
                        json.dumps(specs),
                    )
                    updated += 1
                else:
                    part_id = str(uuid.uuid4())
                    await conn.execute(
                        """INSERT INTO parts_catalog(
                           id, sku, oem_number, name, name_he, manufacturer, manufacturer_id,
                           category, base_price, importer_price_ils, min_price_ils, max_price_ils,
                           specifications, part_condition, aftermarket_tier,
                           needs_oem_lookup, master_enriched, is_active, created_at, updated_at)
                           VALUES($1,$2,$3,$4,$5,$6,$7::uuid,$8,$9,ROUND($9::numeric/1.18,2),$9,$9,$10::jsonb,'new',$11,FALSE,FALSE,TRUE,NOW(),NOW())""",
                        part_id, sku, material,
                        name_he,  # use Hebrew as name until AI translates
                        name_he, MANUFACTURER, MANUFACTURER_ID,
                        category, il_retail,
                        json.dumps(specs), aftermarket_tier,
                    )
                    inserted += 1

                # Upsert supplier_parts
                await conn.execute(
                    """INSERT INTO supplier_parts(
                       id, supplier_id, part_id, supplier_sku,
                       price_ils, price_usd, availability, is_available,
                       warranty_months, estimated_delivery_days, supplier_url,
                       created_at, updated_at)
                       VALUES(gen_random_uuid(),$1::uuid,$2::uuid,$3,$4,0.0,$5,$6,$7,$8,$9,NOW(),NOW())
                       ON CONFLICT(part_id, supplier_id) DO UPDATE SET
                       price_ils=EXCLUDED.price_ils,
                       is_available=EXCLUDED.is_available,
                       updated_at=NOW()""",
                    supplier_id, part_id, material,
                    importer_price,
                    "in_stock" if in_stock else "on_order",
                    in_stock,
                    WARRANTY_MONTHS, DELIVERY_DAYS, SUPPLIER_URL,
                )

                # Fitment rows
                for fit in fitments:
                    await conn.execute(
                        """INSERT INTO part_vehicle_fitment(
                           id, part_id, manufacturer, manufacturer_id,
                           model, year_from, year_to, notes, created_at, updated_at)
                           VALUES(gen_random_uuid(),$1::uuid,$2,$3::uuid,$4,$5,$6,$7,NOW(),NOW())
                           ON CONFLICT(part_id, manufacturer, model, year_from) DO NOTHING""",
                        part_id, MANUFACTURER, MANUFACTURER_ID,
                        fit["model"], fit["year_from"], fit["year_to"],
                        "Samelet Subaru source",
                    )
                    fitment_rows += 1

        except Exception as e:
            errors.append(f"{material}: {e}")
            if len(errors) <= 5:
                print(f"  ERROR {material}: {e}")

        if (idx + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(f"  Progress: {idx+1}/{len(parts)} — inserted={inserted} updated={updated} fitment={fitment_rows} ({elapsed:.1f}s)")

    await conn.close()
    elapsed = time.time() - t0
    result = {
        "task": "import_subaru",
        "status": "ok" if not errors else "partial",
        "scanned": len(parts),
        "updated": updated,
        "inserted": inserted,
        "fitment": fitment_rows,
        "flagged": len(errors),
        "elapsed_s": round(elapsed, 1),
        "errors": errors[:10],
    }
    print(f"\nResult: {json.dumps(result, ensure_ascii=False)}")
    return result


async def verify_counts(conn=None):
    close = False
    if conn is None:
        conn = await asyncpg.connect(DSN)
        close = True
    r = await conn.fetchrow(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE importer_price_ils > 0) AS priced,
                  COUNT(*) FILTER (WHERE min_price_ils IS NOT NULL) AS has_min
           FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE""",
        MANUFACTURER,
    )
    fit = await conn.fetchval(
        """SELECT COUNT(*) FROM part_vehicle_fitment pvf
           JOIN parts_catalog pc ON pvf.part_id=pc.id
           WHERE pc.manufacturer=$1""",
        MANUFACTURER,
    )
    print(f"\n=== {MANUFACTURER} DB Counts ===")
    print(f"  Total active:  {r['total']}")
    print(f"  Priced:        {r['priced']}")
    print(f"  Has min_price: {r['has_min']}")
    print(f"  Fitment rows:  {fit}")
    if close:
        await conn.close()


async def main():
    # Step 1: Harvest
    parts = harvest_all()

    if not parts:
        print("ERROR: No parts harvested. Aborting.")
        return

    # Step 2: Import to DB
    await import_parts(parts)

    # Step 3: Verify
    await verify_counts()

    print("\nDone. Run Meilisearch sync:")
    print("  python3 /app/meili_sync.py --manufacturer Subaru --no-rebuild")


if __name__ == "__main__":
    asyncio.run(main())
