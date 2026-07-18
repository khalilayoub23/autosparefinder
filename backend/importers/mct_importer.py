"""
MCT (Mayer Group) IL Importer — mct_importer.py

Scrapes the Mayer Group internal parts portal (updates.mct.co.il/parts/)
and imports IL importer prices for Volvo, Honda, Polestar, and Lynk & Co.

Prices on the MCT portal are ex-VAT (confirmed from site header:
"מחיר שאינו כולל מע"מ"). Formula:
    importer_price_ils = price_no_vat        (ex-VAT cost from MCT)
    max_price_ils      = price_no_vat * 1.18 (consumer price incl. 18% VAT)
    base_price         = price_no_vat * 1.45 (45% margin — CLAUDE.md rule)

Run inside backend container:
    docker exec autospare_backend python /app/importers/mct_importer.py
    docker exec autospare_backend python /app/importers/mct_importer.py --brands 2,1,18,19

Author: AutoSpareFinder Agent — 2026-07-01
"""

import asyncio
import base64
import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

import asyncpg

Path("/app/state/logs").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/state/logs/mct_importer.log", mode="a"),
    ],
)
log = logging.getLogger("mct_importer")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare",
).replace("postgresql+asyncpg://", "postgresql://")

MCT_API = "https://updates.mct.co.il/parts/core/api/"
MCT_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://updates.mct.co.il/parts/",
    "Origin": "https://updates.mct.co.il",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}
VAT = 0.18
MARGIN = 0.45
BATCH = 50
SLEEP_BETWEEN_PAGES = 0.3

# Car-only brand IDs (skip buses, trucks, marine, generators)
BRAND_MAP = {
    "1":  "Honda",
    "2":  "Volvo",
    "18": "Polestar",
    "19": "Lynk & Co",
}

SUPPLIER_NAME = "MCT (Mayer Group)"
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# MCT API helpers
# ---------------------------------------------------------------------------

