#!/usr/bin/env python3
"""
eliteparts.org GWM / Haval parts scraper + DB importer.
Source: Shopify store with genuine GWM, Haval, Tank parts.

Usage:
  python3 gwm_scraper.py --scrape            # Scrape -> gwm_parts.json
  python3 gwm_scraper.py --import-db         # Import JSON -> DB + fitment
  python3 gwm_scraper.py --scrape --import-db
  python3 gwm_scraper.py --dry-run
"""
import argparse
import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

import asyncpg
import httpx

DATA_FILE = Path("/app/gwm_parts.json")
DATABASE_URL = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed"
    "5e20b26b1d43@postgres_catalog:5432/autospare"
)
SHOPIFY_BASE = "https://www.eliteparts.org/products.json"
DELAY_S = 0.6
BATCH_SIZE = 25

log = logging.getLogger("gwm")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

GWM_IL_MODELS = [
    "Haval H6", "Haval Jolion", "Haval H9",
    "Tank 300", "Tank 500", "GWM Poer", "GWM Wingle",
]

TAG_MODEL_MAP = [
    (r"haval\s*h6",       "Haval H6"),
    (r"haval\s*jolion",   "Haval Jolion"),
    (r"\bjolion\b",       "Haval Jolion"),
    (r"haval\s*h9",       "Haval H9"),
    (r"haval\s*dargo",    "Haval H6"),
    (r"tank\s*500",       "Tank 500"),
    (r"tank\s*300",       "Tank 300"),
    (r"tank\s*400",       "Tank 300"),
    (r"\bpoer\b",         "GWM Poer"),
    (r"\bwingle\b",       "GWM Wingle"),
]

CATEGORY_MAP = [
    (r"brake|disc|rotor|pad|caliper|hose|abs",                   "Brakes"),
    (r"filter|oil.filter|air.filter|fuel.filter|cabin",          "Filters"),
    (r"sensor|switch|control.module|ecu|bcm",                    "Electrical"),
    (r"headlight|tail.light|lamp|bulb|led|fog",                  "Lighting"),
    (r"suspension|shock|spring|strut|bush|arm|ball.joint|link",  "Suspension"),
    (r"steering|rack|tie.rod|column|pump",                       "Steering"),
    (r"engine|piston|valve|timing|belt|chain|gasket|seal|turbo", "Engine"),
    (r"transmission|gear|clutch|gearbox|axle|cv|driveshaft",     "Transmission"),
    (r"radiator|cooling|fan|thermostat|coolant|intercooler",     "Cooling"),
    (r"exhaust|muffler|catalyst|dpf",                            "Exhaust"),
    (r"mirror|door|handle|hinge|lock|window|glass|wiper",        "Body"),
    (r"bumper|fender|hood|bonnet|trunk|grille|panel",            "Body"),
    (r"seat|interior|dash|console|carpet",                       "Interior"),
    (r"key|remote|immobiliser|alarm",                            "Electrical"),
    (r"battery|charger|cable|wire|fuse|relay",                   "Electrical"),
    (r"tire|wheel|rim|lug",                                      "Wheels"),
    (r"ac|air.cond|compressor|blower|hvac",                      "HVAC"),
]

UPSERT_PART_SQL = """
INSERT INTO parts_catalog(
    id, sku, name, name_he, manufacturer, manufacturer_id,
    oem_number, category, part_type, part_condition,
    base_price, is_active, aftermarket_tier,
    needs_oem_lookup, master_enriched,
    specifications, updated_at
) VALUES(
    $1, $2, $3, $4, $5, $6,
    $7, $8, $9, $10,
    $11, TRUE, $12,
    FALSE, FALSE,
    $13::jsonb, NOW()
)
ON CONFLICT(sku) DO UPDATE SET
    oem_number      = EXCLUDED.oem_number,
    name            = EXCLUDED.name,
    category        = EXCLUDED.category,
    base_price      = CASE WHEN EXCLUDED.base_price > 0
                          THEN EXCLUDED.base_price
                          ELSE parts_catalog.base_price END,
    specifications  = EXCLUDED.specifications,
    is_active       = TRUE,
    updated_at      = NOW()
"""

