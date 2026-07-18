"""
champion_motors_harvester.py — Champion Motors IL Price Harvester

Scrapes the Champion Motors (VW Group Israel: VW, Audi, SEAT, Skoda, Cupra)
parts catalog via their WordPress admin-ajax.php endpoint.

Method:
  - POST action=check_mehiron_action with cdesc=<seed> to search by description
  - Seeds: all Hebrew letters + Latin letters + digits (comprehensive coverage)
  - Uses FlareSolverr to maintain session cookies (bypasses Cloudflare bot check)
  - Deduplicates by OEM catalog number
  - Writes JSON to /app/state/champion_motors_parts.json
  - Then runs import_champion_motors.py to load into DB

Response format (HTML table):
  תיאור (name_he) | סוג פריט (type) | מספר קטלוגי (OEM) | תוצר הרכב (brand) |
  דגם (model) | מצאי (in_stock) | אחריות (warranty) | מחיר לצרכן (price ILS incl VAT)

Run inside backend container:
  docker exec autospare_backend python /app/harvesters/champion_motors_harvester.py

Author: AutoSpareFinder Agent — 2026-07-01
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
        logging.FileHandler("/app/state/logs/champion_motors_harvester.log", mode="a"),
    ],
)
log = logging.getLogger("cm_harvester")

FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
CHAMPION_BASE = "https://www.championmotors.co.il"
CHAMPION_CATALOG = f"{CHAMPION_BASE}/catalog/"
CHAMPION_AJAX = f"{CHAMPION_BASE}/wp-admin/admin-ajax.php"
OUTPUT_FILE = Path("/app/state/champion_motors_parts.json")
DELAY_BETWEEN_SEEDS = 0.8  # seconds

# Seeds: Hebrew automotive part names (covers all categories) + OEM number prefixes
# Single Hebrew letters don't work — Champion Motors search needs multi-char terms
SEEDS = [
    # Common part categories — Hebrew
    "מסנן", "בלם", "שמן", "מנוע", "זרוע", "קפיץ", "מצמד", "חיישן", "משאבה",
    "אוורור", "מאוורר", "מפזר", "מחזיר", "מייצב", "מסב", "מחבר", "סלנואיד",
    "תוף", "צינור", "שסתום", "כבל", "ממסר", "נורה", "מגב", "מוט", "כרית",
    "מדרך", "פנס", "מרפק", "מגן", "לחצן", "דוושה", "מחלף", "מאייד",
    "רדיאטור", "קרן", "אחורי", "קדמי", "ימין", "שמאל", "פנימי", "חיצוני",
    "עמוד", "פיסטון", "מרכב", "מכסה", "מגבה", "אוטומטי", "ידני", "בטיחות",
    "תאורה", "דשבורד", "שמשה", "גלגל", "ריפוד", "שלדה", "תליה",
    "דלת", "פגוש", "קצה", "עצמאי", "הגה", "נגד", "הולם", "גלים",
    # Hebrew part types
    "מקורי", "חליפי", "מסנן אויר", "מסנן שמן", "מסנן סולר",
    "צלחת בלם", "רפידות", "הידראולי", "אלקטרוני", "כדורי",
    # OEM prefix searches (2-letter prefixes from VW Group OEM patterns)
    "1K", "3C", "5N", "6R", "7L", "8P", "8V", "5Q", "5G", "3Q",
    "1T", "2K", "5K", "3B", "8J", "1Z", "6N", "6K", "1J",
    "GHE", "WHT", "N90", "N10", "7E0", "7H0", "1H0", "1C0",
    "AHC", "AHY", "BRG", "BMN", "CAX", "CAV", "CAB", "CBZ",
    "JZW", "FEB", "ATE", "TRW", "LUK", "FAG", "INA", "SKF",
    "BG ", "OA", "FCS", "PHC", "URO", "OEM", "HELLA", "BOSCH",
    # Common aftermarket brand names (Champion carries these)
    "FEBI", "LEMFORDER", "MEYLE", "SACHS", "BOGE", "BREMBO",
    "NGK", "VALEO", "DENSO", "PIERBURG", "WAHLER", "MAHLE",
    "MANN", "KNECHT", "KOLBENSCHMIDT", "ELRING", "VICTOR",
]

# Map Hebrew brand names to canonical English form
BRAND_NORMALIZE = {
    "סיאט": "SEAT",
    "סיאט / סקודה": "SEAT",
    "סקודה": "Skoda",
    "סקודה / סיאט": "Skoda",
    "אודי": "Audi",
    "אאודי": "Audi",
    "פולקסווגן": "Volkswagen",
    "פולקסוואגן": "Volkswagen",
    "vw": "Volkswagen",
    "קופרה": "Cupra",
    "cupra": "Cupra",
    "אאודי / פולקסווגן": "Audi",
    "פולקסווגן / אודי": "Volkswagen",
    "פולקסווגן / סקודה": "Volkswagen",
    "פולקסווגן / סיאט": "Volkswagen",
}
DEFAULT_BRAND = "Volkswagen"  # fallback for empty/unknown brand fields


def _fs_request(payload: dict, timeout: int = 45) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        FLARESOLVERR_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def create_session() -> str:
    resp = _fs_request({"cmd": "sessions.create", "maxTimeout": 60000})
    sid = resp.get("session")
    log.info("FlareSolverr session created: %s", sid)
    return sid


def destroy_session(sid: str) -> None:
    try:
        _fs_request({"cmd": "sessions.destroy", "session": sid}, timeout=10)
        log.info("Session %s destroyed", sid)
    except Exception as exc:
        log.warning("Failed to destroy session %s: %s", sid, exc)


def warm_session(sid: str) -> bool:
    """Load catalog page to get session cookies."""
    resp = _fs_request({
        "cmd": "request.get",
        "url": CHAMPION_CATALOG,
        "session": sid,
        "maxTimeout": 30000,
    })
    status = resp.get("solution", {}).get("status")
    log.info("Catalog warm-up status: %s", status)
    return status == 200


def search_by_desc(sid: str, term: str) -> str:
    """Search catalog by description term, return raw HTML."""
    post_data = f"action=check_mehiron_action&cnumber=&cdesc={urllib.parse.quote(term)}"
    try:
        resp = _fs_request({
            "cmd": "request.post",
            "url": CHAMPION_AJAX,
            "session": sid,
            "postData": post_data,
            "maxTimeout": 20000,
        }, timeout=30)
        return resp.get("solution", {}).get("response", "")
    except Exception as exc:
        log.warning("Search failed for %r: %s", term, exc)
        return ""


def parse_table(html: str) -> list[dict]:
    """Parse the HTML table response into part dicts."""
    if not html or "מק''ט לא נמצא" in html or "tbody" not in html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tbody tr")
    parts = []
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 7:
            continue
        name_he = cells[0].strip()
        part_type_he = cells[1].strip()
        oem_raw = cells[2].strip()
        brand_raw = cells[3].strip()
        model_str = cells[4].strip()
        in_stock_raw = cells[5].strip()
        warranty_raw = cells[6].strip()
        price_raw = cells[7].strip() if len(cells) > 7 else "0"

        # Clean OEM number
        oem = re.sub(r"\s+", "", oem_raw).upper()
        if not oem or len(oem) < 3:
            continue

        # Price
        try:
            price_ils = float(re.sub(r"[^\d.]", "", price_raw) or "0")
        except ValueError:
            price_ils = 0.0

        # Brand normalization
        brand_lower = brand_raw.lower().strip()
        vehicle_make = DEFAULT_BRAND
        for key, val in BRAND_NORMALIZE.items():
            if key.lower() in brand_lower or brand_lower in key.lower():
                vehicle_make = val
                break
        # If brand field has Latin "VW" or similar
        if not vehicle_make or brand_lower in ("", "vw דגמים ישנים של"):
            vehicle_make = "Volkswagen"

        # Handle multi-brand (e.g. "סיאט / סקודה") — pick first
        if "/" in brand_raw:
            first = brand_raw.split("/")[0].strip()
            vehicle_make = BRAND_NORMALIZE.get(first, BRAND_NORMALIZE.get(first.lower(), vehicle_make))

        is_original = "מקורי" in part_type_he
        in_stock = "יש" in in_stock_raw

        parts.append({
            "oem_number": oem,
            "name_he": name_he,
            "vehicle_make": vehicle_make,
            "model": model_str,
            "is_original": is_original,
            "part_type_he": part_type_he,
            "price_ils": price_ils,
            "warranty": warranty_raw,
            "in_stock": in_stock,
        })
    return parts


def harvest_all() -> list[dict]:
    """Run all seeds, deduplicate by OEM number, return full parts list."""
    seen: dict[str, dict] = {}  # oem_number → part dict

    sid = create_session()
    try:
        if not warm_session(sid):
            log.error("Failed to warm session — aborting")
            return []

        total_seeds = len(SEEDS)
        for idx, seed in enumerate(SEEDS, 1):
            html = search_by_desc(sid, seed)
            parts = parse_table(html)
            new_count = 0
            for p in parts:
                oem = p["oem_number"]
                if oem not in seen:
                    seen[oem] = p
                    new_count += 1
                elif p["price_ils"] > 0 and seen[oem]["price_ils"] == 0:
                    # Update price if we now have one
                    seen[oem]["price_ils"] = p["price_ils"]
            log.info(
                "Seed [%s] %d/%d: %d results, %d new (total unique: %d)",
                seed, idx, total_seeds, len(parts), new_count, len(seen),
            )
            time.sleep(DELAY_BETWEEN_SEEDS)
    finally:
        destroy_session(sid)

    return list(seen.values())


def main():
    log.info("Champion Motors Harvester starting — %d seeds", len(SEEDS))
    parts = harvest_all()
    log.info("Harvest complete: %d unique parts", len(parts))

    if not parts:
        log.error("No parts harvested — check FlareSolverr and network connectivity")
        sys.exit(1)

    # Stats by brand
    from collections import Counter
    brand_counts = Counter(p["vehicle_make"] for p in parts)
    for brand, count in sorted(brand_counts.items(), key=lambda x: -x[1]):
        log.info("  %-15s %d parts", brand, count)

    # Save JSON
    OUTPUT_FILE.write_text(json.dumps(parts, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved to %s", OUTPUT_FILE)

    # Run import_champion_motors.py
    log.info("Running import_champion_motors.py ...")
    import_script = Path("/app/importers/import_champion_motors.py")
    if import_script.exists():
        env = os.environ.copy()
        env["CM_JSON"] = str(OUTPUT_FILE)
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
        log.warning("import_champion_motors.py not found at %s — skipping DB import", import_script)

    log.info("Done.")


if __name__ == "__main__":
    main()
