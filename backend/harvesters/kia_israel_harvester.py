"""
kia_israel_harvester.py — Kia Israel Parts Price Harvester

Scrapes the Kia Israel official parts price list from kia-israel.co.il.
The page is a simple WordPress POST form — no Cloudflare, no authentication needed.

Method:
  - POST to https://kia-israel.co.il/מחירון-חלפים with partDesc=<seed>
  - Parse the returned HTML table (in-page PHP rendering, not AJAX)
  - Deduplicate by OEM number
  - Writes JSON to /app/state/kia_israel_parts.json
  - Then runs import_kia_israel.py to load into DB

Response table columns:
  מק"ט (OEM) | סיומת (suffix) | תיאור (description Hebrew) |
  מחיר ללא מע"מ (price EX-VAT in ILS) | מלאי (stock)

IMPORTANT: Prices are already ex-VAT (no 1.18 division needed for importer_price_ils)
  importer_price_ils = price (already ex-VAT)
  base_price         = price × 1.45
  max_price_ils      = price × 1.18  (consumer reference with VAT)

Run inside backend container:
  docker exec autospare_backend python3 /app/harvesters/kia_israel_harvester.py
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "beautifulsoup4"])
    from bs4 import BeautifulSoup

Path("/app/state/logs").mkdir(parents=True, exist_ok=True)
Path("/app/state").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/state/logs/kia_israel_harvester.log", mode="a"),
    ],
)
log = logging.getLogger("kia_harvester")

# Hebrew URL must be percent-encoded for urllib.request
KIA_PRICE_URL = "https://kia-israel.co.il/%D7%9E%D7%97%D7%99%D7%A8%D7%95%D7%9F-%D7%97%D7%9C%D7%A4%D7%99%D7%9D"
OUTPUT_FILE = Path("/app/state/kia_israel_parts.json")
DELAY_BETWEEN_SEEDS = 0.5  # seconds — no FlareSolverr overhead

# Seeds: Hebrew automotive part names covering Kia's catalog
# Kia Israel stores parts with Hebrew descriptions — seed by common part categories
SEEDS = [
    # Engine & oil system
    "מנוע", "שמן", "מסנן שמן", "מסנן", "מסנן אויר", "מסנן סולר", "מסנן קבינה",
    "בוכנה", "גל ארכובה", "ראש מנוע", "גסקט", "אטם", "מכסה שסתומים",
    # Brakes
    "בלם", "צלחת בלם", "רפידות", "רפידות בלם", "קליפר", "צינור בלם", "נוזל בלם",
    # Suspension & steering
    "קפיץ", "זרוע", "מוט", "מייצב", "מסב", "גלגל", "הגה", "מנהל הגה",
    "בולם", "מחבר", "ציר", "כדורי", "גומי",
    # Electrical
    "חיישן", "נורה", "מצת", "רלאי", "ממסר", "סלנואיד", "אלטרנטור", "מצבר",
    "מד", "חשמל", "פתיח", "מנוע חשמלי", "מגב", "מנוע מגב",
    # Cooling
    "רדיאטור", "תרמוסטט", "משאבת מים", "מאוורר", "צינור קירור", "נוזל קירור",
    # Transmission & clutch
    "מצמד", "גיר", "תיבת הילוכים", "ציר הנע", "שמן גיר",
    # Body & exterior
    "פגוש", "פנס", "מגב", "שמשה", "מכסה", "דלת", "כנף", "מראה",
    # Fuel system
    "משאבת דלק", "מזרק", "פילטר דלק", "מפרגשת", "צינור דלק",
    # Exhaust
    "פליטה", "קטליזטור", "שטה", "פה", "צינור פליטה",
    # Interior
    "ריפוד", "שטיח", "מושב", "חגורה", "כרית אוויר",
    # Common Kia-specific Hebrew terms
    "ספורטג", "ריו", "פיקנטו", "סרטו", "אופטימה", "קרניבל",
    "סטינגר", "סאול", "קוסמוס", "EV6", "נירו",
    # Note: catalogNum (OEM prefix) search doesn't work — requires exact match
    # Aftermarket brands found in Kia IL catalog
    "MOBIS", "HYUNDAI", "DENSO",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": KIA_PRICE_URL,  # must be ASCII (percent-encoded)
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
}


def post_search(term: str) -> str:
    """POST search term to the Kia Israel price list page, return HTML."""
    data = urllib.parse.urlencode({"partDesc": term, "catalogNum": ""}).encode()
    req = urllib.request.Request(KIA_PRICE_URL, data=data, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("Request failed for %r: %s", term, exc)
        return ""


def post_by_catalog(oem: str) -> str:
    """Search by catalog/OEM number prefix."""
    data = urllib.parse.urlencode({"partDesc": "", "catalogNum": oem}).encode()
    req = urllib.request.Request(KIA_PRICE_URL, data=data, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("Catalog search failed for %r: %s", oem, exc)
        return ""


def parse_table(html: str, search_term: str = "") -> list[dict]:
    """Parse the parts table from the HTML response."""
    if not html or "parts-list" not in html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    parts_div = soup.find(class_="parts-list")
    if not parts_div:
        return []
    rows = parts_div.find_all("tr")
    parts = []
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 4:
            continue  # skip header row (th) or short rows
        oem_raw = cells[0].strip()
        suffix = cells[1].strip()
        name_he = cells[2].strip()
        price_raw = cells[3].strip()
        in_stock_raw = cells[4].strip() if len(cells) > 4 else ""

        oem = re.sub(r"\s+", "", oem_raw).upper()
        if not oem or len(oem) < 4:
            continue

        try:
            price_no_vat = float(re.sub(r"[^\d.]", "", price_raw) or "0")
        except ValueError:
            price_no_vat = 0.0

        in_stock = "יש" in in_stock_raw

        parts.append({
            "oem_number": oem,
            "oem_suffix": suffix,
            "name_he": name_he,
            "price_no_vat": price_no_vat,  # already ex-VAT
            "in_stock": in_stock,
            "search_seed": search_term,
        })
    return parts


def harvest_all() -> list[dict]:
    """Run all seeds, deduplicate by OEM number, return full parts list."""
    seen: dict[str, dict] = {}

    total_seeds = len(SEEDS)
    for idx, seed in enumerate(SEEDS, 1):
        # Determine if this is an OEM prefix (alphanumeric short code) or description
        is_oem_prefix = bool(re.match(r"^[A-Z0-9]{2,6}$", seed, re.IGNORECASE))
        if is_oem_prefix:
            html = post_by_catalog(seed)
        else:
            html = post_search(seed)

        parts = parse_table(html, seed)
        new_count = 0
        for p in parts:
            oem = p["oem_number"]
            if oem not in seen:
                seen[oem] = p
                new_count += 1
            elif p["price_no_vat"] > 0 and seen[oem]["price_no_vat"] == 0:
                seen[oem]["price_no_vat"] = p["price_no_vat"]

        log.info(
            "Seed [%s] %d/%d: %d results, %d new (total: %d)",
            seed, idx, total_seeds, len(parts), new_count, len(seen),
        )
        time.sleep(DELAY_BETWEEN_SEEDS)

    return list(seen.values())


def main():
    log.info("Kia Israel Harvester starting — %d seeds, target: kia-israel.co.il", len(SEEDS))
    parts = harvest_all()
    log.info("Harvest complete: %d unique Kia parts", len(parts))

    if not parts:
        log.error("No parts harvested — check network connectivity to kia-israel.co.il")
        sys.exit(1)

    # Save JSON
    OUTPUT_FILE.write_text(json.dumps(parts, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved to %s", OUTPUT_FILE)

    # Run import
    log.info("Running import_kia_israel.py ...")
    import_script = Path("/app/importers/import_kia_israel.py")
    if import_script.exists():
        env = os.environ.copy()
        env["KIA_JSON"] = str(OUTPUT_FILE)
        result = subprocess.run(
            [sys.executable, str(import_script)],
            env=env,
            capture_output=True,
            text=True,
        )
        log.info("Import stdout:\n%s", result.stdout)
        if result.stderr:
            log.warning("Import stderr:\n%s", result.stderr)
        log.info("Import exit code: %d", result.returncode)
    else:
        log.warning("import_kia_israel.py not found — skipping DB import")

    log.info("Done.")


if __name__ == "__main__":
    main()
