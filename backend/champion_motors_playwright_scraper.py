"""
Champion Motors Playwright Scraper — Server-side full catalog collection
Navigates championmotors.co.il/catalog/ with real browser (bypasses Cloudflare WAF).
Collects full catalog across all brands/models/filters.
Saves to /opt/autosparefinder/champion_motors_parts.json

Run:
  docker exec -i autospare_backend python /opt/autosparefinder/backend/champion_motors_playwright_scraper.py
"""
import json
import re
import time
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUTPUT = "/opt/autosparefinder/champion_motors_parts.json"
BASE = "https://www.championmotors.co.il"
CATALOG_URL = f"{BASE}/catalog/"

seen = set()
parts = []
errors = []

BRAND_MAP = {
    "audi": "Audi",
    "volkswagen": "Volkswagen",
    "vw": "Volkswagen",
    "skoda": "Skoda",
    "seat": "SEAT",
    "cupra": "CUPRA",
}

def extract_brand(manufacturer_text):
    if not manufacturer_text:
        return None
    text_lower = manufacturer_text.lower().strip()
    for key, brand in BRAND_MAP.items():
        if key in text_lower:
            return brand
    return None

def add(p):
    key = (p.get("oem_number") or p.get("sku") or p.get("name", "")).strip()
    if not key or key in seen:
        return False
    seen.add(key)
    
    price_incl = float(p.get("price_ils_incl_vat") or p.get("price") or 0)
    price_net = round(price_incl / 1.18 * 100) / 100 if price_incl else 0
    
    parts.append({
        "oem_number": key,
        "name": p.get("name", "").strip()[:200],
        "description": p.get("description", "").strip()[:500],
        "manufacturer": p.get("manufacturer", "").strip(),
        "brand": p.get("brand") or extract_brand(p.get("manufacturer", "")),
        "model": p.get("model", "").strip(),
        "engine": p.get("engine", "").strip(),
        "warranty": p.get("warranty", "").strip(),
        "price_ils_incl_vat": price_incl,
        "price_ils": price_net,
        "source_url": p.get("source_url", ""),
        "source_context": p.get("source_context", ""),
    })
    return True

def extract_from_page(page):
    added = 0
    try:
        page.wait_for_selector("table tbody tr", timeout=10000)
        rows = page.query_selector_all("table tbody tr")
        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 3:
                continue
            try:
                oem_number = cells[0].text_content().strip() if len(cells) > 0 else ""
                price_text = cells[1].text_content().strip() if len(cells) > 1 else "0"
                brand_col = cells[2].text_content().strip() if len(cells) > 2 else ""
                model = cells[3].text_content().strip() if len(cells) > 3 else ""
                engine = cells[4].text_content().strip() if len(cells) > 4 else ""
                warranty = cells[5].text_content().strip() if len(cells) > 5 else ""
                manufacturer = cells[6].text_content().strip() if len(cells) > 6 else ""
                price_match = re.search(r'[\d,\.]+', price_text.replace(',', '.'))
                price = float(price_match.group()) if price_match else 0
                if oem_number and price > 0:
                    if add({
                        "oem_number": oem_number,
                        "name": f"{brand_col} {model} {oem_number}".strip(),
                        "description": f"{engine} {warranty}".strip() if engine or warranty else "",
                        "manufacturer": manufacturer,
                        "brand": extract_brand(manufacturer),
                        "model": model,
                        "engine": engine,
                        "warranty": warranty,
                        "price_ils_incl_vat": price,
                        "price_ils": round(price / 1.18 * 100) / 100,
                        "source_url": page.url,
                        "source_context": f"Table row on {page.url}",
                    }):
                        added += 1
            except Exception as e:
                errors.append(f"Row parse error: {str(e)}")
                continue
        return added
    except Exception as e:
        errors.append(f"Page extract error: {str(e)}")
        return 0

def get_all_filter_urls(page):
    urls = set()
    try:
        brand_links = page.query_selector_all("a[href*='/catalog/']")
        for link in brand_links:
            href = link.get_attribute("href") or ""
            if href and "catalog" in href:
                full_url = href if href.startswith("http") else f"{BASE}{href}"
                urls.add(full_url)
    except:
        pass
    return list(urls) if urls else [CATALOG_URL]

def run_scraper():
    print(f"[CM Scraper] Starting at {CATALOG_URL}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()
        visited = set()
        queue = [CATALOG_URL]
        while queue:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                print(f"[CM Scraper] Visiting {url}")
                page.goto(url, wait_until="networkidle", timeout=30000)
                time.sleep(2)
                added = extract_from_page(page)
                print(f"[CM Scraper] +{added} new parts | {len(seen)} total | URL: {url}")
                next_urls = get_all_filter_urls(page)
                for next_url in next_urls:
                    if next_url not in visited and next_url not in queue:
                        queue.append(next_url)
                try:
                    next_btn = page.query_selector("a[rel='next'], .pagination-next, button:has-text('Next')")
                    if next_btn and next_btn.get_attribute("href"):
                        next_href = next_btn.get_attribute("href")
                        next_full = next_href if next_href.startswith("http") else f"{BASE}{next_href}"
                        if next_full not in visited and next_full not in queue:
                            queue.append(next_full)
                except:
                    pass
            except PWTimeout:
                errors.append(f"Timeout on {url}")
                continue
            except Exception as e:
                errors.append(f"Error on {url}: {str(e)}")
                continue
        browser.close()

    output = {
        "source": "championmotors.co.il",
        "total_parts": len(parts),
        "errors": len(errors),
        "parts": parts,
    }
    Path(OUTPUT).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[CM Scraper] Saved {len(parts)} parts to {OUTPUT}")
    if errors:
        print(f"[CM Scraper] {len(errors)} errors logged")

if __name__ == "__main__":
    run_scraper()
