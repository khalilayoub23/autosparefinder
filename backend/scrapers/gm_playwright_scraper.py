#!/usr/bin/env python3
"""GM OEM Parts Scraper — vehicle-model navigation strategy
URL structure: g.oempartsonline.com
  Make list:   /v-chevrolet, /v-cadillac, /v-buick, /v-gmc
  Model list:  /v-{make}-{model-slug}
  Year list:   /v-{year}-{make}-{model-slug}
  Trims:       /v-{year}-{make}-{model-slug}--{trim}
  Parts:       paginated /oem-parts/ links on vehicle pages

Targets only models active in the Israeli vehicle registry.

Usage:
  python3 gm_playwright_scraper.py
  MAX_MODELS=5 python3 gm_playwright_scraper.py   # test run
"""
import json, time, os, asyncio, subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import asyncpg

OUTPUT   = "/tmp/gm_vehicle_parts.json"
BASE     = "https://g.oempartsonline.com"
MAX_MODELS = int(os.environ.get("MAX_MODELS", "0"))  # 0 = all

# GM makes to scrape (matching IL registry manufacturers)
GM_MAKES = ["chevrolet", "cadillac", "buick", "gmc"]

# Models confirmed active in Israel (populated at runtime from DB)
IL_MODELS = {}   # { "chevrolet": {"spark", "trax", ...}, ... }

seen  = set()
parts = []


def add(p):
    key = (p.get("oem_number") or p.get("sku") or "").strip()
    if not key or key in seen:
        return False
    seen.add(key)
    parts.append({
        "sku":         p.get("sku", "").strip(),
        "name":        p.get("name", "").strip(),
        "description": (p.get("description") or "")[:500],
        "price":       float(p.get("price") or 0),
        "oem_number":  key,
        "image_url":   (p.get("image_url") or "")[:300],
        "in_stock":    bool(p.get("in_stock")),
        "vehicle":     p.get("vehicle", ""),
        "make":        p.get("make", ""),
    })
    return True


def extract_from_page(page, vehicle_slug="", make=""):
    added = 0
    try:
        for blob in page.evaluate("""() => {
            return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                   .map(s => { try { return JSON.parse(s.textContent); } catch { return null; } })
                   .filter(Boolean);
        }""") or []:
            items = blob if isinstance(blob, list) else [blob]
            for item in items:
                if item.get("@type") == "Product":
                    offers = item.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    img = item.get("image", "")
                    if isinstance(img, list):
                        img = img[0] if img else ""
                    if add({
                        "sku":         item.get("sku") or item.get("mpn", ""),
                        "name":        item.get("name", ""),
                        "description": item.get("description", ""),
                        "price":       offers.get("price", 0),
                        "in_stock":    offers.get("availability", "") == "https://schema.org/InStock",
                        "image_url":   img,
                        "vehicle":     vehicle_slug,
                        "make":        make,
                    }):
                        added += 1
    except Exception:
        pass
    return added


def save():
    Path(OUTPUT).write_text(json.dumps(parts, ensure_ascii=False, indent=2))
    print(f"  Saved {len(parts)} parts → {OUTPUT}", flush=True)


