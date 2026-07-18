#!/usr/bin/env python3
"""
Febest.de OEM cross-reference and vehicle fitment scraper.

Scrapes all 192 catalog pages from febest.de/en/catalog, extracts:
- Febest code, part name, OEM cross-reference number
- Vehicle fitment (make/model/year) from detail pages

Stores in:
- part_cross_reference: links our part_id to OEM ref_numbers
- part_vehicle_fitment: vehicle compatibility rows

Strategy:
1. Scrape 192 listing pages → collect (febest_code, name, oem, slug)
2. Batch-lookup OEMs against our parts_catalog
3. For matching parts: fetch detail page for full OEM list + fitment data
4. Upsert cross-references and fitment rows
"""

import asyncio
import httpx
import asyncpg
import os
import re
import time
import json
import logging
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
BASE_URL = "https://febest.de"
TOTAL_PAGES = 192
CONCURRENCY = 4
DELAY_BETWEEN_REQUESTS = 0.5  # seconds
PROGRESS_FILE = "/tmp/febest_progress.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AutoSpareFinder-Enrichment/1.0)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"pages_done": [], "parts_indexed": 0, "cross_refs_added": 0, "fitment_added": 0}


def save_progress(state):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(state, f)


def normalize_oem(oem: str) -> str:
    """Remove spaces, dashes, dots, uppercase — for DB lookup."""
    return re.sub(r'[\s\-\.]', '', oem).upper()


def parse_listing_page(html: str) -> list[dict]:
    """Parse a catalog listing page. Returns list of {code, name, oem, slug}."""
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    # Find the catalog table rows
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        code_td, name_td, oem_td = tds[0], tds[1], tds[2]
        code = code_td.get_text(strip=True)
        name = name_td.get_text(strip=True)
        oem = oem_td.get_text(strip=True)
        if not code or not oem:
            continue
        # Get slug from link
        link = code_td.find("a") or name_td.find("a")
        slug = ""
        if link and link.get("href"):
            slug = link["href"].strip("/").replace("/en/details/", "")
        parts.append({"code": code, "name": name, "oem": oem, "slug": slug})
    return parts


def parse_detail_page(html: str) -> dict:
    """Parse a part detail page. Returns {oem_list: [...], fitment: [...]}."""
    soup = BeautifulSoup(html, "html.parser")
    oem_list = []
    fitment = []

    # Extract all OEM numbers (often in a table or list)
    # Look for any table that might contain OEM numbers
    for td in soup.find_all("td"):
        text = td.get_text(strip=True)
        # OEM pattern: alphanumeric with dashes, 5-20 chars
        if re.match(r'^[A-Z0-9\-\.]{5,25}$', text, re.I):
            # Avoid duplicates
            if text not in oem_list:
                oem_list.append(text)

    # Extract vehicle fitment from text blocks
    # Pattern: "Brand Model YEAR_FROM-YEAR_TO [region]"
    full_text = soup.get_text(" ", strip=True)

    # Look for application/fitment section
    # Typical pattern: "Make Model SubModel YYYY-YYYY [XX]"
    fitment_pattern = re.compile(
        r'([A-Z][a-zA-Z\-]+)\s+([A-Z][a-zA-Z0-9\-\s]+?)\s+(\d{4})-(\d{4})',
    )
    for m in fitment_pattern.finditer(full_text):
        make = m.group(1).strip()
        model = m.group(2).strip()
        year_from = int(m.group(3))
        year_to = int(m.group(4))
        if 1950 <= year_from <= 2030 and 1950 <= year_to <= 2030:
            fitment.append({
                "manufacturer": make,
                "model": model,
                "year_from": year_from,
                "year_to": year_to,
            })

    return {"oem_list": oem_list, "fitment": fitment}


async def fetch_page(client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore) -> str | None:
    async with semaphore:
        try:
            r = await client.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
            if r.status_code == 200:
                return r.text
            log.warning(f"HTTP {r.status_code} for {url}")
            return None
        except Exception as e:
            log.error(f"Fetch error {url}: {e}")
            return None


async def scrape_listing_pages(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, state: dict) -> list[dict]:
    """Scrape all listing pages, return all parts."""
    all_parts = []
    pages_done = set(state.get("pages_done", []))

    tasks = []
    page_nums = [p for p in range(1, TOTAL_PAGES + 1) if p not in pages_done]
    log.info(f"Pages to scrape: {len(page_nums)} (already done: {len(pages_done)})")

    async def scrape_one(page_num):
        url = f"{BASE_URL}/en/catalog/page/{page_num}"
        html = await fetch_page(client, url, semaphore)
        if not html:
            return []
        parts = parse_listing_page(html)
        log.info(f"Page {page_num}: {len(parts)} parts")
        return parts, page_num

    results = await asyncio.gather(*[scrape_one(p) for p in page_nums])
    for result in results:
        if result:
            parts, page_num = result
            all_parts.extend(parts)
            pages_done.add(page_num)

    state["pages_done"] = list(pages_done)
    save_progress(state)
    return all_parts


