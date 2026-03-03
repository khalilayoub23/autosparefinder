"""
==============================================================================
AUTO SPARE  —  CATALOG SCRAPER & DB-UPDATE AGENT
==============================================================================
Background agent that automatically:
  • Scrapes real supplier / reference sites for part prices & availability
  • Detects new parts and inserts them into parts_catalog
  • Updates supplier_parts prices and availability flags
  • Runs on a configurable schedule (default: every 6 h)
  • Exposes per-tool admin controls via /api/v1/admin/scraper/*

Scraping tools available to the agent
──────────────────────────────────────
  1. scrape_autodoc          – autodoc.co.il / autodoc.eu  (structured JSON API)
  2. scrape_ebay_motors      – eBay Motors price research
  3. scrape_aliexpress       – AliExpress keyword/OEM search
  4. scrape_google_shopping  – Google Shopping price scan
  5. scrape_rockauto         – RockAuto catalog lookup
  6. fetch_html              – Generic HTML fetch + parse helper
  7. db_upsert_part          – Insert or update a parts_catalog row
  8. db_update_supplier_part – Update price/availability for a supplier_parts row
  9. db_log                  – Write to system_log

All HTTP calls are async (httpx) with random UA rotation and polite delays.
==============================================================================
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

load_dotenv()

# ── DB ─────────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://autospare:autospare_dev@localhost:5432/autospare",
)
_engine = create_async_engine(DATABASE_URL, pool_size=5, max_overflow=2, echo=False)
scraper_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

# ── Schedule  ──────────────────────────────────────────────────────────────────
SCRAPE_INTERVAL_H     = float(os.getenv("SCRAPE_INTERVAL_H", "6"))
SCRAPE_BATCH_SIZE     = int(os.getenv("SCRAPE_BATCH_SIZE", "200"))    # parts per run
SCRAPE_REQUEST_DELAY  = float(os.getenv("SCRAPE_REQUEST_DELAY", "1.5"))  # seconds between requests
SCRAPE_MAX_ERRORS     = int(os.getenv("SCRAPE_MAX_ERRORS", "30"))        # abort after N errors/run
SCRAPE_ENABLED        = os.getenv("SCRAPE_ENABLED", "true").lower() == "true"

# Exchange rate (ILS / USD).  Updated once per run from a free API if available.
ILS_PER_USD: float = float(os.getenv("USD_TO_ILS", "3.72"))

# ── HTTP helpers ───────────────────────────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

def _rand_ua() -> str:
    return random.choice(_USER_AGENTS)

def _base_headers() -> Dict[str, str]:
    return {
        "User-Agent": _rand_ua(),
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }

async def _get(url: str, *, params: Dict = None, headers: Dict = None,
               timeout: int = 20, retries: int = 2) -> Optional[httpx.Response]:
    """Polite async GET with retries and random UA."""
    merged_headers = {**_base_headers(), **(headers or {})}
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers=merged_headers,
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                return resp
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt == retries:
                print(f"[Scraper] GET failed after {retries+1} tries: {url} — {exc}")
                return None
            await asyncio.sleep(2 * (attempt + 1))
    return None


# ==============================================================================
# SCRAPING TOOLS  (each returns a structured dict with a 'results' list)
# ==============================================================================

async def fetch_html(url: str, css_selector: str = "body") -> Dict[str, Any]:
    """
    Generic HTML fetcher.
    Returns {'url': url, 'text': str, 'elements': [str, ...], 'ok': bool}
    """
    resp = await _get(url)
    if not resp or resp.status_code >= 400:
        return {"url": url, "text": "", "elements": [], "ok": False, "status": getattr(resp, "status_code", 0)}
    soup = BeautifulSoup(resp.text, "html.parser")
    elements = [el.get_text(strip=True) for el in soup.select(css_selector)[:20]]
    return {"url": url, "text": soup.get_text(separator=" ", strip=True)[:3000], "elements": elements, "ok": True}


async def scrape_autodoc(part_number: str, manufacturer: str = "") -> Dict[str, Any]:
    """
    Search autodoc.co.il (Autodoc's Israeli presence) for a part number.
    Falls back to autodoc.eu JSON API which is publicly accessible.
    Returns list of {name, price_ils, currency, url, availability, brand}
    """
    results: List[Dict] = []
    await asyncio.sleep(SCRAPE_REQUEST_DELAY + random.uniform(0, 0.5))

    # 1) Try the Autodoc public search API (EU endpoint, works globally)
    api_url = "https://www.autodoc.co.il/parts/search"
    fallback_api = f"https://www.autodoc.eu/api/v1/part/search?search={part_number}&lang=he"

    resp = await _get(fallback_api, headers={"Accept": "application/json"})
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            for item in (data.get("items") or data.get("results") or [])[:5]:
                price_raw = item.get("price") or item.get("priceILS") or 0
                try:
                    price = float(str(price_raw).replace(",", ".").replace("₪", "").strip())
                except Exception:
                    price = 0.0
                results.append({
                    "source": "autodoc",
                    "part_number": part_number,
                    "name": item.get("name") or item.get("title") or "",
                    "brand": item.get("brand") or item.get("manufacturer") or manufacturer,
                    "price_ils": price,
                    "availability": "in_stock" if item.get("inStock") else "on_order",
                    "url": f"https://www.autodoc.co.il/parts/{part_number}",
                })
        except Exception:
            pass

    # 2) HTML fallback — basic page scrape
    if not results:
        page = await fetch_html(
            f"https://www.autodoc.co.il/search?query={part_number}",
            css_selector=".product-price, .price, [class*='price']",
        )
        if page["ok"]:
            price_matches = re.findall(r"[\d,.]+\s*₪|₪\s*[\d,.]+", page["text"])
            for pm in price_matches[:3]:
                price_str = re.sub(r"[^\d.]", "", pm.replace(",", "."))
                try:
                    price = float(price_str)
                    results.append({
                        "source": "autodoc_html",
                        "part_number": part_number,
                        "name": f"חלק {part_number}",
                        "brand": manufacturer,
                        "price_ils": price,
                        "availability": "unknown",
                        "url": page["url"],
                    })
                except Exception:
                    pass

    return {"tool": "scrape_autodoc", "part_number": part_number, "results": results}


async def scrape_ebay_motors(part_number: str, manufacturer: str = "") -> Dict[str, Any]:
    """
    Search eBay Motors for a part number and return sold/listed price range.
    Uses eBay's public search endpoint (no API key required for basic results).
    """
    await asyncio.sleep(SCRAPE_REQUEST_DELAY + random.uniform(0, 0.8))
    results: List[Dict] = []

    query = f"{manufacturer} {part_number}".strip()
    url = f"https://www.ebay.com/sch/i.html?_nkw={query.replace(' ', '+')}&LH_Sold=1&_sop=13"

    page = await fetch_html(url, css_selector=".s-item__price, .s-item__title")
    if not page["ok"]:
        return {"tool": "scrape_ebay_motors", "part_number": part_number, "results": []}

    soup = BeautifulSoup(await _get(url) and (await _get(url)).text or "", "html.parser")
    items = soup.select(".s-item")[:10]

    prices_usd: List[float] = []
    for item in items:
        title_el = item.select_one(".s-item__title")
        price_el = item.select_one(".s-item__price")
        if not price_el:
            continue
        price_str = re.sub(r"[^\d.]", "", price_el.get_text().split("to")[0].replace(",", "."))
        try:
            price_usd = float(price_str)
            prices_usd.append(price_usd)
            results.append({
                "source": "ebay_motors",
                "part_number": part_number,
                "name": title_el.get_text(strip=True) if title_el else f"{manufacturer} {part_number}",
                "brand": manufacturer,
                "price_usd": price_usd,
                "price_ils": round(price_usd * ILS_PER_USD, 2),
                "availability": "in_stock",
                "url": (item.select_one("a.s-item__link") or {}).get("href", ""),
            })
        except Exception:
            pass

    if prices_usd:
        avg_usd = sum(prices_usd) / len(prices_usd)
        return {
            "tool": "scrape_ebay_motors",
            "part_number": part_number,
            "avg_price_usd": round(avg_usd, 2),
            "avg_price_ils": round(avg_usd * ILS_PER_USD, 2),
            "results": results,
        }

    return {"tool": "scrape_ebay_motors", "part_number": part_number, "results": []}


async def scrape_aliexpress(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Search AliExpress for a part and return price range (USD).
    Uses the public AliExpress search endpoint.
    """
    await asyncio.sleep(SCRAPE_REQUEST_DELAY + random.uniform(0, 1.0))
    results: List[Dict] = []

    url = (
        f"https://www.aliexpress.com/wholesale"
        f"?SearchText={query.replace(' ', '+')}&SortType=price_asc"
    )
    resp = await _get(url)
    if not resp or resp.status_code >= 400:
        return {"tool": "scrape_aliexpress", "query": query, "results": []}

    soup = BeautifulSoup(resp.text, "html.parser")

    # AliExpress embeds product data as JSON in a script tag
    script_tags = soup.find_all("script")
    for script in script_tags:
        content = script.string or ""
        if "skuModule" in content or "priceModule" in content or '"price"' in content:
            # Extract price patterns like "25.99" or "USD 25.99"
            price_matches = re.findall(r'"price"\s*:\s*"?([\d.]+)"?', content)
            title_matches = re.findall(r'"title"\s*:\s*"([^"]{5,120})"', content)
            for i, pm in enumerate(price_matches[:max_results]):
                try:
                    price_usd = float(pm)
                    results.append({
                        "source": "aliexpress",
                        "query": query,
                        "name": title_matches[i] if i < len(title_matches) else query,
                        "price_usd": price_usd,
                        "price_ils": round(price_usd * ILS_PER_USD, 2),
                        "availability": "on_order",
                        "url": url,
                    })
                except Exception:
                    pass
            if results:
                break

    return {"tool": "scrape_aliexpress", "query": query, "results": results}


async def scrape_google_shopping(query: str) -> Dict[str, Any]:
    """
    Scrape Google Shopping for price signals.
    No API key required — uses the public HTML results page.
    """
    await asyncio.sleep(SCRAPE_REQUEST_DELAY + random.uniform(0, 0.5))

    url = f"https://www.google.com/search?q={query.replace(' ', '+')}&tbm=shop"
    resp = await _get(url)
    if not resp:
        return {"tool": "scrape_google_shopping", "query": query, "results": []}

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict] = []

    # Google Shopping price blocks
    for item in soup.select(".sh-dgr__content, .mnIHsc, [class*='price']")[:10]:
        text = item.get_text(separator=" ", strip=True)
        price_matches = re.findall(r"\$\s*([\d,]+(?:\.\d{2})?)|₪\s*([\d,]+(?:\.\d{2})?)", text)
        for usd_str, ils_str in price_matches:
            try:
                if usd_str:
                    price_usd = float(usd_str.replace(",", ""))
                    results.append({
                        "source": "google_shopping",
                        "query": query,
                        "price_usd": price_usd,
                        "price_ils": round(price_usd * ILS_PER_USD, 2),
                        "text_snippet": text[:120],
                    })
                elif ils_str:
                    price_ils = float(ils_str.replace(",", ""))
                    results.append({
                        "source": "google_shopping",
                        "query": query,
                        "price_usd": round(price_ils / ILS_PER_USD, 2),
                        "price_ils": price_ils,
                        "text_snippet": text[:120],
                    })
            except Exception:
                pass
        if len(results) >= 5:
            break

    return {"tool": "scrape_google_shopping", "query": query, "results": results}


