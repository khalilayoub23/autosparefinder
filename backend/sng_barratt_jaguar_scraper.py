"""
SNG Barratt Jaguar Parts Scraper — fetches all parts, saves to NDJSON.
Usage:
  python sng_barratt_jaguar_scraper.py
  python sng_barratt_jaguar_scraper.py --max-pages 5   # test run
  python sng_barratt_jaguar_scraper.py --start-page 100  # resume
"""
import asyncio, json, logging, os, re, sys, time
from pathlib import Path
import aiohttp

API_BASE   = "https://web-api.sngbarratt.com"
API_KEY    = "6203ed7f2099455c956d1bbe520c896d"
CLIENT_ID  = "2FC6E2AC-18D6-462E-A662-FF6BC75968C9"
HEADERS = {
    "Ocp-Apim-Subscription-Key": API_KEY,
    "x-client-id": CLIENT_ID,
    "Origin": "https://www.sngbarratt.com",
    "Referer": "https://www.sngbarratt.com/",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
}
PAGE_SIZE  = 100
DELAY      = 0.25
MAX_RETRY  = 5
OUTPUT     = Path("/opt/autosparefinder/jaguar_parts_raw.ndjson")
PROGRESS   = Path("/opt/autosparefinder/jaguar_scraper_progress.json")
LOGS_DIR   = Path("/opt/autosparefinder/logs")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(str(LOGS_DIR / "jaguar_scraper.log"))],
)
log = logging.getLogger("sng")

def load_progress():
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text())
    return {"last_page": 0, "total_written": 0}

def save_progress(last_page, total_written, total_pages):
    PROGRESS.write_text(json.dumps({"last_page": last_page,
                                    "total_written": total_written,
                                    "total_pages": total_pages}))

def extract_base(part_number):
    s = re.sub(r"^[A-Z]{1,4}_", "", part_number.strip())
    s = re.sub(r"_[A-Za-z0-9]$", "", s)
    return s

def get_price(branch_products):
    for bp in (branch_products or []):
        if bp.get("siteCode","").upper() == "UK":
            p = bp.get("retailPrice")
            if p and float(p) > 0:
                return float(p)
    for bp in (branch_products or []):
        p = bp.get("retailPrice")
        if p and float(p) > 0:
            return float(p)
    return None

def get_stock(branch_products):
    for bp in (branch_products or []):
        if bp.get("siteCode","").upper() == "UK":
            s = (bp.get("status") or "").upper()
            if s in ("STK","INSTOCK"):
                return "in_stock"
            if s in ("OOS","OUT"):
                return "out_of_stock"
    return "unknown"

def map_type(type_name):
    t = (type_name or "").lower()
    if any(x in t for x in ("original","oem","genuine")):
        return "original", None
    if any(x in t for x in ("oe equivalent","uprated","premium")):
        return "aftermarket", "OE_equivalent"
    if any(x in t for x in ("reproduction","remanufactured")):
        return "aftermarket", "economy"
    return "aftermarket", "generic"

def build_record(p):
    pn = p.get("partNumber","")
    brand = p.get("brand") or {}
    brand_name = brand.get("name","SNG Barratt") if isinstance(brand,dict) else str(brand)
    type_obj = p.get("type") or {}
    type_name = type_obj.get("name","") if isinstance(type_obj,dict) else str(type_obj)
    origin, tier = map_type(type_name)
    apps = [a.strip() for a in (p.get("applicationList","") or "").split("|") if a.strip()]
    return {
        "part_number":      pn,
        "base_part_number": extract_base(pn),
        "web_product_guid": p.get("webProductGuid",""),
        "title":            p.get("title",""),
        "description":      p.get("description",""),
        "sales_note":       p.get("salesNote",""),
        "brand_name":       brand_name,
        "type_name":        type_name,
        "part_origin":      origin,
        "aftermarket_tier": tier,
        "applications":     apps,
        "price_gbp":        get_price(p.get("branchProducts") or []),
        "stock_status":     get_stock(p.get("branchProducts") or []),
        "image_url":        p.get("imageUrl",""),
        "source":           "sng_barratt",
        "manufacturer":     "Jaguar",
    }

async def fetch_page(session, page_no):
    url = f"{API_BASE}/UK/products"
    params = {"pageNo": page_no, "pageSize": PAGE_SIZE, "language": 1}
    delay = 1.0
    for attempt in range(MAX_RETRY):
        try:
            async with session.get(url, params=params, headers=HEADERS,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status in (429, 503, 504):
                    wait = delay * (2 ** attempt)
                    log.warning("HTTP %d page %d attempt %d; sleep %.1fs", resp.status, page_no, attempt+1, wait)
                    await asyncio.sleep(wait)
                    continue
                log.error("HTTP %d page %d", resp.status, page_no)
                return None
        except Exception as e:
            wait = delay * (2 ** attempt)
            log.warning("Error page %d attempt %d: %s; retry in %.1fs", page_no, attempt+1, e, wait)
            await asyncio.sleep(wait)
    return None

async def run(start_page=1, max_pages=None):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    prog = load_progress()
    resume_from = max(start_page, prog["last_page"] + 1)
    total_written = prog["total_written"] if resume_from > 1 else 0

    log.info("Starting from page %d", resume_from)
    mode = "a" if resume_from > 1 else "w"

    async with aiohttp.ClientSession() as session:
        p1 = await fetch_page(session, 1)
        if not p1:
            log.error("Failed to fetch page 1"); return

        total_pages = p1.get("pageCount", 0)
        total_results = p1.get("resultsCount", 0)
        log.info("Catalog: %d parts | %d pages", total_results, total_pages)

        if max_pages:
            total_pages = min(total_pages, start_page - 1 + max_pages)
            log.info("Limited to %d pages", total_pages)

        with open(OUTPUT, mode, encoding="utf-8") as fout:
            if resume_from == 1:
                for p in (p1.get("results") or []):
                    fout.write(json.dumps(build_record(p), ensure_ascii=False) + "\n")
                    total_written += 1
                save_progress(1, total_written, total_pages)
                await asyncio.sleep(DELAY)

            start = resume_from if resume_from > 1 else 2
            for page_no in range(start, total_pages + 1):
                data = await fetch_page(session, page_no)
                if data is None:
                    log.warning("Skipping page %d", page_no); continue

                for p in (data.get("results") or []):
                    fout.write(json.dumps(build_record(p), ensure_ascii=False) + "\n")
                    total_written += 1

                if page_no % 50 == 0 or page_no <= 5:
                    pct = page_no / total_pages * 100 if total_pages else 0
                    log.info("Page %d/%d (%.1f%%) total=%d", page_no, total_pages, pct, total_written)
                    save_progress(page_no, total_written, total_pages)
                    fout.flush()

                await asyncio.sleep(DELAY)

    save_progress(total_pages, total_written, total_pages)
    log.info("Done. Total written: %d -> %s", total_written, OUTPUT)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--max-pages",  type=int, default=None)
    args = ap.parse_args()
    asyncio.run(run(start_page=args.start_page, max_pages=args.max_pages))
