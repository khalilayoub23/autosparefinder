#!/usr/bin/env python3
# ⚠️  BROWSER TOOL REQUIRED — DO NOT RUN HTTP REQUESTS FROM SERVER IP
# The server IP (94.130.150.23) is blocked by Cloudflare and anti-bot systems.
# All external HTTP extraction must be done via the browser tool (Playwright / run_playwright_code).
# Pattern: (1) Extract with browser tool → save JSON, (2) Import JSON with this script.
# See claude.md § Web Scraping Rules.
import requests, json, time, os
from concurrent.futures import ThreadPoolExecutor, as_completed

API = "https://serviceforms.delek-motors.co.il/home/GetPriceListReplacements"
HEADERS = {
    "Referer": "https://campaigns.bmw.co.il/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
OUT_DIR = "/opt/autosparefinder"

BRANDS = {
    1: {"make": "Jaguar Land Rover", "file": "jlr_parts.json"},
    2: {"make": "Ford",              "file": "ford_parts.json"},
    4: {"make": "MINI",              "file": "mini_parts.json"},
    6: {"make": "Brand6",            "file": "brand6_parts.json"},
    7: {"make": "Brand7",            "file": "brand7_parts.json"},
    8: {"make": "Brand8",            "file": "brand8_parts.json"},
    9: {"make": "Brand9",            "file": "brand9_parts.json"},
}

SEEDS = list("אבגדהוזחטיכלמנסעפצקרשת") + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + list("0123456789")

def fetch_seed(brand_id, seed):
    try:
        r = requests.get(API, params={"brandId": brand_id, "sku": "", "name": seed},
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json().get("data") or []
    except Exception as e:
        print(f"  [WARN] brandId={brand_id} seed='{seed}': {e}")
        return []

def harvest_brand(brand_id, make, filename, max_workers=8):
    seen = set()
    parts = []
    total_calls = len(SEEDS)
    print(f"\n[{make}] brandId={brand_id} — {total_calls} seeds")
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_seed, brand_id, s): s for s in SEEDS}
        for fut in as_completed(futures):
            seed = futures[fut]
            items = fut.result()
            for p in items:
                key = (p.get("item") or "").replace(" ", "").upper()
                if not key or len(key) < 2 or key in seen:
                    continue
                seen.add(key)
                pv = float(p.get("priceWithTax") or 0)
                parts.append({
                    "oem_number": p.get("item", ""),
                    "name_he":    p.get("name", ""),
                    "name":       p.get("foreignName", ""),
                    "vehicle_make": make,
                    "model":      p.get("modelDescription", ""),
                    "part_type_he": p.get("isOriginal", ""),
                    "stock":      p.get("isWithQuantity", ""),
                    "price_ils_vat": pv,
                    "price_ils":  round(pv / 1.18, 2),
                    "is_original": p.get("isOriginal") == "\u05de\u05e7\u05d5\u05e8\u05d9",
                    "supplier_sku": (p.get("sku") or "").strip(),
                    "source":     "delek-motors.co.il",
                })
            done += 1
            if done % 10 == 0 or done == total_calls:
                print(f"  [{make}] {done}/{total_calls} seeds | unique: {len(parts)}", flush=True)
    out = {"source": "delek-motors.co.il", "brand_id": brand_id, "make": make,
           "total_parts": len(parts), "parts": parts}
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  [{make}] SAVED {len(parts)} parts → {path}")
    return len(parts)

if __name__ == "__main__":
    total = 0
    for brand_id, info in BRANDS.items():
        count = harvest_brand(brand_id, info["make"], info["file"])
        total += count
    print(f"\nTotal: {total} parts across all brands")
