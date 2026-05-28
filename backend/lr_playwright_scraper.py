"""Land Rover OEM Parts Scraper — Playwright server-side
Navigates landrover.oempartsonline.com with a real browser (bypasses Cloudflare).
Saves to /opt/autosparefinder/land_rover_parts.json

Run:
  docker exec -i autospare_backend python /opt/autosparefinder/backend/lr_playwright_scraper.py
"""
import json, re, time, sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUTPUT = "/opt/autosparefinder/land_rover_parts.json"
BASE   = "https://landrover.oempartsonline.com"
seen   = set()
parts  = []

LR_PREFIXES = [
    "LR0","LR1","LR2","LR3","LR4","LR5","LR6","LR7","LR8","LR9",
    "ANR","AMR","ESR","FTC","NTC","PRC","STC","UQB","YEB","ERR",
    "MXC","SRB","VPLGK","VPLTK","VPLSS","VPLER","VPLWJ",
    "RRD","RRC","RTC","BAC","DAC","FAM","NRC",
]

def add(p):
    key = (p.get("oem_number") or p.get("sku") or p.get("name","")).strip()
    if not key or key in seen:
        return False
    seen.add(key)
    parts.append({
        "sku":         p.get("sku","").strip(),
        "name":        p.get("name","").strip(),
        "description": p.get("description","").strip()[:500],
        "price":       float(p.get("price") or 0),
        "oem_number":  key,
        "category":    p.get("category","").strip(),
        "image_url":   p.get("image_url","").strip()[:300],
        "in_stock":    bool(p.get("in_stock")),
    })
    return True

def extract_from_page(page):
    added = 0
    for blob in page.evaluate("""() => {
        return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
               .map(s => { try { return JSON.parse(s.textContent); } catch { return null; } })
               .filter(Boolean);
    }"""):
        items = blob if isinstance(blob, list) else [blob]
        for item in items:
            if item.get("@type") == "Product" or item.get("sku") or item.get("mpn"):
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if add({
                    "sku":        item.get("sku") or item.get("mpn",""),
                    "name":       item.get("name",""),
                    "description":item.get("description",""),
                    "price":      offers.get("price",0),
                    "oem_number": item.get("mpn") or item.get("sku",""),
                    "category":   item.get("category",""),
                    "image_url":  (item.get("image") or [""])[0] if isinstance(item.get("image"), list) else item.get("image",""),
                    "in_stock":   "InStock" in str(offers.get("availability","")),
                }): added += 1
    for data in page.evaluate("""() => {
        const g = window.__NEXT_DATA__?.props?.pageProps ||
                  window.__INITIAL_STATE__ || window.BCData || {};
        const candidates = g.products || g.items || g.results ||
                           g.category?.products || [];
        return Array.isArray(candidates) ? candidates : [];
    }""") or []:
        price_raw = data.get("price") or data.get("prices",{}).get("price",{}).get("value",0)
        if add({
            "sku":        str(data.get("entityId") or data.get("sku","")),
            "name":       data.get("name",""),
            "description":data.get("description",""),
            "price":      float(price_raw) if price_raw else 0,
            "oem_number": data.get("mpn") or data.get("sku",""),
            "category":   (data.get("categories") or [""])[0],
            "image_url":  data.get("defaultImage",{}).get("url","") if isinstance(data.get("defaultImage"), dict) else "",
            "in_stock":   data.get("availability") == "available",
        }): added += 1
    cards = page.evaluate("""() => {
        const results = [];
        const sels = ['[data-product-id]','[data-entity-id]','.productCard',
                      '.product-card','[itemtype*="Product"]','article.product'];
        sels.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                const name  = (el.querySelector('.card-title,.product-name,.productCard-title,[itemprop="name"],h3,h4')?.textContent||'').trim();
                const sku   = el.dataset.productId || el.dataset.entityId || '';
                const price = parseFloat((el.querySelector('[data-product-price],.price')?.textContent||'0').replace(/[^0-9.]/g,''))||0;
                const img   = el.querySelector('img')?.src || '';
                const oem   = el.querySelector('[itemprop="mpn"]')?.textContent?.trim() || sku;
                if (name || sku) results.push({name, sku, price, oem_number:oem, image_url:img});
            });
        });
        return results;
    """) or []
    for c in cards:
        if add(c): added += 1
    single = page.evaluate("""() => {
        const h1 = document.querySelector('h1.productView-title,h1.product-title,h1[itemprop="name"],h1');
        if (!h1) return null;
        return {
            name:        h1.textContent.trim(),
            sku:         (document.querySelector('[itemprop="sku"],.sku-value,[data-sku]')?.textContent||'').trim(),
            price:       parseFloat((document.querySelector('[itemprop="price"],.price--main')?.textContent||'0').replace(/[^0-9.]/g,''))||0,
            oem_number:  (document.querySelector('[itemprop="sku"],.sku-value,[data-sku]')?.textContent||'').trim(),
            description: (document.querySelector('[itemprop="description"],.productView-description')?.textContent||'').trim().slice(0,500),
            category:    (document.querySelector('.breadcrumb li:nth-last-child(2)')?.textContent||'').trim(),
            image_url:   document.querySelector('.productView-image img,[itemprop="image"]')?.src || '',
            in_stock:    !!document.querySelector('[itemprop="availability"][href*="InStock"],.stock-in'),
        };
    """)
    if single and single.get("name") and add(single):
        added += 1
    return added