async def match_oems_in_db(conn: asyncpg.Connection, parts: list[dict]) -> dict:
    """
    Look up parts by OEM number in parts_catalog.
    Returns dict: normalized_oem -> list of {id, manufacturer, oem_number, name}
    Processes in batches of 500 to avoid large ANY() arrays timing out.
    """
    if not parts:
        return {}

    oem_list = list(set(normalize_oem(p["oem"]) for p in parts if p.get("oem")))
    log.info(f"Looking up {len(oem_list)} unique OEM numbers in DB (batched)...")

    oem_map = {}
    BATCH = 500
    total_rows = 0

    for i in range(0, len(oem_list), BATCH):
        batch = oem_list[i:i + BATCH]
        rows = await conn.fetch("""
            SELECT id, manufacturer, oem_number, name
            FROM parts_catalog
            WHERE UPPER(REPLACE(REPLACE(REPLACE(oem_number, ' ', ''), '-', ''), '.', '')) = ANY($1)
              AND is_active = TRUE
        """, batch)

        for r in rows:
            key = normalize_oem(r["oem_number"])
            if key not in oem_map:
                oem_map[key] = []
            oem_map[key].append(dict(r))
        total_rows += len(rows)

        if (i // BATCH) % 10 == 0:
            log.info(f"  Batch {i//BATCH+1}/{(len(oem_list)-1)//BATCH+1}: {total_rows} matches so far")

    log.info(f"DB matches: {len(oem_map)} unique OEMs ({total_rows} part rows)")
    return oem_map


async def upsert_cross_reference(conn: asyncpg.Connection, part_id: str, ref_number: str, source: str = "febest"):
    """Insert cross-reference if not already there."""
    await conn.execute("""
        INSERT INTO part_cross_reference (id, part_id, ref_number, manufacturer, ref_type, created_at)
        VALUES (gen_random_uuid(), $1, $2, $3, 'aftermarket_cross', NOW())
        ON CONFLICT DO NOTHING
    """, part_id, ref_number, source)


async def upsert_fitment(conn: asyncpg.Connection, part_id: str, fitment_row: dict):
    """Insert fitment row if not already there."""
    await conn.execute("""
        INSERT INTO part_vehicle_fitment
            (id, part_id, manufacturer, model, year_from, year_to, notes, created_at, updated_at)
        VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, NOW(), NOW())
        ON CONFLICT DO NOTHING
    """,
        part_id,
        fitment_row["manufacturer"],
        fitment_row["model"],
        fitment_row["year_from"],
        fitment_row["year_to"],
        "febest.de",
    )


async def process_matched_parts(
    client: httpx.AsyncClient,
    conn: asyncpg.Connection,
    semaphore: asyncio.Semaphore,
    parts: list[dict],
    oem_map: dict,
    state: dict,
) -> tuple[int, int]:
    """For parts matching our DB, fetch detail pages and upsert data."""
    cross_refs_added = 0
    fitment_added = 0
    detail_fetched = 0

    # Group matched parts by slug (deduplicate)
    matched = [(p, oem_map[normalize_oem(p["oem"])]) for p in parts if normalize_oem(p.get("oem","")) in oem_map]
    log.info(f"Processing {len(matched)} matched parts (fetching detail pages)...")

    async def process_one(p, db_parts):
        nonlocal cross_refs_added, fitment_added, detail_fetched
        slug = p.get("slug", "")
        if not slug:
            return

        url = f"{BASE_URL}/en/details/{slug}"
        html = await fetch_page(client, url, semaphore)
        if not html:
            return

        detail = parse_detail_page(html)
        detail_fetched += 1

        for db_part in db_parts:
            pid = str(db_part["id"])

            # Add febest cross-reference (febest code as ref_number)
            await upsert_cross_reference(conn, pid, p["code"], "febest")
            cross_refs_added += 1

            # Add OEM cross-references from detail page
            for oem_ref in detail["oem_list"]:
                await upsert_cross_reference(conn, pid, oem_ref, "oem")
                cross_refs_added += 1

            # Add fitment rows
            for fit in detail["fitment"]:
                await upsert_fitment(conn, pid, fit)
                fitment_added += 1

        if detail_fetched % 50 == 0:
            log.info(f"Detail pages fetched: {detail_fetched}, cross_refs: {cross_refs_added}, fitment: {fitment_added}")

    await asyncio.gather(*[process_one(p, db_parts) for p, db_parts in matched])
    return cross_refs_added, fitment_added


async def main():
    log.info("=== Febest.de Scraper ===")
    state = load_progress()
    log.info(f"Progress state: {state}")

    conn = await asyncpg.connect(DB_URL)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient() as client:
        # Phase 1: Scrape all listing pages
        log.info("Phase 1: Scraping listing pages...")
        t0 = time.time()
        all_parts = await scrape_listing_pages(client, semaphore, state)
        log.info(f"Total parts indexed: {len(all_parts)} in {time.time()-t0:.0f}s")
        state["parts_indexed"] = len(all_parts)
        save_progress(state)

        # Phase 2: Match OEMs against our DB
        log.info("Phase 2: Matching OEMs in DB...")
        oem_map = await match_oems_in_db(conn, all_parts)

        # Phase 3: Fetch detail pages for matched parts
        log.info("Phase 3: Fetching detail pages for matched parts...")
        t1 = time.time()
        cross_refs, fitment = await process_matched_parts(
            client, conn, semaphore, all_parts, oem_map, state
        )
        log.info(f"Phase 3 done in {time.time()-t1:.0f}s — cross_refs: {cross_refs}, fitment: {fitment}")

        state["cross_refs_added"] = cross_refs
        state["fitment_added"] = fitment
        save_progress(state)

    await conn.close()
    log.info("=== Febest scraper complete ===")
    log.info(f"Final: {len(all_parts)} parts scraped, {cross_refs} cross-refs, {fitment} fitment rows")


if __name__ == "__main__":
    asyncio.run(main())
