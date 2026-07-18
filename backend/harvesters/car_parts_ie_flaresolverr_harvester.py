#!/usr/bin/env python3
"""
car_parts_ie_flaresolverr_harvester.py — Server-side harvester using FlareSolverr to bypass Cloudflare.

How it works:
  1. FlareSolverr creates a real Chrome browser session → solves CF challenge → gets cf_clearance
  2. Harvester uses that session to fetch category pages (server-rendered HTML with parts data)
  3. Parts are parsed and POSTed to the relay endpoint
  4. Runs 24/7 without needing the user's browser open

Start:
  nohup python3 /opt/autosparefinder/backend/car_parts_ie_flaresolverr_harvester.py >> /opt/autosparefinder/backend/logs/flaresolverr_harvester.log 2>&1 &
  echo $! > /opt/autosparefinder/backend/flaresolverr_harvester.pid
"""

import asyncio
import json
import os
import re
import time
import logging
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

_state_lock = threading.Lock()

# ── Config ────────────────────────────────────────────────────────────────────
# FLARESOLVERR_URL override lets this run either on the host (default, talks to
# the published port) or inside the backend container (set to the service name).
FLARESOLVERR     = os.environ.get("FLARESOLVERR_URL", "http://localhost:8191/v1")
RELAY            = "https://autosparefinder.co.il/api/v1/system/collect"
_COLLECT_SECRET  = os.environ.get("COLLECT_SECRET", "")
BASE             = "https://www.car-parts.ie"
_BASE_DIR        = Path(__file__).resolve().parent.parent  # /app (script now in harvesters/)
STATE_FILE       = _BASE_DIR / "state" / "flaresolverr_state.json"
LOG_DIR          = _BASE_DIR / "state" / "logs"
PARALLEL_SESSIONS = 3      # concurrent FlareSolverr browser sessions. Tried 5 on 2026-06-30 after
                           # fixing a session leak (see fs_cleanup_stale_sessions) expecting freed
                           # headroom to allow more — measured the opposite: per-slug latency roughly
                           # doubled (load avg 12-17 -> 22-25 on this 4-core box) and net throughput
                           # dropped to 0 models/10min vs ~1/1.4min at 3. The box is CPU-bound by
                           # concurrent headless-Chrome rendering, not memory — reverted to 3.
BATCH_SIZE       = 50
PAGE_TIMEOUT     = 20000   # ms per page request
INTER_MODEL      = 5       # seconds between models

# ── Smart harvest queue (goal 2026-07-07) ────────────────────────────────────
# The harvester is now QUEUE-DRIVEN instead of iterating a hardcoded 144-model
# list. `harvest_queue` (seeded from vehicle_market_il, prioritized by active
# Israeli road vehicles) is the single source of truth. Each worker pulls the
# next highest-priority pending model, harvests it, records parts_found, marks
# it done, and pulls the next — automatically advancing through the full IL
# market list (1,401 models / 83 brands) without any hardcoded queue.
_DB_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

def _db_conn():
    import psycopg2
    return psycopg2.connect(_DB_URL)

