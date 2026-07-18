"""
Delek Motors Multi-Brand Importer — delek_multi_importer.py

Imports IL importer prices for all Delek Motors brands from the Delek API.

Known brand IDs (discovered 2026-07-01 by probing with Hebrew seed "שמן"):
    brandId=1: Mazda (handled separately by mazda_il_importer.py)
    brandId=2: Ford USA Heavy Duty (F-150, F-250, Expedition, Lincoln, Bronco)
    brandId=3: BMW (Delek Motors — OEM format 5xxxx...)
    brandId=4: Ford (Mustang, Focus, F-350, F-550, Navigator)
    brandId=6: NIO (Chinese EV — new brand added 2026-07-01, 2,265 parts, 100% priced)
    brandId=7: MAXUS M-Hero (electric pickup)
    brandId=8: Voyah (FREE, DREAM)
    brandId=9: MAXUS M-Hero Series 2 (additional variant)

API: GET https://serviceforms.delek-motors.co.il/home/GetPriceListReplacements
     ?brandId={id}&sku=&name={seed}
Response field priceWithTax = ILS price INCLUDING 18% VAT.
Formula:
    importer_price_ils = priceWithTax / 1.18   (ex-VAT cost)
    max_price_ils      = priceWithTax           (consumer reference)
    base_price         = (priceWithTax/1.18) * 1.45  (45% margin)

Run inside backend container:
    docker exec autospare_backend python /app/importers/delek_multi_importer.py           # all brands
    docker exec autospare_backend python /app/importers/delek_multi_importer.py --brands 3,6   # BMW + NIO
    docker exec autospare_backend python /app/importers/delek_multi_importer.py --brands 2,4,7,8  # Ford+MAXUS+Voyah

Author: AutoSpareFinder Agent — 2026-07-01
"""

import asyncio
import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import asyncpg

Path("/app/state/logs").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/state/logs/delek_multi_importer.log", mode="a"),
    ],
)
log = logging.getLogger("delek_multi")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare",
).replace("postgresql+asyncpg://", "postgresql://")

API_URL = "https://serviceforms.delek-motors.co.il/home/GetPriceListReplacements"
API_HEADERS = {
    "Referer": "https://www.mazda.co.il/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# Hebrew + Latin + digit seeds (same as Mazda importer)
SEEDS = (
    list("אבגדהוזחטיכלמנסעפצקרשת")
    + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    + list("0123456789")
)

VAT = 0.18
MARGIN = 0.45

# Brand configuration: brand_id → {name, manufacturer_name, supplier_name}
BRAND_CONFIG = {
    1: {"name": "Mazda (Delek)",               "manufacturer": "Mazda",  "supplier": "Mazda Delek Motors IL"},
    2: {"name": "Ford (Delek USA Heavy Duty)", "manufacturer": "Ford",   "supplier": "Delek Motors Ford IL"},
    3: {"name": "BMW (Delek)",                 "manufacturer": "BMW",    "supplier": "BMW Delek Motors"},
    4: {"name": "Ford (Delek)",                "manufacturer": "Ford",   "supplier": "Delek Motors Ford IL"},
    6: {"name": "NIO (Delek)",                 "manufacturer": "NIO",    "supplier": "NIO Delek Motors IL"},
    7: {"name": "MAXUS M-Hero",                "manufacturer": "Maxus",  "supplier": "Delek Motors MAXUS IL"},
    8: {"name": "Voyah",                       "manufacturer": "Voyah",  "supplier": "Delek Motors Voyah IL"},
    9: {"name": "MAXUS M-Hero Series 2",       "manufacturer": "Maxus",  "supplier": "Delek Motors MAXUS IL"},
}


# ---------------------------------------------------------------------------
# API harvest helpers
# ---------------------------------------------------------------------------

def _fetch_seed(brand_id: int, seed: str) -> list:
    url = f"{API_URL}?brandId={brand_id}&sku=&name={urllib.parse.quote(seed)}"
    req = urllib.request.Request(url, headers=API_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("data") or []
    except Exception as exc:
        log.warning("seed %r brand %d: %s", seed, brand_id, exc)
        return []


def harvest_brand(brand_id: int) -> list[dict]:
    """Fetch all unique parts for a brand using all seeds."""
    seen: set[str] = set()
    parts: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_fetch_seed, brand_id, s): s for s in SEEDS}
        done = 0
        for fut in as_completed(futs):
            done += 1
            for p in fut.result():
                oem = (p.get("item") or "").strip().upper()
                if oem and len(oem) >= 3 and oem not in seen:
                    seen.add(oem)
                    parts.append(p)
            if done % 10 == 0:
                log.info("  seeds %d/%d | unique parts so far: %d", done, len(SEEDS), len(parts))
    log.info("Harvest complete for brand %d: %d unique parts", brand_id, len(parts))
    return parts


