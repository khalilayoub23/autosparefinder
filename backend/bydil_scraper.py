#!/usr/bin/env python3
"""
Script: bydil_scraper.py
Purpose: Scrape BYD Israel (bydauto.co.il) parts catalog and import into parts_catalog.
         Official Israeli BYD importer: Shlomo Motors.

Process:
  1. POST to bydauto.co.il admin-ajax API with Hebrew search terms
  2. Parse part data: catalog number, Hebrew name, price, model, type
  3. Upsert to parts_catalog with per-row savepoints and specifications JSONB
  4. Get-or-create 'BYD IL (Shlomo Motors)' supplier record
  5. Upsert supplier_parts record per part
  6. Insert vehicle fitment into part_vehicle_fitment per model
  7. Create REX agent todo for missing English names and OEM cross-refs

Data Imported / Modified:
  - parts_catalog: sku, name, name_he, manufacturer, manufacturer_id, oem_number,
                   category, part_type, part_condition, base_price, aftermarket_tier,
                   specifications JSONB
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, is_available,
                    warranty_months, supplier_url
  - part_vehicle_fitment: part_id, manufacturer_id, manufacturer, model, year_from, year_to
  - agent_todos: REX task for missing English names and OEM cross-refs

Data Sources / Web Links:
  - BYD Israel official: https://bydauto.co.il
  - Parts API: POST https://bydauto.co.il/wp-admin/admin-ajax.php
  - Shlomo Motors (importer): https://shlomo.co.il

Missing Data Delegation:
  - English names → REX agent todo (ai_catalog_builder.py)
  - OEM cross-refs → needs_oem_lookup=True, REX todo

VAT Rules:
  - bydauto.co.il prices are ILS INCL. 18% VAT (consumer price)
  - Store in base_price as-is (incl. VAT); price_ils_vat also stored in specifications

Confidence tier: 0.85 (scraped from official dealer API)

Usage:
  python3 bydil_scraper.py --scrape            # Scrape to bydil_parts.json
  python3 bydil_scraper.py --import-db         # Import JSON → DB + fitment
  python3 bydil_scraper.py --scrape --import-db  # Both
  python3 bydil_scraper.py --dry-run           # Import dry run

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""
import argparse
import asyncio
import json
import logging
import os
import re
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path

import asyncpg
import httpx

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
API_URL   = "https://bydauto.co.il/wp-admin/admin-ajax.php"
DATA_FILE = Path("/opt/autosparefinder/bydil_parts.json")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
).replace("postgresql+asyncpg://", "postgresql://")
DELAY_S   = 0.8   # polite delay between requests
BATCH_SIZE = 25

log = logging.getLogger("bydil")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ──────────────────────────────────────────────────────────────────────────────
# Hebrew automotive search terms (3+ chars, covers all major part categories)
# ──────────────────────────────────────────────────────────────────────────────
SEARCH_TERMS = [
    # Engine
    "מנוע", "שמן", "מסנן", "טורבו", "בוכנה", "שסתום", "גלגלת",
    "חגורה", "רצועה", "מצת", "פלאג", "דחסן", "קמה", "זרבובית",
    # Brakes
    "בלם", "דיסק", "רפידה", "קליפר", "בולם",
    # Suspension / Steering
    "קפיץ", "מוט", "זרוע", "מסב", "מתלה", "היגוי", "הגה", "פין",
    "אלמנט", "ריכוז", "עמוד היגוי",
    # Electrical / Electronics
    "חיישן", "חוטים", "כבל", "ממסר", "מתג", "לוח", "מודול",
    "יחידה", "תוכנה", "מצבר", "נורה", "מנורה", "פנס", "מצלמה",
    "אנטנה", "מחשב", "בקר", "אינוורטר", "שנאי", "מחזיר",
    # Body
    "פגוש", "כנף", "דלת", "זכוכית", "שמשה", "מגב", "מראה",
    "ידית", "מנגנון", "כיסוי", "הגנה", "מסגרת", "מאחז",
    "אחורי", "קדמי", "ימין", "שמאל", "עליון", "תחתון",
    # Cooling / HVAC
    "רדיאטור", "מאוורר", "קולר", "תרמוסטט", "נוזל", "מיזוג",
    "קומפרסור", "אוורור",
    # Transmission / Drivetrain
    "גיר", "מצמד", "ציר", "גלגל", "מחצלת",
    # Fuel / Fluid
    "דלק", "משאבה", "מכל", "פקק", "צינור", "אטם",
    # Connectors / Hardware
    "מתאם", "חיבור", "מחבר", "הדק", "מהדק", "בורג", "טבעת",
    "פתיל", "סוגר", "מגן",
    # Wheels / Tires
    "צמיג", "חישוק", "מכסה גלגל",
    # Generic terms in catalog names
    "BYD", "assy", "kit",
    # Safety
    "כריות", "חגורת", "מאיץ",
    # Charging (EVs)
    "טעינה", "עמדת", "חיבור",
]

# BYD models sold in Israel (for fitment) — passenger cars only
BYD_IL_MODELS = [
    "BYD ATTO 3",
    "BYD ATTO 3 EVO",
    "BYD ATTO 2",
    "BYD ATTO 2 DM-i",
    "BYD DOLPHIN",
    "BYD DOLPHIN SURF",
    "BYD SEAL",
    "BYD SEAL U EV",
    "BYD SEAL U DM-i",
    "BYD SEAL 5 DM-i",
    "BYD SEALION 5 DM-i",
    "BYD SEALION 7",
    "BYD TANG",
]

# Mapping from model strings in the DB to BYD_IL_MODELS (substring match)
MODEL_KEYWORD_MAP = {
    "ATTO 3 EVO": "BYD ATTO 3 EVO",
    "ATTO 3":     "BYD ATTO 3",
    "ATTO 2 DM":  "BYD ATTO 2 DM-i",
    "ATTO 2":     "BYD ATTO 2",
    "DOLPHIN SURF": "BYD DOLPHIN SURF",
    "DOLPHIN":    "BYD DOLPHIN",
    "SEAL 5":     "BYD SEAL 5 DM-i",
    "SEAL U DMI": "BYD SEAL U DM-i",
    "SEAL U DM-I":"BYD SEAL U DM-i",
    "SEAL U EV":  "BYD SEAL U EV",
    "SEAL U":     "BYD SEAL U EV",
    "SEAL":       "BYD SEAL",
    "SEALION 5":  "BYD SEALION 5 DM-i",
    "SEALION 7":  "BYD SEALION 7",
    "TANG":       "BYD TANG",
}

# ──────────────────────────────────────────────────────────────────────────────
# HTML table parser
# ──────────────────────────────────────────────────────────────────────────────
class TableParser(HTMLParser):
    """Parses the <table> HTML returned by the BYD API."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._in_td = False
        self._cell = ""
        self._cur_row = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cur_row = []
        elif tag == "td":
            self._in_td = True
            self._cell = ""

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_td = False
            self._cur_row.append(self._cell.strip())
        elif tag == "tr":
            if len(self._cur_row) >= 6:
                self.rows.append(self._cur_row[:])

    def handle_data(self, data):
        if self._in_td:
            self._cell += data


