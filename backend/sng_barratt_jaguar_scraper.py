"""
SNG Barratt Jaguar Parts Scraper
=================================
Fetches all Jaguar/Daimler parts from the SNG Barratt catalogue API.
Saves raw data to /opt/autosparefinder/jaguar_parts_raw.ndjson (NDJSON format).
Checkpoint/resume safe — will skip already-fetched pages.

Usage:
  python sng_barratt_jaguar_scraper.py
  python sng_barratt_jaguar_scraper.py --start-page 500
  python sng_barratt_jaguar_scraper.py --max-pages 50

Stats: 251,091 parts | 2,511 pages @ 100 per page
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import ClientSession

API_BASE = "https://web-api.sngbarratt.com"
PRODUCTS_ENDPOINT = "/UK/products"
API_KEY = "6203ed7f2099455c956d1bbe520c896d"
CLIENT_ID = "2FC6E2AC-18D6-462E-A662-FF6BC75968C9"

HEADERS = {
    "Ocp-Apim-Subscription-Key": API_KEY,
    "x-client-id": CLIENT_ID,
    "Origin": "https://www.sngbarratt.com",
    "Referer": "https://www.sngbarratt.com/",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

PAGE_SIZE = 100
LANGUAGE = 1
DELAY_BETWEEN_PAGES = 0.25
MAX_RETRIES = 5
RETRY_BACKOFF = 2.0

OUTPUT_FILE = Path("/opt/autosparefinder/jaguar_parts_raw.ndjson")
PROGRESS_FILE = Path("/opt/autosparefinder/jaguar_scraper_progress.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/opt/autosparefinder/logs/jaguar_scraper.log"),
    ],
)
log = logging.getLogger("sng_scraper")


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"last_page": 0, "total_written": 0, "total_pages": None}


def save_progress(last_page: int, total_written: int, total_pages: int | None):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(
            {"last_page": last_page, "total_written": total_written, "total_pages": total_pages},
            f,
        )


def extract_base_part_number(part_number: str) -> str:
    import re
    s = part_number.strip()
    s = re.sub(r"^[A-Z]{1,4}_", "", s)
    s = re.sub(r"_[A-Za-z0-9]$", "", s)
    return s


def map_part_type(type_name: str) -> tuple[str, str | None]:
    t = (type_name or "").lower()
    if any(x in t for x in ("original", "oem", "genuine")):
        return ("original", None)
    if any(x in t for x in ("oe equivalent", "oe_equivalent", "uprated", "premium")):
        return ("aftermarket", "OE_equivalent")
    if any(x in t for x in ("reproduction", "remanufactured")):
        return ("aftermarket", "economy")
    return ("aftermarket", "generic")


def get_uk_retail_price(branch_products: list) -> float | None:
    if not branch_products:
        return None
    for bp in branch_products:
        if bp.get("siteCode", "").upper() == "UK":
            price = bp.get("retailPrice")
            if price is not None and float(price) > 0:
                return float(price)
    for bp in branch_products:
        price = bp.get("retailPrice")
        if price is not None and float(price) > 0:
            return float(price)
    return None


def get_stock_status(branch_products: list) -> str:
    for bp in branch_products:
        if bp.get("siteCode", "").upper() == "UK":
            status = (bp.get("status") or "").upper()
            if status in ("STK", "INSTOCK", "IN_STOCK"):
                return "in_stock"
            if status in ("OOS", "OUT", "OUTOFSTOCK"):
                return "out_of_stock"
    return "unknown"


async def fetch_page(session: ClientSession, page_no: int) -> dict | None:
    url = f"{API_BASE}{PRODUCTS_ENDPOINT}"
    params = {"pageNo": page_no, "pageSize": PAGE_SIZE, "language": LANGUAGE}
    delay = RETRY_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429:
                    wait = delay * (2 ** attempt)
                    log.warning("Rate limited page %d; sleeping %.1fs", page_no, wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status in (503, 504):
                    wait = delay * (2 ** attempt)
                    log.warning("Server error %d on page %d; sleeping %.1fs", resp.status, page_no, wait)
                    await asyncio.sleep(wait)
                    continue
                log.error("Unexpected HTTP %d on page %d", resp.status, page_no)
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            wait = delay * (2 ** attempt)
            log.warning("Network error page %d attempt %d: %s; retry in %.1fs", page_no, attempt + 1, e, wait)
            await asyncio.sleep(wait)
    log.error("All retries exhausted for page %d", page_no)
    return None


async def run_scraper(start_page: int = 1, max_pages: int | None = None):
    progress = load_progress()
    resume_from = max(start_page, progress["last_page"] + 1)
    total_written = progress["total_written"] if resume_from > 1 else 0
    log.info("Starting SNG Barratt Jaguar scraper from page %d", resume_from)
    output_mode = "a" if resume_from > 1 else "w"
    os.makedirs(OUTPUT_FILE.parent, exist_ok=True)
    os.makedirs(Path("/opt/autosparefinder/logs"), exist_ok=True)
    async with ClientSession() as session:
        log.info("Fetching page 1 to determine total pages...")
        first_page = await fetch_page(session, 1)
        if first_page is None:
            log.error("Failed to fetch first page. Aborting.")
            return
        total_pages = first_page.get("pageCount", 0)
        total_results = first_page.get("resultsCount", 0)
        log.info("Catalog: %d parts | %d pages @ %d per page", total_results, total_pages, PAGE_SIZE)
        if max_pages:
            total_pages = min(total_pages, start_page - 1 + max_pages)
            log.info("Limited to %d pages by --max-pages", total_pages)
        with open(OUTPUT_FILE, output_mode, encoding="utf-8") as fout:
            if resume_from == 1:
                for product in (first_page.get("results") or []):
                    record = _build_record(product)
                    fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                    total_written += 1
                save_progress(1, total_written, total_pages)
                await asyncio.sleep(DELAY_BETWEEN_PAGES)
            start = resume_from if resume_from > 1 else 2
            for page_no in range(start, total_pages + 1):
                t0 = time.monotonic()
                data = await fetch_page(session, page_no)
                if data is None:
                    log.warning("Skipping page %d (fetch failed)", page_no)
                    continue
                results = data.get("results") or []
                written_this_page = 0
                for product in results:
                    record = _build_record(product)
                    fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                    total_written += 1
                    written_this_page += 1
                elapsed = time.monotonic() - t0
                pct = (page_no / total_pages) * 100 if total_pages else 0
                if page_no % 50 == 0 or page_no <= 5:
                    log.info(
                        "Page %d/%d (%.1f%%) | +%d parts | total=%d | %.2fs/page",
                        page_no, total_pages, pct, written_this_page, total_written, elapsed,
                    )
                    save_progress(page_no, total_written, total_pages)
                    fout.flush()
                await asyncio.sleep(DELAY_BETWEEN_PAGES)
    save_progress(total_pages, total_written, total_pages)
    log.info("Scrape complete. Total parts written: %d \u2192 %s", total_written, OUTPUT_FILE)


def _build_record(product: dict) -> dict:
    part_number = product.get("partNumber", "")
    brand = product.get("brand") or {}
    brand_name = brand.get("name", "SNG Barratt") if isinstance(brand, dict) else str(brand)
    type_obj = product.get("type") or {}
    type_name = type_obj.get("name", "") if isinstance(type_obj, dict) else str(type_obj)
    part_origin, aftermarket_tier = map_part_type(type_name)
    branch_products = product.get("branchProducts") or []
    price_gbp = get_uk_retail_price(branch_products)
    stock_status = get_stock_status(branch_products)
    application_list_raw = product.get("applicationList", "") or ""
    applications = [a.strip() for a in application_list_raw.split("|") if a.strip()]
    return {
        "part_number": part_number,
        "base_part_number": extract_base_part_number(part_number),
        "web_product_guid": product.get("webProductGuid", ""),
        "title": product.get("title", ""),
        "description": product.get("description", ""),
        "sales_note": product.get("salesNote", ""),
        "brand_name": brand_name,
        "type_name": type_name,
        "part_origin": part_origin,
        "aftermarket_tier": aftermarket_tier,
        "applications": applications,
        "price_gbp": price_gbp,
        "stock_status": stock_status,
        "image_url": product.get("imageUrl", ""),
        "source": "sng_barratt",
        "manufacturer": "Jaguar",
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SNG Barratt Jaguar parts scraper")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(run_scraper(start_page=args.start_page, max_pages=args.max_pages))
