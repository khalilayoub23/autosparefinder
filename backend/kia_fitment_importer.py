#!/usr/bin/env python3
"""
KIA Fitment Importer — kia.parts scraper
Scrapes vehicle+category pages to build OEM->vehicle fitment records.

Usage:
  python kia_fitment_importer.py [--dry-run] [--model optima] [--limit 100]
  python kia_fitment_importer.py --skip-sitemap urls.json [--dry-run]
"""

import re
import sys
import time
import gzip
import logging
import argparse
import json
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime

import psycopg2
import psycopg2.extras

# Config
DB_DSN = "postgresql://autospare:autospare@autospare_postgres_catalog:5432/autospare"
SITEMAP_BASE = "https://www.kia.parts/sitemaps/products/products_00{:02d}.xml.gz"
BASE_URL = "https://www.kia.parts"
REQUEST_DELAY = 2.0
MAX_RETRIES = 3
BATCH_SIZE = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/opt/autosparefinder/logs/kia_fitment_importer.log"),
    ],
)
log = logging.getLogger(__name__)

TARGET_MODELS_KEYWORDS = [
    'optima', 'forte', 'cadenza', 'ev6', 'ev9', 'k5', 'k900', 'carnival',
    'sorento', 'ceed', 'soul', 'sedona', 'telluride', 'amanti', 'borrego',
    'k4', 'rondo', 'rio5', 'sephia', 'spectra', 'stinger', 'seltos',
    'forte5', 'forte-koup', 'niro-ev', 'spectra5', 'soul-ev',
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.kia.parts/",
}

MODEL_MAP = {
    "forte-koup": "Forte Koup", "forte5": "Forte5", "forte": "Forte",
    "optima": "Optima", "sorento": "Sorento", "carnival": "Carnival",
    "cadenza": "Cadenza", "sedona": "Sedona", "soul-ev": "Soul EV",
    "soul": "Soul", "niro-ev": "Niro EV", "spectra5": "Spectra5",
    "spectra": "Spectra", "stinger": "Stinger", "telluride": "Telluride",
    "seltos": "Seltos", "ceed": "Ceed", "rondo": "Rondo", "rio5": "Rio5",
    "sephia": "Sephia", "amanti": "Amanti", "borrego": "Borrego",
    "k4": "K4", "k5": "K5", "k900": "K900", "ev6": "EV6", "ev9": "EV9",
}


def parse_vehicle_url(url):
    path = url.replace(BASE_URL, "")
    m = re.match(r"/v-(\d{4})-kia-([a-z0-9-]+?)--([a-z0-9-]+?)--([a-z0-9.-]+)/(.+)$", path)
    if not m:
        m2 = re.match(r"/v-(\d{4})-kia-([a-z0-9-]+)/(.+)$", path)
        if m2:
            return {"year": int(m2.group(1)), "model": MODEL_MAP.get(m2.group(2), m2.group(2).replace("-"," ").title()),
                    "trim": "", "engine": "", "category": m2.group(3), "url": url}
        return None
    model_slug = m.group(2)
    engine_raw = m.group(4)
    engine = re.sub(r'-', ' ', engine_raw).upper()
    # Fix decimal: "2 0L" -> "2.0L"
    engine = re.sub(r'(\d) (\d)', r'\1.\2', engine)
    return {
        "year": int(m.group(1)),
        "model": MODEL_MAP.get(model_slug, model_slug.replace("-"," ").title()),
        "trim": m.group(3).replace("-"," ").upper(),
        "engine": engine,
        "category": m.group(5),
        "url": url,
    }


def extract_oem_from_url(product_url):
    """Extract OEM number from /oem-parts/kia-{desc}-{oemslug}"""
    m = re.search(r'/oem-parts/kia-(?:[a-z0-9-]+-)?([a-z0-9]+)$', product_url)
    if not m:
        return None
    slug = m.group(1).upper()
    if len(slug) < 4:
        return None
    if len(slug) >= 5:
        return slug[:5] + '-' + slug[5:]
    return slug


def fetch_url(url, retries=MAX_RETRIES):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                ce = resp.headers.get("Content-Encoding", "")
                if ce == "gzip" or url.endswith(".gz"):
                    data = gzip.decompress(data)
                return data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 504):
                wait = (2 ** attempt) * 5
                log.warning(f"Rate limited ({e.code}), waiting {wait}s")
                time.sleep(wait)
            elif e.code in (403, 404):
                return None
            else:
                log.error(f"HTTP {e.code} on {url}")
                return None
        except Exception as e:
            log.warning(f"Fetch error (attempt {attempt+1}): {e}")
            time.sleep(2 * (attempt + 1))
    return None


def fetch_sitemap_gz(url):
    html = fetch_url(url)
    if not html:
        return []
    return re.findall(r'<loc>(https?://[^<]+)</loc>', html)


def scrape_oem_parts_from_page(url):
    html = fetch_url(url)
    if not html:
        return []
    if "Just a moment" in html or "Enable JavaScript" in html:
        log.warning(f"Cloudflare block: {url}")
        return []
    product_urls = re.findall(r'href="(/oem-parts/kia-[^"]+)"', html)
    if not product_urls:
        product_urls = re.findall(r'(/oem-parts/kia-[a-z0-9-]+)', html)
    oems = set()
    for pu in product_urls:
        oem = extract_oem_from_url(pu)
        if oem:
            oems.add(oem)
    return list(oems)


