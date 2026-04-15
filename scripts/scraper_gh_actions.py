#!/usr/bin/env python3
"""GitHub Actions scraper runner for aftermarket enrichment.

Reads parts needing OEM lookup and scrapes multiple sources using Playwright.
Writes results directly to catalog DB via CATALOG_DB_URL.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import asyncpg
from bs4 import BeautifulSoup
from playwright.async_api import Browser, async_playwright


PART_LIMIT = 200
PROGRESS_EVERY = 25
SOURCE_DELAY_SECONDS = 1.0
PAGE_TIMEOUT_MS = 20_000
ZERO_RESULTS_SKIP_THRESHOLD = 5
ILS_PER_USD = 3.70
USD_PER_EUR = 1.08

BLOCK_MARKERS = (
    "captcha",
    "just a moment",
    "verify you are human",
    "access denied",
    "enable javascript and cookies",
)

PART_HINT_MARKERS = (
    "part number",
    "price",
    "sku",
    "article",
    "catalog",
    "oem",
    "cross",
)

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
)

OEM_TOKEN_RE = re.compile(r"\b[A-Z0-9][A-Z0-9\-]{4,17}\b")
PART_NUMBER_RE = re.compile(
    r"\b(?:SKU|Part\s*No|Part\s*Number|Article|Item\s*No)[:\s#-]*([A-Z0-9\-]{4,25})\b",
    re.IGNORECASE,
)
EUR_PRICE_RE = re.compile(r"(?:€|EUR\s?)([0-9]{1,7}(?:[.,][0-9]{1,2})?)", re.IGNORECASE)
EUR_PRICE_RE_REV = re.compile(r"([0-9]{1,7}(?:[.,][0-9]{1,2})?)\s?(?:€|EUR)", re.IGNORECASE)
ILS_PRICE_RE = re.compile(r"(?:₪|NIS\s?|ILS\s?)([0-9]{1,7}(?:[.,][0-9]{1,2})?)", re.IGNORECASE)
ILS_PRICE_RE_REV = re.compile(r"([0-9]{1,7}(?:[.,][0-9]{1,2})?)\s?(?:₪|NIS|ILS)", re.IGNORECASE)


@dataclass(frozen=True)
class SourceConfig:
    key: str
    supplier_name: str
    website: str
    search_url: str
    currency: str  # EUR / ILS / MIXED
    brand_hint: str | None = None


SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(
        key="motorstore.co.il",
        supplier_name="Motorstore IL",
        website="https://www.motorstore.co.il",
        search_url="https://www.motorstore.co.il/search?q={query}",
        currency="ILS",
    ),
    SourceConfig(
        key="meyle.com",
        supplier_name="Meyle",
        website="https://www.meyle.com",
        search_url="https://www.meyle.com/en/search/?q={query}",
        currency="MIXED",
        brand_hint="Meyle",
    ),
    SourceConfig(
        key="bilstein.com",
        supplier_name="Bilstein",
        website="https://bilstein.com",
        search_url="https://bilstein.com/en/search/?q={query}",
        currency="MIXED",
        brand_hint="Bilstein",
    ),
    SourceConfig(
        key="mann-filter.com",
        supplier_name="Mann Filter",
        website="https://www.mann-filter.com",
        search_url="https://www.mann-filter.com/en/search.html?q={query}",
        currency="MIXED",
        brand_hint="Mann Filter",
    ),
    SourceConfig(
        key="gates.com",
        supplier_name="Gates",
        website="https://www.gates.com",
        search_url="https://www.gates.com/us/en/search.html?q={query}",
        currency="MIXED",
        brand_hint="Gates",
    ),
    SourceConfig(
        key="brembo.com",
        supplier_name="Brembo",
        website="https://www.brembo.com",
        search_url="https://www.brembo.com/en/search?q={query}",
        currency="MIXED",
        brand_hint="Brembo",
    ),
)


def _parse_price(text: str, prefer: str) -> tuple[float | None, float | None]:
    def first_float(*patterns: re.Pattern[str]) -> float | None:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                raw = match.group(1).replace(",", "")
                try:
                    return float(raw)
                except ValueError:
                    continue
        return None

    eur = first_float(EUR_PRICE_RE, EUR_PRICE_RE_REV)
    ils = first_float(ILS_PRICE_RE, ILS_PRICE_RE_REV)

    if prefer == "EUR" and eur is not None and ils is None:
        ils = round(eur * USD_PER_EUR * ILS_PER_USD, 2)
    if prefer == "ILS" and ils is not None and eur is None:
        eur = round((ils / ILS_PER_USD) / USD_PER_EUR, 2)

    return eur, ils


def _extract_oem_numbers(text: str, seed_sku: str) -> list[str]:
    skip_tokens = {
        "HTTPS", "HTTP", "CLASS", "STYLE", "SCRIPT", "SEARCH", "CATALOG",
        "COOKIE", "ENABLE", "TITLE", "PRICE", "PRODUCT", "CONTENT", "ARTICLE",
    }
    found: list[str] = []
    seen: set[str] = set()
    for token in OEM_TOKEN_RE.findall(text.upper()):
        normalized = token.strip("- ")
        if len(normalized) < 5:
            continue
        if normalized in skip_tokens:
            continue
        if normalized == seed_sku.upper():
            continue
        if not any(ch.isalpha() for ch in normalized) or not any(ch.isdigit() for ch in normalized):
            continue
        if normalized in seen:
            continue
        # Skip UUIDs (8-4-4-4-12 hex pattern)
        if re.match(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}', normalized):
            continue
        # Skip internal website tokens
        if normalized in {"IABV2SETTINGS", "YEAR", "HTTPS", "HTTP"}:
            continue
        # Require at least one letter AND one digit
        if not (any(c.isalpha() for c in normalized) and any(c.isdigit() for c in normalized)):
            continue
        seen.add(normalized)
        found.append(normalized)
        if len(found) >= 12:
            break
    return found


def _extract_part_number(text: str, seed_sku: str) -> str | None:
    match = PART_NUMBER_RE.search(text)
    if match:
        return match.group(1).upper()

    tokens = OEM_TOKEN_RE.findall(text.upper())
    for token in tokens:
        if token == seed_sku.upper():
            continue
        if any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token):
            return token
    return seed_sku.upper() if seed_sku else None


def _extract_part_name(soup: BeautifulSoup, fallback: str) -> str:
    for selector in ("h1", "h2", "meta[property='og:title']", "title"):
        node = soup.select_one(selector)
        if not node:
            continue
        if selector.startswith("meta"):
            value = (node.get("content") or "").strip()
        else:
            value = node.get_text(" ", strip=True)
        if value:
            return value[:255]
    return fallback[:255] if fallback else "Unknown part"


def _extract_brand(text: str, source: SourceConfig) -> str | None:
    if source.brand_hint:
        return source.brand_hint
    known = ("Meyle", "Bilstein", "Mann", "Gates", "Brembo")
    lower_text = text.lower()
    for brand in known:
        if brand.lower() in lower_text:
            return brand
    return None


async def _fetch_html(browser: Browser, url: str, user_agent: str) -> str:
    context = await browser.new_context(user_agent=user_agent)
    page = await context.new_page()
    try:
        await page.goto(url, timeout=PAGE_TIMEOUT_MS)
        try:
            await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
        except Exception:
            pass
        html = await page.content()
        return html
    finally:
        await context.close()


async def scrape_source(
    browser: Browser,
    source: SourceConfig,
    query: str,
    sku: str,
) -> dict[str, Any]:
    url = source.search_url.format(query=quote_plus(query))
    user_agent = random.choice(USER_AGENTS)

    try:
        html = await _fetch_html(browser, url, user_agent)
    except Exception as exc:
        return {"error": str(exc)[:250], "url": url, "has_result": False}

    lower_html = html.lower()
    blocked = any(marker in lower_html for marker in BLOCK_MARKERS)

    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    eur, ils = _parse_price(text, source.currency)
    if source.key == "motorstore.co.il":
        ils_match = re.search(r'(\d{2,6}(?:\.\d{1,2})?)\s*₪', text)
        if ils_match:
            ils = float(ils_match.group(1))
    oems = _extract_oem_numbers(text, seed_sku=sku)
    part_number = _extract_part_number(text, seed_sku=sku)
    part_name = _extract_part_name(soup, fallback=query)
    brand = _extract_brand(text, source)

    has_parts = any(marker in lower_html for marker in PART_HINT_MARKERS)
    has_result = bool(part_number or oems or eur is not None or ils is not None or has_parts)

    return {
        "url": url,
        "blocked": blocked,
        "has_result": has_result,
        "oem_numbers": oems,
        "part_number": part_number,
        "part_name": part_name,
        "price_eur": eur,
        "price_ils": ils,
        "brand": brand,
        "title": (soup.title.string.strip() if soup.title and soup.title.string else None),
    }


async def get_target_parts(conn: asyncpg.Connection, limit: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, sku, name, manufacturer, oem_number
        FROM parts_catalog
                WHERE (needs_oem_lookup = TRUE OR oem_number IS NULL)
                    AND sku ~ '^[A-Za-z0-9\\-\\.]+$'
                    AND length(sku) >= 4
                    AND length(sku) <= 30
        ORDER BY updated_at NULLS FIRST
        LIMIT $1
        """,
        limit,
    )


