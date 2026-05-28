#!/usr/bin/env python3
"""
Land Rover Parts Scraper — lrparts.net (accessible, no Cloudflare block)
Saves to /opt/autosparefinder/land_rover_parts.json
"""
import json, re, time, random
from pathlib import Path
from html.parser import HTMLParser
try:
    import urllib.request as ureq
except ImportError:
    import urllib.request as ureq

OUTPUT  = "/opt/autosparefinder/land_rover_parts.json"
BASE    = "https://www.lrparts.net"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

seen   = set()
parts  = []

# Category map (lrparts.net path segment → autosparefinder category)
CAT_MAP = {
    "axle": "suspension-steering",
    "axles": "suspension-steering",
    "drivetrain": "suspension-steering",
    "brakes": "brakes-clutch",
    "brake": "brakes-clutch",
    "engine": "engine",
    "engine-components": "engine",
    "cooling": "cooling-system",
    "fuel": "fuel-system",
    "fuel-and-air": "fuel-system",
    "exhaust": "exhaust",
    "exhausts": "exhaust",
    "gearbox": "gearbox",
    "electrics": "electrical-lighting",
    "suspension": "suspension-steering",
    "body": "body-parts",
    "body-and-chassis": "body-parts",
    "interior": "interior",
    "seats": "interior",
    "service": "filters-oils",
    "oils": "filters-oils",
    "carrying": "accessories",
    "exterior": "accessories",
    "performance": "accessories",
    "books": "accessories",
    "expedition": "accessories",
}

def guess_cat(url_path):
    for seg in url_path.lower().split("/"):
        for k, v in CAT_MAP.items():
            if k in seg:
                return v
    return "engine"

def get(url, retries=3):
    for i in range(retries):
        try:
            req = ureq.Request(url, headers=HEADERS)
            with ureq.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if i < retries - 1:
                time.sleep(2 + i * 2)
            else:
                return ""
    return ""

def add(p):
    key = (p.get("oem_number") or "").strip()
    if not key:
        key = p.get("sku","").strip()
    if not key or key in seen:
        return False
    seen.add(key)
    parts.append({
        "sku": p.get("sku","").strip()[:50],
        "name": (p.get("name") or "").strip()[:300],
        "description": (p.get("description") or "").strip()[:500],
        "price": float(p.get("price") or 0),
        "oem_number": key[:50],
        "category": (p.get("category") or "engine").strip(),
        "image_url": (p.get("image_url") or "").strip()[:300],
        "in_stock": bool(p.get("in_stock", True)),
    })
    return True

def save():
    Path(OUTPUT).write_text(json.dumps({
        "manufacturer": "Land Rover",
        "total": len(parts),
        "parts": parts,
    }, ensure_ascii=False, indent=2))

def extract_part_number(url_path, h1_text):
    """Extract OEM part number from product page."""
    # Try H1 format: "ABC123 - Description"
    m = re.match(r'^([A-Z0-9]{3,20})\s*[-–]\s*', h1_text.strip())
    if m:
        return m.group(1)
    # Try from slug: /abc123-description.html
    slug = url_path.strip("/").split(".")[0]
    first_seg = slug.split("-")[0].upper()
    if re.match(r'^[A-Z]{2,4}\d{4,}', first_seg):
        return first_seg
    return ""