def claim_next_model() -> "tuple[str,str] | None":
    """Atomically claim the highest-priority pending model (FOR UPDATE SKIP
    LOCKED so parallel workers never grab the same one). Returns (id, slug)."""
    try:
        conn = _db_conn(); conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, model_slug FROM harvest_queue
                WHERE status = 'pending' AND model_slug IS NOT NULL
                ORDER BY priority_rank ASC NULLS LAST
                LIMIT 1 FOR UPDATE SKIP LOCKED
            """)
            row = cur.fetchone()
            if not row:
                conn.commit(); conn.close(); return None
            qid, slug = row
            cur.execute("UPDATE harvest_queue SET status='in_progress', attempts=attempts+1, updated_at=NOW() WHERE id=%s", (qid,))
            conn.commit()
        conn.close()
        return (str(qid), slug)
    except Exception as e:
        log.error(f"claim_next_model failed: {e}")
        return None

def complete_model(qid: str, parts_found: int) -> None:
    """Mark a claimed model done (or 'empty' if 0 parts — likely a slug that
    car-parts.ie doesn't have; the supervisor surfaces these for remapping)."""
    try:
        conn = _db_conn(); conn.autocommit = True
        status = "done" if parts_found > 0 else "empty"
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE harvest_queue
                SET status=%s, parts_found=parts_found+%s, last_harvested_at=NOW(), updated_at=NOW()
                WHERE id=%s
            """, (status, parts_found, qid))
        conn.close()
    except Exception as e:
        log.error(f"complete_model failed: {e}")

def _queue_pending_count() -> int:
    try:
        conn = _db_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM harvest_queue WHERE status='pending' AND model_slug IS NOT NULL")
            n = cur.fetchone()[0]
        conn.close()
        return int(n)
    except Exception as e:
        log.error(f"_queue_pending_count failed: {e}")
        return 0

def _queue_progress() -> dict:
    try:
        conn = _db_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('done','empty')) AS done,
                    COUNT(*) AS total,
                    COUNT(DISTINCT brand_en) FILTER (WHERE status='done') AS brands_done,
                    COUNT(DISTINCT brand_en) AS brands_total
                FROM harvest_queue
            """)
            d, t, bd, bt = cur.fetchone()
        conn.close()
        return {"done": int(d), "total": int(t), "brands_done": int(bd), "brands_total": int(bt)}
    except Exception as e:
        log.error(f"_queue_progress failed: {e}")
        return {"done": 0, "total": 0, "brands_done": 0, "brands_total": 0}

def reclaim_stale_in_progress(minutes: int = 30) -> int:
    """Reset models stuck 'in_progress' (harvester killed mid-model — restart,
    stall-kill, crash) back to 'pending' so they get retried. Without this a
    killed-mid-model row would wedge forever. Run at harvester startup."""
    try:
        conn = _db_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE harvest_queue SET status='pending', updated_at=NOW()
                WHERE status='in_progress' AND updated_at < NOW() - (%s || ' minutes')::interval
            """, (str(minutes),))
            n = cur.rowcount
        conn.close()
        return n
    except Exception as e:
        log.error(f"reclaim_stale_in_progress failed: {e}")
        return 0

def requeue_completed_for_refresh(days: int = 14) -> int:
    """Re-queue models harvested >N days ago so prices/stock stay fresh once the
    initial full pass through the IL list is complete. Returns count requeued."""
    try:
        conn = _db_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE harvest_queue SET status='pending', updated_at=NOW()
                WHERE status IN ('done','empty') AND last_harvested_at < NOW() - (%s || ' days')::interval
            """, (str(days),))
            n = cur.rowcount
        conn.close()
        return n
    except Exception as e:
        log.error(f"requeue_completed_for_refresh failed: {e}")
        return 0

# ── Top-level category slugs (discovered from browser sessions) ───────────────
# These work for most European car models
BASE_SLUGS = [
    "brake-pads", "brake-discs", "oil-filter", "air-filter", "pollen-filter",
    "shock-absorber", "spark-plug", "fuel-filter", "wiper-blades", "engine-oil",
    "timing-belt", "timing-chain", "clutch", "alternator", "starter",
    "water-pump", "radiator", "thermostat", "fuel-pump", "camshaft",
    "crankshaft", "cylinder-head", "turbocharger", "exhaust", "catalytic-converter",
    "lambda-sensor", "mass-airflow-sensor", "abs-sensor", "wheel-bearing",
    "cv-joint", "tie-rod", "ball-joint", "control-arm", "stabiliser-link",
    "steering-rack", "power-steering-pump", "brake-caliper", "brake-master-cylinder",
    "clutch-disc", "clutch-pressure-plate", "flywheel", "gearbox",
    "drive-shaft", "differential", "engine-mount", "gearbox-mount",
    "headlight", "tail-light", "fog-light", "indicator", "windscreen",
    "door-mirror", "wiper-motor", "window-regulator", "central-locking",
    "battery", "fuse", "relay", "bulb", "switch",
    "seat", "carpet", "dashboard", "steering-wheel", "airbag",
    "brakes-auto", "filters-auto", "suspension-auto", "engine-auto",
    "belt-drive-auto", "ignition-system-auto", "engine-cooling-system-auto",
    "exhaust-system-auto", "fuel-supply-system-auto", "transmission-auto",
    "steering-auto", "body-auto", "interior-auto", "electrics-auto",
]

