#!/usr/bin/env python3
"""
car_parts_ie_playwright_harvester.py — Server-side background harvester for car-parts.ie

Runs headless Chromium via Playwright, using stored CF cookies to bypass Cloudflare.
Cycles through van models indefinitely, POSTing batches to the relay endpoint.

Setup:
  1. Export CF cookies from Chrome DevTools console (on www.car-parts.ie):
       copy(JSON.stringify([...document.cookie.split('; ').map(c=>{const i=c.indexOf('=');return{name:c.slice(0,i),value:c.slice(i+1),domain:'.car-parts.ie',path:'/',secure:true,httpOnly:false}})]))
     Paste result into: /opt/autosparefinder/backend/car_parts_ie_cookies.json

  2. Run:
       nohup python3 /opt/autosparefinder/backend/car_parts_ie_playwright_harvester.py >> /opt/autosparefinder/backend/logs/cpi_harvester.log 2>&1 &
       echo $! > /opt/autosparefinder/backend/cpi_harvester.pid

  3. Monitor:
       tail -f /opt/autosparefinder/backend/logs/cpi_harvester.log
       cat /opt/autosparefinder/backend/car_parts_ie_harvest_state.json
"""

import json
import re
import sys
import time
import logging
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, BrowserContext

# ── Config ───────────────────────────────────────────────────────────────────
RELAY        = "https://autosparefinder.co.il/api/v1/system/collect"
BASE         = "https://www.car-parts.ie"
COOKIES_FILE = Path("/opt/autosparefinder/backend/car_parts_ie_cookies.json")
STATE_FILE   = Path("/opt/autosparefinder/backend/car_parts_ie_harvest_state.json")
LOG_DIR      = Path("/opt/autosparefinder/backend/logs")
BATCH_SIZE   = 50
PAGE_DELAY   = 0.15    # seconds between category fetches
VARIANT_LIMIT = 3      # max variant pages to scan for slugs
INTER_MODEL  = 3       # seconds between models
CYCLE_REST   = 120     # seconds between full cycles

# ── Van model queue (priority: high new-insert rate first) ───────────────────
VAN_MODELS = [
    # ─── Commercial vans — highest insert rate ─────────────────────────────
    "mercedes-benz/sprinter-3-t-box-906",
    "mercedes-benz/sprinter-3-5-t-box-906",
    "mercedes-benz/sprinter-4-6-t-box-906",
    "mercedes-benz/sprinter-2-t-box-901-902",
    "mercedes-benz/sprinter-3-t-box-903",
    "mercedes-benz/sprinter-3-t-bus-903",
    "mercedes-benz/vito-bus-w639",
    "mercedes-benz/vito-box-638",
    "mercedes-benz/vito-bus-638",
    "mercedes-benz/vito-mixto-box-w639",
    "mercedes-benz/vito-box-w447",
    "mercedes-benz/vito-tourer-w447",
    "vw/crafter-30-50-box-2e",
    "vw/transporter-vi-bus-sgb-sgg-sgj",
    "vw/transporter-v-bus-7hb-7hj-7eb-7ej-7ef",
    "vw/transporter-iv-bus-70xb-70xc-7db-7dw",
    "ford/transit-box-fa",
    "ford/transit-platform-chassis-fa",
    "ford/transit-connect-p65-p70-p80",
    "renault/master-iii-box-fv",
    "renault/master-iii-platform-chassis-ev-hv-uv",
    "renault/master-ii-bus-jd",
    "renault/trafic-ii-box-el-fl-gl",
    "iveco/daily-iv-box-body-estate",
    "iveco/daily-v-box-body-estate",
    "peugeot/boxer-bus-230p",
    "peugeot/boxer-platform-chassis-230",
    "fiat/ducato-box-244",
    "fiat/ducato-platform-chassis-244",
    "citroen/jumper-bus-230p",
    "citroen/jumper-platform-chassis-230",
    # ─── Pickup trucks / 4x4 ───────────────────────────────────────────────
    "toyota/land-cruiser-pick-up-80-parts",
    "nissan/navara-d40-parts",
    "ford/ranger-es-parts",
    "mitsubishi/l200-ka-t-parts",
    "toyota/hilux-vii-kun-ggn-tgn-parts",
]

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "cpi_harvester.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("cpi")