def scrape_product_page(url):
    """Scrape a single product page. Returns part dict or None."""
    html = get(url)
    if not html or len(html) < 500:
        return None

    # Title / H1
    h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    h1_raw = h1_m.group(1) if h1_m else ""
    h1 = re.sub(r'<[^>]+>', '', h1_raw).strip()
    if not h1:
        # Try og:title
        og_m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
        h1 = og_m.group(1).strip() if og_m else ""
    if not h1:
        return None

    # Extract OEM part number from H1 or URL slug
    oem = extract_part_number(url.replace(BASE,""), h1)
    if not oem:
        return None

    # Price — take the first/lowest price (aftermarket)
    prices = re.findall(r'data-ge-price="true"[^>]*>£([\d,]+\.?\d*)', html)
    price_val = 0.0
    if prices:
        price_val = float(prices[0].replace(",",""))

    # Description from meta
    desc_m = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html)
    desc = desc_m.group(1).strip()[:500] if desc_m else ""

    # Category from URL
    cat = guess_cat(url.replace(BASE,""))

    # Image
    img_m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
    img = img_m.group(1) if img_m else ""

    # In stock
    in_stock = bool(re.search(r'(in.?stock|add.to.cart|add.to.basket)', html, re.I))

    # Part name: clean up H1 (remove part number prefix)
    name = re.sub(r'^[A-Z0-9]{3,20}\s*[-–]\s*', '', h1).strip()
    if not name:
        name = h1

    return {
        "sku": oem,
        "name": name,
        "description": desc,
        "price": price_val,
        "oem_number": oem,
        "category": cat,
        "image_url": img,
        "in_stock": in_stock,
    }

def get_all_vehicle_category_pages():
    """Get all vehicle-specific category pages."""
    html = get(f"{BASE}/vehicle.html")
    if not html:
        return []
    links = re.findall(r'href="(/vehicle/[^"]+\.html)"', html)
    # Only subcategory pages (not top-level /vehicle/model.html)
    cat_pages = []
    for l in links:
        parts_count = l.strip("/").count("/")
        if parts_count >= 2:  # /vehicle/model/category.html
            cat_pages.append(l)
    return list(set(cat_pages))

def get_product_links_from_category(cat_url):
    """Get all product page URLs from a category page."""
    full_url = BASE + cat_url
    html = get(full_url)
    if not html:
        return []
    all_hrefs = re.findall(r'href="(/[^"]+\.html)"', html)
    product_hrefs = set()
    for h in all_hrefs:
        seg = h.lstrip("/").split(".")[0].split("-")[0].upper()
        # Filter: first segment looks like a part number (2-4 letters + digits)
        if re.match(r'^[A-Z]{2,4}[\dA-Z]{3,}', seg):
            # Exclude non-product pages
            if not any(x in h.lower() for x in ["/vehicle/","/blog/","/new/","/gift","/lr-live/","/camping","/about","/faq","/deliver","/return","/contact"]):
                product_hrefs.add(h)
    return list(product_hrefs)

def main():
    print("Land Rover Parts Scraper — lrparts.net", flush=True)
    print("=" * 50, flush=True)

    print("Getting all vehicle category pages...", flush=True)
    cat_pages = get_all_vehicle_category_pages()
    print(f"Found {len(cat_pages)} category pages", flush=True)

    # Collect all product URLs first (deduplicated)
    all_product_urls = set()
    for i, cat in enumerate(cat_pages):
        product_links = get_product_links_from_category(cat)
        all_product_urls.update([BASE + l for l in product_links])
        if (i+1) % 20 == 0:
            print(f"  Scanned {i+1}/{len(cat_pages)} categories | {len(all_product_urls)} product URLs", flush=True)
        time.sleep(0.2)

    print(f"\nTotal unique product URLs: {len(all_product_urls)}", flush=True)
    save()

    # Scrape each product page
    product_list = sorted(all_product_urls)
    for i, url in enumerate(product_list):
        p = scrape_product_page(url)
        if p:
            added = add(p)
            if added and (len(parts) % 50 == 0):
                print(f"[{i+1}/{len(product_list)}] {len(parts)} parts | last: {p['oem_number']} {p['name'][:40]}", flush=True)
                save()
        # Polite delay
        time.sleep(random.uniform(0.3, 0.7))
        if (i+1) % 100 == 0:
            save()
            print(f"  Progress: {i+1}/{len(product_list)} scraped | {len(parts)} imported", flush=True)

    save()
    print(f"\nDONE. Total parts imported: {len(parts)}", flush=True)

if __name__ == "__main__":
    main()
