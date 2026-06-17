#!/usr/bin/env python3
"""
gm_run_import.py — Adapter to import GM scraper output into parts_catalog.

Reads /tmp/gm_vehicle_parts.json produced by gm_playwright_scraper.py,
transforms fields to match oempartsonline_importer.import_products() expectations,
splits by GM sub-brand (chevrolet / cadillac / buick / gmc), and imports each.

Usage:
  python3 gm_run_import.py                         # import existing JSON
  python3 gm_run_import.py --run-scraper           # scrape first, then import
  python3 gm_run_import.py --file /tmp/custom.json # use a different JSON file
  MAX_MODELS=3 python3 gm_run_import.py --run-scraper  # test scrape (3 models)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent))
from oempartsonline_importer import (
    SUPPLIER_MAP,
    get_usd_to_ils,
    ensure_supplier,
    import_products,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)
GM_JSON = "/tmp/gm_vehicle_parts.json"

# Maps scraper's make field → SUPPLIER_MAP brand_key
MAKE_TO_BRAND = {
    "chevrolet": "chevrolet",
    "cadillac": "cadillac",
    "buick": "buick",
    "gmc": "gmc_brand",
}


def transform(raw: dict) -> dict:
    """Map GM scraper output fields to oempartsonline_importer input fields."""
    oem = (raw.get("oem_number") or raw.get("sku") or "").strip()
    price = float(raw.get("price") or 0)
    return {
        "sku": oem,
        "name": (raw.get("name") or oem).strip(),
        "description": (raw.get("description") or "").strip(),
        "msrp": price,
        "sale_price": price,
        "category_path": "",
        "vehicle_slug": (raw.get("vehicle") or "").strip(),
        "in_stock": bool(raw.get("in_stock")),
        "product_url": "",
    }


def run_scraper(json_path: str) -> bool:
    script = Path(__file__).parent / "gm_playwright_scraper.py"
    if not script.exists():
        log.error("gm_playwright_scraper.py not found: %s", script)
        return False
    log.info("Running GM scraper → %s …", json_path)
    env = {**os.environ, "OUTPUT": json_path}
    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        timeout=7200,
    )
    if result.returncode != 0:
        log.error("Scraper exited with code %d", result.returncode)
        return False
    log.info("Scraper completed.")
    return True


async def import_make(conn: asyncpg.Connection, products: list[dict], brand_key: str, usd_to_ils: float) -> None:
    supplier_id = await ensure_supplier(conn, brand_key)
    cfg = SUPPLIER_MAP[brand_key]
    log.info("[%s] importing %d parts (manufacturer=%s) …", brand_key, len(products), cfg["manufacturer"])
    result = await import_products(conn, products, brand_key, supplier_id, usd_to_ils)
    log.info(
        "[%s] done: scanned=%d inserted=%d fitment=%d errors=%d",
        brand_key, result["scanned"], result["inserted"],
        result["fitment_rows"], len(result["errors"]),
    )
    if result["errors"]:
        for e in result["errors"][:5]:
            log.warning("  error: %s", e)


async def main(json_path: str) -> None:
    if not Path(json_path).exists():
        log.error("JSON not found: %s", json_path)
        sys.exit(1)

    raw_list = json.loads(Path(json_path).read_text())
    if isinstance(raw_list, dict):
        raw_list = raw_list.get("products", [])

    log.info("Loaded %d raw GM parts from %s", len(raw_list), json_path)

    # Group by make
    by_make: dict[str, list[dict]] = {}
    skipped = 0
    for raw in raw_list:
        make = (raw.get("make") or "").lower().strip()
        brand_key = MAKE_TO_BRAND.get(make)
        if not brand_key:
            skipped += 1
            continue
        by_make.setdefault(brand_key, []).append(transform(raw))

    if skipped:
        log.warning("Skipped %d parts with unknown make", skipped)

    for brand_key, parts in by_make.items():
        log.info("[%s] %d parts queued for import", brand_key, len(parts))

    conn = await asyncpg.connect(DB_DSN)
    try:
        usd_to_ils = await get_usd_to_ils(conn)
        log.info("USD→ILS rate: %.4f", usd_to_ils)
        for brand_key, parts in by_make.items():
            await import_make(conn, parts, brand_key, usd_to_ils)
    finally:
        await conn.close()

    log.info("GM import complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import GM OEM parts from scraper JSON")
    parser.add_argument("--file", default=GM_JSON, help=f"JSON file (default: {GM_JSON})")
    parser.add_argument("--run-scraper", action="store_true", help="Run gm_playwright_scraper.py first")
    args = parser.parse_args()

    if args.run_scraper:
        ok = run_scraper(args.file)
        if not ok:
            sys.exit(1)

    asyncio.run(main(args.file))