async def ensure_supplier(conn: asyncpg.Connection, source: SourceConfig) -> str:
    row = await conn.fetchrow("SELECT id::text AS id FROM suppliers WHERE name = $1::text", source.supplier_name)
    if row:
        return row["id"]

    inserted = await conn.fetchrow(
        """
        INSERT INTO suppliers (
            id, name, website, country, is_active, priority, created_at, updated_at
        ) VALUES (
            gen_random_uuid(), $1::text, $2::text, 'Unknown', TRUE, 50, NOW(), NOW()
        )
        ON CONFLICT (name)
        DO UPDATE SET website = EXCLUDED.website, updated_at = NOW()
        RETURNING id::text AS id
        """,
        source.supplier_name,
        source.website,
    )
    return inserted["id"]


async def resolve_aftermarket_brand(conn: asyncpg.Connection, brand_name: str | None) -> str | None:
    if not brand_name:
        return None
    row = await conn.fetchrow(
        """
        SELECT id::text AS id
        FROM aftermarket_brands
        WHERE LOWER(name) = LOWER($1::text)
        LIMIT 1
        """,
        brand_name,
    )
    return row["id"] if row else None


async def insert_aftermarket_cross_refs(
    conn: asyncpg.Connection,
    part_id: str,
    source_manufacturer: str,
    ref_numbers: list[str],
) -> int:
    inserted_count = 0
    for ref in ref_numbers:
        result = await conn.fetchval(
            """
            WITH ins AS (
                INSERT INTO part_cross_reference (
                    id, part_id, ref_number, manufacturer, ref_type, is_superseded, created_at
                )
                SELECT gen_random_uuid(), $1::uuid, $2::text, $3::text, $4::text, FALSE, NOW()
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM part_cross_reference
                    WHERE part_id = $1::uuid
                      AND ref_number = $2::text
                      AND manufacturer = $3::text
                      AND ref_type = $4::text
                )
                RETURNING 1
            )
            SELECT COALESCE((SELECT 1 FROM ins), 0)
            """,
            part_id,
            ref,
            source_manufacturer,
            "aftermarket",
        )
        inserted_count += int(result or 0)
    return inserted_count