UPSERT_FITMENT_SQL = """
INSERT INTO part_vehicle_fitment(
    id, part_id, manufacturer_id, manufacturer, model,
    year_from, year_to, notes
) VALUES(
    $1, $2, $3, 'GWM', $4,
    2018, 2026, 'eliteparts.org'
)
ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
"""


def infer_category(title):
    t = title.lower()
    for pattern, cat in CATEGORY_MAP:
        if re.search(pattern, t):
            return cat
    return "Other"


def resolve_gwm_models(tags):
    matched = set()
    for tag in tags:
        t = tag.strip().lower()
        for pattern, model in TAG_MODEL_MAP:
            if model and re.search(pattern, t):
                matched.add(model)
    return list(matched)


async def ensure_entity(conn, table, name):
    row = await conn.fetchrow(f"SELECT id FROM {table} WHERE LOWER(name)=$1", name.lower())
    if row:
        return str(row["id"])
    new_id = str(uuid.uuid4())
    if table == "car_brands":
        await conn.execute(
            "INSERT INTO car_brands(id, name, created_at) VALUES($1,$2,NOW()) ON CONFLICT DO NOTHING",
            new_id, name
        )
    row = await conn.fetchrow(f"SELECT id FROM {table} WHERE LOWER(name)=$1", name.lower())
    return str(row["id"]) if row else new_id


async def scrape_all():
    parts = {}
    page = 1
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AutoSpareFinder/1.0)",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
        while True:
            url = f"{SHOPIFY_BASE}?limit=250&page={page}"
            log.info("Fetching page %d ...", page)
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning("HTTP %d on page %d, stopping.", resp.status_code, page)
                break
            data = resp.json()
            products = data.get("products", [])
            if not products:
                log.info("Page %d empty - done.", page)
                break

            gwm_count = 0
            for p in products:
                tags = p.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",")]
                tag_str = " ".join(t.lower() for t in tags)
                if not any(kw in tag_str for kw in ("haval","gwm","tank","jolion","poer","wingle","dargo")):
                    continue

                for variant in p.get("variants", []):
                    sku_raw = variant.get("sku", "").strip()
                    sku_clean = re.sub(r"[^A-Za-z0-9]", "", sku_raw.split("/")[0]).upper()[:60]
                    if not sku_clean:
                        continue
                    catalog_sku = f"EP-GWM-{sku_clean}"
                    if catalog_sku in parts:
                        continue
                    try:
                        price_usd = float(variant.get("price", 0) or 0)
                    except (ValueError, TypeError):
                        price_usd = 0.0
                    parts[catalog_sku] = {
                        "catalog_sku": catalog_sku,
                        "oem_number": sku_raw,
                        "name": p.get("title", "").strip(),
                        "product_type": p.get("product_type", ""),
                        "tags": tags,
                        "price_usd": price_usd,
                        "source": "eliteparts.org",
                    }
                    gwm_count += 1

            log.info("  Page %d: %d GWM/Haval variants (total: %d)", page, gwm_count, len(parts))
            page += 1
            await asyncio.sleep(DELAY_S)

    result = list(parts.values())
    log.info("Scrape complete: %d unique GWM/Haval parts", len(result))
    return result