def parse_table(html: str) -> list[dict]:
    if not html.startswith("<table"):
        return []
    p = TableParser()
    p.feed(html)
    parts = []
    for row in p.rows:
        name      = row[0] if len(row) > 0 else ""
        part_type = row[1] if len(row) > 1 else ""
        catalog   = row[2] if len(row) > 2 else ""
        brand     = row[3] if len(row) > 3 else ""
        model     = row[4] if len(row) > 4 else ""
        stock     = row[5] if len(row) > 5 else ""
        warranty  = row[6] if len(row) > 6 else ""
        price_str = row[7] if len(row) > 7 else ""
        if not catalog or not name:
            continue
        try:
            price_ils_vat = float(price_str) if price_str else 0.0
        except ValueError:
            price_ils_vat = 0.0
        parts.append({
            "name_he":       name,
            "part_type_he":  part_type,
            "catalog_number": catalog.strip(),
            "brand":          brand.strip(),
            "model_he":       model.strip(),
            "in_stock":       "יש" in stock,
            "warranty":       warranty.strip(),
            "price_ils_vat":  price_ils_vat,
            "price_ils":      round(price_ils_vat / 1.18, 2) if price_ils_vat else 0.0,
            "source":         "bydauto.co.il",
        })
    return parts


# ──────────────────────────────────────────────────────────────────────────────
# Scraper
# ──────────────────────────────────────────────────────────────────────────────
async def scrape_all() -> list[dict]:
    all_parts: dict[str, dict] = {}   # catalog_number → part dict

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer":    "https://bydauto.co.il/catalog/",
        "Origin":     "https://bydauto.co.il",
        "Accept":     "*/*",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        for term in SEARCH_TERMS:
            try:
                r = await client.post(
                    API_URL,
                    data={"action": "check_mehiron_action", "cnumber": "", "cdesc": term},
                )
                r.raise_for_status()
                parts = parse_table(r.text)
                new = 0
                for p in parts:
                    cat = p["catalog_number"]
                    if cat not in all_parts:
                        all_parts[cat] = p
                        new += 1
                log.info("term=%-20s → %d results, %d new  (total unique: %d)",
                         term, len(parts), new, len(all_parts))
            except Exception as e:
                log.warning("term=%s error: %s", term, e)
            await asyncio.sleep(DELAY_S)

    result = list(all_parts.values())
    log.info("Scrape complete. %d unique parts collected.", len(result))
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Category mapper
# ──────────────────────────────────────────────────────────────────────────────
def categorise(name_he: str, catalog: str) -> str:
    t = name_he.lower()
    c = catalog.upper()
    if any(w in t for w in ["מנוע", "שמן", "מסנן", "טורבו", "בוכנה", "שסתום", "קמה", "בוכנ", "בוכנ"]):
        return "Engine Parts"
    if any(w in t for w in ["בלם", "דיסק", "רפידה", "קליפר"]):
        return "Brakes"
    if any(w in t for w in ["קפיץ", "מוט", "זרוע", "מסב", "בולם", "מתלה", "היגוי", "הגה"]):
        return "Suspension"
    if any(w in t for w in ["חיישן", "חוטים", "כבל", "ממסר", "מתג", "לוח", "מודול", "בקר",
                              "אינוורטר", "שנאי", "חשמל", "מצבר", "נורה", "מנורה", "פנס", "מצלמה", "תוכנה"]):
        return "Electrical"
    if any(w in t for w in ["פגוש", "כנף", "דלת", "זכוכית", "שמשה", "מגב", "מסגרת", "מרכב"]):
        return "Body Parts"
    if any(w in t for w in ["רדיאטור", "מאוורר", "קולר", "תרמוסטט", "נוזל", "מיזוג", "קומפרסור"]):
        return "Cooling System"
    if any(w in t for w in ["גיר", "מצמד", "ציר", "תיבה"]):
        return "Transmission"
    if any(w in t for w in ["דלק", "משאבה", "מכל", "צינור"]):
        return "Fuel System"
    if any(w in t for w in ["מגב", "שמשה", "מראה"]):
        return "Body Parts"
    if any(w in t for w in ["כריות", "חגורת", "חגורה"]):
        return "Safety"
    if any(w in t for w in ["טעינה", "עמדת"]):
        return "Electrical"
    if any(w in t for w in ["אטם", "גסקט", "אחיזה", "הדק", "מהדק", "בורג", "טבעת"]):
        return "Engine Parts"
    return "General Parts"