def parse_part(raw: dict, manufacturer: str) -> dict | None:
    oem = (raw.get("item") or "").strip()
    name_he = (raw.get("name") or "").strip()
    name_en = (raw.get("foreignName") or "").strip()
    price_with_vat = raw.get("priceWithTax") or 0
    try:
        price_with_vat = float(price_with_vat)
    except (ValueError, TypeError):
        return None
    if not oem or price_with_vat <= 0:
        return None

    cost = round(price_with_vat / (1 + VAT), 2)
    return {
        "oem_number": oem,
        "name_he": name_he or name_en,
        "name_en": name_en or name_he,
        "manufacturer": manufacturer,
        "importer_price_ils": cost,
        "max_price_ils": round(price_with_vat, 2),
        "base_price": round(cost * (1 + MARGIN), 2),
        "is_available": (raw.get("isWithQuantity") or "") == "יש",
        "vehicle_model": raw.get("modelDescription") or "",
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def get_or_create_supplier(conn: asyncpg.Connection, supplier_name: str) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name = $1", supplier_name)
    if row:
        return str(row["id"])
    sid = await conn.fetchval(
        """INSERT INTO suppliers (id, name, country, website, is_active)
           VALUES (gen_random_uuid(),$1,'IL','https://www.delek-motors.co.il/',true) RETURNING id""",
        supplier_name,
    )
    return str(sid)


async def get_manufacturer_id(conn: asyncpg.Connection, name: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) = LOWER($1) LIMIT 1", name
    )
    return str(row["id"]) if row else None