# ── Van & car models queue ────────────────────────────────────────────────────
VAN_MODELS = [
    "mercedes-benz/sprinter-3-t-box-906",
    "mercedes-benz/sprinter-3-5-t-box-906",
    "mercedes-benz/sprinter-4-6-t-box-906",
    "mercedes-benz/vito-bus-w639",
    "mercedes-benz/vito-box-638",
    "vw/crafter-30-50-box-2e",
    "vw/transporter-v-bus-7hb-7hj-7eb-7ej-7ef",
    "ford/transit-box-fa",
    "ford/transit-platform-chassis-fa",
    "renault/master-iii-box-fv",
    "renault/trafic-ii-box-el-fl-gl",
    "iveco/daily-iv-box-body-estate",
    "iveco/daily-v-box-body-estate",
    "peugeot/boxer-bus-230p",
    "fiat/ducato-box-244",
    "citroen/jumper-bus-230p",
    "opel/movano-b-box",
    # Passenger cars
    "mercedes-benz/e-class-w210",
    "mercedes-benz/e-class-w211",
    "mercedes-benz/c-class-w202",
    "mercedes-benz/c-class-w203",
    "bmw/3-e46",
    "bmw/3-e90",
    "bmw/5-e60",
    "bmw/7-e38",
    "vw/golf-iv-1j1",
    "vw/golf-v-1k1",
    "vw/passat-3c2",
    "audi/a4-8e2-b6",
    "audi/a4-8k2-b8",
    "audi/a6-c5-4b2",
    "opel/astra-f-hatchback-53-54-58-59",
    "opel/astra-g-hatchback-f48-f08",
    "opel/vectra-b-36",
    "ford/focus-i-daw-dbw",
    "ford/mondeo-iii-b5y",
    "renault/megane-ii-bm0-1-cm0-1",
    "renault/clio-ii-bb0-1-cb0-1",
    "peugeot/307-3a-c",
    "peugeot/206-2a-2c",
    "citroen/xsara-n1",
    "seat/leon-1p1",
    "seat/leon-1m1",        # SEAT Leon I (1999-2006)
    "seat/leon-5f1",        # SEAT Leon III (2013-2020)
    "seat/ibiza-6l1",       # SEAT Ibiza III (2002-2009)
    "seat/ibiza-6j5",       # SEAT Ibiza IV (2008-2017)
    "seat/ibiza-6j1",       # SEAT Ibiza IV hatchback variant
    "seat/ateca-kh7",       # SEAT Ateca (2016+)
    "seat/arona-kj7",       # SEAT Arona (2017+)
    "skoda/octavia-1z5",
    "skoda/octavia-ii-combi-1z5",
    "skoda/fabia-6y2",      # Fabia I (1999-2007)
    "skoda/fabia-542",      # Fabia II (2007-2014)
    "alfa-romeo/159-939",
    "fiat/punto-176",
    "fiat/punto-evo-199",   # Punto Evo (2009-2012)
    # Toyota — popular in Israel
    "toyota/corolla-e12",           # Corolla E120 (2001-2007)
    "toyota/corolla-verso-zze12-nre12",
    "toyota/yaris-scp90-ncp90-nlp90-zsp90",  # Yaris II (2005-2011)
    "toyota/auris-zze15-nze15",     # Auris I (2006-2012)
    "toyota/rav4-iv-a4",            # RAV4 IV (2012-2018)
    "toyota/rav4-iii-a3",           # RAV4 III (2005-2012)
    "toyota/avensis-t25",           # Avensis II (2003-2009)
    # Hyundai — popular in Israel
    "hyundai/tucson-tl",            # Tucson III (2015-2020)
    "hyundai/tucson-jm",            # Tucson I (2004-2009)
    "hyundai/i30-fd",               # i30 I (2007-2011)
    "hyundai/i30-gd",               # i30 II (2011-2015)
    "hyundai/i20-pb",               # i20 I (2008-2014)
    "hyundai/elantra-xd",           # Elantra III (2000-2006)
    "hyundai/santa-fe-i-sm",        # Santa Fe I (2000-2006)
    # Kia — popular in Israel
    "kia/sportage-sl",              # Sportage III (2010-2016)
    "kia/sportage-je-km",           # Sportage II (2004-2010)
    "kia/picanto-ta",               # Picanto II (2011-2017)
    "kia/ceed-ed",                  # Ceed I (2006-2012)
    "kia/rio-dc",                   # Rio I (2000-2005)
    # Mazda — popular in Israel
    "mazda/3-bk14",                 # Mazda 3 I (2003-2009)
    "mazda/6-gh-gj",                # Mazda 6 II/III
    "mazda/cx-5-ke",                # CX-5 I (2011-2017)
    # Nissan — popular in Israel
    "nissan/qashqai-j10",           # Qashqai I (2006-2013)
    "nissan/note-e11",              # Note I (2005-2013)
    "nissan/tiida-c11",             # Tiida (2004-2012)
    # Honda — already imported via MCT but car-parts.ie has more parts
    "honda/civic-fn-fk",            # Civic VIII (2005-2012)
    "honda/cr-v-rd1-3-7",          # CR-V I (1995-2001)
    "honda/jazz-gd1-4",             # Jazz I (2001-2008)
    # VW Group additional models
    "vw/polo-9n",                   # Polo IV (2001-2009)
    "vw/polo-6r",                   # Polo V (2009-2014)
    "vw/golf-vi-5k1",               # Golf VI (2008-2012)
    "vw/touareg-7l6-7l7-7la",       # Touareg I (2002-2010)
    "vw/tiguan-5n",                 # Tiguan I (2007-2011)
    "audi/a3-8l1",                  # A3 I (1996-2003)
    "audi/a3-8pa",                  # A3 II Sportback (2004-2013)
    # Mercedes additional
    "mercedes-benz/a-class-w168",   # A-Class I (1997-2004)
    "mercedes-benz/b-class-w245",   # B-Class I (2005-2011)
    "mercedes-benz/clk-c208",       # CLK (1997-2003)
    # BMW additional
    "bmw/1-e87",                    # 1-Series (2003-2013)
    "bmw/x5-e53",                   # X5 I (1999-2006)
    # ── Pre-2000 generations (added 2026-07-05) ──────────────────────────────
    # ROADMAP Phase 2: pre-2000 models = 200-400K genuinely new parts (eBay
    # catalog has gaps here). TecDoc-style slugs; wrong guesses yield 0 parts
    # and are skipped harmlessly.
    "bmw/3-e36",                    # 3-Series (1990-1998)
    "bmw/3-e30",                    # 3-Series (1982-1994)
    "bmw/5-e39",                    # 5-Series (1995-2003)
    "bmw/5-e34",                    # 5-Series (1988-1995)
    "mercedes-benz/190-w201",       # 190 (1982-1993)
    "mercedes-benz/e-class-w124",   # E-Class (1993-1995)
    "mercedes-benz/sprinter-2-t-box-901-902",  # Sprinter I (1995-2006)
    "vw/golf-iii-1h1",              # Golf III (1991-1997)
    "vw/golf-ii-19e-1g1",           # Golf II (1983-1992)
    "vw/polo-6n1",                  # Polo III (1994-1999)
    "vw/passat-3b2",                # Passat B5 (1996-2000)
    "vw/transporter-iv-bus-70xb-70xc-7db-7dw",  # T4 (1990-2003)
    "audi/a4-8d2-b5",               # A4 B5 (1994-2000)
    "audi/80-8c-b4",                # 80 B4 (1991-1996)
    "audi/a6-4a-c4",                # A6 C4 (1994-1997)
    "opel/corsa-b-73-78-79",        # Corsa B (1993-2000)
    "opel/vectra-a-86-87",          # Vectra A (1988-1995)
    "opel/omega-b-25-26-27",        # Omega B (1994-2003)
    "ford/escort-vii-gal-aal-abl",  # Escort VII (1995-1998)
    "ford/fiesta-iv-ja-jb",         # Fiesta IV (1995-2002)
    "ford/mondeo-i-gbp",            # Mondeo I (1993-1996)
    "ford/mondeo-ii-bap",           # Mondeo II (1996-2000)
    "ford/transit-box-e",           # Transit (1994-2000)
    "renault/clio-i-b-c57-5-357",   # Clio I (1990-1998)
    "renault/megane-i-ba0-1",       # Megane I (1995-2003)
    "renault/laguna-i-b56-556",     # Laguna I (1993-2001)
    "peugeot/306-hatchback-7a-7c-n3-n5",  # 306 (1993-2001)
    "peugeot/406-8b",               # 406 (1995-2004)
    "citroen/saxo-s0-s1",           # Saxo (1996-2004)
    "citroen/xantia-x1",            # Xantia (1993-1998)
    "fiat/bravo-182",               # Bravo I (1995-2001)
    "fiat/marea-185",               # Marea (1996-2002)
    "toyota/corolla-e11",           # Corolla E110 (1997-2002)
    "toyota/avensis-t22",           # Avensis I (1997-2003)
    "toyota/carina-e-t19",          # Carina E (1992-1997)
    "nissan/micra-ii-k11",          # Micra II (1992-2003)
    "nissan/almera-i-n15",          # Almera I (1995-2000)
    "nissan/primera-p11",           # Primera II (1996-2001)
    "honda/civic-vi-hatchback-ej-ek",  # Civic VI (1995-2001)
    "honda/accord-v-cc-cd",         # Accord V (1993-1998)
    "mazda/323-f-v-ba",             # 323F V (1994-1998)
    "mazda/626-iv-ge",              # 626 IV (1991-1997)
    "volvo/850-ls",                 # 850 (1991-1997)
    "volvo/v70-i-lv",               # V70 I (1996-2000)
    "seat/ibiza-ii-6k1",            # Ibiza II (1993-1999)
    "seat/toledo-i-1l",             # Toledo I (1991-1999)
    "skoda/felicia-i-6u1",          # Felicia I (1994-1998)
    "skoda/octavia-i-1u2",          # Octavia I (1996-2010)
]

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "flaresolverr_harvester.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("fsharv")