def _mct_post(payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode()
    boundary = "MCTBoundary"
    form = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"data\"\r\n\r\n"
        + body.decode()
        + f"\r\n--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        MCT_API,
        data=form,
        headers={**MCT_HEADERS, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:
        raw = resp.read().strip()
    return json.loads(base64.b64decode(raw))


def iter_brand_pages(brand_id: str):
    """Yield lists of raw part dicts for a given brand_id, one page at a time."""
    page = 0
    while True:
        for attempt in range(3):
            try:
                data = _mct_post({"w": "parts", "b": brand_id, "p": page, "c": ""})
                break
            except Exception as exc:
                log.warning("MCT page %d attempt %d: %s", page, attempt + 1, exc)
                time.sleep(5 * (attempt + 1))
        else:
            log.error("MCT page %d failed after 3 attempts, skipping", page)
            break
        parts = data.get("parts", [])
        if parts:
            yield parts
        if not data.get("pages", {}).get("pageNext"):
            break
        page += 1
        time.sleep(SLEEP_BETWEEN_PAGES)


def parse_price(raw: str) -> float:
    return float(raw.replace(",", "").strip())


def parse_part(raw: dict, brand_name: str) -> dict | None:
    oem = raw.get("a", "").strip()
    name_he = raw.get("b", "").strip()
    price_str = raw.get("f", "0")
    in_stock = raw.get("e", "לא") == "כן"
    if not oem or not name_he:
        return None
    try:
        price_no_vat = parse_price(price_str)
    except ValueError:
        return None
    if price_no_vat <= 0:
        return None
    return {
        "oem_number": oem,
        "name_he": name_he,
        "manufacturer": brand_name,
        "importer_price_ils": round(price_no_vat, 2),
        "max_price_ils": round(price_no_vat * (1 + VAT), 2),
        "base_price": round(price_no_vat * (1 + MARGIN), 2),
        "is_available": in_stock,
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def get_or_create_supplier(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name = $1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = await conn.fetchval(
        """INSERT INTO suppliers (id, name, country, website, is_active)
           VALUES (gen_random_uuid(),$1,'IL','https://updates.mct.co.il/parts/',true) RETURNING id""",
        SUPPLIER_NAME,
    )
    return str(sid)


async def get_manufacturer_id(conn: asyncpg.Connection, name: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) = LOWER($1) LIMIT 1", name
    )
    return str(row["id"]) if row else None


async def upsert_batch(
    conn: asyncpg.Connection,
    parts: list[dict],
    supplier_id: str,
    manufacturer_id: str | None,
    brand_name: str,
) -> tuple[int, int]:
    inserted = updated = 0
    async with conn.transaction():
        for p in parts:
            oem = p["oem_number"]
            sku = f"MCT-{re.sub(r'[^A-Za-z0-9]', '', oem).upper()[:50]}"

            # Check if part already exists by OEM+manufacturer
            existing_id = await conn.fetchval(
                "SELECT id FROM parts_catalog WHERE oem_number = $1 AND manufacturer = $2 LIMIT 1",
                oem, brand_name,
            )

            if existing_id:
                await conn.execute(
                    """UPDATE parts_catalog SET
                        importer_price_ils = CASE WHEN $1 > 0 THEN $1 ELSE importer_price_ils END,
                        max_price_ils      = CASE WHEN $2 > 0 THEN $2 ELSE max_price_ils END,
                        base_price         = CASE WHEN $3 > 0 THEN $3 ELSE base_price END,
                        updated_at         = NOW()
                       WHERE id = $4""",
                    p["importer_price_ils"], p["max_price_ils"], p["base_price"], existing_id,
                )
                part_id = existing_id
                updated += 1
            else:
                # Insert new part — handle duplicate SKU gracefully
                part_id = await conn.fetchval(
                    """INSERT INTO parts_catalog
                        (id, sku, oem_number, name, name_he, manufacturer, manufacturer_id,
                         part_type, part_condition, importer_price_ils, max_price_ils,
                         base_price, is_active, specifications)
                       VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,$6,'oem','new',$7,$8,$9,true,$10)
                       ON CONFLICT (sku) DO UPDATE SET
                         importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0
                                               THEN EXCLUDED.importer_price_ils
                                               ELSE parts_catalog.importer_price_ils END,
                         max_price_ils = CASE WHEN EXCLUDED.max_price_ils > 0
                                          THEN EXCLUDED.max_price_ils
                                          ELSE parts_catalog.max_price_ils END,
                         base_price = CASE WHEN EXCLUDED.base_price > 0
                                       THEN EXCLUDED.base_price
                                       ELSE parts_catalog.base_price END,
                         updated_at = NOW()
                       RETURNING id""",
                    sku, oem, p["name_he"], p["name_he"], brand_name, manufacturer_id,
                    p["importer_price_ils"], p["max_price_ils"], p["base_price"],
                    json.dumps({
                        "source": "mct_importer",
                        "source_url": MCT_API,
                        "part_brand": brand_name,
                        "price_ils": p["importer_price_ils"],
                        "in_stock": p["is_available"],
                        "oem_ref": oem,
                    }),
                )
                if part_id:
                    inserted += 1
                else:
                    # ON CONFLICT UPDATE path doesn't return for some edge cases
                    part_id = await conn.fetchval(
                        "SELECT id FROM parts_catalog WHERE sku = $1", sku
                    )
                    updated += 1

            if not part_id:
                continue

            # Upsert supplier_parts — conflict on (part_id, supplier_id)
            await conn.execute(
                """INSERT INTO supplier_parts
                    (id, supplier_id, part_id, supplier_sku, price_usd, price_ils,
                     is_available, supplier_url, updated_at)
                   VALUES (gen_random_uuid(),$1,$2,$3,0,$4,$5,$6,NOW())
                   ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
                     price_ils    = EXCLUDED.price_ils,
                     is_available = EXCLUDED.is_available,
                     supplier_url = EXCLUDED.supplier_url,
                     updated_at   = NOW()""",
                supplier_id, part_id, oem,
                p["max_price_ils"],
                p["is_available"],
                "https://updates.mct.co.il/parts/",
            )
    return inserted, updated


# ---------------------------------------------------------------------------
# Main import loop
# ---------------------------------------------------------------------------

async def import_brand(brand_id: str, brand_name: str, conn: asyncpg.Connection, supplier_id: str) -> None:
    manufacturer_id = await get_manufacturer_id(conn, brand_name)
    log.info("Starting import: brand=%s id=%s manufacturer_id=%s", brand_name, brand_id, manufacturer_id)

    total_inserted = total_updated = total_pages = 0
    batch: list[dict] = []

    for raw_parts in iter_brand_pages(brand_id):
        total_pages += 1
        for raw in raw_parts:
            parsed = parse_part(raw, brand_name)
            if parsed:
                batch.append(parsed)
        if len(batch) >= BATCH:
            ins, upd = await upsert_batch(conn, batch, supplier_id, manufacturer_id, brand_name)
            total_inserted += ins
            total_updated += upd
            batch = []
            if total_pages % 100 == 0:
                log.info(
                    "  %s page=%d inserted=%d updated=%d",
                    brand_name, total_pages, total_inserted, total_updated,
                )

    if batch:
        ins, upd = await upsert_batch(conn, batch, supplier_id, manufacturer_id, brand_name)
        total_inserted += ins
        total_updated += upd

    log.info(
        "DONE %s: pages=%d inserted=%d updated=%d total=%d",
        brand_name, total_pages, total_inserted, total_updated,
        total_inserted + total_updated,
    )


async def main() -> None:
    brand_ids_arg = None
    for i, arg in enumerate(sys.argv):
        if arg == "--brands" and i + 1 < len(sys.argv):
            brand_ids_arg = sys.argv[i + 1]

    requested_ids = brand_ids_arg.split(",") if brand_ids_arg else list(BRAND_MAP.keys())
    brands_to_run = {bid: BRAND_MAP[bid] for bid in requested_ids if bid in BRAND_MAP}

    if not brands_to_run:
        log.error("No valid brand IDs. Available: %s", list(BRAND_MAP.keys()))
        sys.exit(1)

    log.info("MCT importer starting. Brands: %s", brands_to_run)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        supplier_id = await get_or_create_supplier(conn)
        log.info("Supplier id=%s (%s)", supplier_id, SUPPLIER_NAME)
        for brand_id, brand_name in brands_to_run.items():
            await import_brand(brand_id, brand_name, conn, supplier_id)
    finally:
        await conn.close()

    log.info("MCT import complete.")


if __name__ == "__main__":
    asyncio.run(main())
