# ⚠️  BROWSER TOOL REQUIRED — DO NOT RUN HTTP REQUESTS FROM SERVER IP
# The server IP (94.130.150.23) is blocked by Cloudflare and anti-bot systems.
# All external HTTP extraction must be done via the browser tool (Playwright / run_playwright_code).
# Pattern: (1) Extract with browser tool → save JSON, (2) Import JSON with this script.
# See claude.md § Web Scraping Rules.
"""Match raw Israeli transport manufacturers to unmatched car_brands via Hebrew variants."""
import os
import json
import re
from typing import Dict, Set, List, Tuple
import psycopg2
from difflib import SequenceMatcher
import requests

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog/autospare"

# Enhanced Romanization mapping for common Hebrew brand names
HEBREW_TO_ENGLISH = {
    "טויוטה": "Toyota",
    "יונדאי": "Hyundai",
    "קיה": "Kia",
    "מזדה": "Mazda",
    "סקודה": "Skoda",
    "סוזוקי": "Suzuki",
    "מיצובישי": "Mitsubishi",
    "ניסאן": "Nissan",
    "פולקסווגן": "Volkswagen",
    "סיאט": "SEAT",
    "רנו": "Renault",
    "הונדה": "Honda",
    "סיטרואן": "Citroen",
    "סובארו": "Subaru",
    "מרצדס בנץ": "Mercedes-Benz",
    "פיג'ו": "Peugeot",
    "פורד": "Ford",
    "צ'ירי": "Chery",
    "בי אם וו": "BMW",
    "בי וואי די": "BYD",
    "אודי": "Audi",
    "דייהצו": "Daihatsu",
    "פרארי": "Ferrari",
    "פיאט": "Fiat",
    "ג'אק": "JAC",
    "ג'יאנג סי": "Geely",
    "גרץ": "Great Wall",
    "היוונדאי": "Hyundai",
    "אינפיניטי": "Infiniti",
    "ג'וקואר": "Jaguar",
    "לאדה": "Lada",
    "למבורגיני": "Lamborghini",
    "לנד רובר": "Land Rover",
    "לקסוס": "Lexus",
    "לוטוס": "Lotus",
    "מטשודה": "Mazda",
    "מקלארן": "McLaren",
    "מיני": "MINI",
    "מוגן": "Morgan",
    "ניוטוניק": "Geely",
    "אוראל": "Oral",
    "סימקה": "Simca",
    "סטרו": "Stirling",
    "ווקסוול": "Volvo",
}

def fetch_transport_data():
    """Fetch raw data from Israeli Transportation Office API."""
    print("[Alias] Fetching transport data...")
    
    # Fetch resource 1: vehicle specs
    resource1 = "142afde2-6228-49f9-8a29-9b6c3a0cbe40"
    url1 = f"https://data.gov.il/api/3/action/datastore_search?resource_id={resource1}&limit=100000"
    r1_data = {}
    try:
        resp = requests.get(url1, timeout=30)
        resp.raise_for_status()
        r1_data = resp.json().get("result", {})
    except Exception as e:
        print(f"[Alias] Error fetching resource 1: {e}")
        return []
    
    # Fetch resource 2: vehicle counts
    resource2 = "5e87a7a1-2f6f-41c1-8aec-7216d52a6cf6"
    url2 = f"https://data.gov.il/api/3/action/datastore_search?resource_id={resource2}&limit=100000"
    r2_data = {}
    try:
        resp = requests.get(url2, timeout=30)
        resp.raise_for_status()
        r2_data = resp.json().get("result", {})
    except Exception as e:
        print(f"[Alias] Error fetching resource 2: {e}")
        return []
    
    r1_records = r1_data.get("records", [])
    r2_records = r2_data.get("records", [])
    print(f"[Alias] Fetched: resource1={len(r1_records)}, resource2={len(r2_records)}")
    
    # Build map of r2 records by tozar
    r2_map = {}
    for rec in r2_records:
        tozar = rec.get("tozar", "").strip()
        if tozar:
            if tozar not in r2_map:
                r2_map[tozar] = rec
    
    # Merge: take tozar from r1, add counts from r2
    merged = []
    for rec in r1_records:
        tozar = rec.get("tozar", "").strip()
        if tozar and tozar in r2_map:
            rec["mispar_rechavim_pailim"] = r2_map[tozar].get("mispar_rechavim_pailim", 0)
            merged.append(rec)
    
    print(f"[Alias] Merged records: {len(merged)}")
    return merged

