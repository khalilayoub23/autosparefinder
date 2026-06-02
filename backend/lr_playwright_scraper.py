#!/usr/bin/env python3
"""Land Rover OEM Parts Scraper — Playwright server-side v2"""
import json, time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUTPUT = "/tmp/lr_parts.json"   # writable inside container
BASE   = "https://landrover.oempartsonline.com"
seen   = set()
parts  = []

LR_PREFIXES = [
    "LR0","LR1","LR2","LR3","LR4","LR5","LR6","LR7","LR8","LR9",
    "ANR","AMR","ESR","FTC","NTC","PRC","STC","UQB","YEB","ERR",
    "MXC","SRB","RRD","RRC","RTC","BAC","DAC","FAM","NRC","ANF",
    "CUB","YEB","STC","WJN","JPT","LHP","LR","land-rover","Range Rover",
]

def add(p):
    key = (p.get("oem_number") or p.get("sku") or p.get("name","")).strip()
    if not key or key in seen: return False
    seen.add(key)
    parts.append({
        "sku": p.get("sku","").strip(),
        "name": p.get("name","").strip(),
        "description": (p.get("description") or "").strip()[:500],
        "price": float(p.get("price") or 0),
        "oem_number": key,
        "category": (p.get("category") or "").strip(),
        "image_url": (p.get("image_url") or "").strip()[:300],
        "in_stock": bool(p.get("in_stock")),
    })
    return True

def extract_from_page(page):
    added = 0
    try:
        for blob in page.evaluate("""() => {
            return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                   .map(s => { try { return JSON.parse(s.textContent); } catch { return null; } })
                   .filter(Boolean);
        }""") or []:
            items = blob if isinstance(blob, list) else [blob]
            for item in items:
                if item.get("@type") == "Product":
                    offers = item.get("offers") or {}
                    if isinstance(offers, list): offers = offers[0] if offers else {}
                    if add({"sku": item.get("sku") or item.get("mpn",""),
                            "name": item.get("name",""),
                            "description": item.get("description",""),
                            "price": offers.get("price",0),
                            "oem_number": item.get("mpn") or item.get("sku",""),
                            "category": item.get("category",""),
                            "image_url": (item.get("image") or [""])[0] if isinstance(item.get("image"), list) else item.get("image",""),
                            "in_stock": "InStock" in str(offers.get("availability","")),
                    }): added += 1
    except Exception as e:
        print(f"    jsonld err: {e}", flush=True)

    try:
        cards = page.evaluate("""() => {
            const results = [];
            const sels = ['[data-product-id]','[data-entity-id]','.productCard','.product-card','[itemtype*="Product"]',
                          'article.product','.listItem-figure'];
            sels.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    const name = (el.querySelector('.card-title,.product-name,.productCard-title,[itemprop="name"],h3,h4')?.textContent||'').trim();
                    const sku  = el.dataset.productId || el.dataset.entityId || '';
                    const price= parseFloat((el.querySelector('[data-product-price],.price--withTax,.price')?.textContent||'0').replace(/[^0-9.]/g,''))||0;
                    const img  = el.querySelector('img')?.src || '';
                    const oem  = el.querySelector('[itemprop="mpn"]')?.textContent?.trim() || sku;
                    if (name || sku) results.push({name, sku:String(sku), price, oem_number:oem, image_url:img});
                });
            });
            return results;
        }""") or []
        for c in cards:
            if add(c): added += 1
    except Exception as e:
        print(f"    cards err: {e}", flush=True)

    try:
        single = page.evaluate("""() => {
            const h1 = document.querySelector('h1.productView-title,h1.product-title,h1[itemprop="name"],h1');
            if (!h1) return null;
            return {
                name: h1.textContent.trim(),
                sku: (document.querySelector('[itemprop="sku"],.sku-value,[data-sku]')?.textContent||'').trim(),
                price: parseFloat((document.querySelector('[itemprop="price"],.price--withTax,.price--main')?.textContent||'0').replace(/[^0-9.]/g,''))||0,
                oem_number: (document.querySelector('[itemprop="sku"],.sku-value,[data-sku]')?.textContent||'').trim(),
                description: (document.querySelector('[itemprop="description"],.productView-description')?.textContent||'').trim().slice(0,500),
                category: (document.querySelector('.breadcrumb li:nth-last-child(2)')?.textContent||'').trim(),
                image_url: document.querySelector('.productView-image img,[itemprop="image"]')?.src || '',
                in_stock: !!document.querySelector('[itemprop="availability"][href*="InStock"],.stock-in'),
            };
        }""")
        if single and single.get("name") and add(single): added += 1
    except Exception as e:
        print(f"    single err: {e}", flush=True)
    return added

def save():
    data = json.dumps({"manufacturer":"Land Rover","total":len(parts),"parts":parts}, ensure_ascii=False, indent=2)
    Path(OUTPUT).write_text(data)
    print(f"  [saved] {len(parts)} parts -> {OUTPUT}", flush=True)

def wait_for_real_page(page, timeout=15):
    """Wait until Cloudflare challenge clears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = page.title()
        if "just a moment" not in title.lower() and "cloudflare" not in title.lower():
            print(f"  Page ready: {title[:60]}", flush=True)
            return True
        time.sleep(1.5)
    print(f"  Still on CF page after {timeout}s", flush=True)
    return False

def goto(page, url):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        wait_for_real_page(page)
        return True
    except PWTimeout:
        return False

def main():
    with sync_playwright() as pw:
        print("Launching Chromium...", flush=True)
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width":1280,"height":900},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
        )
        page = ctx.new_page()
        # Mask navigator.webdriver
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        print("Loading homepage (Cloudflare handshake)...", flush=True)
        goto(page, BASE)
        print(f"  Title after wait: {page.title()}", flush=True)

        # Phase 1: Browse /oem-parts/ listing pages
        print("[Phase 1] Browsing /oem-parts/ listing...", flush=True)
        if goto(page, f"{BASE}/oem-parts/"):
            pg = 1
            while pg <= 200:
                n = extract_from_page(page)
                title = page.title()
                print(f"  page {pg}: +{n} | total {len(parts)} | {title[:50]}", flush=True)
                save()
                nxt = page.query_selector('a[rel="next"],.pagination-item--next a,.next a')
                if not nxt: break
                nxt_url = nxt.get_attribute("href")
                if not nxt_url or nxt_url == page.url: break
                goto(page, nxt_url)
                pg += 1

        # Phase 2: LR OEM prefix searches
        print(f"[Phase 2] LR prefix searches ({len(LR_PREFIXES)} terms)...", flush=True)
        for i, pfx in enumerate(LR_PREFIXES):
            if goto(page, f"{BASE}/search.php?search_query={pfx}&section=product"):
                # Paginate search results too
                spg = 1
                while spg <= 10:
                    n = extract_from_page(page)
                    if n > 0 or spg == 1:
                        print(f"  [{i+1}/{len(LR_PREFIXES)}] {pfx} p{spg}: +{n} | total {len(parts)}", flush=True)
                    nxt = page.query_selector('a[rel="next"],.pagination-item--next a,.next a')
                    if not nxt: break
                    nxt_url = nxt.get_attribute("href")
                    if not nxt_url: break
                    goto(page, nxt_url)
                    spg += 1
            save()

        browser.close()
    save()
    print("DONE.", flush=True)

if __name__ == "__main__":
    main()
