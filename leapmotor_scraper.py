#!/usr/bin/env python3
# ⚠️  BROWSER TOOL REQUIRED — DO NOT RUN HTTP REQUESTS FROM SERVER IP
# The server IP (94.130.150.23) is blocked by Cloudflare and anti-bot systems.
# All external HTTP extraction must be done via the browser tool (Playwright / run_playwright_code).
# Pattern: (1) Extract with browser tool → save JSON, (2) Import JSON with this script.
# See claude.md § Web Scraping Rules.
"""
Leapmotor parts scraper - samelet.com/api
Sweeps description search terms, deduplicates by Material (OEM) number.
"""
import requests, json, time
from pathlib import Path

OUTPUT_FILE = "/opt/autosparefinder/leapmotor_parts.json"
API_URL = "https://samelet.com/api"
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://samelet.com/form/parts-prices/leap",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://samelet.com",
}
TOKEN = "7165a31e6a82f7a4aaff12a2332099d7"
HEBREW = list("אבגדהוזחטיכלמנסעפצקרשת")
TERMS = (
    ["C10","T03","LPM","PT03","RT03","ליפ"] + HEBREW +
    ["oil","brake","filter","motor","sensor","pump","valve","belt",
     "door","mirror","light","lamp","glass","seal","bearing","spring",
     "shock","wiper","camera","battery","cable","hose","kit","cover",
     "air","wheel","disc","pad","shaft","hub","arm","lock","switch",
     "relay","fuse","harness","module","bumper","hood","seat","handle",
     "trim","panel","LED","AC","compressor","EV","charger","plug",
     "inverter","connector","radiator","fan","turbo","exhaust","gear",
     "coolant","clutch","wiper","antenna","grille","emblem","badge"]
)

def search_parts(term, mode=2):
    try:
        r = requests.post(API_URL, data={
            "site":"leap","tag":"parts-prices","page_name":"מחירון חלפים",
            "token":TOKEN,"campaign":"","agency":"","source":"",
            "part_search":term,"part_search_options":str(mode),
        }, headers=HEADERS, timeout=15)
        r.raise_for_status()
        d = r.json()
        p = d.get("parts", [])
        if isinstance(p, dict): return [p] if p else []
        return p if isinstance(p, list) else []
    except Exception as e:
        print(f"  WARN term={term!r}: {e}")
        return []

seen = set()
all_parts = []
print(f"Sweeping {len(TERMS)} terms...")
for i, term in enumerate(TERMS):
    parts = search_parts(term, mode=2)
    new = 0
    for p in parts:
        mat = p.get("Material","").strip()
        if mat and mat not in seen:
            seen.add(mat)
            all_parts.append(p)
            new += 1
    if parts:
        print(f"  [{i+1}/{len(TERMS)}] '{term}': {len(parts)} results, {new} new — total: {len(all_parts)}")
    time.sleep(0.3)

print(f"\nDone. Unique parts: {len(all_parts)}")
Path(OUTPUT_FILE).write_text(json.dumps(all_parts, ensure_ascii=False, indent=2))
print(f"Saved: {OUTPUT_FILE}")
print("\nSample (first 5):")
for p in all_parts[:5]:
    print(f"  {p.get('Material')} | {p.get('MatDescHe')} | {p.get('MatTypeDesc')} | {p.get('PriceNoVat')} ILS")
