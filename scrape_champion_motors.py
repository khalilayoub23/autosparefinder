#!/usr/bin/env python3
"""
Champion Motors Catalog Scraper
Runs on YOUR LOCAL Windows/Mac machine (residential IP bypasses WAF).

INSTALL (once):
    pip install playwright requests
    playwright install chromium

RUN:
    python scrape_champion_motors.py
"""
import subprocess, sys, json, time, re

def ensure(pkg):
    try: __import__(pkg.split("[")[0].replace("-","_"))
    except ImportError:
        print(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

ensure("playwright")
ensure("requests")

try:
    from playwright.sync_api import sync_playwright
except Exception:
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    from playwright.sync_api import sync_playwright

import requests

CATALOG_URL  = "https://www.championmotors.co.il/catalog/"
SERVER_URL   = "http://207.180.217.129:9191"
SAVE_LOCALLY = "champion_motors_parts.json"

# Column order in DOM (RTL table)
# td[0]=price  td[1]=warranty  td[2]=stock  td[3]=model
# td[4]=make   td[5]=oem       td[6]=type   td[7]=description

def clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())

def scrape(page):
    parts = []
    seen  = set()

    def extract():
        count = 0
        for row in page.query_selector_all("table tr"):
            cells = row.query_selector_all("td")
            if len(cells) < 8: continue
            t = [clean(c.inner_text()) for c in cells]
            oem = t[5]
            if not oem or oem in ("מספר קטלוגי", ""): continue
            key = re.sub(r"\s+","",oem).upper()
            if key in seen: continue
            seen.add(key)
            try: price_vat = float(re.sub(r"[^\d.]","",t[0].replace(",",".")))
            except: price_vat = 0.0
            parts.append({
                "oem_number":    oem,
                "name_he":       t[7],
                "vehicle_make":  t[4],
                "model":         t[3],
                "part_type_he":  t[6],
                "stock":         t[2],
                "warranty":      t[1],
                "price_ils_vat": price_vat,
                "price_ils":     round(price_vat / 1.18, 2),
                "is_original":   "מקורי" in t[6],
                "source":        "championmotors.co.il",
            })
            count += 1
        return count

    print("Waiting for table...")
    try: page.wait_for_selector("table tr td", timeout=30000)
    except: print("WARNING: Table slow to load")

    page_num = 1
    while True:
        time.sleep(2)
        n = extract()
        print(f"  Page {page_num}: {n} new rows, {len(parts)} total unique")

        # Try next-page button
        next_btn = None
        for sel in [
            "a.next","a[aria-label='Next']",".pagination .next",
            "a.page-numbers.next",".wp-pagenavi a.nextpostslink",
            "a:has-text('>')", "a:has-text('הבא')", "a:has-text('»')",
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    next_btn = btn; break
            except: continue

        if not next_btn:
            # Try to set per-page to max
            for sel in ["select[name='per_page']","select.per_page","#per_page"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        opts = page.eval_on_selector_all(f"{sel} option","els=>els.map(e=>e.value)")
                        max_opt = max(opts, key=lambda x: int(x) if x.isdigit() else 0)
                        page.select_option(sel, max_opt)
                        print(f"  Set per-page to {max_opt}, waiting...")
                        time.sleep(3); extract()
                except: pass
            break

        print(f"  -> Next page {page_num+1}")
        next_btn.click(); page_num += 1
        try: page.wait_for_load_state("networkidle", timeout=15000)
        except: time.sleep(3)

    return parts

def main():
    print("="*60)
    print("Champion Motors Scraper — running from LOCAL machine")
    print(f"URL:    {CATALOG_URL}")
    print(f"Server: {SERVER_URL}")
    print("="*60)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            locale="he-IL",
            extra_http_headers={"Accept-Language":"he-IL,he;q=0.9,en;q=0.8"}
        )
        page = ctx.new_page()
        print(f"\nOpening catalog...")
        page.goto(CATALOG_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        parts = scrape(page)
        browser.close()

    if not parts:
        print("\nNo parts found — check if catalog loaded in browser")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Scraped {len(parts)} unique parts")

    payload = {"source":"championmotors.co.il","total_parts":len(parts),"parts":parts}
    with open(SAVE_LOCALLY,"w",encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Local backup: {SAVE_LOCALLY}")

    print(f"\nSending to server {SERVER_URL}...")
    try:
        r = requests.post(SERVER_URL, json=parts, timeout=60)
        res = r.json()
        print(f"Server: {res}")
        if res.get("ok"):
            print(f"\nSUCCESS: {res['saved']} parts on server!")
        else:
            print("Unexpected server response")
    except requests.exceptions.ConnectionError:
        print(f"\nServer unreachable. Upload manually:")
        print(f"  scp {SAVE_LOCALLY} root@207.180.217.129:/opt/autosparefinder/champion_motors_parts.json")

if __name__ == "__main__":
    main()