# ──────────────────────────────────────────────────────────────────────────────
# Model → fitment list mapper
# ──────────────────────────────────────────────────────────────────────────────
def resolve_fitment_models(model_he: str) -> list[str]:
    """Return a list of standard BYD model strings for fitment."""
    if not model_he:
        return []
    upper = model_he.upper()
    # Multi-model = fits all passenger BYDs
    if "מרובה" in model_he or "רבים" in model_he:
        return list(BYD_IL_MODELS)
    # Truck / bus — skip for consumer platform
    if any(w in upper for w in ["משאית", "אוטובוס", "ETM", "B12", "T7", "T5"]):
        return []
    # Try keyword map (longest match first)
    for kw, model in sorted(MODEL_KEYWORD_MAP.items(), key=lambda x: -len(x[0])):
        if kw.upper() in upper:
            return [model]
    # Fallback: if BYD is in it, link to all passenger models
    if "BYD" in upper:
        return list(BYD_IL_MODELS)
    return []


# ──────────────────────────────────────────────────────────────────────────────
# DB Importer
# ──────────────────────────────────────────────────────────────────────────────
UPSERT_PART_SQL = """
INSERT INTO parts_catalog(
    id, sku, name, name_he, manufacturer, manufacturer_id,
    oem_number, category, part_type, part_condition,
    base_price, importer_price_ils, min_price_ils, max_price_ils,
    is_active, aftermarket_tier,
    needs_oem_lookup, master_enriched,
    specifications, updated_at
) VALUES(
    $1, $2, $3, $4, $5, $6,
    $7, $8, $9, $10,
    $11, 0, $11, $11::numeric,
    TRUE, $12,
    FALSE, FALSE,
    $13, NOW()
)
ON CONFLICT(sku) DO UPDATE SET
    oem_number   = EXCLUDED.oem_number,
    name_he      = EXCLUDED.name_he,
    category     = EXCLUDED.category,
    base_price   = CASE WHEN EXCLUDED.base_price > 0
                        THEN EXCLUDED.base_price
                        ELSE parts_catalog.base_price END,
    min_price_ils = CASE WHEN EXCLUDED.min_price_ils > 0
                    THEN EXCLUDED.min_price_ils
                    ELSE parts_catalog.min_price_ils END,
    max_price_ils = CASE WHEN EXCLUDED.max_price_ils > 0
                    THEN EXCLUDED.max_price_ils
                    ELSE parts_catalog.max_price_ils END,
    importer_price_ils = 0,
    specifications = EXCLUDED.specifications,
    is_active    = TRUE,
    updated_at   = NOW()
"""

