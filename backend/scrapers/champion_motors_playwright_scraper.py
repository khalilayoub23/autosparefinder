"""
Champion Motors Playwright Scraper — Server-side full catalog
"""
import json, re, time, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT = "/opt/autosparefinder/champion_motors_parts.json"
BASE = "https://www.championmotors.co.il"
seen = set()
parts = []

def add(p):
    key = (p.get("oem_number") or p.get("name","")).strip()
    if not key or key in seen:
        return False
    seen.add(key)
    price_incl = float(p.get("price_ils_incl_vat") or 0)
    parts.append({
        "oem_number": key,
        "name": p.get("name","")[:200],
        "manufacturer": p.get("manufacturer",""),
        "brand": p.get("brand"),
        "model": p.get("model",""),
        "price_ils_incl_vat": price_incl,
        "price_ils": round(price_incl / 1.18 * 100) / 100,
        "source_url": p.get("source_url",""),
    })
    return True

def run_scraper():
    print("[CM Scraper] Starting...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.championmotors.co.il/catalog/", wait_until="networkidle", timeout=30000)
        time.sleep(2)
        
        try:
            page.wait_for_selector("table tbody tr", timeout=10000)
            rows = page.query_selector_all("table tbody tr")
            
            for row in rows:
                cells = row.query_selector_all("td")
                if len(cells) < 3: continue
                try:
                    oem = cells[0].text_content().strip()
                    price_text = cells[1].text_content().strip()
                    brand_col = cells[2].text_content().strip()
                    model = cells[3].text_content().strip() if len(cells) > 3 else ""
                    mfr = cells[6].text_content().strip() if len(cells) > 6 else ""
                    
                    price_match = re.search(r"[\d,\.]+", price_text.replace(",","."))
                    price = float(price_match.group()) if price_match else 0
                    
                    if oem and price > 0:
                        add({
                            "oem_number": oem,
                            "name": f"{brand_col} {model} {oem}",
                            "manufacturer": mfr,
                            "brand": mfr.strip() if mfr else brand_col,
                            "model": model,
                            "price_ils_incl_vat": price,
                            "source_url": page.url,
                        })
                except:
                    pass
        except:
            pass
        
        browser.close()
    
    output = {
        "source": "championmotors.co.il",
        "total_parts": len(parts),
        "parts": parts,
    }
    
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"[CM Scraper] ✓ Saved {len(parts)} parts to {OUTPUT}")
    return output

if __name__ == "__main__":
    result = run_scraper()
    sys.exit(0 if result["total_parts"] > 0 else 1)