def get_manufacturer_id(db):
    with db.cursor() as cur:
        cur.execute("SELECT id FROM manufacturers WHERE LOWER(name) = 'kia' LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None


def find_parts_by_oem(db, oem_numbers):
    if not oem_numbers:
        return {}
    clean_map = {re.sub(r'[-\s]', '', o).upper(): o for o in oem_numbers}
    with db.cursor() as cur:
        cur.execute(
            """SELECT id, oem_number FROM parts_catalog
               WHERE manufacturer = 'Kia' AND is_active = true AND oem_number IS NOT NULL
               AND UPPER(REPLACE(oem_number, '-', '')) = ANY(%s)""",
            (list(clean_map.keys()),)
        )
        rows = cur.fetchall()
    result = defaultdict(list)
    for pid, oem in rows:
        result[re.sub(r'[-\s]', '', oem).upper()].append(pid)
    return result


def upsert_fitment(db, records, dry_run=False):
    if not records:
        return 0
    if dry_run:
        log.info(f"[DRY RUN] Would insert {len(records)} fitment records")
        for r in records[:3]:
            log.info(f"  {r}")
        return len(records)
    inserted = 0
    with db.cursor() as cur:
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i+BATCH_SIZE]
            try:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO part_vehicle_fitment
                        (part_id, manufacturer, model, year_from, year_to, engine_type, notes, manufacturer_id, updated_at)
                       VALUES %s
                       ON CONFLICT ON CONSTRAINT uix_pvf_part_mfr_model_year_from DO UPDATE
                         SET year_to = EXCLUDED.year_to, engine_type = EXCLUDED.engine_type,
                             notes = EXCLUDED.notes, updated_at = NOW()""",
                    [(r['part_id'], r['manufacturer'], r['model'], r['year_from'],
                      r['year_to'], r['engine_type'], r['notes'], r.get('manufacturer_id'),
                      datetime.utcnow()) for r in batch],
                    template="(%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                )
                db.commit()
                inserted += len(batch)
            except Exception as e:
                db.rollback()
                log.error(f"DB error: {e}")
    return inserted


def load_sitemap_urls(model_filter=None):
    log.info("Loading sitemap URLs from kia.parts...")
    all_urls = []
    for n in range(2, 22):
        url = SITEMAP_BASE.format(n)
        log.info(f"  Fetching sitemap {n}/21: {url}")
        urls = fetch_sitemap_gz(url)
        log.info(f"    Got {len(urls)} URLs")
        models = [model_filter] if model_filter else TARGET_MODELS_KEYWORDS
        filtered = [u for u in urls if any(f"kia-{m}" in u.split("/v-", 1)[-1] for m in models)]
        all_urls.extend(filtered)
        time.sleep(1)
    unique = list(set(all_urls))
    log.info(f"Total unique URLs: {len(unique)}")
    return unique


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", help="Filter to model slug e.g. optima")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--skip-sitemap", help="Load URLs from JSON file")
    args = parser.parse_args()

    log.info(f"KIA Fitment Importer: dry_run={args.dry_run} model={args.model} limit={args.limit}")

    if args.skip_sitemap:
        with open(args.skip_sitemap) as f:
            urls = json.load(f)
        log.info(f"Loaded {len(urls)} URLs from file")
    else:
        urls = load_sitemap_urls(model_filter=args.model)

    if not urls:
        log.error("No URLs found")
        sys.exit(1)

    db = psycopg2.connect(DB_DSN)
    mfr_id = get_manufacturer_id(db)
    log.info(f"KIA manufacturer_id: {mfr_id}")

    target = urls[:args.limit] if args.limit else urls
    stats = {"processed": 0, "skipped": 0, "oem_extracted": 0, "oem_matched": 0, "inserted": 0, "errors": 0, "cf_blocked": 0}
    vehicle_oem_cache = defaultdict(set)

    for i, url in enumerate(target):
        if i % 50 == 0:
            log.info(f"[{i}/{len(target)}] inserted={stats['inserted']} matched={stats['oem_matched']} cf_blocked={stats['cf_blocked']}")

        vehicle = parse_vehicle_url(url)
        if not vehicle:
            stats["skipped"] += 1
            continue

        if vehicle["category"].startswith("accessories"):
            stats["skipped"] += 1
            continue

        try:
            oems = scrape_oem_parts_from_page(url)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            log.error(f"Error: {e}")
            stats["errors"] += 1
            continue

        if not oems:
            stats["skipped"] += 1
            continue

        stats["processed"] += 1
        stats["oem_extracted"] += len(oems)

        vkey = f"{vehicle['year']}_{vehicle['model']}"
        new_oems = [o for o in oems if o not in vehicle_oem_cache[vkey]]
        vehicle_oem_cache[vkey].update(new_oems)

        if not new_oems:
            continue

        part_map = find_parts_by_oem(db, new_oems)
        if not part_map:
            continue

        records = []
        for oem_key, pids in part_map.items():
            stats["oem_matched"] += 1
            for pid in pids:
                records.append({
                    "part_id": pid, "manufacturer": "Kia",
                    "model": vehicle["model"],
                    "year_from": vehicle["year"], "year_to": vehicle["year"],
                    "engine_type": vehicle["engine"], "notes": "kia.parts scrape",
                    "manufacturer_id": mfr_id,
                })

        inserted = upsert_fitment(db, records, dry_run=args.dry_run)
        stats["inserted"] += inserted

    db.close()
    log.info(f"DONE: {stats}")
    print(f"status=ok scanned={stats['processed']} updated={stats['inserted']} oem_matched={stats['oem_matched']}")


if __name__ == "__main__":
    main()