UPSERT_FITMENT_SQL = """
INSERT INTO part_vehicle_fitment(
    id, part_id, manufacturer_id, manufacturer, model,
    year_from, year_to, notes, created_at
) VALUES(
    $1, $2, $3, 'BYD', $4,
    2020, 2026, 'bydauto.co.il', NOW()
)
ON CONFLICT DO NOTHING
"""


async def ensure_entity(conn, table: str, name: str, extra: dict = None) -> str:
    row = await conn.fetchrow(f"SELECT id FROM {table} WHERE LOWER(name)=$1", name.lower())
    if row:
        return str(row["id"])
    new_id = str(uuid.uuid4())
    if table == "car_brands":
        await conn.execute(
            "INSERT INTO car_brands(id, name, created_at) VALUES($1,$2,NOW()) ON CONFLICT DO NOTHING",
            new_id, name
        )
    elif table == "suppliers":
        await conn.execute(
            "INSERT INTO suppliers(id,name,country,website,is_active,created_at)"
            " VALUES($1,$2,'IL',$3,TRUE,NOW()) ON CONFLICT DO NOTHING",
            new_id, name, (extra or {}).get("website", "")
        )
    row = await conn.fetchrow(f"SELECT id FROM {table} WHERE LOWER(name)=$1", name.lower())
    return str(row["id"]) if row else new_id