async def upsert_parts(
    conn: asyncpg.Connection,
    parts: list[dict],
    supplier_id: str,
    manufacturer_id: str | None,
    batch_size: int = 50,
) -> tuple[int, int, int]:
    inserted = updated = errors = 0
    for i in range(0, len(parts), batch_size):
        batch = parts[i : i + batch_size]
        for p in batch:
            oem = p["oem_number"]
            mfr = p["manufacturer"]
            sku = f"DELEK-{re.sub(r'[^A-Za-z0-9]','',oem).upper()[:50]}"
            try:
                async with conn.transaction():  # per-row savepoint — isolates failures
                    existing_id = await conn.fetchval(
                        "SELECT id FROM parts_catalog WHERE oem_number=$1 AND manufacturer=$2 LIMIT 1",
                        oem, mfr,
                    )
                    if existing_id:
                        await conn.execute(
                            """UPDATE parts_catalog SET
                                importer_price_ils = CASE WHEN $1>0 THEN $1 ELSE importer_price_ils END,
                                max_price_ils      = CASE WHEN $2>0 THEN $2 ELSE max_price_ils END,
                                base_price         = CASE WHEN $3>0 THEN $3 ELSE base_price END,
                                updated_at         = NOW()
                               WHERE id=$4""",
                            p["importer_price_ils"], p["max_price_ils"], p["base_price"], existing_id,
                        )
                        part_id = existing_id
                        updated += 1
                    else:
                        part_id = await conn.fetchval(
                            """INSERT INTO parts_catalog
                                (id,sku,oem_number,name,name_he,manufacturer,manufacturer_id,
                                 part_type,part_condition,importer_price_ils,max_price_ils,
                                 base_price,is_active,specifications)
                               VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,$6,'oem','new',$7,$8,$9,true,$10)
                               ON CONFLICT (sku) DO UPDATE SET
                                 importer_price_ils=CASE WHEN EXCLUDED.importer_price_ils>0
                                   THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
                                 max_price_ils=CASE WHEN EXCLUDED.max_price_ils>0
                                   THEN EXCLUDED.max_price_ils ELSE parts_catalog.max_price_ils END,
                                 base_price=CASE WHEN EXCLUDED.base_price>0
                                   THEN EXCLUDED.base_price ELSE parts_catalog.base_price END,
                                 updated_at=NOW()
                               RETURNING id""",
                            sku, oem, p["name_en"], p["name_he"], mfr, manufacturer_id,
                            p["importer_price_ils"], p["max_price_ils"], p["base_price"],
                            json.dumps({
                                "source": "delek_multi_importer",
                                "part_brand": mfr,
                                "price_ils": p["importer_price_ils"],
                                "in_stock": p["is_available"],
                                "oem_ref": oem,
                            }),
                        )
                        if part_id:
                            inserted += 1
                        else:
                            part_id = await conn.fetchval(
                                "SELECT id FROM parts_catalog WHERE sku=$1", sku
                            )
                            updated += 1

                    if not part_id:
                        errors += 1
                        continue

                    await conn.execute(
                        """INSERT INTO supplier_parts
                            (id,supplier_id,part_id,supplier_sku,price_usd,price_ils,is_available,supplier_url,updated_at)
                           VALUES (gen_random_uuid(),$1,$2,$3,0,$4,$5,'https://www.delek-motors.co.il/',NOW())
                           ON CONFLICT (supplier_id,supplier_sku) DO UPDATE SET
                             price_ils=EXCLUDED.price_ils,
                             is_available=EXCLUDED.is_available,
                             updated_at=NOW()""",
                        supplier_id, part_id, oem,
                        p["max_price_ils"],
                        p["is_available"],
                    )
            except Exception as exc:
                log.warning("Row error %s: %s", oem, exc)
                errors += 1
        if (i + batch_size) % 500 == 0 or i + batch_size >= len(parts):
            log.info("  DB progress: %d/%d | ins=%d upd=%d err=%d",
                     min(i + batch_size, len(parts)), len(parts), inserted, updated, errors)
    return inserted, updated, errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    brand_ids_arg = None
    for i, arg in enumerate(sys.argv):
        if arg == "--brands" and i + 1 < len(sys.argv):
            brand_ids_arg = sys.argv[i + 1]
    requested_ids = [int(x) for x in (brand_ids_arg or ",".join(str(k) for k in BRAND_CONFIG)).split(",")]
    brands_to_run = {bid: BRAND_CONFIG[bid] for bid in requested_ids if bid in BRAND_CONFIG}

    if not brands_to_run:
        log.error("No valid brand IDs. Available: %s", list(BRAND_CONFIG.keys()))
        sys.exit(1)

    log.info("Delek multi-importer starting. Brands: %s", {k: v["name"] for k, v in brands_to_run.items()})

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Group Ford brands under one supplier
        supplier_cache: dict[str, str] = {}

        for brand_id, cfg in brands_to_run.items():
            log.info("=== Brand %d: %s ===", brand_id, cfg["name"])
            raw_parts = harvest_brand(brand_id)

            supplier_name = cfg["supplier"]
            if supplier_name not in supplier_cache:
                supplier_cache[supplier_name] = await get_or_create_supplier(conn, supplier_name)
            supplier_id = supplier_cache[supplier_name]

            manufacturer_id = await get_manufacturer_id(conn, cfg["manufacturer"])
            log.info("supplier_id=%s manufacturer_id=%s", supplier_id, manufacturer_id)

            parsed = [p for raw in raw_parts if (p := parse_part(raw, cfg["manufacturer"])) is not None]
            log.info("Parsed %d valid parts from %d raw (brand %d)", len(parsed), len(raw_parts), brand_id)

            ins, upd, err = await upsert_parts(conn, parsed, supplier_id, manufacturer_id)
            log.info("DONE brand %d: inserted=%d updated=%d errors=%d", brand_id, ins, upd, err)

    finally:
        await conn.close()

    log.info("Delek multi-importer complete.")


if __name__ == "__main__":
    asyncio.run(main())