def wait_cf(page, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = page.title()
        if "just a moment" not in title.lower() and "רק רגע" not in title:
            return True
        time.sleep(2)
    return False


def goto(page, url, retries=3):
    for attempt in range(retries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3000)
            if wait_cf(page):
                return True
        except PWTimeout:
            print(f"  Timeout on {url} attempt {attempt+1}", flush=True)
    return False


def get_links(page, pattern):
    try:
        return list(set(page.evaluate(f"""() =>
            Array.from(document.querySelectorAll('a[href*="{pattern}"]'))
                 .map(a => a.href)
        """) or []))
    except Exception:
        return []


def load_il_models():
    """Query DB for GM models active in Israeli registry."""
    import asyncio

    async def _fetch():
        db_url = os.environ.get("DATABASE_URL", "")
        dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        rows = await conn.fetch("""
            SELECT manufacturer, kinuy_mishari as model,
                   SUM(mispar_rechavim_pailim) as active
            FROM vehicle_market_il
            WHERE manufacturer ILIKE ANY(ARRAY[
                '%chevrolet%', '%cadillac%', '%buick%', '%gmc%'
            ])
              AND mispar_rechavim_pailim > 0
            GROUP BY manufacturer, kinuy_mishari
            ORDER BY SUM(mispar_rechavim_pailim) DESC
        """)
        await conn.close()
        return rows

    rows = asyncio.run(_fetch())
    result = {}
    for r in rows:
        mfr = r["manufacturer"].lower()
        # Normalize model to URL slug (lowercase, spaces/special chars → hyphens)
        import re
        model_slug = re.sub(r"[^a-z0-9]+", "-", r["model"].lower()).strip("-")
        if mfr not in result:
            result[mfr] = set()
        result[mfr].add(model_slug)
    return result


def main():
    global IL_MODELS

    print("Loading Israeli vehicle registry models...", flush=True)
    IL_MODELS = load_il_models()
    for make, models in IL_MODELS.items():
        print(f"  {make}: {len(models)} models in IL registry", flush=True)

    xvfb = subprocess.Popen(
        ["Xvfb", ":99", "-screen", "0", "1280x900x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.environ["DISPLAY"] = ":99"
    time.sleep(1)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            page = ctx.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            total_makes = 0
            for make in GM_MAKES:
                il_slugs = IL_MODELS.get(make, set())
                if not il_slugs:
                    print(f"\nSkipping {make} — no IL models", flush=True)
                    continue

                print(f"\n{'='*50}", flush=True)
                print(f"Make: {make} ({len(il_slugs)} IL models)", flush=True)

                # Get all model links for this make
                if not goto(page, f"{BASE}/v-{make}"):
                    print(f"  ERROR: could not load /v-{make}", flush=True)
                    continue

                model_links = get_links(page, f"/v-{make}-")
                # Filter to only IL-relevant models
                filtered = []
                for url in model_links:
                    slug = url.split(f"/v-{make}-")[-1].rstrip("/").split("?")[0]
                    # Check if this slug or any IL slug is a prefix/match
                    if any(slug == s or slug.startswith(s) or s.startswith(slug.split("-")[0])
                           for s in il_slugs):
                        filtered.append(url)

                if not filtered:
                    # Fallback: take all model links if filtering too aggressive
                    filtered = model_links[:20]

                if MAX_MODELS:
                    filtered = filtered[:MAX_MODELS]

                print(f"  Model pages (IL-filtered): {len(filtered)}", flush=True)
                total_makes += 1

                for mi, model_url in enumerate(sorted(set(filtered))):
                    model_slug = model_url.split(f"/v-{make}-")[-1].rstrip("/").split("?")[0]
                    print(f"\n  [{mi+1}/{len(filtered)}] {make}/{model_slug}", flush=True)

                    if not goto(page, model_url):
                        print(f"    SKIP: could not load", flush=True)
                        continue

                    # Get year-specific links
                    year_links = [u for u in get_links(page, "/v-20")
                                  if f"{make}-{model_slug}" in u]
                    year_links = sorted(set(year_links), reverse=True)  # newest first
                    print(f"    Years: {len(year_links)}", flush=True)

                    targets = year_links if year_links else [model_url]
                    for year_url in targets:
                        if not goto(page, year_url):
                            continue

                        # Get trim links
                        trim_links = get_links(page, f"{make}-{model_slug}--")
                        trim_links = sorted(set(trim_links))
                        print(f"      {year_url.split('/')[-1]}: {len(trim_links)} trims", flush=True)

                        targets2 = trim_links if trim_links else [year_url]
                        for trim_url in targets2:
                            vehicle_slug = trim_url.split("/v-")[-1].rstrip("/").split("?")[0]
                            if not goto(page, trim_url):
                                continue

                            pg = 1
                            while pg <= 30:
                                n = extract_from_page(page, vehicle_slug, make)
                                print(f"        p{pg}: +{n} | total={len(parts)}", flush=True)
                                nxt = page.query_selector('a[rel="next"],.pagination-item--next a,.next a')
                                if not nxt:
                                    break
                                nxt_url = nxt.get_attribute("href")
                                if not nxt_url or nxt_url == page.url:
                                    break
                                goto(page, nxt_url)
                                pg += 1

                    save()

            browser.close()
            print(f"\nDone. {len(parts)} unique parts from {total_makes} makes.", flush=True)

    finally:
        xvfb.terminate()


if __name__ == "__main__":
    main()
