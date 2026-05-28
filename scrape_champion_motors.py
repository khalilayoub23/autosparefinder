#!/usr/bin/env python3
"""
Champion Motors Catalog Scraper — runs on YOUR LOCAL machine (Windows/Mac/Linux).
Residential IP bypasses WAF. Sends data directly to production server.

INSTALL (one time):
    pip install playwright requests
    playwright install chromium

RUN:
    python scrape_champion_motors.py
"""

import subprocess, sys, json, time, re

def ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
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

COL = dict(price=0, warranty=1, stock=2, model=3, make=4, oem=5, part_type=6, name=7)


def clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def scrape_all_parts(page):
    parts = []
    seen_oem = set()

    def extract_rows():
        rows = page.query_selector_all("table tr")
        count = 0
        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 8:
                continue
            texts = [clean(c.inner_text()) for c in cells]
            oem = texts[COL["oem"]]
            if not oem or oem == "\u05de\u05e1\u05e4\u05e8 \u05e7\u05d8\u05dc\u05d5\u05d2\u05d9":
                continue
            key = oem.replace(" ", "").upper()
            if key in seen_oem:
                continue
            seen_oem.add(key)

            price_raw = re.sub(r"[^\d.]", "", texts[COL["price"]].replace(",", "."))
            try:
                price_ils = float(price_raw)
            except ValueError:
                price_ils = 0.0

            parts.append({
                "oem_number":    oem,
                "name_he":       texts[COL["name"]],
                "vehicle_make":  texts[COL["make"]],
                "model":         texts[COL["model"]],
                "part_type_he":  texts[COL["part_type"]],
                "stock":         texts[COL["stock"]],
                "warranty":      texts[COL["warranty"]],
                "price_ils_vat": price_ils,
                "price_ils":     round(price_ils / 1.18, 2),
                "is_original":   "\u05de\u05e7\u05d5\u05e8\u05d9" in texts[COL["part_type"]],
                "source":        "championmotors.co.il",
            })
            count += 1
        return count

    print("Waiting for catalog table to load...")
    try:
        page.wait_for_selector("table tr td", timeout=30000)
    except Exception:
        print("WARNING: Table did not load in 30s — trying anyway")

    page_num = 1
    while True:
        time.sleep(1.5)
        before = len(parts)
        found = extract_rows()
        print(f"  Page {page_num}: found {found} new rows, total unique so far: {len(parts)}")

        next_btn = None
        for sel in [
            "a.next", "a[aria-label='Next']", ".pagination .next",
            "a:has-text('>')", "a:has-text('\u05d4\u05d1\u05d0')", "a:has-text('\u00bb')",
            ".wp-pagenavi a.nextpostslink", "a.page-numbers.next",
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    next_btn = btn
                    break
            except Exception:
                continue

        if not next_btn:
            for sel in ["select[name='per_page']", "select.per_page", "#per_page"]:
                try:
                    sel_el = page.query_selector(sel)
                    if sel_el:
                        opts = page.eval_on_selector_all(
                            f"{sel} option",
                            "els => els.map(e => e.value)"
                        )
                        if opts:
                            max_opt = max(opts, key=lambda x: int(x) if x.isdigit() else 0)
                            page.select_option(sel, max_opt)
                            print(f"  Set per-page to {max_opt}, reloading...")
                            time.sleep(3)
                            extract_rows()
                except Exception:
                    pass
            break

        print(f"  Clicking next page ({page_num + 1})...")
        next_btn.click()
        page_num += 1
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            time.sleep(3)

    return parts


def main():
    print("=" * 60)
    print("Champion Motors Scraper")
    print(f"Target: {CATALOG_URL}")
    print(f"Server: {SERVER_URL}")
    print("=" * 60)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            locale="he-IL",
            extra_http_headers={
                "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
            }
        )
        page = ctx.new_page()

        print(f"\nOpening {CATALOG_URL} ...")
        page.goto(CATALOG_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        parts = scrape_all_parts(page)
        browser.close()

    if not parts:
        print("\nNo parts found. The page structure may have changed.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Scraped {len(parts)} unique parts")
    print(f"Saving locally to {SAVE_LOCALLY}...")
    with open(SAVE_LOCALLY, "w", encoding="utf-8") as f:
        json.dump({"source": "championmotors.co.il", "total": len(parts), "parts": parts},
                  f, ensure_ascii=False, indent=2)

    print(f"Sending {len(parts)} parts to {SERVER_URL}/import-champion-motors ...")
    try:
        resp = requests.post(
            f"{SERVER_URL}/import-champion-motors",
            json={"source": "championmotors.co.il", "total": len(parts), "parts": parts},
            timeout=120
        )
        print(f"Server response: {resp.status_code} — {resp.text[:200]}")
    except requests.RequestException as e:
        print(f"Could not send to server: {e}")
        print(f"Parts saved locally at: {SAVE_LOCALLY}")


if __name__ == "__main__":
    main()
