#!/usr/bin/env python3
"""
amayama_flaresolverr_harvester.py — SERVER-SIDE Amayama harvester (2026-07-13).

Uses the EXISTING FlareSolverr container (same tool as the car-parts.ie harvester —
no new installs, no paid services) to bypass Amayama's Cloudflare. The trick that
makes it work (learned 2026-07-13): a one-off request.get on a deep /part/ URL hits a
HARD challenge and times out — but a **persistent session warmed on the homepage
first** clears CF, and subsequent /en/search?q=<oem> fetches return HTTP 200 with the
part table. (Cold, no-session request = fail; session + homepage warmup = success.)

Amayama shows the part CATALOG anonymously but renders PRICES + IL SHIPPING only when
LOGGED IN. So the harvester injects the account's login cookie(s) — read from
amayama_session.json (Playwright/FlareSolverr cookie format) — into the FlareSolverr
session. Without it, the catalog loads but prices are blank (logged + skipped).

Flow: create session → warm homepage → per unpriced Japanese-brand OEM (from
/api/v1/system/unpriced-oems): request.get /en/search?q=<oem> (with login cookies) →
parse tr.part-table__row (.part-price + .shipping_price) → relay to /collect
(brand=amayama) → amayama_price_import.py. Session recycled every N requests.

Run (supervised): docker exec -d autospare_backend python3 /app/harvesters/amayama_flaresolverr_harvester.py
"""
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

