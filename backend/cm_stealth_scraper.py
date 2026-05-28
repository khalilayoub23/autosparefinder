"""
Champion Motors Real Data Scraper
- Uses Playwright with stealth mode
- Routes through Tor SOCKS5 proxy to bypass IP WAF
- Intercepts AJAX requests to discover catalog action name
- Loops through search terms to collect full catalog
"""
import json
import re
import time
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

OUTPUT = "/opt/autosparefinder/champion_motors_parts.json"
CATALOG_URL = "https://www.championmotors.co.il/catalog/"
TOR_PROXY = "socks5://127.0.0.1:9050"

SEARCH_TERMS = [
    "\u05d0\u05d8\u05dd", "\u05de\u05e1\u05e0\u05df", "\u05d1\u05d5\u05dc\u05dd", "\u05d1\u05dc\u05dd", "\u05d7\u05d2\u05d5\u05e8\u05d4", "\u05de\u05d9\u05e1\u05d1", "\u05d6\u05e8\u05d5\u05e2",
    "\u05de\u05e0\u05d5\u05e2", "\u05d2\u05d9\u05e8", "\u05e7\u05d5\u05e4\u05dc\u05d9\u05e0\u05d2", "\u05de\u05e0\u05e2\u05d5\u05dc", "\u05d7\u05dc\u05d5\u05df", "\u05de\u05e8\u05d0\u05d4",
    "\u05e4\u05e0\u05e1", "\u05e9\u05de\u05e9\u05d4", "\u05de\u05e6\u05de\u05d3", "\u05e7\u05e4\u05d9\u05e5", "\u05e6\u05d9\u05e0\u05d5\u05e8", "\u05e9\u05e1\u05ea\u05d5\u05dd", "\u05de\u05e9\u05d0\u05d1\u05d4",
    "\u05e8\u05d3\u05d9\u05d0\u05d8\u05d5\u05e8", "\u05de\u05e7\u05d5\u05e8\u05e8", "\u05d7\u05d9\u05e9\u05d5\u05e7", "\u05d2\u05dc\u05d2\u05dc", "\u05de\u05d5\u05d8", "\u05ea\u05d9\u05d1\u05d4",
    "oil", "filter", "seal", "bearing", "brake", "shock",
    "belt", "pump", "valve", "sensor", "switch", "gasket",
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
            if len(cells) < 6:
                continue
            price_raw  = cells[0].inner_text().strip() if len(cells) > 0 else ""
            model      = cells[3].inner_text().strip() if len(cells) > 3 else ""
            veh_make   = cells[4].inner_text().strip() if len(cells) > 4 else ""
            oem        = cells[5].inner_text().strip() if len(cells) > 5 else ""
            part_type  = cells[6].inner_text().strip() if len(cells) > 6 else ""
            name       = cells[7].inner_text().strip() if len(cells) > 7 else ""

            if not oem:
                continue
            oem_clean = re.sub(r"\s+", " ", oem).strip()
            if oem_clean in seen_oem:
                continue
            seen_oem.add(oem_clean)

            price_match = re.search(r"[\d]+\.?[\d]*", price_raw.replace(",", ""))
            price_incl  = float(price_match.group()) if price_match else 0.0
            price_excl  = round(price_incl / 1.18, 2) if price_incl else 0.0

            manufacturer = "Champion Motors"
            vm = veh_make.upper()
            if "\u05d0\u05d5\u05d3\u05d9" in vm or "AUDI" in vm:
                manufacturer = "Audi"
            elif "\u05e1\u05e7\u05d5\u05d3\u05d4" in vm or "SKODA" in vm:
                manufacturer = "Skoda"
            elif "\u05e1\u05d9\u05d0\u05d8" in vm or "SEAT" in vm:
                manufacturer = "SEAT"
            elif "CUPRA" in vm or "\u05e7\u05d5\u05e4\u05e8\u05d4" in vm:
                manufacturer = "CUPRA"
            elif "VW" in vm or "\u05d5\u05d5\u05dc\u05e7\u05e1\u05d5\u05d5\u05d2\u05df" in vm or "\u05de\u05e1\u05d7\u05e8\u05d9\u05d5\u05ea" in vm:
                manufacturer = "Volkswagen"

            if "/" in veh_make and manufacturer == "Champion Motors":
                manufacturer = "Volkswagen"

            origin = "aftermarket"
            if "\u05de\u05e7\u05d5\u05e8\u05d9" in part_type or "original" in part_type.lower():
                origin = "original"

            parts.append({
                "oem_number":       oem_clean,
                "name":             name or f"{oem_clean}",
                "name_he":          name,
                "manufacturer":     manufacturer,
                "vehicle_make_raw": veh_make,
                "model":            model,
                "price_ils_incl_vat": price_incl,
                "price_ils":        price_excl,
                "origin":           origin,
                "source_url":       CATALOG_URL,
            })
            found += 1
        except Exception:
            pass
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
        except Exception:
            pass


def run():
    print("[CM Scraper] Starting with Playwright stealth + Tor proxy...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            proxy={"server": TOR_PROXY},
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            viewport={"width": 1280, "height": 900},
            extra_http_headers={
                "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
                "Referer": "https://www.championmotors.co.il/",
            }
        )
        page = context.new_page()
        stealth_sync(page)
        page.on("request", intercept_request)

        print("[CM Scraper] Loading catalog page...")
        try:
            resp = page.goto(CATALOG_URL, wait_until="networkidle", timeout=45000)
            print(f"[CM Scraper] Page status: {resp.status if resp else 'unknown'}")
        except Exception as e:
            print(f"[CM Scraper] Page load error: {e}")
            browser.close()
            return False

        title = page.title()
        print(f"[CM Scraper] Page title: {title}")
        if "403" in title or "denied" in title.lower() or "blocked" in title.lower():
            print("[CM Scraper] Still blocked. Trying another Tor circuit...")
            browser.close()
            return False

        time.sleep(2)
        count = parse_table_rows(page)
        print(f"[CM Scraper] Initial rows: {count}")

        for term in SEARCH_TERMS:
            try:
                search_input = page.query_selector("input[type='text'], input[name*='search'], input[placeholder]")
                if not search_input:
                    print(f"[CM Scraper] No search input found, skipping term '{term}'")
                    break
                search_input.triple_click()
                search_input.fill(term)
                page.keyboard.press("Enter")
                try:
                    page.wait_for_selector("table tbody tr", timeout=10000)
                    time.sleep(1.5)
                except Exception:
                    time.sleep(2)
                new_found = parse_table_rows(page)
                total = len(parts)
                print(f"[CM Scraper] '{term}': +{new_found} new | total: {total}")
            except Exception as e:
                print(f"[CM Scraper] Error on term '{term}': {e}")
                continue

        browser.close()

    print(f"\n[CM Scraper] Collection complete: {len(parts)} unique parts")
    output = {
        "source": "championmotors.co.il",
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_parts": len(parts),
        "parts": parts,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[CM Scraper] Saved to {OUTPUT}")
    return len(parts) > 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