async def upsert_supplier_price(
    conn: asyncpg.Connection,
    part_id: str,
    supplier_id: str,
    supplier_sku: str,
    price_eur: float | None,
    price_ils: float | None,
) -> int:
    if price_eur is None and price_ils is None:
        return 0

    usd = price_eur * USD_PER_EUR if price_eur is not None else None
    ils = price_ils

    if usd is None and ils is not None:
        usd = ils / ILS_PER_USD
    if ils is None and usd is not None:
        ils = usd * ILS_PER_USD

    if usd is None:
        return 0

    await conn.execute(
        """
        INSERT INTO supplier_parts (
            id, supplier_id, part_id, supplier_sku,
            price_usd, price_ils,
            availability, is_available,
            last_checked_at, created_at, updated_at
        ) VALUES (
            gen_random_uuid(), $1::uuid, $2::uuid, $3::text,
            ROUND($4::numeric, 2), ROUND($5::numeric, 2),
            'In Stock', TRUE,
            NOW(), NOW(), NOW()
        )
        ON CONFLICT (supplier_id, supplier_sku)
        DO UPDATE SET
            part_id = EXCLUDED.part_id,
            price_usd = EXCLUDED.price_usd,
            price_ils = EXCLUDED.price_ils,
            availability = 'In Stock',
            is_available = TRUE,
            last_checked_at = NOW(),
            updated_at = NOW()
        """,
        supplier_id,
        part_id,
        supplier_sku,
        float(usd),
        float(ils),
    )
    return 1


async def mark_part_success(
    conn: asyncpg.Connection,
    part_id: str,
    first_oem: str | None,
    aftermarket_brand_id: str | None,
) -> None:
    await conn.execute(
        """
        UPDATE parts_catalog
        SET
            needs_oem_lookup = FALSE,
            oem_number = COALESCE(oem_number, $2::text),
            aftermarket_brand_id = COALESCE($3::uuid, aftermarket_brand_id),
            updated_at = NOW()
        WHERE id = $1::uuid
        """,
        part_id,
        first_oem,
        aftermarket_brand_id,
    )


