#!/usr/bin/env python3
"""
KiaPartsNow Fitment Importer — Fixed Parser
URL: https://www.kiapartsnow.com/genuine/{OEM}.html
Vehicle Fitment is in: <li data-id="Vehicle Fitment"> ... title="YYYY-YYYY Kia Model"
"""

import re
import os
import sys
import time
import logging
import argparse
import urllib.request
import urllib.error

import psycopg2
import psycopg2.extras

DB_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://autospare:autospare@postgres_catalog:5432/autospare"
).replace("postgresql+asyncpg://", "postgresql://")

BASE_URL = "https://www.kiapartsnow.com/genuine/{oem}.html"
REQUEST_DELAY = 1.5
MAX_RETRIES = 3
BATCH_SIZE = 25

_handlers = [logging.StreamHandler()]
if os.path.isdir('/app/logs'):
    _handlers.append(logging.FileHandler('/app/logs/kiapartsnow_fitment.log'))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=_handlers)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MODEL_MAP = {
    "stinger": "Stinger", "ev6": "EV6", "ev9": "EV9", "ev3": "EV3",
    "k900": "K900", "cadenza": "Cadenza", "telluride": "Telluride",
    "carnival": "Carnival", "sorento": "Sorento", "sportage": "Sportage",
    "seltos": "Seltos", "soul": "Soul", "niro": "Niro", "rio": "Rio",
    "picanto": "Picanto", "forte": "Forte", "optima": "Optima",
    "k5": "K5", "ceed": "Ceed", "k2500": "K2500 Bongo", "k2700": "K2700",
    "mohave": "Mohave", "k8": "K8",
}

GAP_KEYWORDS = ["EV6", "EV9", "EV3", "K900", "K25", "K27",
                 "\u05e1\u05d8\u05d9\u05e0\u05d2\u05e8",   # סטינגר
                 "\u05e7\u05d3\u05e0\u05d6\u05d4",          # קדנזה
                 "\u05d8\u05dc\u05e8\u05d9\u05d3"]          # טלריד


def fetch_url(url):
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 503):
                wait = 30 * (attempt + 1)
                log.warning(f"Rate limited ({e.code}), waiting {wait}s")
                time.sleep(wait)
            else:
                log.warning(f"HTTP {e.code} for {url}")
                return None
        except Exception as ex:
            log.warning(f"Fetch error attempt {attempt+1}: {ex}")
            time.sleep(3)
    return None


def parse_fitment(html):
    """Extract fitment rows from <li data-id="Vehicle Fitment"> section."""
    results = []
    idx = html.find('<li data-id="Vehicle Fitment">')
    if idx < 0:
        return results
    end_idx = html.find('<li data-id=', idx + 30)
    block = html[idx:end_idx] if end_idx > 0 else html[idx:idx + 6000]

    year_model_re = re.compile(
        r'title="(\d{4}(?:-\d{4})?\s+Kia\s+[^"]+)"',
        re.IGNORECASE
    )
    for m in year_model_re.finditer(block):
        s = m.group(1)
        ym = re.match(r'^(\d{4})(?:-(\d{4}))?\s+Kia\s+(.+)$', s, re.IGNORECASE)
        if not ym:
            continue
        year_from = int(ym.group(1))
        year_to = int(ym.group(2)) if ym.group(2) else year_from
        model_raw = ym.group(3).strip()
        model_lower = model_raw.lower()
        model = next((c for k, c in MODEL_MAP.items() if k in model_lower), model_raw)
        results.append({"model": model, "year_from": year_from, "year_to": year_to})

    return results


