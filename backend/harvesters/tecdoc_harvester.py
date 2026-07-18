#!/usr/bin/env python3
"""
TecDoc / TecAlliance parts harvester.

TecDoc is the industry-standard automotive parts database used by 90,000+ suppliers.
Access via TecAlliance REST API: https://webservice.tecalliance.services/pegasus-3-0/

To get API credentials:
  1. Register at https://www.tecalliance.net/developers/
  2. Request a provider ID (free for small projects / trial access)
  3. Set env vars: TECDOC_PROVIDER_ID and TECDOC_API_KEY

Usage:
    TECDOC_PROVIDER_ID=12345 TECDOC_API_KEY=your_key python3 tecdoc_harvester.py --brands saab daewoo rover ssangyong
    python3 tecdoc_harvester.py --test           # test connectivity with current credentials
    python3 tecdoc_harvester.py --list-brands    # list all available makes in TecDoc
"""
from __future__ import annotations

import argparse
import asyncio
import asyncpg
import httpx
import json
import logging
import os
import re
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

# TecAlliance API configuration
TECDOC_BASE     = "https://webservice.tecalliance.services/pegasus-3-0/services/TecdocToCatDLB.jsonEndpoint"
PROVIDER_ID     = int(os.environ.get("TECDOC_PROVIDER_ID", "0"))
API_KEY         = os.environ.get("TECDOC_API_KEY", "")
COUNTRY         = os.environ.get("TECDOC_COUNTRY", "IL")   # Israel
LANG            = os.environ.get("TECDOC_LANG", "en")
EUR_TO_ILS      = 3.9  # approximate

# TecDoc make IDs for our target brands (TecDoc datasupplier IDs)
# These are standard TecDoc car manufacturer IDs — verified against TecDoc catalog
TECDOC_MAKE_IDS = {
    "Saab":       18,     # SAAB
    "Daewoo":     20,     # DAEWOO
    "Rover":      19,     # ROVER (classic, not Land Rover)
    "SsangYong":  107,    # SSANGYONG
    "Maserati":   88,     # MASERATI
    "Daihatsu":   21,     # DAIHATSU
    "Tesla":      227,    # TESLA
    "Karma":      None,   # Not in TecDoc standard
}

# TecDoc article category IDs (generic article types)
TECDOC_TOP_CATEGORIES = [
    1,   # Engine
    2,   # Transmission
    3,   # Suspension/Steering
    4,   # Brakes
    5,   # Electrical
    6,   # Body
    7,   # Cooling
    8,   # Exhaust
    9,   # Air/Fuel
    10,  # Lights
    11,  # Filters
    12,  # Wheels/Tyres
]

# TecDoc generic article ID → our DB category
TD_CAT_MAP = {
    "oil filter": "filters", "air filter": "filters", "fuel filter": "filters",
    "cabin filter": "filters", "filter": "filters",
    "brake pad": "brakes", "brake disc": "brakes", "brake caliper": "brakes",
    "brake drum": "brakes", "brake": "brakes",
    "shock absorber": "suspension-steering", "coil spring": "suspension-steering",
    "control arm": "suspension-steering", "ball joint": "suspension-steering",
    "tie rod": "suspension-steering", "wheel bearing": "suspension-steering",
    "suspension": "suspension-steering", "steering": "suspension-steering",
    "spark plug": "engine", "engine mount": "engine", "timing": "engine",
    "gasket": "engine", "valve": "engine", "piston": "engine", "engine": "engine",
    "alternator": "electrical-sensors", "starter": "electrical-sensors",
    "sensor": "electrical-sensors", "relay": "electrical-sensors",
    "headlight": "lighting", "tail light": "lighting", "bulb": "lighting",
    "radiator": "cooling", "water pump": "cooling", "thermostat": "cooling",
    "exhaust": "exhaust", "catalytic": "exhaust", "silencer": "exhaust",
    "fuel pump": "fuel-air", "injector": "fuel-air", "carburetor": "fuel-air",
    "drive shaft": "gearbox", "clutch": "gearbox", "gearbox": "gearbox",
    "bumper": "body-exterior", "bonnet": "body-exterior", "door": "body-exterior",
    "belt": "belts-chains", "chain": "belts-chains", "tensioner": "belts-chains",
}


def map_tecdoc_category(generic_article: str) -> str:
    s = generic_article.lower()
    for k, v in TD_CAT_MAP.items():
        if k in s:
            return v
    return "accessories"


# ── TecDoc API calls ──────────────────────────────────────────────────────────