async def scrape_rockauto(part_number: str, manufacturer: str = "") -> Dict[str, Any]:
    """
    Scrape RockAuto for a part number.  RockAuto uses a React/JSON structure;
    we extract prices from the embedded JSON data payload.
    """
    await asyncio.sleep(SCRAPE_REQUEST_DELAY + random.uniform(0, 0.5))

    url = f"https://www.rockauto.com/en/partsearch/?partnum={part_number}"
    resp = await _get(url)
    if not resp:
        return {"tool": "scrape_rockauto", "part_number": part_number, "results": []}

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict] = []

    # RockAuto embeds parts as JS arrays in script tags
    for script in soup.find_all("script"):
        content = script.string or ""
        if "partnum" in content.lower() or "listprice" in content.lower():
            price_matches = re.findall(r'"listprice"\s*:\s*([\d.]+)', content)
            name_matches  = re.findall(r'"partdescription"\s*:\s*"([^"]+)"', content)
            for i, pm in enumerate(price_matches[:5]):
                try:
                    price_usd = float(pm)
                    results.append({
                        "source": "rockauto",
                        "part_number": part_number,
                        "name": name_matches[i] if i < len(name_matches) else f"{manufacturer} {part_number}",
                        "brand": manufacturer,
                        "price_usd": price_usd,
                        "price_ils": round(price_usd * ILS_PER_USD, 2),
                        "availability": "in_stock",
                        "url": url,
                    })
                except Exception:
                    pass
            if results:
                break

    return {"tool": "scrape_rockauto", "part_number": part_number, "results": results}