def get_kia_manufacturer_id(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM car_brands WHERE LOWER(name) = 'kia' LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("Kia not found in car_brands table")
        return str(row[0])


def get_target_parts(conn, model_filter, use_all, limit):
    if use_all:
        where = ""
    elif model_filter:
        clauses = []
        for kw in model_filter:
            safe_kw = kw.replace("'", "''")
            clauses.append(f"pc.name ILIKE '%{safe_kw}%'")
        where = "AND (" + " OR ".join(clauses) + ")"
    else:
        clauses = []
        for kw in GAP_KEYWORDS:
            safe_kw = kw.replace("'", "''")
            clauses.append(f"pc.name ILIKE '%{safe_kw}%'")
        where = "AND (" + " OR ".join(clauses) + ")"

    limit_clause = f"LIMIT {limit}" if limit else ""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""
            SELECT pc.id, pc.oem_number, pc.name
            FROM parts_catalog pc
            WHERE pc.manufacturer = 'Kia'
              AND pc.is_active = true
              AND pc.oem_number IS NOT NULL
              AND LENGTH(TRIM(pc.oem_number)) >= 6
              {where}
            ORDER BY pc.oem_number
            {limit_clause}
        """)
        return cur.fetchall()


def upsert_batch(conn, batch, mfr_id, dry_run):
    if dry_run:
        for row in batch:
            log.info(
                f"[DRY-RUN] part_id={row['part_id']} model={row['model']} "
                f"{row['year_from']}-{row['year_to']}"
            )
        return 0
    inserted = 0
    with conn.cursor() as cur:
        for row in batch:
            try:
                cur.execute("""
                    INSERT INTO part_vehicle_fitment
                        (part_id, manufacturer, model, year_from, year_to, manufacturer_id, updated_at)
                    VALUES (%s, 'Kia', %s, %s, %s, %s, NOW())
                    ON CONFLICT (part_id, manufacturer, model, year_from) DO UPDATE
                        SET year_to = EXCLUDED.year_to,
                            manufacturer_id = EXCLUDED.manufacturer_id,
                            updated_at = NOW()
                """, (row["part_id"], row["model"], row["year_from"], row["year_to"], mfr_id))
                inserted += 1
            except Exception as e:
                log.error(f"Insert error: {e} | row={row}")
                conn.rollback()
                return inserted
    conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser(description="KiaPartsNow reverse-OEM fitment importer")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true", help="Process ALL active KIA OEM numbers")
    parser.add_argument("--models", help="Comma-separated model keywords, e.g. EV6,Stinger")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    model_filter = [m.strip() for m in args.models.split(",")] if args.models else None

    conn = psycopg2.connect(DB_DSN)
    mfr_id = get_kia_manufacturer_id(conn)
    parts = get_target_parts(conn, model_filter, args.all, args.limit)

    log.info(f"Processing {len(parts)} parts | dry_run={args.dry_run}")

    stats = {"processed": 0, "found": 0, "inserted": 0, "not_found": 0}
    batch = []

    for i, part in enumerate(parts):
        oem = part["oem_number"].strip()
        part_id = str(part["id"])
        html = fetch_url(BASE_URL.format(oem=oem))
        stats["processed"] += 1

        if html is None:
            stats["not_found"] += 1
        else:
            rows = parse_fitment(html)
            if rows:
                stats["found"] += 1
                for r in rows:
                    batch.append({"part_id": part_id, "model": r["model"],
                                  "year_from": r["year_from"], "year_to": r["year_to"]})
                log.info(
                    f"[{i+1}/{len(parts)}] {oem} → "
                    + ", ".join(f"{r['model']} {r['year_from']}-{r['year_to']}" for r in rows)
                )
            else:
                stats["not_found"] += 1

        if len(batch) >= BATCH_SIZE:
            stats["inserted"] += upsert_batch(conn, batch, mfr_id, args.dry_run)
            batch = []

        time.sleep(REQUEST_DELAY)

        if stats["processed"] % 50 == 0:
            log.info(
                f"--- Progress {stats['processed']}/{len(parts)} | "
                f"found={stats['found']} inserted={stats['inserted']} miss={stats['not_found']} ---"
            )

    if batch:
        stats["inserted"] += upsert_batch(conn, batch, mfr_id, args.dry_run)

    conn.close()
    log.info(
        f"\n=== COMPLETE ===\n"
        f"  Processed : {stats['processed']}\n"
        f"  Found     : {stats['found']} ({100*stats['found']//max(stats['processed'],1)}%)\n"
        f"  Inserted  : {stats['inserted']}\n"
        f"  Not found : {stats['not_found']}"
    )


if __name__ == "__main__":
    main()
