#!/usr/bin/env python3
"""
Generic OEM Parts Online scraper — works for any brand subdomain on oempartsonline.com.
Generalised from audi_playwright_scraper.py.

URL structure:
  Model list:  /{brand}.oempartsonline.com/v-{brand}
  Model years: /{brand}.oempartsonline.com/v-{brand}-{slug}
  Trims:       /{brand}.oempartsonline.com/v-{year}-{brand}-{slug}
  Parts pages: /{brand}.oempartsonline.com/v-{year}-{brand}-{slug}--{trim}--{engine}

Usage:
  python3 oem_parts_online_scraper.py --brand toyota
  python3 oem_parts_online_scraper.py --brand honda --output /tmp/honda_oem.json
  MAX_MODELS=5 python3 oem_parts_online_scraper.py --brand nissan
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

MAX_MODELS = int(os.environ.get("MAX_MODELS", "0"))  # 0 = all models

seen: set[str] = set()
parts: list[dict] = []


def add(p: dict) -> bool:
    key = (p.get("oem_number") or p.get("sku") or p.get("name", "")).strip()
    if not key or key in seen:
        return False
    seen.add(key)
    parts.append({
        "sku":         p.get("sku", "").strip(),
        "name":        p.get("name", "").strip(),
        "description": (p.get("description") or "").strip()[:500],
        "price":       float(p.get("price") or 0),
        "oem_number":  key,
        "category":    (p.get("category") or "").strip(),
        "image_url":   (p.get("image_url") or "").strip()[:300],
        "in_stock":    bool(p.get("in_stock")),
        "vehicle":     p.get("vehicle", ""),
    })
    return True


def extract_from_page(page, vehicle_slug: str = "") -> int:
    added = 0
    # JSON-LD structured data
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
                    }):
                        added += 1
    except Exception:
        pass

    # DOM product cards fallback
    try:
        cards = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('.card,.product-card,.listItem,[class*="product"]'))
                   .map(el => ({
                       sku:   (el.querySelector('[data-sku],[data-part-number]') || {}).dataset?.sku || '',
                       name:  (el.querySelector('h3,h4,.card-title,.product-title') || {}).textContent?.trim() || '',
                       price: parseFloat((el.querySelector('[data-price],.price') || {}).textContent?.replace(/[^0-9.]/g,'') || '0'),
                   }))
                   .filter(p => p.name);
        }""") or []
        for p in cards:
            p["vehicle"] = vehicle_slug
            if add(p):
                added += 1
    except Exception:
        pass

    return added


def save(output_path: str) -> None:
    Path(output_path).write_text(json.dumps(parts, ensure_ascii=False, indent=2))
    print(f"  Saved {len(parts)} parts → {output_path}", flush=True)


def wait_for_cf(page, timeout: int = 25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = page.title().lower()
        if "just a moment" not in title and "cloudflare" not in title:
            return True
        time.sleep(1.5)
    print(f"  CF still blocking after {timeout}s", flush=True)
    return False


def goto(page, url: str, retries: int = 2) -> bool:
    for _ in range(retries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            if wait_for_cf(page):
                return True
        except PWTimeout:
            pass
    return False


def get_links(page, pattern: str) -> list[str]:
    try:
        return list(set(page.evaluate(f"""() =>
            Array.from(document.querySelectorAll('a[href*="{pattern}"]'))
                 .map(a => a.href)
        """) or []))
    except Exception:
        return []


def scrape_brand(brand: str, output_path: str) -> None:
    brand = brand.lower().strip()
    base = f"https://{brand}.oempartsonline.com"
    prefix = f"/v-{brand}"

    print(f"\n=== OEM Parts Online scraper — brand: {brand} ===", flush=True)
    print(f"    Base: {base}", flush=True)

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
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            page = ctx.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            # ── Step 1: model list ────────────────────────────────────────────
            print(f"Getting model list from {base}{prefix} ...", flush=True)
            if not goto(page, f"{base}{prefix}"):
                print("ERROR: Cannot load model list page", flush=True)
                browser.close()
                return

            model_links = get_links(page, f"{prefix}-")
            model_links = sorted(set(model_links))
            if MAX_MODELS:
                model_links = model_links[:MAX_MODELS]
            print(f"  Found {len(model_links)} model pages", flush=True)

            # ── Step 2: years per model ───────────────────────────────────────
            for mi, model_url in enumerate(model_links):
                model_slug = model_url.split(f"{prefix}-")[-1].rstrip("/")
                print(f"\n[{mi + 1}/{len(model_links)}] Model: {model_slug}", flush=True)

                if not goto(page, model_url):
                    print(f"  SKIP: could not load {model_url}", flush=True)
                    continue

                year_links = get_links(page, f"/v-20")
                year_links = [u for u in year_links if f"{brand}-{model_slug}" in u]
                year_links = sorted(set(year_links), reverse=True)
                print(f"  Years found: {len(year_links)}", flush=True)

                # ── Step 3: trims + parts ─────────────────────────────────────
                for year_url in year_links:
                    if not goto(page, year_url):
                        continue
                    trim_links = get_links(page, f"{model_slug}--")
                    trim_links = sorted(set(trim_links))
                    print(f"    {year_url.split('/')[-1]}: {len(trim_links)} trims", flush=True)

                    targets = trim_links if trim_links else [year_url]
                    for trim_url in targets:
                        vehicle_slug = trim_url.split("/v-")[-1].rstrip("/")
                        if not goto(page, trim_url):
                            continue

                        pg = 1
                        while pg <= 20:
                            n = extract_from_page(page, vehicle_slug)
                            print(f"      p{pg}: +{n} | total={len(parts)}", flush=True)
                            nxt = page.query_selector('a[rel="next"],.pagination-item--next a,.next a')
                            if not nxt:
                                break
                            nxt_url = nxt.get_attribute("href")
                            if not nxt_url or nxt_url == page.url:
                                break
                            goto(page, nxt_url)
                            pg += 1

                    save(output_path)

            browser.close()
    finally:
        xvfb.terminate()

    save(output_path)
    print(f"\nDONE. {len(parts)} {brand} OEM parts saved to {output_path}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape OEM parts from oempartsonline.com")
    ap.add_argument("--brand", required=True,
                    help="Brand slug, e.g. toyota, honda, nissan, ford, bmw, ...")
    ap.add_argument("--output", default="",
                    help="Output JSON path (default: /tmp/{brand}_oem_parts.json)")
    args = ap.parse_args()

    output = args.output or f"/tmp/{args.brand.lower()}_oem_parts.json"
    scrape_brand(args.brand, output)


if __name__ == "__main__":
    main()