def search_and_scrape(page, query):
    total_added = 0
    try:
        url = f"{BASE}/search.php?search_query={query}&section=product"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        page_num = 1
        while True:
            added = extract_from_page(page)
            total_added += added
            next_link = page.query_selector('a[rel="next"],.pagination-item--next a,.next a')
            if not next_link:
                break
            next_url = next_link.get_attribute("href")
            if not next_url or next_url == page.url:
                break
            page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            page_num += 1
            if page_num > 20:
                break
    except PWTimeout:
        pass
    return total_added

def browse_all_products(page):
    total_added = 0
    try:
        page.goto(f"{BASE}/oem-parts/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        page_num = 1
        while True:
            added = extract_from_page(page)
            total_added += added
            print(f"  /oem-parts/ page {page_num}: +{added} | total {len(parts)}")
            next_link = page.query_selector('a[rel="next"],.pagination-item--next a,.next a')
            if not next_link:
                break
            next_url = next_link.get_attribute("href")
            if not next_url or next_url == page.url:
                break
            page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            page_num += 1
            if page_num > 200:
                break
    except PWTimeout:
        pass
    return total_added

def save():
    Path(OUTPUT).write_text(json.dumps({
        "manufacturer": "Land Rover",
        "total": len(parts),
        "parts": parts,
    }, ensure_ascii=False, indent=2))
    print(f"\nSaved {len(parts)} parts \u2192 {OUTPUT}")

def main():
    with sync_playwright() as pw:
        print("Launching Chromium...")
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        print("Loading homepage (Cloudflare handshake)...")
        try:
            page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            print(f"  Title: {page.title()}")
        except PWTimeout:
            print("  Timeout on homepage \u2014 proceeding anyway")
        print("\n[Phase 1] Browsing /oem-parts/ product listing...")
        n = browse_all_products(page)
        print(f"  Phase 1 done: +{n} parts")
        print(f"\n[Phase 2] Searching {len(LR_PREFIXES)} LR OEM prefixes...")
        for i, pfx in enumerate(LR_PREFIXES):
            n = search_and_scrape(page, pfx)
            print(f"  [{i+1}/{len(LR_PREFIXES)}] {pfx}: +{n} | total {len(parts)}")
            save()
        print(f"\n[Phase 3] 2-char prefix sweep... ({len(parts)} so far)")
        chars = "abcdefghijklmnopqrstuvwxyz0123456789"
        for a in chars:
            for b in chars:
                q = a + b
                n = search_and_scrape(page, q)
                if n > 0:
                    print(f"  {q}: +{n} | total {len(parts)}")
            save()
        browser.close()
    save()
    print("DONE.")

if __name__ == "__main__":
    main()