async def import_to_db(parts: list[dict], dry_run: bool = False) -> dict:
    import urllib.parse as up
    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        user=p.username, password=p.password, database=p.path.lstrip("/")
    )

    brand_id    = await ensure_entity(conn, "car_brands", "BYD")
    supplier_id = await ensure_entity(conn, "suppliers", "BYD IL (Shlomo Motors)",
                                      {"website": "https://bydauto.co.il"})
    log.info("BYD brand_id=%s  supplier_id=%s", brand_id, supplier_id)

    stats = {"inserted": 0, "fitment": 0, "skipped": 0, "errors": 0}
    batch_parts = []
    batch_fitment = []
    sku_to_id: dict[str, str] = {}   # sku → new uuid

    def make_sku(catalog: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9]", "", catalog).upper()[:50]
        return f"BYDIL-{clean}"

    async def flush():
        nonlocal stats
        if not batch_parts:
            return
        if dry_run:
            log.info("[DRY-RUN] would insert %d parts + %d fitment", len(batch_parts), len(batch_fitment))
            batch_parts.clear(); batch_fitment.clear(); sku_to_id.clear()
            return
        for row in batch_parts:
            try:
                async with conn.transaction():
                    await conn.execute(UPSERT_PART_SQL, *row)
                stats["inserted"] += 1
            except Exception as e:
                log.warning("Part insert error %s: %s", row[1], e)
                stats["errors"] += 1

        # Re-query actual IDs by SKU after upsert (handles ON CONFLICT)
        skus = [r[1] for r in batch_parts]
        rows = await conn.fetch(
            "SELECT id, sku FROM parts_catalog WHERE sku = ANY($1)", skus
        )
        actual_ids = {r["sku"]: str(r["id"]) for r in rows}

        # Upsert supplier_parts
        for row in batch_parts:
            part_id = actual_ids.get(row[1])
            if not part_id:
                continue
            try:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO supplier_parts (
                            id, supplier_id, part_id, supplier_sku,
                            price_ils, price_usd, availability, is_available,
                            warranty_months, estimated_delivery_days, supplier_url,
                            created_at, updated_at)
                        VALUES (gen_random_uuid(), $1::uuid, $2::uuid, $3, $4, 0.0,
                                'in_stock', TRUE, 12, 21, $5, NOW(), NOW())
                        ON CONFLICT (part_id, supplier_id) DO UPDATE SET
                            price_ils=EXCLUDED.price_ils,
                            is_available=EXCLUDED.is_available,
                            updated_at=NOW()
                    """, supplier_id, part_id, row[1],
                         float(row[10] or 0), 'https://bydauto.co.il')
            except Exception as e:
                log.debug("supplier_parts error %s: %s", row[1], e)

        for part_sku, models in batch_fitment:
            part_id = actual_ids.get(part_sku)
            if not part_id:
                continue
            for model in models:
                try:
                    async with conn.transaction():
                        await conn.execute(UPSERT_FITMENT_SQL,
                                           str(uuid.uuid4()), part_id, brand_id, model)
                    stats["fitment"] += 1
                except Exception as e:
                    log.debug("Fitment error %s/%s: %s", part_sku, model, e)

        batch_parts.clear(); batch_fitment.clear(); sku_to_id.clear()

    for part in parts:
        catalog = part["catalog_number"]
        if not catalog:
            stats["skipped"] += 1
            continue

        sku      = make_sku(catalog)
        name_he  = part.get("name_he", "").strip()
        is_orig  = part.get("part_type_he", "") == "מקורי"
        price    = part.get("price_ils", 0.0)
        model_he = part.get("model_he", "")
        fitment_models = resolve_fitment_models(model_he)

        specs = {
            "source":      "bydauto.co.il",
            "in_stock":    part.get("in_stock", False),
            "warranty":    part.get("warranty", ""),
            "model_he":    model_he,
            "price_ils_vat": part.get("price_ils_vat", 0.0),
        }

        batch_parts.append((
            str(uuid.uuid4()),          # $1  id
            sku,                        # $2  sku
            name_he,                    # $3  name (Hebrew as primary)
            name_he,                    # $4  name_he
            "BYD",                      # $5  manufacturer
            brand_id,                   # $6  manufacturer_id
            catalog,                    # $7  oem_number
            categorise(name_he, catalog),# $8 category
            "original" if is_orig else "oe_equivalent",  # $9 part_type
            "New",                      # $10 part_condition
            price,                      # $11 base_price (incl. VAT; importer/min/max computed in SQL)
            None if is_orig else "OE_equivalent",        # $12 aftermarket_tier
            json.dumps(specs, ensure_ascii=False),        # $13 specifications
        ))

        if fitment_models:
            batch_fitment.append((sku, fitment_models))

        if len(batch_parts) >= BATCH_SIZE:
            await flush()

    await flush()

    # REX todo for missing English names and OEM cross-refs
    try:
        await conn.execute("""
            INSERT INTO agent_todos
                (id, agent_name, title, description, priority, status, created_at, updated_at)
            VALUES (gen_random_uuid(), 'REX',
                'Translate and OEM-cross-ref BYD Israel parts',
                'BYD Israel parts imported from bydauto.co.il. Hebrew names only. '
                'Translate to English and cross-reference OEM numbers via BYD global catalog.',
                'medium', 'not_started', NOW(), NOW())
        """)
    except Exception:
        pass

    await conn.close()

    log.info("Import done: inserted=%d fitment=%d skipped=%d errors=%d",
             stats["inserted"], stats["fitment"], stats["skipped"], stats["errors"])
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
async def main():
    ap = argparse.ArgumentParser(description="BYD Israel catalog scraper + importer")
    ap.add_argument("--scrape",    action="store_true", help="Scrape bydauto.co.il → bydil_parts.json")
    ap.add_argument("--import-db", action="store_true", help="Import bydil_parts.json → DB")
    ap.add_argument("--dry-run",   action="store_true", help="Import without DB writes")
    args = ap.parse_args()

    if not args.scrape and not args.import_db:
        ap.print_help()
        return

    parts = []

    if args.scrape:
        log.info("Starting scrape of bydauto.co.il …")
        parts = await scrape_all()
        DATA_FILE.write_text(
            json.dumps({"source": "bydauto.co.il", "total_parts": len(parts), "parts": parts},
                       ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        log.info("Saved %d parts to %s", len(parts), DATA_FILE)

    if args.import_db:
        if not parts:
            if not DATA_FILE.exists():
                log.error("%s not found. Run --scrape first.", DATA_FILE)
                return
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            parts = raw.get("parts", raw) if isinstance(raw, dict) else raw
            log.info("Loaded %d parts from %s", len(parts), DATA_FILE)

        await import_to_db(parts, dry_run=args.dry_run)

        if not args.dry_run:
            # Verify
            import urllib.parse as up
            p = up.urlparse(DATABASE_URL)
            conn = await asyncpg.connect(
                host=p.hostname, port=p.port or 5432,
                user=p.username, password=p.password, database=p.path.lstrip("/")
            )
            row = await conn.fetchrow(
                "SELECT COUNT(*) as n FROM parts_catalog WHERE manufacturer='BYD' AND is_active=TRUE"
            )
            log.info("DB verify: BYD active parts = %d", row["n"])
            models_q = await conn.fetch(
                "SELECT model, COUNT(DISTINCT part_id) as parts FROM part_vehicle_fitment "
                "WHERE manufacturer='BYD' GROUP BY model ORDER BY parts DESC"
            )
            log.info("BYD fitment by model:")
            for r in models_q:
                log.info("  %-30s %d parts", r["model"], r["parts"])
            await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
