"""
toyota_il_harvester.py — Harvest Toyota IL OEM parts price list.

Source: https://union-motors.toyota.co.il/replacement_parts.php
        (Union Motors Israel — official Toyota IL importer)
Accessibility: Direct HTTP GET works (no Cloudflare/Akamai on this subdomain)
Price type: EX-VAT (confirmed: "המחירים המוצגים הינם ללא מע"מ")
Results cap: 500 per search → need many OEM-prefix seeds

Strategy:
  1. Search by 2-digit numeric OEM prefixes (00-99) + letter pairs
  2. If a prefix returns 500 (cap hit), auto-split to 3-digit sub-prefixes
  3. Deduplicate by OEM number
  4. Save to /app/state/toyota_il_parts.json
  5. Run toyota_il_importer.py to load into DB

Run:
    docker exec autospare_backend python3 /app/harvesters/toyota_il_harvester.py
"""

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import ssl
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/state/logs/toyota_il_harvester.log", mode="a"),
    ],
)
log = logging.getLogger("toyota_il")

BASE_URL = "https://union-motors.toyota.co.il/replacement_parts.php"
OUTPUT_FILE = "/app/state/toyota_il_parts.json"
IMPORT_SCRIPT = "/app/importers/toyota_il_importer.py"
SLEEP_S = 0.25
MAX_RESULTS = 500  # page hard cap

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "he-IL,he;q=0.9",
    "Referer": BASE_URL,
}


def _fetch(seed: str) -> list[dict]:
    url = f"{BASE_URL}?s={urllib.parse.quote(seed, safe='')}"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=20) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("seed %r: fetch error %s", seed, exc)
        return []
    return _parse_html(html)


def _parse_html(html: str) -> list[dict]:
    parts = []
    tbody_m = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
    if not tbody_m:
        return []
    tbody = tbody_m.group(1)
    for row_m in re.finditer(r"<tr>(.*?)</tr>", tbody, re.DOTALL):
        cells = re.findall(r"<td>(.*?)</td>", row_m.group(1), re.DOTALL)
        if len(cells) < 6:
            continue
        oem = re.sub(r"<[^>]+>", "", cells[0]).strip()
        name_he = re.sub(r"<[^>]+>", "", cells[1]).strip()
        price_str = re.sub(r"<[^>]+>", "", cells[2]).strip()
        models_raw = re.sub(r"<[^>]+>", "", cells[3]).strip()
        part_type = re.sub(r"<[^>]+>", "", cells[4]).strip()
        in_stock_str = re.sub(r"<[^>]+>", "", cells[5]).strip()
        if not oem or not price_str:
            continue
        try:
            price = float(price_str.replace(",", ""))
        except ValueError:
            continue
        models = [m.strip() for m in models_raw.split("\n") if m.strip()]
        in_stock = in_stock_str in ("כן", "yes", "1")
        parts.append({
            "oem": oem,
            "name_he": name_he,
            "price": price,
            "models": models,
            "part_type": part_type,  # מקורי / חליפי
            "in_stock": in_stock,
        })
    return parts


def _generate_seeds() -> list[str]:
    seeds = []
    # 2-digit numeric prefixes (OEM numbers are typically numeric)
    for a in "0123456789":
        for b in "0123456789":
            seeds.append(a + b)
    # 2-char alphanumeric for letter-prefix OEMs (SU, GY, etc.)
    for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
            seeds.append(a + b)
    return seeds


def harvest() -> dict:
    Path("/app/state/logs").mkdir(parents=True, exist_ok=True)
    seen: dict[str, dict] = {}
    seeds = _generate_seeds()
    log.info("Toyota IL harvester — %d initial seeds", len(seeds))

    for i, seed in enumerate(seeds):
        parts = _fetch(seed)
        capped = len(parts) == MAX_RESULTS

        if capped:
            # Split to 3-digit sub-prefixes
            sub_seeds = [seed + d for d in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
            for sub in sub_seeds:
                sub_parts = _fetch(sub)
                for p in sub_parts:
                    if p["oem"] and p["oem"] not in seen:
                        seen[p["oem"]] = p
                time.sleep(SLEEP_S)
        else:
            for p in parts:
                if p["oem"] and p["oem"] not in seen:
                    seen[p["oem"]] = p

        if i % 50 == 0 and i > 0:
            log.info("seeds %d/%d | unique: %d", i, len(seeds), len(seen))
        time.sleep(SLEEP_S)

    result = list(seen.values())
    log.info("Harvest complete: %d unique Toyota IL OEM parts", len(result))
    return {"parts": result, "count": len(result)}


def main():
    data = harvest()
    out_path = Path(OUTPUT_FILE)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved %d parts to %s", data["count"], OUTPUT_FILE)

    log.info("Running toyota_il_importer.py ...")
    r = subprocess.run(
        ["python3", IMPORT_SCRIPT],
        capture_output=True, text=True, timeout=600
    )
    if r.returncode == 0:
        log.info("Import OK:\n%s", r.stdout[-1000:])
    else:
        log.error("Import FAILED:\n%s\n%s", r.stdout[-500:], r.stderr[-500:])


if __name__ == "__main__":
    main()