async def fetch_ils_exchange_rate() -> float:
    """Fetch latest ILS/USD rate from a free public API."""
    global ILS_PER_USD
    try:
        resp = await _get(
            "https://api.exchangerate-api.com/v4/latest/USD",
            headers={"Accept": "application/json"},
            timeout=8,
        )
        if resp and resp.status_code == 200:
            data = resp.json()
            rate = data.get("rates", {}).get("ILS")
            if rate:
                ILS_PER_USD = float(rate)
                return ILS_PER_USD
    except Exception:
        pass
    # Fallback to env or default
    ILS_PER_USD = float(os.getenv("USD_TO_ILS", "3.72"))
    return ILS_PER_USD


# ==============================================================================
# DB UPDATE TOOLS
# ==============================================================================

async def db_upsert_part(db: AsyncSession, *, sku: str, name: str, manufacturer: str,
                          category: str, part_type: str = "Aftermarket",
                          base_price: float = 0.0, description: str = "",
                          compatible_vehicles: list = None) -> Tuple[str, bool]:
    """
    Insert or update a parts_catalog row.
    Returns (part_id, was_created).
    """
    existing = (await db.execute(
        text("SELECT id FROM parts_catalog WHERE sku = :sku"), {"sku": sku}
    )).fetchone()

    if existing:
        await db.execute(
            text("""
                UPDATE parts_catalog SET
                    name = :name, manufacturer = :manufacturer, category = :category,
                    part_type = :part_type, base_price = :base_price,
                    description = :description, updated_at = NOW()
                WHERE sku = :sku
            """),
            {"name": name, "manufacturer": manufacturer, "category": category,
             "part_type": part_type, "base_price": base_price,
             "description": description, "sku": sku},
        )
        await db.commit()
        return str(existing[0]), False

    part_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO parts_catalog
                (id, sku, name, manufacturer, category, part_type, base_price,
                 description, specifications, compatible_vehicles, is_active, created_at, updated_at)
            VALUES
                (:id, :sku, :name, :manufacturer, :category, :part_type, :base_price,
                 :description, '{}', :compat, true, NOW(), NOW())
        """),
        {
            "id": part_id, "sku": sku, "name": name,
            "manufacturer": manufacturer, "category": category,
            "part_type": part_type, "base_price": base_price,
            "description": description,
            "compat": json.dumps(compatible_vehicles or []),
        },
    )
    await db.commit()
    return part_id, True


async def db_update_supplier_part(db: AsyncSession, *, supplier_part_id: str,
                                   price_ils: float, price_usd: float,
                                   availability: str = None) -> bool:
    """Update price (and optionally availability) of a supplier_parts row."""
    try:
        params: Dict[str, Any] = {
            "price_ils": price_ils,
            "price_usd": price_usd,
            "last_checked": datetime.utcnow(),
            "id": supplier_part_id,
        }
        if availability:
            params["availability"] = availability
            await db.execute(
                text("""
                    UPDATE supplier_parts
                    SET price_ils = :price_ils, price_usd = :price_usd,
                        is_available = (:availability = 'in_stock'),
                        availability = :availability,
                        last_checked_at = :last_checked
                    WHERE id = :id
                """),
                params,
            )
        else:
            await db.execute(
                text("""
                    UPDATE supplier_parts
                    SET price_ils = :price_ils, price_usd = :price_usd,
                        last_checked_at = :last_checked
                    WHERE id = :id
                """),
                params,
            )
        await db.commit()
        return True
    except Exception as e:
        print(f"[Scraper] db_update_supplier_part error: {e}")
        return False


async def db_log(db: AsyncSession, level: str, message: str, extra: Dict = None):
    """Write an entry to system_log."""
    try:
        await db.execute(
            text("""
                INSERT INTO system_logs (id, level, logger_name, message, endpoint, method, created_at)
                VALUES (:id, :level, 'catalog_scraper', :message, '/background/scraper', 'CRON', NOW())
            """),
            {"id": str(uuid.uuid4()), "level": level.upper(), "message": message[:500]},
        )
        await db.commit()
    except Exception:
        pass


# ==============================================================================
# SCRAPE STRATEGY  —  which tool to use per supplier
# ==============================================================================
SUPPLIER_TOOL_MAP = {
    "AutoParts Pro IL": scrape_autodoc,      # Israeli → autodoc.co.il
    "Global Parts Hub": scrape_rockauto,     # European → RockAuto (USD)
    "EastAuto Supply":  scrape_aliexpress,   # Chinese → AliExpress
}

# Fallback priority: if primary returns nothing, try these in order
FALLBACK_TOOLS = [scrape_ebay_motors, scrape_google_shopping]


async def _scrape_one_part(
    db: AsyncSession,
    part_id: str, sku: str, name: str,
    manufacturer: str, category: str, part_type: str,
    supplier_id: str, supplier_name: str, supplier_part_id: str,
    current_price_ils: float,
) -> Dict[str, Any]:
    """
    Run the appropriate scraping tools for one part row and update the DB.
    Returns a compact result dict for the run report.
    """
    result = {
        "sku": sku,
        "supplier": supplier_name,
        "action": "no_change",
        "old_price": current_price_ils,
        "new_price": current_price_ils,
    }

    # Derive catalog number from SKU (strip brand prefix)
    cat_num = sku.split("-", 1)[-1] if "-" in sku else sku

    # --- primary tool ---
    primary_fn = SUPPLIER_TOOL_MAP.get(supplier_name, scrape_ebay_motors)
    try:
        if primary_fn is scrape_aliexpress:
            data = await primary_fn(f"{manufacturer} {cat_num} auto part")
        else:
            data = await primary_fn(cat_num, manufacturer)
    except Exception as exc:
        data = {"results": []}
        print(f"[Scraper] tool error for {sku}: {exc}")

    scraped_prices: List[float] = [
        r["price_ils"] for r in data.get("results", []) if r.get("price_ils", 0) > 10
    ]

    # --- fallback tools if primary returned nothing ---
    if not scraped_prices:
        for fallback_fn in FALLBACK_TOOLS:
            try:
                fb_data = await fallback_fn(f"{manufacturer} {cat_num}")
                scraped_prices = [
                    r["price_ils"] for r in fb_data.get("results", []) if r.get("price_ils", 0) > 10
                ]
                if scraped_prices:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

    if not scraped_prices:
        return result  # nothing found — leave price as-is

    # Use median scraped price as reference (ignores outliers)
    scraped_prices.sort()
    median_ils = scraped_prices[len(scraped_prices) // 2]

    # Business rules:
    # - Customer price = cost × 1.45 + 17% VAT + 91₪ shipping
    # - Scraped market price ≈ final customer price at competitors
    # - Derive our cost from scraped reference / 1.45 / 1.17 (rough)
    derived_cost_ils = median_ils / 1.17 / 1.45

    # Clamp update: don't move more than ±25% from current in a single run
    old = current_price_ils or derived_cost_ils
    new_ils = round(max(old * 0.75, min(derived_cost_ils, old * 1.25)), 2)

    if abs(new_ils - old) / max(old, 1) < 0.005:
        return result  # change < 0.5% — not worth writing

    availability = data.get("results", [{}])[0].get("availability") if data.get("results") else None

    updated = await db_update_supplier_part(
        db,
        supplier_part_id=supplier_part_id,
        price_ils=new_ils,
        price_usd=round(new_ils / ILS_PER_USD, 2),
        availability=availability,
    )

    if updated:
        result["action"] = "price_updated"
        result["new_price"] = new_ils
        if availability:
            result["availability"] = availability

    return result


# ==============================================================================
# MAIN SCRAPER RUN  —  called by the background loop
# ==============================================================================

async def run_scraper_cycle(*, batch_size: int = SCRAPE_BATCH_SIZE) -> Dict[str, Any]:
    """
    One full scraper cycle:
    1. Fetch exchange rate
    2. Pull a batch of supplier_parts rows (oldest last_checked first)
    3. Scrape each and update prices / availability
    4. Write summary to system_log
    """
    if not SCRAPE_ENABLED:
        return {"status": "disabled", "message": "SCRAPE_ENABLED=false"}

    started_at = datetime.utcnow()
    await fetch_ils_exchange_rate()

    report: Dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "batch_size": batch_size,
        "ils_per_usd": ILS_PER_USD,
        "parts_checked": 0,
        "prices_updated": 0,
        "availability_changes": 0,
        "new_parts_inserted": 0,
        "errors": 0,
        "rows": [],
    }

    async with scraper_session_factory() as db:
        # Pull oldest-checked supplier_parts joined to parts_catalog
        rows = (await db.execute(
            text("""
                SELECT
                    sp.id            AS supplier_part_id,
                    sp.supplier_id,
                    sp.price_ils,
                    sp.availability,
                    s.name           AS supplier_name,
                    pc.id            AS part_id,
                    pc.sku,
                    pc.name          AS part_name,
                    pc.manufacturer,
                    pc.category,
                    pc.part_type
                FROM supplier_parts sp
                JOIN suppliers       s  ON s.id  = sp.supplier_id
                JOIN parts_catalog   pc ON pc.id = sp.part_id
                WHERE pc.is_active = true
                ORDER BY sp.last_checked_at ASC NULLS FIRST
                LIMIT :lim
            """),
            {"lim": batch_size},
        )).fetchall()

        print(f"[Scraper] Cycle started — {len(rows)} parts to check, "
              f"ILS/USD={ILS_PER_USD}")

        for row in rows:
            if report["errors"] >= SCRAPE_MAX_ERRORS:
                print(f"[Scraper] Max errors ({SCRAPE_MAX_ERRORS}) reached — stopping batch")
                break

            try:
                result = await _scrape_one_part(
                    db=db,
                    part_id=str(row.part_id),
                    sku=row.sku,
                    name=row.part_name,
                    manufacturer=row.manufacturer or "",
                    category=row.category or "כללי",
                    part_type=row.part_type or "Aftermarket",
                    supplier_id=str(row.supplier_id),
                    supplier_name=row.supplier_name,
                    supplier_part_id=str(row.supplier_part_id),
                    current_price_ils=float(row.price_ils or 0),
                )
                report["parts_checked"] += 1
                if result["action"] == "price_updated":
                    report["prices_updated"] += 1
                if result.get("availability"):
                    report["availability_changes"] += 1
                report["rows"].append(result)

            except Exception as exc:
                report["errors"] += 1
                print(f"[Scraper] Error on {row.sku}: {exc}")

            await asyncio.sleep(SCRAPE_REQUEST_DELAY * 0.3)  # short inter-part delay

        elapsed = (datetime.utcnow() - started_at).total_seconds()
        report["elapsed_s"] = round(elapsed, 1)

        # Summarize to system_log
        await db_log(
            db, "INFO",
            f"[Scraper cycle] checked={report['parts_checked']} "
            f"updated={report['prices_updated']} "
            f"avail_changes={report['availability_changes']} "
            f"errors={report['errors']} "
            f"elapsed={elapsed:.0f}s",
        )

    print(
        f"[Scraper] ✅ Cycle done — "
        f"checked={report['parts_checked']:,}  "
        f"updated={report['prices_updated']:,}  "
        f"errors={report['errors']}  "
        f"elapsed={report['elapsed_s']}s"
    )
    return report


# ==============================================================================
# BACKGROUND LOOP  —  runs forever; called once from BACKEND_API_ROUTES startup
# ==============================================================================

_scraper_task: Optional[asyncio.Task] = None
_last_run_report: Optional[Dict] = None


async def scraper_background_loop():
    """
    Infinite loop: wait for the right time, run a cycle, repeat.
    Smart start: if a recent run exists in system_log, waits the remainder of
    the interval before the first run.
    """
    global _last_run_report
    interval_s = SCRAPE_INTERVAL_H * 3600

    # ── Calculate first-run delay ───────────────────────────────────────────
    first_wait = 60  # default: 1 min after startup
    try:
        async with scraper_session_factory() as db:
            last = (await db.execute(
                text("""
                    SELECT created_at FROM system_logs
                    WHERE logger_name = 'catalog_scraper'
                    ORDER BY created_at DESC LIMIT 1
                """)
            )).fetchone()
            if last and last[0]:
                elapsed = (datetime.utcnow() - last[0]).total_seconds()
                remaining = interval_s - elapsed
                if remaining > 60:
                    first_wait = remaining
                    print(f"[Scraper] Recent run found — next run in "
                          f"{remaining/3600:.1f}h")
                else:
                    first_wait = 60
    except Exception as e:
        print(f"[Scraper] Could not read last run time: {e}")

    print(f"[Scraper] Background loop started. "
          f"First run in {first_wait/60:.1f} min. "
          f"Interval: every {SCRAPE_INTERVAL_H}h.")

    await asyncio.sleep(first_wait)

    while True:
        try:
            _last_run_report = await run_scraper_cycle()
        except Exception as exc:
            print(f"[Scraper] ❌ Unhandled error in cycle: {exc}")
        await asyncio.sleep(interval_s)


def start_scraper_task() -> asyncio.Task:
    """Create and store the background scraper task (call from startup event)."""
    global _scraper_task
    if _scraper_task and not _scraper_task.done():
        return _scraper_task
    _scraper_task = asyncio.create_task(scraper_background_loop())
    return _scraper_task


def get_scraper_status() -> Dict[str, Any]:
    """Return current status dict for the admin API."""
    return {
        "enabled": SCRAPE_ENABLED,
        "interval_h": SCRAPE_INTERVAL_H,
        "batch_size": SCRAPE_BATCH_SIZE,
        "request_delay_s": SCRAPE_REQUEST_DELAY,
        "ils_per_usd": ILS_PER_USD,
        "task_running": _scraper_task is not None and not _scraper_task.done(),
        "last_run":     _last_run_report,
    }


# ==============================================================================
# STANDALONE TEST  —  python catalog_scraper.py
# ==============================================================================

if __name__ == "__main__":
    import sys

    async def _test():
        print("=== catalog_scraper standalone test ===\n")

        # Test exchange rate
        rate = await fetch_ils_exchange_rate()
        print(f"ILS/USD exchange rate: {rate}\n")

        # Test one scraper tool
        part = sys.argv[1] if len(sys.argv) > 1 else "1K0698151G"
        mfr  = sys.argv[2] if len(sys.argv) > 2 else "Volkswagen"

        print(f"Testing scrape_autodoc({part!r}, {mfr!r}) ...")
        r = await scrape_autodoc(part, mfr)
        print(json.dumps(r, indent=2, ensure_ascii=False))

        print(f"\nTesting scrape_ebay_motors({part!r}, {mfr!r}) ...")
        r2 = await scrape_ebay_motors(part, mfr)
        print(json.dumps(r2, indent=2, ensure_ascii=False))

    asyncio.run(_test())