FS = os.environ.get("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
BASE = "https://www.amayama.com"
API = os.environ.get("SELF_API_BASE", "https://autosparefinder.co.il/api/v1/system")
SECRET = os.environ.get("COLLECT_SECRET", "")
# Relative to this file so it resolves both on the host and inside the container
# (backend is bind-mounted at /app, so an absolute /opt/... path won't exist there).
COOKIES_FILE = Path(__file__).resolve().parent.parent / "amayama_session.json"  # /app root
BRANDS = "toyota,lexus,honda,nissan,mazda,subaru,mitsubishi,infiniti,acura,suzuki,daihatsu"
BATCH = int(os.environ.get("AMAYAMA_BATCH", "40"))
SESSION_REQUESTS = 60          # recycle the FS session after this many fetches
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0.0.0 Safari/537.36")
PRICE = re.compile(r"\d+\.\d{2}|\d+")


def log(m): print(f"[amayama_fs] {m}", flush=True)


def fs_request(cmd: dict) -> dict:
    data = json.dumps(cmd).encode()
    req = urllib.request.Request(FS, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cmd.get("maxTimeout", 90000) // 1000 + 20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"status": "error", "message": e.read().decode()[:200]}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


def load_login_cookies() -> list:
    if COOKIES_FILE.exists():
        try:
            ck = json.loads(COOKIES_FILE.read_text())
            log(f"loaded {len(ck)} login cookie(s)")
            return ck
        except Exception as e:
            log(f"cookie file parse error: {e}")
    else:
        log("NO amayama_session.json — catalog will load but PRICES will be blank "
            "(login-gated). Provide the account login cookie(s) to get prices.")
    return []


def _num(s):
    m = PRICE.search((s or "").replace(",", ""))
    return float(m.group()) if m else 0.0


def parse(html: str, oem: str) -> list:
    if "part-table__row" not in html:
        return []
    d = BeautifulSoup(html, "html.parser")
    a = d.select_one('a[href*="/genuine-catalogs/"]')
    brand = ""
    if a and a.get("href"):
        mm = re.search(r"genuine-catalogs/([^/?\"]+)", a["href"])
        brand = (mm.group(1) if mm else "").lower()
    name = ""
    for line in d.get_text("\n").split("\n"):
        t = line.strip()
        if re.match(r"^[A-Z][A-Z, /]{3,30}$", t):
            name = t
            break
    out = []
    for row in d.select("tr.part-table__row"):
        pe = row.select_one(".part-price")
        price = _num(pe.get_text()) if pe else 0.0
        if price <= 0:
            continue
        se = row.select_one(".shipping_price") or row.select_one('[class*="shipping"]')
        ship = _num(se.get_text()) if se else 0.0
        txt = re.sub(r"\s+", " ", row.get_text(" ")).strip()
        whm = re.search(r"Japan \(Osaka\)|UAE|Russia|Japan", txt)
        pm = re.search(r"Photo for ([A-Za-z\-]+) ([0-9A-Za-z\-]+)", txt)
        out.append({"oem": oem, "name": name, "brand": brand,
                    "price_usd": price, "shipping_usd": ship if ship > 0 else None,
                    "warehouse": whm.group(0) if whm else "",
                    "part_num": pm.group(2) if pm else "",
                    "part_type": "analog" if (pm and pm.group(1).lower() != brand) else "genuine"})
    return out


# The self-API calls go out through Cloudflare, which 403s the default urllib UA
# (bot-fight mode, error 1010). A browser UA is required — same fix as the car-parts.ie
# relay. Content-Type text/plain keeps it a CORS "simple" POST (no preflight).
_SELF_HEADERS = {"Content-Type": "text/plain", "User-Agent": UA}


def feed(limit=BATCH) -> list:
    body = json.dumps({"secret": SECRET, "source": "amayama_fs",
                       "limit": limit, "brands": BRANDS}).encode()
    req = urllib.request.Request(API + "/unpriced-oems", data=body, headers=_SELF_HEADERS)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read()).get("oems", [])
    except Exception as e:
        log(f"feed error: {e}")
        return []


def relay(parts, done=False):
    body = json.dumps({"brand": "amayama", "parts": parts, "done": done,
                       "secret": SECRET}).encode()
    req = urllib.request.Request(API + "/collect", data=body, headers=_SELF_HEADERS)
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:
        log(f"relay error: {e}")


def new_session(login_cookies):
    r = fs_request({"cmd": "sessions.create"})
    sid = r.get("session", "")
    if not sid:
        return ""
    # WARM on homepage so CF clears for the session before hitting deep URLs.
    warm = fs_request({"cmd": "request.get", "url": f"{BASE}/en/", "session": sid,
                       "maxTimeout": 90000})
    if (warm.get("solution", {}) or {}).get("status") != 200:
        log(f"homepage warmup failed: {warm.get('message','?')[:80]}")
        fs_request({"cmd": "sessions.destroy", "session": sid})
        return ""
    return sid


def fetch(sid, oem, login_cookies):
    cmd = {"cmd": "request.get", "url": f"{BASE}/en/search?q={oem}",
           "session": sid, "maxTimeout": 120000}
    if login_cookies:
        cmd["cookies"] = login_cookies
    r = fs_request(cmd)
    return (r.get("solution", {}) or {}).get("response", "") or ""


def main():
    if not SECRET:
        log("COLLECT_SECRET not set — exiting.")
        return
    login_cookies = load_login_cookies()
    # clean up any leaked sessions from a prior run
    for sd in (fs_request({"cmd": "sessions.list"}).get("sessions", []) or []):
        fs_request({"cmd": "sessions.destroy", "session": sd})

    sid = new_session(login_cookies)
    if not sid:
        log("could not establish a CF-cleared session — is FlareSolverr up? Exiting for supervisor to retry.")
        return
    log(f"session ready ({sid[:12]}) — Cloudflare cleared. Harvest loop starting.")
    reqs = 0
    rounds = 0
    try:
        while True:
            oems = feed(BATCH)
            if not oems:
                log("feed empty — sleeping 60s")
                time.sleep(60)
                continue
            parts, matched = [], 0
            for oem in oems:
                if reqs >= SESSION_REQUESTS:      # recycle session periodically
                    fs_request({"cmd": "sessions.destroy", "session": sid})
                    sid = new_session(login_cookies)
                    reqs = 0
                    if not sid:
                        log("session re-create failed — exiting for supervisor retry.")
                        return
                html = fetch(sid, oem, login_cookies)
                reqs += 1
                rows = parse(html, oem)
                if rows:
                    matched += 1
                    parts.extend(rows)
                time.sleep(0.4)
            if parts:
                for i in range(0, len(parts), 200):
                    relay(parts[i:i + 200], done=(i + 200 >= len(parts)))
            rounds += 1
            priced = sum(1 for p in parts if p.get("price_usd", 0) > 0)
            log(f"round {rounds}: {matched}/{len(oems)} matched, {len(parts)} rows ({priced} priced) relayed")
            time.sleep(1.0)
    finally:
        fs_request({"cmd": "sessions.destroy", "session": sid})


if __name__ == "__main__":
    main()