# ── State ─────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"runs": [], "total_parts": 0, "cycles": 0}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── Relay POST ────────────────────────────────────────────────────────────────
def post_relay(vehicle: str, parts: list, done: bool = False) -> bool:
    payload = json.dumps({
        "source": "car-parts.ie",
        "vehicle": vehicle,
        "parts": parts,
        "done": done,
    }).encode()
    req = urllib.request.Request(
        RELAY,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        log.warning(f"relay POST failed ({vehicle}): {e}")
        return False

# ── HTML parsing ──────────────────────────────────────────────────────────────
def parse_parts(html: str, path: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    for el in soup.select(".rec_products_single_block"):
        name_el = el.select_one(".title")
        sku_el  = el.select_one(".artikle")
        if not sku_el:
            continue
        name = name_el.get_text(strip=True) if name_el else ""
        sku  = re.sub(r"Article\s*\S+\s*", "", sku_el.get_text(strip=True), flags=re.I).strip()
        price_text = (el.select_one(".bottom_block") or el).get_text()
        pm = re.search(r"([\d.]+)", price_text)
        if name and sku:
            parts.append({
                "name": name,
                "sku": sku,
                "price_eur": float(pm.group(1)) if pm else None,
                "brand": name.split()[0] if name else "",
                "source_url": f"{BASE}{path}",
            })
    return parts

def parse_slugs(html: str, brand: str, model: str, variant=False) -> list:
    soup = BeautifulSoup(html, "html.parser")
    slugs = []
    prefix = f"/car-parts/{brand}/{model}/"
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if prefix not in href:
            continue
        if variant:
            m = re.search(rf"/car-parts/{re.escape(brand)}/{re.escape(model)}/[^/]+/([^/]+)/", href)
        else:
            m = re.search(rf"/car-parts/{re.escape(brand)}/{re.escape(model)}/([^/?#]+)", href)
        if m:
            slugs.append(m.group(1))
    return slugs

def parse_variant_ids(html: str, brand: str, model: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    pattern = re.compile(rf"/car-brands/{re.escape(brand)}/{re.escape(model)}/(\d+)$")
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        m = pattern.search(href)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids

# ── Playwright request helper ─────────────────────────────────────────────────
def fetch_html(ctx: BrowserContext, path: str) -> str:
    """Fetch a car-parts.ie path using Playwright's APIRequestContext (sends CF cookies)."""
    try:
        resp = ctx.request.get(f"{BASE}{path}", timeout=20000)
        if resp.ok:
            return resp.text()
    except Exception as e:
        log.debug(f"fetch_html {path}: {e}")
    return ""

# ── Slug discovery ─────────────────────────────────────────────────────────────
def get_all_slugs(ctx: BrowserContext, brand: str, model: str) -> list:
    html = fetch_html(ctx, f"/car-brands/{brand}/{model}-parts")
    if not html:
        return []

    slugs = set(parse_slugs(html, brand, model, variant=False))
    variant_ids = parse_variant_ids(html, brand, model)[:VARIANT_LIMIT]

    for vid in variant_ids:
        vhtml = fetch_html(ctx, f"/car-brands/{brand}/{model}/{vid}")
        if vhtml:
            for s in parse_slugs(vhtml, brand, model, variant=True):
                slugs.add(s)
        time.sleep(0.3)

    return list(slugs)

# ── Harvest one model ─────────────────────────────────────────────────────────
def harvest_model(ctx: BrowserContext, vehicle: str) -> int:
    brand, model = vehicle.split("/", 1)
    log.info(f"▶ {vehicle}")
    slugs = get_all_slugs(ctx, brand, model)
    if not slugs:
        log.warning(f"  no slugs for {vehicle} — skip")
        return 0

    log.info(f"  {len(slugs)} categories")
    seen = set()
    batch = []
    sent = 0

    for i, slug in enumerate(slugs):
        html = fetch_html(ctx, f"/car-parts/{brand}/{model}/{slug}")
        if html:
            new_parts = [p for p in parse_parts(html, f"/car-parts/{brand}/{model}/{slug}")
                         if p["sku"] not in seen]
            for p in new_parts:
                seen.add(p["sku"])
            batch.extend(new_parts)

        if len(batch) >= BATCH_SIZE:
            if post_relay(vehicle, batch[:BATCH_SIZE]):
                sent += BATCH_SIZE
            batch = batch[BATCH_SIZE:]

        if (i + 1) % 50 == 0:
            log.info(f"  {vehicle}: {i+1}/{len(slugs)} cats | {sent+len(batch)} parts")

        time.sleep(PAGE_DELAY)

    if batch:
        if post_relay(vehicle, batch):
            sent += len(batch)

    post_relay(vehicle, [], done=True)
    log.info(f"✓ {vehicle}: {sent} parts")
    return sent

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    state = load_state()

    cookies = []
    if COOKIES_FILE.exists():
        cookies = json.loads(COOKIES_FILE.read_text())
        log.info(f"Loaded {len(cookies)} cookies from {COOKIES_FILE.name}")
    else:
        log.warning(f"No cookies file at {COOKIES_FILE} — Cloudflare will likely block. "
                    f"Export from Chrome DevTools console on car-parts.ie:\n"
                    f"  copy(JSON.stringify([...document.cookie.split('; ').map(c=>{{const i=c.indexOf('=');return{{name:c.slice(0,i),value:c.slice(i+1),domain:'.car-parts.ie',path:'/',secure:true}}}})]))")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        if cookies:
            ctx.add_cookies(cookies)

        log.info("Playwright browser ready. Starting harvest loop.")

        while True:
            state["cycles"] = state.get("cycles", 0) + 1
            cycle_start = time.time()
            cycle_parts = 0
            log.info(f"═══ Cycle {state['cycles']} ═══")

            for vehicle in VAN_MODELS:
                try:
                    n = harvest_model(ctx, vehicle)
                    cycle_parts += n
                    state["total_parts"] = state.get("total_parts", 0) + n
                    state.setdefault("runs", []).append({
                        "vehicle": vehicle,
                        "parts": n,
                        "ts": datetime.utcnow().isoformat(),
                    })
                    # Keep only last 200 runs in state
                    state["runs"] = state["runs"][-200:]
                    save_state(state)
                except Exception as e:
                    log.error(f"harvest_model {vehicle} error: {e}")

                time.sleep(INTER_MODEL)

            elapsed = int(time.time() - cycle_start)
            log.info(f"Cycle {state['cycles']} done: {cycle_parts} parts in {elapsed}s. "
                     f"Total: {state['total_parts']}. Resting {CYCLE_REST}s...")
            time.sleep(CYCLE_REST)


if __name__ == "__main__":
    main()
