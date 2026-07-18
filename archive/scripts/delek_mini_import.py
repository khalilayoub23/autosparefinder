#!/usr/bin/env python3
"""
Delek Motors MINI parts harvest + import.
Harvests MINI-specific parts from brandId=3 (BMW Group at Delek Motors)
filtered by MINI model codes in part names.
"""

import asyncio
import logging
import argparse
import re
import uuid
from datetime import datetime

import asyncpg
import httpx

API_URL   = "https://serviceforms.delek-motors.co.il/home/GetPriceListReplacements"
BRAND_ID  = 3

API_HEADERS = {
    "Origin":  "https://campaigns.mini.co.il",
    "Referer": "https://campaigns.mini.co.il/",
    "Accept":  "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
}

MINI_NAME_QUERIES = [
    "R50","R52","R53","R55","R56","R57","R58","R59",
    "R60","R61","R62",
    "F54","F55","F56","F57","F60","F66",
    "MINI","Clubman","Countryman","Paceman","JCW",
]

DB_URL       = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@localhost:5432/autospare"
MINI_BRAND_ID = "47a433bf-4f6f-4f8f-a686-a8c02f7727a8"
SUPPLIER_NAME = "Delek Motors IL"
SUPPLIER_URL  = "https://www.delekmotors.co.il"
SKU_PREFIX    = "MINI-DL"

IS_ORIGINAL_MAP = {
    "מקורי":  ("Original",    None),
    "תחליפי": ("Aftermarket", "OE_equivalent"),
    "חליפי":  ("Aftermarket", "economy"),
}

log = logging.getLogger("delek_mini")


async def fetch_name(client, name):
    for attempt in range(3):
        try:
            r = await client.get(API_URL,
                params={"brandId": BRAND_ID, "sku": "", "name": name},
                headers=API_HEADERS, timeout=30)
            r.raise_for_status()
            return r.json().get("data", [])
        except Exception as e:
            log.warning("Attempt %d name=%s: %s", attempt+1, name, e)
            await asyncio.sleep(2**attempt)
    return []


async def harvest():
    seen, parts = set(), []
    async with httpx.AsyncClient() as client:
        for q in MINI_NAME_QUERIES:
            results = await fetch_name(client, q)
            new = 0
            for p in results:
                pid = p.get("id")
                if pid not in seen:
                    seen.add(pid)
                    parts.append(p)
                    new += 1
            log.info("name=%-12s -> %4d results, %4d new (total: %d)", q, len(results), new, len(parts))
            await asyncio.sleep(0.3)
    log.info("Harvest done: %d unique MINI parts", len(parts))
    return parts


def normalize(raw):
    oem = (raw.get("item") or "").strip()
    if not oem:
        return None
    name_he = (raw.get("name") or "").strip()
    name_en = (raw.get("foreignName") or "").strip()
    is_orig = (raw.get("isOriginal") or "מקורי").strip()
    price   = float(raw.get("priceWithTax") or 0.0)
    part_type, aftermarket_tier = IS_ORIGINAL_MAP.get(is_orig, ("Original", None))
    sku = f"MINI-DL-{oem.replace(chr(32), chr(95))}"
    return dict(oem=oem, sku=sku, name_he=name_he, name_en=name_en,
                part_type=part_type, aftermarket_tier=aftermarket_tier, price=price)


async def ensure_supplier(conn):
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        sid = str(row["id"])
        log.info("Supplier exists: %s", sid)
        return sid
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.90,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL)
    log.info("Created supplier: %s", sid)
    return sid


async def upsert(conn, p, supplier_id):
    existing = await conn.fetchrow("SELECT id FROM parts_catalog WHERE sku=$1", p["sku"])
    if existing:
        part_id = str(existing["id"])
        await conn.execute(
            "UPDATE parts_catalog SET name=$1,description=$2,part_type=$3,aftermarket_tier=$4,updated_at=NOW() WHERE sku=$5",
            p["name_he"], p["name_en"], p["part_type"], p["aftermarket_tier"], p["sku"])
        action = "upd"
    else:
        part_id = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO parts_catalog(id,sku,oem_number,name,description,part_type,aftermarket_tier,
               manufacturer_id,manufacturer,is_active,created_at,updated_at)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,'MINI',TRUE,NOW(),NOW())""",
            part_id, p["sku"], p["oem"], p["name_he"], p["name_en"],
            p["part_type"], p["aftermarket_tier"], MINI_BRAND_ID)
        action = "ins"

    sp = await conn.fetchrow("SELECT id FROM supplier_parts WHERE part_id=$1 AND supplier_id=$2",
                             part_id, supplier_id)
    if sp:
        await conn.execute(
            "UPDATE supplier_parts SET price_ils=$1,price_usd=$1,is_available=TRUE,updated_at=NOW() WHERE id=$2",
            p["price"], str(sp["id"]))
    else:
        await conn.execute(
            """INSERT INTO supplier_parts(id,part_id,supplier_id,supplier_sku,price_usd,price_ils,is_available,created_at,updated_at)
               VALUES($1,$2,$3,$4,$5,$5,TRUE,NOW(),NOW())""",
            str(uuid.uuid4()), part_id, supplier_id, p["sku"], p["price"])
    return action


async def run(dry_run=False, limit=0):
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
    raw_parts = await harvest()
    if limit:
        raw_parts = raw_parts[:limit]
    parts = [p for r in raw_parts if (p := normalize(r)) is not None]
    log.info("Normalized: %d | Original: %d | Aftermarket: %d | With price: %d",
             len(parts),
             sum(1 for p in parts if p["part_type"]=="Original"),
             sum(1 for p in parts if p["part_type"]=="Aftermarket"),
             sum(1 for p in parts if p["price"]>0))
    if dry_run:
        log.info("DRY-RUN — no DB writes.")
        if parts:
            log.info("  Sample: %s | %s | %.2f ILS", parts[0]["sku"], parts[0]["name_he"], parts[0]["price"])
        return
    conn = await asyncpg.connect(DB_URL)
    try:
        sid = await ensure_supplier(conn)
        ins = upd = err = 0
        t0 = datetime.utcnow()
        for i, p in enumerate(parts, 1):
            try:
                a = await upsert(conn, p, sid)
                if a=="ins": ins+=1
                else: upd+=1
            except Exception as e:
                err+=1
                log.error("Error %s: %s", p.get("sku"), e)
                if err > 20:
                    log.error("Too many errors, stopping")
                    break
            if i%100==0:
                el=(datetime.utcnow()-t0).total_seconds()
                log.info("Progress %d/%d | ins=%d upd=%d err=%d | %.0f/s", i, len(parts), ins, upd, err, i/el if el else 0)
        el=(datetime.utcnow()-t0).total_seconds()
        log.info("DONE MINI | inserted=%d updated=%d errors=%d | %.1fs", ins, upd, err, el)
        row = await conn.fetchrow("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='MINI'")
        log.info("  DB count MINI: %d", row[0])
    finally:
        await conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run, limit=args.limit))
