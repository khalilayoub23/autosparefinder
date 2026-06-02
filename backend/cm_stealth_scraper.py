"""
Champion Motors Real Data Scraper
- Uses Playwright with stealth mode + Tor SOCKS5 proxy
"""
import json, re, time, sys
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

OUTPUT = "/opt/autosparefinder/champion_motors_parts.json"
CATALOG_URL = "https://www.championmotors.co.il/catalog/"
TOR_PROXY = "socks5://127.0.0.1:9050"

SEARCH_TERMS = [
    "אטם", "מסנן", "בולם", "בלם", "חגורה", "מיסב", "זרוע",
    "מנוע", "גיר", "מנעול", "קפיץ", "צינור", "שסתום", "משאבה",
    "רדיאטור", "פנס", "מצמד", "חישוק", "מוט", "תיבה",
    "oil", "filter", "seal", "bearing", "brake", "shock",
    "belt", "pump", "valve", "sensor", "gasket",
]

seen_oem = set()
parts = []
discovered_action = None

def parse_table_rows(page):
    found = 0
    rows = page.query_selector_all("table tbody tr")
    for row in rows:
        try:
            cells = row.query_selector_all("td")
            if len(cells) < 6: continue
            price_raw = cells[0].inner_text().strip() if len(cells) > 0 else ""
            model     = cells[3].inner_text().strip() if len(cells) > 3 else ""
            veh_make  = cells[4].inner_text().strip() if len(cells) > 4 else ""
            oem       = cells[5].inner_text().strip() if len(cells) > 5 else ""
            part_type = cells[6].inner_text().strip() if len(cells) > 6 else ""
            name      = cells[7].inner_text().strip() if len(cells) > 7 else ""
            if not oem: continue
            oem_clean = re.sub(r"\s+", " ", oem).strip()
            if oem_clean in seen_oem: continue
            seen_oem.add(oem_clean)
            pm = re.search(r"[\d]+\.?[\d]*", price_raw.replace(",",""))
            price_incl = float(pm.group()) if pm else 0.0
            vm = veh_make.upper()
            manufacturer = "Volkswagen"
            if "אודי" in vm or "AUDI" in vm: manufacturer = "Audi"
            elif "סקודה" in vm or "SKODA" in vm: manufacturer = "Skoda"
            elif "סיאט" in vm or "SEAT" in vm: manufacturer = "SEAT"
            elif "CUPRA" in vm or "קופרה" in vm: manufacturer = "CUPRA"
            origin = "original" if ("מקורי" in part_type or "original" in part_type.lower()) else "aftermarket"
            parts.append({
                "oem_number": oem_clean,
                "name": name or oem_clean,
                "name_he": name,
                "manufacturer": manufacturer,
                "vehicle_make_raw": veh_make,
                "model": model,
                "price_ils_incl_vat": price_incl,
                "price_ils": round(price_incl / 1.18, 2) if price_incl else 0.0,
                "origin": origin,
                "source_url": CATALOG_URL,
            })
            found += 1
        except: pass
    return found

def intercept_request(request):
    global discovered_action
    if "admin-ajax.php" in request.url and request.method == "POST":
        try:
            body = request.post_data or ""
            m = re.search(r"action=([^&]+)", body)
            if m:
                action = m.group(1)
                if action != "send_form_action" and discovered_action is None:
                    print(f"[Scraper] Discovered AJAX action: {action}")
                    discovered_action = action
        except: pass

def run():
    print("[CM Scraper] Starting Playwright stealth + Tor...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            proxy={"server": TOR_PROXY},
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="he-IL", timezone_id="Asia/Jerusalem",
            viewport={"width":1280,"height":900},
            extra_http_headers={"Accept-Language":"he-IL,he;q=0.9,en-US;q=0.8"}
        )
        page = ctx.new_page()
        Stealth().apply_stealth_sync(page)
        page.on("request", intercept_request)

        print("[CM Scraper] Loading catalog...")
        try:
            resp = page.goto(CATALOG_URL, wait_until="networkidle", timeout=45000)
            print(f"[CM Scraper] Status: {resp.status if resp else '?'}")
        except Exception as e:
            print(f"[CM Scraper] Load error: {e}")
            browser.close()
            return False

        title = page.title()
        print(f"[CM Scraper] Title: {title}")
        if "403" in title or "denied" in title.lower():
            print("[CM Scraper] WAF blocked via Tor too.")
            browser.close()
            return False

        time.sleep(2)
        count = parse_table_rows(page)
        print(f"[CM Scraper] Initial: {count} rows")

        for term in SEARCH_TERMS:
            try:
                inp = page.query_selector("input[type='text'], input[name*='search'], input[placeholder*='חפש'], input[placeholder*='search']")
                if not inp:
                    print(f"[Scraper] No search box found")
                    break
                inp.triple_click()
                inp.fill(term)
                page.keyboard.press("Enter")
                try:
                    page.wait_for_selector("table tbody tr", timeout=8000)
                    time.sleep(1.5)
                except: time.sleep(2)
                new = parse_table_rows(page)
                print(f"[CM Scraper] '{term}': +{new} | total={len(parts)}")
            except Exception as e:
                print(f"[CM Scraper] Error '{term}': {e}")

        browser.close()

    print(f"\n[CM Scraper] Done: {len(parts)} unique parts")
    out = {"source":"championmotors.co.il","scraped_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"total_parts":len(parts),"parts":parts}
    with open(OUTPUT,"w",encoding="utf-8") as f:
        json.dump(out,f,indent=2,ensure_ascii=False)
    print(f"[CM Scraper] Saved -> {OUTPUT}")
    return len(parts) > 0

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