# ── State ─────────────────────────────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"total_parts": 0, "cycles": 0, "runs": []}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── FlareSolverr helpers ──────────────────────────────────────────────────────
def fs_request(cmd: dict) -> dict:
    data = json.dumps(cmd).encode()
    req = urllib.request.Request(
        FLARESOLVERR,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cmd.get("maxTimeout", 20000) // 1000 + 5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "error", "message": str(e)}

def fs_create_session() -> str:
    r = fs_request({"cmd": "sessions.create"})
    return r.get("session", "")

def fs_destroy_session(session_id: str):
    fs_request({"cmd": "sessions.destroy", "session": session_id})

def fs_cleanup_stale_sessions() -> int:
    """
    Destroy any sessions left open from a previous run that died mid-cycle
    (container restart, SIGTERM from the healthcheck watchdog, crash, etc).
    Each leaked session is a live headless Chrome process that keeps consuming
    CPU/RAM forever since FlareSolverr has no TTL — left unchecked these pile up
    across restarts and starve the sessions actually doing work.
    Raises RuntimeError if FlareSolverr is unreachable.
    """
    r = fs_request({"cmd": "sessions.list"})
    if r.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr unreachable: {r.get('message', 'unknown error')}")
    stale = r.get("sessions", [])
    for sid in stale:
        fs_destroy_session(sid)
    return len(stale)

def fs_get(url: str, session_id: str, timeout_ms: int = 20000) -> str:
    r = fs_request({
        "cmd": "request.get",
        "url": url,
        "session": session_id,
        "maxTimeout": timeout_ms,
    })
    if r.get("status") == "ok":
        return r.get("solution", {}).get("response", "")
    return ""

# ── Relay POST ────────────────────────────────────────────────────────────────
def post_relay(vehicle: str, parts: list, done: bool = False) -> bool:
    payload = json.dumps({
        "source": "car-parts.ie",
        "vehicle": vehicle,
        "parts": parts,
        "done": done,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        # Cloudflare's bot-fight-mode blocks the default "Python-urllib/x.y" UA
        # with error 1010 — a normal browser UA gets past it for our own endpoint.
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    if _COLLECT_SECRET:
        headers["X-Collect-Secret"] = _COLLECT_SECRET
    req = urllib.request.Request(RELAY, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        log.warning(f"Relay POST failed ({vehicle}): {e}")
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

def get_additional_slugs(html: str, brand: str, model: str) -> list:
    """Extract additional slugs from model/variant page HTML."""
    slugs = set()
    prefix = f"/car-parts/{brand}/{model}/"
    for m in re.finditer(rf"{re.escape(prefix)}([^/?#\"']+)", html):
        slugs.add(m.group(1))
    return list(slugs)

# ── Harvest one model ─────────────────────────────────────────────────────────
def harvest_model(session_id: str, vehicle: str, state: dict) -> int:
    brand, model = vehicle.split("/", 1)
    log.info(f"▶ {vehicle}")

    # Phase 1: Get additional slugs from model page
    model_html = fs_get(f"{BASE}/car-brands/{brand}/{model}-parts", session_id)
    extra_slugs = get_additional_slugs(model_html, brand, model) if model_html else []

    # Also try first variant page
    variant_ids = re.findall(rf"/car-brands/{re.escape(brand)}/{re.escape(model)}/(\d+)", model_html or "")
    if variant_ids:
        var_html = fs_get(f"{BASE}/car-brands/{brand}/{model}/{variant_ids[0]}", session_id)
        if var_html:
            extra_slugs += get_additional_slugs(var_html, brand, model)

    all_slugs = list(dict.fromkeys(BASE_SLUGS + extra_slugs))  # deduplicate preserving order
    log.info(f"  {vehicle}: {len(all_slugs)} slugs ({len(extra_slugs)} extra)")

    seen = set()
    batch = []
    sent = 0

    for i, slug in enumerate(all_slugs):
        path = f"/car-parts/{brand}/{model}/{slug}"
        html = fs_get(f"{BASE}{path}", session_id, timeout_ms=15000)
        if html:
            parts = [p for p in parse_parts(html, path) if p["sku"] not in seen]
            for p in parts:
                seen.add(p["sku"])
            batch.extend(parts)

        if len(batch) >= BATCH_SIZE:
            if post_relay(vehicle, batch[:BATCH_SIZE]):
                sent += BATCH_SIZE
                batch = batch[BATCH_SIZE:]
            else:
                log.warning(f"  {vehicle}: relay failed, retaining batch for retry")

        if (i + 1) % 20 == 0:
            log.info(f"  {vehicle}: {i+1}/{len(all_slugs)} slugs | {sent+len(batch)} parts")

        time.sleep(0.1)  # small delay between requests

    if batch:
        if post_relay(vehicle, batch):
            sent += len(batch)

    post_relay(vehicle, [], done=True)
    log.info(f"✓ {vehicle}: {sent} parts")
    return sent

# ── Worker (queue-driven) ─────────────────────────────────────────────────────
def worker(worker_id: int, models_this_cycle: int, state: dict):
    """Pull the next highest-priority pending model from harvest_queue, harvest
    it, record the result, repeat — until the per-cycle budget is spent or the
    queue is empty. No hardcoded model list; the IL-market-priority queue drives
    everything. `models_this_cycle` bounds how many this worker takes before the
    cycle rests (keeps the box from being pinned indefinitely)."""
    session_id = fs_create_session()
    if not session_id:
        log.error(f"Worker {worker_id}: failed to create session")
        return

    log.info(f"Worker {worker_id}: session {session_id[:8]}...")
    fs_get(f"{BASE}/", session_id, timeout_ms=30000)  # warm up CF challenge

    done = 0
    while done < models_this_cycle:
        claim = claim_next_model()
        if not claim:
            log.info(f"Worker {worker_id}: queue empty — nothing pending")
            break
        qid, vehicle = claim
        try:
            n = harvest_model(session_id, vehicle, state)
            complete_model(qid, n)
            with _state_lock:
                state["total_parts"] = state.get("total_parts", 0) + n
                state["runs"].append({
                    "vehicle": vehicle, "parts": n,
                    "ts": datetime.utcnow().isoformat(), "worker": worker_id,
                })
                state["runs"] = state["runs"][-200:]
                save_state(state)
            log.info(f"Worker {worker_id}: {vehicle} → {n} parts")
        except Exception as e:
            log.error(f"Worker {worker_id} error on {vehicle}: {e}")
            complete_model(qid, 0)  # mark empty so we don't wedge on it
        done += 1
        time.sleep(INTER_MODEL)

    fs_destroy_session(session_id)
    log.info(f"Worker {worker_id}: done ({done} models this cycle)")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    state = load_state()

    # Verify FlareSolverr is running, then clear out any sessions orphaned by a
    # previous run that didn't get to exit cleanly — see fs_cleanup_stale_sessions.
    try:
        n = fs_cleanup_stale_sessions()
        log.info(f"FlareSolverr ready — cleared {n} stale session(s) from previous run")
    except Exception as e:
        log.error(f"FlareSolverr not reachable: {e}")
        return

    # Reclaim any models left 'in_progress' by a previous run killed mid-model,
    # so they get retried instead of wedging the queue forever.
    reclaimed = reclaim_stale_in_progress(minutes=30)
    if reclaimed:
        log.info(f"Reclaimed {reclaimed} stale in_progress model(s) → pending")

    while True:
        state["cycles"] = state.get("cycles", 0) + 1

        # ROOT FIX 2026-07-07: recycle sessions at the START of every cycle, not
        # just at process startup. FlareSolverr has NO session TTL, and a worker
        # that errors (or a failed destroy, or a mid-cycle restart) leaks its
        # headless-Chrome session forever. Over days these piled up — found 33
        # zombie renderers driving host load to 90 and starving search of DB
        # connections. Since every worker from the previous cycle has already
        # been join()ed, ANY session alive here is a leak — safe to destroy.
        try:
            leaked = fs_cleanup_stale_sessions()
            if leaked:
                log.warning(f"Cycle start: destroyed {leaked} leaked session(s) from prior cycle")
        except Exception as e:
            log.error(f"Cycle-start session cleanup failed (FlareSolverr may be down): {e}")

        # Periodic stale-reclaim (fix 2026-07-08): startup-only reclaim missed
        # orphans on a long-running harvester — the top-3 models sat stuck
        # in_progress 19.5h. Reclaim every cycle with a 45-min threshold (well
        # beyond a legit ~10-min model harvest, so active work is never reset).
        stuck = reclaim_stale_in_progress(minutes=45)
        if stuck:
            log.warning(f"Cycle start: reclaimed {stuck} stale in_progress model(s) → pending")

        # If the whole IL list has been harvested (nothing pending), requeue the
        # oldest-harvested models so prices/stock stay fresh — the queue never
        # runs dry, it cycles through the market by priority forever.
        pending = _queue_pending_count()
        if pending == 0:
            n_refresh = requeue_completed_for_refresh(days=14)
            log.info(f"Queue drained — full IL pass complete. Requeued {n_refresh} models >14d old for refresh.")
            pending = _queue_pending_count()

        # Progress snapshot toward the goal (all IL-market brands + models).
        prog = _queue_progress()
        log.info(
            f"═══ Cycle {state['cycles']} — queue-driven | "
            f"done={prog['done']}/{prog['total']} models "
            f"({prog['brands_done']}/{prog['brands_total']} brands), pending={pending} ═══"
        )

        # Each of the 3 workers takes a bounded slice of the queue this cycle,
        # then the cycle rests — keeps host load in check on the 4-core box.
        MODELS_PER_WORKER_PER_CYCLE = int(os.getenv("HARVEST_MODELS_PER_WORKER", "8"))
        threads = []
        for i in range(PARALLEL_SESSIONS):
            t = threading.Thread(
                target=worker,
                args=(i + 1, MODELS_PER_WORKER_PER_CYCLE, state),
                daemon=True,
            )
            t.start()
            threads.append(t)
            time.sleep(10)  # stagger session creation

        for t in threads:
            t.join()

        log.info(f"Cycle {state['cycles']} done. Total parts: {state['total_parts']}. Resting 120s...")
        time.sleep(120)


if __name__ == "__main__":
    main()
