#!/usr/bin/env python3
"""
Script: maintenance/seed_car_parts_ie_full_catalog.py
Purpose: Seed harvest_queue with EVERY car-parts.ie brand+model so the existing, proven
         slug-harvester (car_parts_ie_flaresolverr_harvester.py) covers the FULL catalogue,
         not just the 1,401 IL-market models it was originally seeded with.

Why this exists (2026-07-23): car-parts.ie exposes its whole catalogue as STATIC pages that
are fetchable with a cf_clearance cookie (no Playwright / numeric-cascade needed — the
maker_id/model_id/car_id "spares-search" path returns 0 results and is a dead end):
  • /car-brands                       -> the 176 brand slugs (the full brand index)
  • /car-brands/{brand}-parts         -> every model slug for that brand ("{model}-parts")
  • /car-brands/{brand}/{model}-parts -> parts + category links (harvested by the slug harvester)
This seeder walks the first two levels and inserts each "{brand}/{model}" into harvest_queue.
The existing supervised harvester then claims + harvests them (real slugs, so they are NOT the
729 wrong-slug 'empty' rows the IL-name-guessed seeding produced).

Process:
  1. FlareSolverr solves Cloudflare -> cf_clearance cookie + UA (reused for the whole run;
     re-solved automatically on a 403/503).
  2. GET /car-brands -> 176 brand slugs.
  3. For each brand, GET /car-brands/{brand}-parts -> model slugs (strip the '-parts' suffix).
  4. INSERT (brand_en, model_name, model_slug='{brand}/{model}', source='car_parts_ie_full',
     status='pending', priority_rank=100000+brand_index) ON CONFLICT (brand_en, model_slug)
     DO NOTHING  — IL-market rows keep their higher priority (claimed first).
  5. The harvester (already running, supervised) processes the new pending rows automatically.

Idempotent + re-runnable (ON CONFLICT DO NOTHING) — safe to run monthly to pick up new models
car-parts.ie adds.

Data Modified: harvest_queue (new pending rows only).
Data Sources: https://www.car-parts.ie/car-brands , /car-brands/{brand}-parts
Last Updated: 2026-07-23
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request

import asyncpg

BASE = "https://www.car-parts.ie"
FS_URLS = [
    os.getenv("FLARESOLVERR_URL_2", "http://flaresolverr2:8191/v1"),
    os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1"),
]
DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
_UA_FALLBACK = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36")

_COOKIE = ""
_UA = _UA_FALLBACK


def _fs_req(fs: str, cmd: dict, tmo: int = 95) -> dict:
    r = urllib.request.Request(fs, data=json.dumps(cmd).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(r, timeout=tmo) as resp:
        return json.loads(resp.read())


def refresh_cookie() -> None:
    """Solve Cloudflare via a warm FlareSolverr session; set the module cookie header + UA."""
    global _COOKIE, _UA
    last = ""
    for _ in range(4):
        for fs in FS_URLS:
            sid = ""
            try:
                sid = _fs_req(fs, {"cmd": "sessions.create"}).get("session", "")
                sol = _fs_req(fs, {"cmd": "request.get", "url": BASE + "/",
                                   "session": sid, "maxTimeout": 60000}).get("solution", {})
                if sol.get("status") == 200 and sol.get("cookies"):
                    _COOKIE = "; ".join(c["name"] + "=" + c["value"] for c in sol["cookies"])
                    _UA = sol.get("userAgent") or _UA_FALLBACK
                    return
                last = f"status={sol.get('status')}"
            except Exception as e:
                last = str(e)[:80]
            finally:
                if sid:
                    try:
                        _fs_req(fs, {"cmd": "sessions.destroy", "session": sid})
                    except Exception:
                        pass
        time.sleep(4)
    raise RuntimeError(f"FlareSolverr CF solve failed: {last}")


def fetch(path: str, retries: int = 4) -> str:
    url = path if path.startswith("http") else BASE + path
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": _UA, "Cookie": _COOKIE,
                "Accept-Language": "en-IE,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if e.code in (403, 429, 503):        # challenge / rate — re-solve
                refresh_cookie()
                time.sleep(2)
                continue
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return ""


def brand_display(slug: str, existing: dict) -> str:
    if slug in existing:
        return existing[slug]
    return " ".join(w.capitalize() for w in slug.split("-"))


async def main() -> None:
    print("solving Cloudflare…", flush=True)
    refresh_cookie()
    conn = await asyncpg.connect(DB)

    # Reuse existing brand_en spellings so we don't create duplicate rows for a brand
    # the IL seeding already used (unique key is (brand_en, model_slug)).
    existing = {}
    for r in await conn.fetch("SELECT DISTINCT brand_en FROM harvest_queue"):
        be = r["brand_en"]
        existing[re.sub(r"[^a-z0-9]+", "-", be.lower()).strip("-")] = be

    index_html = fetch("/car-brands")
    brands = sorted(set(re.findall(r"/car-brands/([a-z0-9-]+?)-parts", index_html)))
    if not brands:
        print("FATAL: no brands found on /car-brands (CF block?)", flush=True)
        await conn.close()
        return
    print(f"brands discovered: {len(brands)}", flush=True)

    total_models = 0
    inserted = 0
    empty_brands = []
    for bi, bslug in enumerate(brands):
        html = fetch(f"/car-brands/{bslug}-parts")
        models = sorted(set(
            re.findall(rf"/car-brands/{re.escape(bslug)}/([a-z0-9-]+?)-parts", html)))
        if not models:
            empty_brands.append(bslug)
        be = brand_display(bslug, existing)
        for mslug in models:
            model_slug = f"{bslug}/{mslug}"
            model_name = mslug.replace("-", " ").title()[:120]
            try:
                res = await conn.execute(
                    """INSERT INTO harvest_queue
                         (brand_en, model_name, model_slug, source, status,
                          priority_rank, il_vehicle_count)
                       VALUES ($1, $2, $3, 'car_parts_ie_full', 'pending', $4, 0)
                       ON CONFLICT (brand_en, model_slug) DO NOTHING""",
                    be, model_name, model_slug, 100000 + bi)
                if res.endswith(" 1"):
                    inserted += 1
            except Exception as e:
                print(f"  insert err {model_slug}: {str(e)[:60]}", flush=True)
        total_models += len(models)
        if bi % 15 == 0 or bi == len(brands) - 1:
            print(f"[{bi+1}/{len(brands)}] {bslug}: {len(models)} models "
                  f"(seen={total_models} inserted={inserted})", flush=True)
        time.sleep(0.35)

    pending = await conn.fetchval("SELECT COUNT(*) FROM harvest_queue WHERE status='pending'")
    print(f"DONE brands={len(brands)} models_seen={total_models} "
          f"newly_inserted={inserted} queue_pending_now={pending}", flush=True)
    if empty_brands:
        print(f"brands with 0 models parsed ({len(empty_brands)}): {empty_brands[:20]}", flush=True)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
