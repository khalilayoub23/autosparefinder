#!/usr/bin/env python3
"""
Harvest all Delek Motors brand catalogs from serviceforms.delek-motors.co.il
Brands: Jaguar/Land Rover (1), Ford/Mazda (2), MINI (4), small brands (6-9)
Runs entirely server-side - no browser needed.
"""
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

    print(f"\n[{make}] brandId={brand_id} — {total_calls} seeds, {max_workers} workers")

    def fetch_task(seed):
        return seed, fetch_seed(brand_id, seed)

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_task, s): s for s in SEEDS}
        for fut in as_completed(futures):
            seed, items = fut.result()
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
                    "is_original": p.get("isOriginal") == "מקורי",
                    "supplier_sku": (p.get("sku") or "").strip(),
                    "source":     "delek-motors.co.il",
                })
            done += 1
            if done % 10 == 0 or done == total_calls:
                print(f"  [{make}] {done}/{total_calls} seeds | unique: {len(parts)}")

    out = {"source": "delek-motors.co.il", "brand_id": brand_id,
           "make": make, "total_parts": len(parts), "parts": parts}
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  [{make}] DONE — {len(parts)} unique parts → {path}")
    return len(parts)

if __name__ == "__main__":
    total = 0
    for brand_id, info in BRANDS.items():
        n = harvest_brand(brand_id, info["make"], info["file"])
        total += n
        time.sleep(1)
    print(f"\n=== HARVEST COMPLETE: {total} parts across {len(BRANDS)} brands ===")