async def import_to_db(parts, dry_run=False):
    import urllib.parse as up
    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        user=p.username, password=p.password, database=p.path.lstrip("/")
    )
    brand_id = await ensure_entity(conn, "car_brands", "GWM")
    log.info("GWM brand_id=%s", brand_id)

    ILS_RATE = 3.70
    stats = {"inserted": 0, "fitment": 0, "skipped": 0, "errors": 0}
    batch_parts = []
    batch_fitment = []

    async def flush():
        nonlocal stats
        if not batch_parts:
            return
        if dry_run:
            log.info("[DRY-RUN] %d parts + %d fitment", len(batch_parts), len(batch_fitment))
            batch_parts.clear(); batch_fitment.clear()
            return
        async with conn.transaction():
            for row in batch_parts:
                try:
                    await conn.execute(UPSERT_PART_SQL, *row)
                    stats["inserted"] += 1
                except Exception as e:
                    log.warning("Part insert error %s: %s", row[1], e)
                    stats["errors"] += 1

        skus = [r[1] for r in batch_parts]
        rows = await conn.fetch("SELECT id, sku FROM parts_catalog WHERE sku = ANY($1)", skus)
        actual_ids = {r["sku"]: str(r["id"]) for r in rows}

        async with conn.transaction():
            for part_sku, models in batch_fitment:
                pid = actual_ids.get(part_sku)
                if not pid:
                    continue
                for model in models:
                    try:
                        await conn.execute(UPSERT_FITMENT_SQL, str(uuid.uuid4()), pid, brand_id, model)
                        stats["fitment"] += 1
                    except Exception as e:
                        log.debug("Fitment error %s/%s: %s", part_sku, model, e)
        batch_parts.clear(); batch_fitment.clear()

    for part in parts:
        sku = part["catalog_sku"]
        tags = part.get("tags", [])
        fitment_models = resolve_gwm_models(tags)
        price_ils = round(part["price_usd"] * ILS_RATE, 2) if part["price_usd"] else 0.0
        specs = {
            "source": "eliteparts.org",
            "price_usd": part["price_usd"],
            "tags": tags[:10],
            "product_type": part.get("product_type", ""),
        }
        batch_parts.append((
            str(uuid.uuid4()), sku,
            part["name"], part["name"],
            "GWM", brand_id,
            part["oem_number"],
            infer_category(part["name"]),
            "original", "New",
            price_ils, None,
            json.dumps(specs, ensure_ascii=False),
        ))
        if fitment_models:
            batch_fitment.append((sku, fitment_models))
        else:
            stats["skipped"] += 1
        if len(batch_parts) >= BATCH_SIZE:
            await flush()

    await flush()
    await conn.close()
    log.info("Import done: inserted=%d fitment=%d skipped=%d errors=%d",
             stats["inserted"], stats["fitment"], stats["skipped"], stats["errors"])
    return stats


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrape",    action="store_true")
    ap.add_argument("--import-db", action="store_true", dest="import_db")
    ap.add_argument("--dry-run",   action="store_true", dest="dry_run")
    args = ap.parse_args()
    if not args.scrape and not args.import_db:
        ap.print_help(); return

    if args.scrape:
        parts = await scrape_all()
        DATA_FILE.write_text(json.dumps(parts, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Saved %d parts to %s", len(parts), DATA_FILE)

    if args.import_db:
        if not DATA_FILE.exists():
            log.error("%s not found. Run --scrape first.", DATA_FILE); return
        parts = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        log.info("Loaded %d parts from %s", len(parts), DATA_FILE)
        await import_to_db(parts, dry_run=args.dry_run)

        import urllib.parse as up
        p = up.urlparse(DATABASE_URL)
        conn = await asyncpg.connect(host=p.hostname, port=p.port or 5432,
                                     user=p.username, password=p.password, database=p.path.lstrip("/"))
        row = await conn.fetchrow("SELECT COUNT(*) as n FROM parts_catalog WHERE manufacturer='GWM' AND is_active=TRUE")
        log.info("DB verify: GWM active parts = %d", row["n"])
        models_q = await conn.fetch(
            "SELECT model, COUNT(DISTINCT part_id) as parts FROM part_vehicle_fitment "
            "WHERE manufacturer='GWM' GROUP BY model ORDER BY parts DESC"
        )
        log.info("GWM fitment by model:")
        for r in models_q:
            log.info("  %-30s %d parts", r["model"], r["parts"])
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