def extract_manufacturers(records: List[Dict]) -> Dict[str, int]:
    """Extract manufacturer variants from merged records, grouped by tozar (clean name)."""
    mfg_counts = {}
    for rec in records:
        tozar = rec.get("tozar", "").strip()
        if tozar:
            count = int(rec.get("mispar_rechavim_pailim") or 0) or 1
            mfg_counts[tozar] = mfg_counts.get(tozar, 0) + count
    return mfg_counts

def similarity(a: str, b: str) -> float:
    """Compute normalized string similarity."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def match_brands_to_hebrew(
    hebrew_mfgs: Dict[str, int], 
    unmatched_brands: List[str]
) -> Dict[str, List[str]]:
    """Match Hebrew manufacturers to unmatched English brands."""
    matches = {}
    
    for he_mfg in hebrew_mfgs.keys():
        # Try to romanize using manual mapping
        en_guess = None
        for he_key, en_val in HEBREW_TO_ENGLISH.items():
            if he_key in he_mfg:
                en_guess = en_val
                break
        
        if not en_guess:
            # Try fuzzy match against brand list
            best_match = None
            best_score = 0
            for brand in unmatched_brands:
                score = similarity(he_mfg, brand)
                if score > best_score and score > 0.4:
                    best_score = score
                    best_match = brand
            en_guess = best_match
        
        if en_guess and en_guess in unmatched_brands:
            if en_guess not in matches:
                matches[en_guess] = []
            matches[en_guess].append(he_mfg)
    
    return matches

def update_aliases_in_db(matches: Dict[str, List[str]]):
    """Update car_brands aliases with matched Hebrew variants."""
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()
    
    updated = 0
    for brand_en, he_variants in matches.items():
        # Get current aliases
        cur.execute(
            "SELECT aliases FROM car_brands WHERE name = %s AND is_active = TRUE",
            (brand_en,)
        )
        row = cur.fetchone()
        if not row:
            print(f"[Alias] Brand not found: {brand_en}")
            continue
        
        current_aliases = row[0] or []
        new_aliases = list(set(current_aliases + he_variants))
        
        # Update
        cur.execute(
            "UPDATE car_brands SET aliases = %s WHERE name = %s",
            (new_aliases, brand_en)
        )
        updated += 1
        print(f"[Alias] {brand_en}: added {len(he_variants)} variants -> {len(new_aliases)} total aliases")
    
    conn.commit()
    cur.close()
    conn.close()
    print(f"[Alias] Updated {updated} brands with Hebrew aliases")

def get_unmatched_brands() -> List[str]:
    """Get list of brands with NULL il_market_priority."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM car_brands WHERE is_active = TRUE AND il_market_priority IS NULL ORDER BY name"
    )
    brands = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return brands

if __name__ == "__main__":
    unmatched = get_unmatched_brands()
    print(f"[Alias] Found {len(unmatched)} unmatched brands")
    
    records = fetch_transport_data()
    if not records:
        print("[Alias] No transport data fetched")
        exit(1)
    
    hebrew_mfgs = extract_manufacturers(records)
    print(f"[Alias] Extracted {len(hebrew_mfgs)} Hebrew manufacturer variants")
    
    matches = match_brands_to_hebrew(hebrew_mfgs, unmatched)
    print(f"[Alias] Found matches for {len(matches)} brands")
    
    for brand, variants in sorted(matches.items()):
        print(f"  {brand}: {variants[:3]}")  # Show first 3
    
    if matches:
        update_aliases_in_db(matches)
        print("[Alias] ✓ Aliases updated in DB")
    else:
        print("[Alias] No matches found")
