"""
MINI Israel Price List Scraper
================================
Scrapes https://campaigns.mini.co.il/Service-forms_newDesign/spare-parts-price-list-mini.html
The page is a static HTML table (same format as bmw.co.il price lists).
Outputs: /opt/autosparefinder/mini_parts.json
"""
import json, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUTPUT = Path("/opt/autosparefinder/mini_parts.json")
URL    = "https://campaigns.mini.co.il/Service-forms_newDesign/spare-parts-price-list-mini.html"

def clean_price(s: str) -> float | None:
    s = s.replace(",", "").replace("₪", "").replace("ILS", "").strip()
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m else None

def run():
    parts = []
    seen_oem = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            locale="he-IL",
        )
        page = ctx.new_page()
        print(f"[MINI] Loading {URL}...")

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(3)  # Allow JS rendering
        except PWTimeout:
            print("[MINI] Timeout loading page, trying anyway...")

        # Try to find the parts table
        # Structure: look for tables or specific elements
        content = page.content()
        print(f"[MINI] Page loaded, content length: {len(content)}")

        # Detect table structure
        tables = page.query_selector_all("table")
        print(f"[MINI] Tables found: {len(tables)}")

        for t_idx, table in enumerate(tables):
            rows = table.query_selector_all("tr")
            print(f"[MINI] Table {t_idx}: {len(rows)} rows")

            for row in rows:
                cells = row.query_selector_all("td, th")
                texts = [c.text_content().strip() for c in cells]
                if len(texts) < 2:
                    continue

                # Skip header rows
                if any(h in " ".join(texts).lower() for h in ("מק\"ט", "מחיר", "price", "oem", "part")):
                    if t_idx == 0:
                        print(f"[MINI] Header: {texts}")
                    continue

                # Detect OEM number (alphanumeric, >5 chars)
                oem = None
                price = None
                name_he = None

                for cell_text in texts:
                    if re.match(r"^[A-Z0-9][\w\-]{4,}$", cell_text.strip()):
                        if oem is None:
                            oem = cell_text.strip()
                    elif re.search(r"[\d,.]{2,}", cell_text.replace(" ", "")):
                        p = clean_price(cell_text)
                        if p and p > 0 and price is None:
                            price = p
                    elif len(cell_text) > 3 and not re.match(r"^\d+$", cell_text):
                        if name_he is None:
                            name_he = cell_text

                if oem and oem not in seen_oem:
                    seen_oem.add(oem)
                    parts.append({
                        "oem_number": oem,
                        "name_he": name_he or "",
                        "name": "",
                        "vehicle_make": "MINI",
                        "model": "מרובה דגמים",
                        "part_type_he": "מקורי",
                        "stock": "יש",
                        "price_ils_vat": price,
                        "price_ils": round(price / 1.18, 2) if price else None,
                        "is_original": True,
                        "source": "campaigns.mini.co.il",
                        "raw_cells": texts[:8],
                    })

        # If no table worked, dump page structure for debugging
        if not parts:
            print("[MINI] No parts found from tables. Checking page structure...")
            # Try looking for any list or repeated elements
            all_text = page.inner_text("body")
            lines = [l.strip() for l in all_text.split("\n") if l.strip()]
            print(f"[MINI] Total non-empty lines: {len(lines)}")
            print("[MINI] First 30 lines:")
            for l in lines[:30]:
                print(f"  {repr(l)}")

            # Dump raw HTML snippet for manual analysis
            print("\n[MINI] Raw HTML snippet (first 3000 chars):")
            print(content[:3000])

        browser.close()

    print(f"\n[MINI] Total parts scraped: {len(parts)}")
    if parts:
        out = {"source": "campaigns.mini.co.il", "total_parts": len(parts), "parts": parts}
        OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[MINI] ✓ Saved to {OUTPUT}")
        # Sample
        for p in parts[:3]:
            print(f"  {p['oem_number']} | {p['name_he']} | ₪{p['price_ils_vat']}")
    return len(parts)

if __name__ == "__main__":
    n = run()
    sys.exit(0 if n > 0 else 1)