async def main() -> None:
    db_url = os.getenv("CATALOG_DB_URL", "").strip()
    if not db_url:
        raise RuntimeError("CATALOG_DB_URL is required")

    summary = {
        "parts_processed": 0,
        "oem_found": 0,
        "prices_updated": 0,
        "errors": 0,
    }

    source_zero_streak = {source.key: 0 for source in SOURCES}
    source_skipped = {source.key: False for source in SOURCES}

    conn = await asyncpg.connect(db_url)
    try:
        target_parts = await get_target_parts(conn, PART_LIMIT)
        print(f"Loaded target parts: {len(target_parts)}")

        supplier_ids: dict[str, str] = {}
        for source in SOURCES:
            supplier_ids[source.key] = await ensure_supplier(conn, source)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            try:
                for idx, part in enumerate(target_parts, start=1):
                    part_id = str(part["id"])
                    sku = (part["sku"] or "").strip()
                    name = (part["name"] or "").strip()
                    query = sku or name

                    if not query:
                        summary["errors"] += 1
                        continue

                    summary["parts_processed"] += 1
                    part_succeeded = False
                    first_oem: str | None = None
                    resolved_brand_id: str | None = None

                    for source in SOURCES:
                        if source_skipped[source.key]:
                            continue

                        try:
                            result = await scrape_source(browser, source, query, sku=sku)
                        except Exception as source_exc:
                            summary["errors"] += 1
                            print(f"{source.key} part={sku}: ERROR {str(source_exc)[:160]}")
                            await asyncio.sleep(SOURCE_DELAY_SECONDS)
                            continue

                        if result.get("error"):
                            summary["errors"] += 1
                            print(f"{source.key} part={sku}: ERROR {result['error']}")
                            await asyncio.sleep(SOURCE_DELAY_SECONDS)
                            continue

                        if not result.get("has_result", False):
                            source_zero_streak[source.key] += 1
                            if source_zero_streak[source.key] >= ZERO_RESULTS_SKIP_THRESHOLD:
                                source_skipped[source.key] = True
                                print(f"Skipping source for this run: {source.key} (5 zero-result streak)")
                            await asyncio.sleep(SOURCE_DELAY_SECONDS)
                            continue

                        source_zero_streak[source.key] = 0

                        oem_numbers = result.get("oem_numbers") or []
                        if oem_numbers:
                            source_mfr = result.get("brand") or source.brand_hint or source.supplier_name
                            inserted = await insert_aftermarket_cross_refs(conn, part_id, source_mfr, oem_numbers)
                            summary["oem_found"] += inserted
                            if first_oem is None:
                                first_oem = oem_numbers[0]

                        brand_name = result.get("brand")
                        if brand_name and resolved_brand_id is None:
                            resolved_brand_id = await resolve_aftermarket_brand(conn, brand_name)

                        prices_updated = await upsert_supplier_price(
                            conn,
                            part_id=part_id,
                            supplier_id=supplier_ids[source.key],
                            supplier_sku=(result.get("part_number") or sku or query),
                            price_eur=result.get("price_eur"),
                            price_ils=result.get("price_ils"),
                        )
                        summary["prices_updated"] += prices_updated

                        if oem_numbers or prices_updated:
                            part_succeeded = True

                        print(
                            f"{source.key} part={sku}: blocked={result.get('blocked')} "
                            f"part_number={result.get('part_number')} "
                            f"part_name={result.get('part_name')} "
                            f"oem={len(oem_numbers)} price_eur={result.get('price_eur')} "
                            f"price_ils={result.get('price_ils')}"
                        )

                        await asyncio.sleep(SOURCE_DELAY_SECONDS)

                    if part_succeeded:
                        await mark_part_success(conn, part_id, first_oem, resolved_brand_id)

                    if idx % PROGRESS_EVERY == 0:
                        print(
                            f"Progress: processed={summary['parts_processed']} "
                            f"oem_found={summary['oem_found']} "
                            f"prices_updated={summary['prices_updated']} "
                            f"errors={summary['errors']}"
                        )
            finally:
                await browser.close()
    finally:
        await conn.close()

    print(
        "SUMMARY "
        f"parts_processed={summary['parts_processed']} "
        f"oem_found={summary['oem_found']} "
        f"prices_updated={summary['prices_updated']} "
        f"errors={summary['errors']}"
    )


if __name__ == "__main__":
    asyncio.run(main())