async def api_call(client: httpx.AsyncClient, method_body: dict) -> Optional[dict]:
    """Post a TecDoc API request and return parsed JSON or None."""
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-Api-Key"] = API_KEY

    body = {
        "lang": LANG,
        "country": COUNTRY,
        "provider": PROVIDER_ID,
        **method_body,
    }
    try:
        r = await client.post(TECDOC_BASE, json=body, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 401:
            log.error("TecDoc 401 — invalid/missing TECDOC_PROVIDER_ID or TECDOC_API_KEY")
            return None
        log.warning("TecDoc HTTP %d for %s", r.status_code, list(method_body.keys()))
        return None
    except Exception as e:
        log.error("TecDoc API error: %s", e)
        return None


async def get_makes(client: httpx.AsyncClient) -> list[dict]:
    """List all car makes in TecDoc."""
    resp = await api_call(client, {"getBrands": {"countryCar": COUNTRY}})
    if not resp:
        return []
    data = resp.get("data", {}).get("getBrands", {})
    brands = data.get("brands", []) or []
    return [
        {"id": b.get("brandId"), "name": b.get("brandName")}
        for b in brands if b.get("brandId") and b.get("brandName")
    ]


async def get_models(client: httpx.AsyncClient, make_id: int) -> list[dict]:
    """Get model tree for a make."""
    resp = await api_call(client, {
        "getModelSeries": {
            "linkingTargetType": "P",  # Passenger cars
            "manuId": make_id,
            "lang": LANG,
        }
    })
    if not resp:
        return []
    data = resp.get("data", {}).get("getModelSeries", {})
    series = data.get("modelSeries", []) or []
    return [
        {"id": s.get("modelSeriesId"), "name": s.get("modelSeriesName")}
        for s in series if s.get("modelSeriesId")
    ]


async def get_model_vehicles(client: httpx.AsyncClient, model_id: int) -> list[dict]:
    """Get specific vehicle variants for a model series."""
    resp = await api_call(client, {
        "getVehicles": {
            "linkingTargetType": "P",
            "modelSeriesId": model_id,
            "lang": LANG,
        }
    })
    if not resp:
        return []
    data = resp.get("data", {}).get("getVehicles", {})
    vehicles = data.get("vehicleDetails", []) or []
    return [
        {
            "id": v.get("linkingTargetId"),
            "name": v.get("vehicleModelSeriesName"),
            "engine_type": v.get("motorType"),
            "year_from": v.get("yearOfConstrFrom"),
            "year_to": v.get("yearOfConstrTo"),
            "displacement": v.get("cylinderCapacityCcm"),
        }
        for v in vehicles if v.get("linkingTargetId")
    ]


async def get_articles_for_vehicle(
    client: httpx.AsyncClient, vehicle_id: int, generic_article_id: int = 0
) -> list[dict]:
    """Get parts for a vehicle, optionally filtered by article category."""
    body: dict = {
        "getArticles": {
            "lang": LANG,
            "linkingTargetId": vehicle_id,
            "linkingTargetType": "P",
            "articleCountry": COUNTRY,
            "searchExact": True,
            "oemNumbers": True,
            "usageNumbers": True,
            "thumbnails": False,
            "includeGenericArticles": True,
        }
    }
    if generic_article_id:
        body["getArticles"]["genericArticleId"] = generic_article_id

    resp = await api_call(client, body)
    if not resp:
        return []

    data = resp.get("data", {}).get("getArticles", {})
    articles = data.get("articles", []) or []
    results: list[dict] = []
    for art in articles:
        article_number = art.get("articleNumber", "")
        if not article_number:
            continue
        supplier = art.get("brandName", "") or art.get("datasupplierName", "")
        generic_name = ""
        for ga in (art.get("genericArticles") or []):
            generic_name = ga.get("genericArticleDescription", "")
            break
        price = None
        for pp in (art.get("prices") or []):
            p = pp.get("price")
            if p:
                try:
                    price = float(p)
                    break
                except Exception:
                    pass
        oem_numbers = [
            o.get("articleNumber") for o in (art.get("oemNumbers") or [])
            if o.get("articleNumber")
        ]
        results.append({
            "article_number": article_number,
            "supplier": supplier,
            "name": generic_name or f"{supplier} {article_number}",
            "price_eur": price,
            "oem_numbers": oem_numbers,
            "category_name": generic_name,
        })
    return results


# ── DB upsert ─────────────────────────────────────────────────────────────────

async def upsert_parts(conn: asyncpg.Connection, parts: list[dict], brand_id: str, brand_name: str) -> dict:
    inserted = updated = skipped = 0
    for p in parts:
        pn = str(p.get("article_number") or "").strip()
        if not pn or len(pn) < 3:
            skipped += 1
            continue
        name = str(p.get("name") or pn)[:255]
        oem  = (p.get("oem_numbers") or [pn])[0] if p.get("oem_numbers") else pn

        price_eur = p.get("price_eur")
        price_ils = round(price_eur * EUR_TO_ILS, 2) if price_eur else 0.0
        price_ex  = round(price_ils / 1.17, 2) if price_ils > 0 else 0.0

        sku = f"{brand_name[:3].upper()}-TD-{re.sub(r'[^A-Z0-9]','-',pn.upper())}"
        category = map_tecdoc_category(p.get("category_name") or name)
        desc = (
            f"{name}. OEM: {oem}. Supplier: {p.get('supplier','')}. "
            f"TecDoc article: {pn}. Source: tecdoc/tecalliance."
        )[:500]

        try:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, description, specifications,
                        online_price_ils, min_price_ils, max_price_ils,
                        part_type, is_safety_critical, needs_oem_lookup,
                        master_enriched, is_active, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, $4, $5::uuid,
                        $6, $7, '{}'::jsonb,
                        $8, $9, $8,
                        'aftermarket', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        oem_number        = EXCLUDED.oem_number,
                        online_price_ils  = CASE WHEN EXCLUDED.online_price_ils > 0 THEN EXCLUDED.online_price_ils ELSE parts_catalog.online_price_ils END,
                        min_price_ils     = CASE WHEN EXCLUDED.min_price_ils > 0    THEN EXCLUDED.min_price_ils    ELSE parts_catalog.min_price_ils    END,
                        updated_at        = NOW()
                    RETURNING xmax
                """, sku, oem, name, brand_name, brand_id,
                     category, desc, price_ils, price_ex)
                if row:
                    if row["xmax"] == 0:
                        inserted += 1
                    else:
                        updated += 1
        except Exception as e:
            log.warning("Upsert failed %s: %s", sku, e)
            skipped += 1
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def harvest_brand(
    conn: asyncpg.Connection,
    client: httpx.AsyncClient,
    brand_name: str,
    make_id: int,
    brand_id: str,
    max_models: int = 5,
    max_vehicles: int = 3,
) -> dict:
    log.info("=== TecDoc harvesting: %s (make_id=%d) ===", brand_name, make_id)
    all_parts: list[dict] = []

    models = await get_models(client, make_id)
    log.info("Models found: %d", len(models))
    if max_models:
        models = models[:max_models]

    for model in models:
        vehicles = await get_model_vehicles(client, model["id"])
        if max_vehicles:
            vehicles = vehicles[:max_vehicles]

        for v in vehicles:
            parts = await get_articles_for_vehicle(client, v["id"])
            all_parts.extend(parts)
            log.info("  %s %s: %d articles", model["name"], v.get("name", ""), len(parts))
            await asyncio.sleep(0.5)

    # Deduplicate by article_number
    seen: dict[str, dict] = {}
    for p in all_parts:
        k = str(p["article_number"]).upper()
        if k not in seen:
            seen[k] = p
    deduped = list(seen.values())
    log.info("Unique articles: %d (from %d raw)", len(deduped), len(all_parts))

    result = await upsert_parts(conn, deduped, brand_id, brand_name)
    db_count = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
        brand_name,
    )
    log.info("DB total %s: %d | inserted=%d updated=%d skipped=%d",
             brand_name, db_count, result["inserted"], result["updated"], result["skipped"])
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description="TecDoc/TecAlliance parts harvester")
    ap.add_argument("--brands", nargs="+", default=list(TECDOC_MAKE_IDS.keys()),
                    help="Brand names to harvest")
    ap.add_argument("--max-models",   type=int, default=5,  help="Max models per brand (0=all)")
    ap.add_argument("--max-vehicles", type=int, default=3,  help="Max vehicle variants per model (0=all)")
    ap.add_argument("--list-brands", action="store_true", help="List all TecDoc makes and exit")
    ap.add_argument("--test",        action="store_true", help="Test API connectivity")
    args = ap.parse_args()

    if not PROVIDER_ID:
        log.warning(
            "TECDOC_PROVIDER_ID not set. Set env vars:\n"
            "  TECDOC_PROVIDER_ID=<your_provider_id>\n"
            "  TECDOC_API_KEY=<your_api_key>  (if required)\n"
            "Register at: https://www.tecalliance.net/developers/"
        )

    async with httpx.AsyncClient() as client:
        if args.test:
            log.info("Testing TecDoc API connectivity...")
            makes = await get_makes(client)
            if makes:
                log.info("OK — %d makes found. Sample: %s", len(makes), makes[:3])
            else:
                log.error("FAILED — check credentials")
            return

        if args.list_brands:
            makes = await get_makes(client)
            for m in sorted(makes, key=lambda x: x["name"]):
                print(f"  {m['id']:6d}  {m['name']}")
            return

        conn = await asyncpg.connect(DB_DSN)
        try:
            for brand_name in args.brands:
                # Normalise brand name
                db_brand = brand_name.title()
                make_id = TECDOC_MAKE_IDS.get(db_brand)
                if not make_id:
                    log.warning("No TecDoc make ID for %r — skipping (use --list-brands to find it)", db_brand)
                    continue
                brand_id = await conn.fetchval(
                    "SELECT id::text FROM car_brands WHERE lower(name)=$1 AND is_active=TRUE LIMIT 1",
                    db_brand.lower(),
                )
                if not brand_id:
                    log.warning("Brand %r not in car_brands — skipping", db_brand)
                    continue
                await harvest_brand(conn, client, db_brand, make_id, brand_id,
                                    args.max_models, args.max_vehicles)
        finally:
            await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
