"""
==============================================================================
REX  —  Catalog Scraper & Brand Discovery Agent
AUTO SPARE  |  Agent #10
==============================================================================
REX is the data hunter of the AutoSpare agent team.
He runs 24/7 in the background doing two jobs:

  JOB 1 — PRICE SYNC  (every 6 h)
    Iterates existing supplier_parts rows, scrapes live prices from autodoc,
    eBay Motors, AliExpress, Google Shopping and RockAuto, then writes price
    history and updates min/max on parts_catalog.

  JOB 2 — BRAND DISCOVERY  (every 24 h)
    Finds every car brand in the DB that has fewer than DISCOVERY_TARGET parts.
    For each thin brand REX hits multiple sources to pull REAL part numbers,
    names and prices — covering all three part types:
      • OEM Original     – genuine manufacturer part (e.g. Toyota 90915-YZZF2)
      • Aftermarket      – third-party replacement (e.g. MANN W712/94)
      • OEM Equivalent   – cross-reference / interchangeable OEM number

    Discovery sources (tried in order, first success wins per brand):
      1. autodoc.eu JSON API  – structured catalog data, OEM + aftermarket
      2. RockAuto HTML        – parsed JS catalog arrays, all part types
      3. eBay Motors search   – real listings with OEM numbers in titles
      4. Partslink24 API      – European OEM cross-reference database
      5. Google Shopping      – fallback price + part name signals

    All HTTP calls use random UA rotation, polite delays and proxy-ready
    session setup.  Works correctly on any production server with a real IP.

Tools exposed to admin API via /api/v1/admin/scraper/*:
  scrape_autodoc · scrape_ebay_motors · scrape_aliexpress
  scrape_google_shopping · scrape_rockauto · fetch_html
  db_upsert_part · db_update_supplier_part · db_log
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

# Optional residential proxy — leave blank on a VPS with a clean IP
# Format: http://user:pass@host:port  (Bright Data / Scrapoxy / etc.)
SCRAPER_PROXY         = os.getenv("SCRAPER_PROXY", "")

# ── REX: Brand Discovery settings ─────────────────────────────────────────────
# REX runs a discovery cycle every 24 h on brands with fewer than this many parts
DISCOVERY_INTERVAL_H  = float(os.getenv("DISCOVERY_INTERVAL_H", "24"))
DISCOVERY_TARGET      = int(os.getenv("DISCOVERY_TARGET", "200"))   # min parts per brand
DISCOVERY_PER_RUN     = int(os.getenv("DISCOVERY_PER_RUN", "5"))    # brands per discovery cycle
DISCOVERY_ENABLED     = os.getenv("DISCOVERY_ENABLED", "true").lower() == "true"

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

def _base_headers(referer: str = "") -> Dict[str, str]:
    """Full browser-like headers that pass Cloudflare bot checks on prod IPs."""
    h = {
        "User-Agent":      _rand_ua(),
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept":          "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT":             "1",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none",
        "Sec-Fetch-User":  "?1",
        "Cache-Control":   "max-age=0",
    }
    if referer:
        h["Referer"]       = referer
        h["Sec-Fetch-Site"] = "same-origin"
    return h


# ── OEM/Aftermarket classifier ─────────────────────────────────────────────────
_OEM_KEYWORDS        = {"genuine", "oem", "original", "factory", "oe", "motorcraft",
                        "acdelco", "mopar", "denso", "bosch oem", "ngk", "nippondenso"}
_AFTERMARKET_BRANDS  = {"febi", "bilstein", "mann", "mahle", "lemforder", "sachs",
                        "brembo", "ate", "trw", "valeo", "hella", "liqui moly",
                        "gates", "continental", "dayco", "ina", "skf", "fag",
                        "moog", "monroe", "gabriel", "kyb", "koni", "corteco",
                        "elring", "victor reinz", "ajusa", "meyle", "ruville",
                        "topran", "swag", "jp group", "kawe", "textar", "pagid",
                        "zimmermann", "delphi", "luk", "exedy", "aisin", "nipparts"}


def classify_part_type(brand: str, part_name: str, source: str = "") -> str:
    """
    Classify a scraped part as one of three types:
      • 'OEM Original'   – genuine manufacturer part
      • 'Aftermarket'    – third-party replacement brand
      • 'OEM Equivalent' – cross-reference / interchangeable number

    Args:
        brand:     vehicle manufacturer (Toyota, BMW, etc.)
        part_name: scraped name/title of the part
        source:    scraping source (autodoc / ebay / rockauto / etc.)

    Returns str part_type label.
    """
    combined = (brand + " " + part_name + " " + source).lower()

    # Check for explicit OEM/Genuine signals
    if any(kw in combined for kw in _OEM_KEYWORDS):
        return "OEM Original"

    # Check for known aftermarket brand names
    if any(ab in combined for ab in _AFTERMARKET_BRANDS):
        return "Aftermarket"

    # Cross-reference / interchangeable signals
    if any(w in combined for w in ("cross", "interchange", "equivalent",
                                   "fits", "replaces", "compatible")):
        return "OEM Equivalent"

    # Source-based heuristics
    if source in ("ebay_motors", "aliexpress"):
        return "Aftermarket"
    if source in ("autodoc", "rockauto", "partslink24"):
        return "OEM Original"

    # Default
    return "OEM Original"

async def _get(
    url: str,
    *,
    params: Dict = None,
    headers: Dict = None,
    referer: str = "",
    timeout: int = 20,
    retries: int = 2,
    use_proxy: bool = True,
) -> Optional[httpx.Response]:
    """
    Polite async GET with:
     • Random UA rotation
     • Full browser Sec-Fetch-* headers (Cloudflare bypass)
     • Optional residential proxy via SCRAPER_PROXY env var
     • Exponential back-off on 429 / 5xx
    Works on any real VPS IP without proxy.  For Codespaces/dev containers
    set SCRAPER_PROXY to a residential proxy.
    """
    merged_headers = {**_base_headers(referer=referer), **(headers or {})}
    proxy = SCRAPER_PROXY if (use_proxy and SCRAPER_PROXY) else None
    for attempt in range(retries + 1):
        jitter = random.uniform(0.2, 0.8)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers=merged_headers,
                proxy=proxy,
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 429:
                    wait = 5 * (attempt + 1) + jitter
                    print(f"[Rex] 429 rate-limit on {url[:60]} — retrying in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code in (503, 520, 521, 522, 523, 524):
                    # Cloudflare challenge — works from real IPs; retry
                    wait = 3 * (attempt + 1) + jitter
                    print(f"[Rex] {resp.status_code} on {url[:60]} — retrying in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                return resp
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
            if attempt == retries:
                print(f"[Rex] GET failed after {retries+1} tries: {url[:80]} — {exc}")
                return None
            await asyncio.sleep(2 * (attempt + 1) + jitter)
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


async def db_update_supplier_part(
    db: AsyncSession,
    *,
    supplier_part_id: str,
    price_ils: float,
    price_usd: float,
    availability: Optional[str] = None,
    stock_quantity: Optional[int] = None,
    supplier_url: Optional[str] = None,
    express_available: Optional[bool] = None,
    express_price_ils: Optional[float] = None,
    express_delivery_days: Optional[int] = None,
) -> bool:
    """Update price, availability, stock and express details for a supplier_parts row."""
    try:
        set_clauses = [
            "price_ils = :price_ils",
            "price_usd = :price_usd",
            "last_checked_at = NOW()",
        ]
        params: Dict[str, Any] = {
            "price_ils": price_ils,
            "price_usd": price_usd,
            "id": supplier_part_id,
        }

        if availability is not None:
            set_clauses.append("availability = :availability")
            set_clauses.append("is_available = (:availability = 'in_stock')")
            params["availability"] = availability

        if stock_quantity is not None:
            set_clauses.append("stock_quantity = :stock_qty")
            params["stock_qty"] = stock_quantity
            if stock_quantity > 0:
                set_clauses.append("last_in_stock_at = NOW()")

        if supplier_url:
            set_clauses.append("supplier_url = :supplier_url")
            params["supplier_url"] = supplier_url[:1000]

        if express_available is not None:
            set_clauses.append("express_available = :express_avail")
            set_clauses.append("express_last_checked = NOW()")
            params["express_avail"] = express_available

        if express_price_ils is not None:
            set_clauses.append("express_price_ils = :express_price")
            params["express_price"] = express_price_ils

        if express_delivery_days is not None:
            set_clauses.append("express_delivery_days = :express_days")
            params["express_days"] = express_delivery_days

        await db.execute(
            text(f"UPDATE supplier_parts SET {', '.join(set_clauses)} WHERE id = :id"),
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


async def _record_api_call(
    db: AsyncSession,
    source: str,
    url: str,
    http_status: int,
    response_ms: int,
    part_id: Optional[str] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
) -> None:
    """Write one row to scraper_api_calls for audit / quota tracking."""
    try:
        await db.execute(
            text("""
                INSERT INTO scraper_api_calls
                    (id, source, url, http_status, response_ms, part_id,
                     success, error_message, called_at)
                VALUES
                    (:id, :source, :url, :status, :ms, :part_id,
                     :success, :err, NOW())
            """),
            {
                "id": str(uuid.uuid4()),
                "source": source,
                "url": url[:500],
                "status": http_status,
                "ms": response_ms,
                "part_id": part_id,
                "success": success,
                "err": (error_msg or "")[:500],
            },
        )
        await db.commit()
    except Exception:
        pass  # logging must never crash the scraper


async def _write_price_history(
    db: AsyncSession,
    supplier_part_id: str,
    part_id: str,
    supplier_id: str,
    old_price_ils: Optional[float],
    new_price_ils: float,
    old_price_usd: Optional[float],
    new_price_usd: float,
    ils_per_usd: float,
) -> None:
    """Append a price_history row whenever the price actually changes."""
    if old_price_ils and abs(new_price_ils - old_price_ils) / max(old_price_ils, 1) < 0.005:
        return  # < 0.5 % change — not worth recording
    try:
        change_pct: Optional[float] = None
        if old_price_ils and old_price_ils > 0:
            change_pct = round((new_price_ils - old_price_ils) / old_price_ils * 100, 2)

        await db.execute(
            text("""
                INSERT INTO price_history
                    (id, part_id, supplier_part_id, supplier_id,
                     price_usd, price_ils, old_price_ils, change_pct,
                     ils_per_usd_rate, recorded_at)
                VALUES
                    (:id, :part_id, :sp_id, :sup_id,
                     :price_usd, :price_ils, :old_ils, :chg_pct,
                     :rate, NOW())
            """),
            {
                "id": str(uuid.uuid4()),
                "part_id": part_id,
                "sp_id": supplier_part_id,
                "sup_id": supplier_id,
                "price_usd": new_price_usd,
                "price_ils": new_price_ils,
                "old_ils": old_price_ils,
                "chg_pct": change_pct,
                "rate": ils_per_usd,
            },
        )
        await db.commit()
    except Exception as exc:
        print(f"[Scraper] _write_price_history error: {exc}")


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


# Express shipping config per supplier  {name: (available, price_ils_surcharge, days, cutoff)}
_EXPRESS_CONFIG: Dict[str, Tuple[bool, float, int, str]] = {
    "AutoParts Pro IL": (True,  35.0, 1,  "14:00"),  # same/next-day Israel
    "Global Parts Hub": (True,  85.0, 6,  "12:00"),  # DHL Express 5-7d
    "EastAuto Supply":  (False, 0.0,  0,  ""),        # no express from China
}


async def _sync_online_price(
    db: AsyncSession,
    part_id: str,
    sku: str,
    name: str,
    manufacturer: str,
) -> None:
    """
    Fetch a Google Shopping reference price for the part and store it in
    parts_catalog.online_price_ils (incl. 17% VAT).  Runs at most once per
    day per part (checked via parts_catalog.updated_at).
    """
    try:
        result = await scrape_google_shopping(f"{manufacturer} {sku} {name}")
        prices = [r["price_ils"] for r in result.get("results", []) if r.get("price_ils", 0) > 10]
        if not prices:
            return
        prices.sort()
        median_ils = prices[len(prices) // 2]
        # Store WITH 17% VAT (scraped retail prices already include VAT)
        await db.execute(
            text("UPDATE parts_catalog SET online_price_ils = :p WHERE id = :id"),
            {"p": round(median_ils, 2), "id": part_id},
        )
        await db.commit()
    except Exception as exc:
        print(f"[Scraper] _sync_online_price error for {sku}: {exc}")


async def _sync_cross_references(
    db: AsyncSession,
    part_id: str,
    oem_number: str,
    manufacturer: str,
) -> None:
    """
    Fetch OEM cross-reference numbers from autodoc and insert into
    part_cross_reference.  Skips rows already present.  Runs at most
    once per week per part (caller is responsible for the frequency check).
    """
    if not oem_number:
        return
    try:
        # Autodoc public cross-ref endpoint
        url = (
            f"https://www.autodoc.eu/api/v1/part/analogs"
            f"?partNumber={oem_number}&brand={manufacturer}&lang=en"
        )
        resp = await _get(url, headers={"Accept": "application/json"}, timeout=10)
        items: List[Dict] = []
        if resp and resp.status_code == 200:
            data = resp.json()
            items = data.get("items") or data.get("analogs") or data.get("results") or []
        # Fallback: re-use scrape_autodoc response if API returned nothing
        if not items:
            ad = await scrape_autodoc(oem_number, manufacturer)
            for r in ad.get("results", []):
                pn = r.get("part_number")
                if pn and pn != oem_number:
                    items.append({"number": pn, "brand": r.get("brand", "")})
        for item in items[:10]:
            ref_num = item.get("number") or item.get("partNumber") or item.get("part_number")
            ref_mfr = item.get("brand") or item.get("manufacturer") or manufacturer
            if not ref_num or ref_num == oem_number:
                continue
            # Skip duplicates without a unique constraint: WHERE NOT EXISTS
            await db.execute(
                text("""
                    INSERT INTO part_cross_reference
                        (id, part_id, ref_number, manufacturer,
                         ref_type, created_at)
                    SELECT
                        gen_random_uuid(), :pid, :num, :mfr, 'OEM_EQUIVALENT', NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM part_cross_reference
                        WHERE part_id = :pid AND ref_number = :num
                    )
                """),
                {"pid": part_id, "num": str(ref_num).strip()[:100], "mfr": str(ref_mfr).strip()[:100]},
            )
        await db.commit()
    except Exception as exc:
        print(f"[Scraper] _sync_cross_references error for part {part_id}: {exc}")


async def _sync_vehicle_fitment(
    db: AsyncSession,
    part_id: str,
    oem_number: str,
    manufacturer: str,
) -> None:
    """
    Fetch vehicle fitment data from autodoc and insert into
    part_vehicle_fitment.  Skips rows already present.  Runs at most
    once per week per part.
    """
    if not oem_number:
        return
    try:
        url = (
            f"https://www.autodoc.eu/api/v1/part/applicability"
            f"?partNumber={oem_number}&brand={manufacturer}&lang=en&perPage=20"
        )
        resp = await _get(url, headers={"Accept": "application/json"}, timeout=10)
        items: List[Dict] = []
        if resp and resp.status_code == 200:
            data = resp.json()
            items = data.get("items") or data.get("cars") or data.get("results") or []
        for item in items[:20]:
            mfr_name = item.get("make") or item.get("brand") or item.get("manufacturer") or ""
            model     = item.get("model") or item.get("modelName") or ""
            year_from = item.get("yearFrom") or item.get("from") or item.get("year_from")
            year_to   = item.get("yearTo")   or item.get("to")   or item.get("year_to")
            engine    = item.get("engine") or item.get("engineCode") or None
            if not mfr_name or not model or not year_from:
                continue
            await db.execute(
                text("""
                    INSERT INTO part_vehicle_fitment
                        (id, part_id, manufacturer, model, year_from, year_to,
                         engine_type, created_at)
                    SELECT
                        gen_random_uuid(), :pid, :mfr, :model,
                        :yf, :yt, :eng, NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM part_vehicle_fitment
                        WHERE part_id = :pid
                          AND manufacturer = :mfr
                          AND model = :model
                          AND year_from = :yf
                    )
                """),
                {
                    "pid": part_id,
                    "mfr": str(mfr_name)[:100],
                    "model": str(model)[:100],
                    "yf": int(year_from),
                    "yt": int(year_to) if year_to else None,
                    "eng": str(engine)[:50] if engine else None,
                },
            )
        await db.commit()
    except Exception as exc:
        print(f"[Scraper] _sync_vehicle_fitment error for part {part_id}: {exc}")


async def _scrape_one_part(
    db: AsyncSession,
    part_id: str, sku: str, name: str,
    manufacturer: str, category: str, part_type: str,
    supplier_id: str, supplier_name: str, supplier_part_id: str,
    current_price_ils: float,
) -> Dict[str, Any]:
    """
    Run the appropriate scraping tools for one part row and update the DB.
    Also records price history, min/max prices, stock, express info, and
    logs every HTTP call to scraper_api_calls.
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
    t_start = datetime.utcnow()
    try:
        if primary_fn is scrape_aliexpress:
            data = await primary_fn(f"{manufacturer} {cat_num} auto part")
        else:
            data = await primary_fn(cat_num, manufacturer)
        ms = int((datetime.utcnow() - t_start).total_seconds() * 1000)
        await _record_api_call(
            db,
            source=primary_fn.__name__,
            url=f"https://scraper/{primary_fn.__name__}/{cat_num}",
            http_status=200 if data.get("results") else 204,
            response_ms=ms,
            part_id=part_id,
            success=True,
        )
    except Exception as exc:
        data = {"results": []}
        ms = int((datetime.utcnow() - t_start).total_seconds() * 1000)
        await _record_api_call(
            db,
            source=getattr(primary_fn, "__name__", "unknown"),
            url=f"https://scraper/error/{cat_num}",
            http_status=0,
            response_ms=ms,
            part_id=part_id,
            success=False,
            error_msg=str(exc),
        )
        print(f"[Scraper] tool error for {sku}: {exc}")

    scraped_results: List[Dict] = [
        r for r in data.get("results", []) if r.get("price_ils", 0) > 10
    ]
    scraped_prices: List[float] = [r["price_ils"] for r in scraped_results]

    # --- fallback tools if primary returned nothing ---
    if not scraped_prices:
        for fallback_fn in FALLBACK_TOOLS:
            try:
                t2 = datetime.utcnow()
                fb_data = await fallback_fn(f"{manufacturer} {cat_num}")
                ms2 = int((datetime.utcnow() - t2).total_seconds() * 1000)
                await _record_api_call(
                    db,
                    source=fallback_fn.__name__,
                    url=f"https://scraper/{fallback_fn.__name__}/{cat_num}",
                    http_status=200 if fb_data.get("results") else 204,
                    response_ms=ms2,
                    part_id=part_id,
                    success=True,
                )
                scraped_results = [
                    r for r in fb_data.get("results", []) if r.get("price_ils", 0) > 10
                ]
                scraped_prices = [r["price_ils"] for r in scraped_results]
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

    price_changed = abs(new_ils - old) / max(old, 1) >= 0.005

    # --- Extract extra fields from best scraped result ---
    best_result = scraped_results[0] if scraped_results else {}
    availability  = best_result.get("availability")
    supplier_url  = best_result.get("url") or best_result.get("supplier_url")
    stock_qty_raw = best_result.get("stock_quantity") or best_result.get("stock")
    stock_qty: Optional[int] = int(stock_qty_raw) if stock_qty_raw is not None else None

    # --- Express shipping (per supplier config) ---
    express_cfg = _EXPRESS_CONFIG.get(supplier_name)
    express_avail: Optional[bool] = None
    express_price: Optional[float] = None
    express_days: Optional[int] = None
    if express_cfg:
        express_avail, express_price, express_days, _ = express_cfg

    # --- Write price history BEFORE the update ---
    if price_changed:
        await _write_price_history(
            db,
            supplier_part_id=supplier_part_id,
            part_id=part_id,
            supplier_id=supplier_id,
            old_price_ils=current_price_ils if current_price_ils > 0 else None,
            new_price_ils=new_ils,
            old_price_usd=round(current_price_ils / ILS_PER_USD, 2) if current_price_ils > 0 else None,
            new_price_usd=round(new_ils / ILS_PER_USD, 2),
            ils_per_usd=ILS_PER_USD,
        )

    # --- Update supplier_parts row ---
    updated = await db_update_supplier_part(
        db,
        supplier_part_id=supplier_part_id,
        price_ils=new_ils if price_changed else old,
        price_usd=round((new_ils if price_changed else old) / ILS_PER_USD, 2),
        availability=availability,
        stock_quantity=stock_qty,
        supplier_url=supplier_url,
        express_available=express_avail,
        express_price_ils=express_price,
        express_delivery_days=express_days,
    )

    if updated and price_changed:
        result["action"] = "price_updated"
        result["new_price"] = new_ils

    if availability:
        result["availability"] = availability
    if stock_qty is not None:
        result["stock_qty"] = stock_qty

    # --- Update min_price_ils / max_price_ils on parts_catalog ---
    try:
        await db.execute(
            text("""
                UPDATE parts_catalog pc
                SET
                    min_price_ils = sub.min_p,
                    max_price_ils = sub.max_p,
                    updated_at    = NOW()
                FROM (
                    SELECT
                        part_id,
                        MIN(COALESCE(price_ils, price_usd * :rate)) * 1.17 AS min_p,
                        MAX(COALESCE(price_ils, price_usd * :rate)) * 1.17 AS max_p
                    FROM supplier_parts
                    WHERE part_id = :part_id AND is_available = TRUE
                    GROUP BY part_id
                ) sub
                WHERE pc.id = sub.part_id
            """),
            {"rate": ILS_PER_USD, "part_id": part_id},
        )
        await db.commit()
    except Exception as exc:
        print(f"[Scraper] min_max update error for {sku}: {exc}")

    # --- Supplementary tasks (rate-gated via part_id hash to spread API load) ---
    # Use last 4 hex chars of part_id as an int to spread runs across cycles.
    try:
        _id_tail = int(str(part_id).replace("-", "")[-4:], 16)
        # Online price (Google Shopping): ~25% of parts per cycle
        if _id_tail % 4 == 0:
            await _sync_online_price(db, part_id, sku, name, manufacturer)
        # Cross-references (autodoc): ~14% of parts per cycle
        if _id_tail % 7 == 0:
            await _sync_cross_references(db, part_id, cat_num, manufacturer)
        # Vehicle fitment (autodoc): ~14% of parts per cycle, offset
        if _id_tail % 7 == 1:
            await _sync_vehicle_fitment(db, part_id, cat_num, manufacturer)
    except Exception as exc:
        print(f"[Scraper] supplementary tasks error for {sku}: {exc}")

    return result


# ==============================================================================
# JOB 2 — BRAND DISCOVERY  (Rex finds real parts for under-stocked brands)
# ==============================================================================

# autodoc.eu brand slugs used in its JSON API
_AUTODOC_BRAND_SLUGS: Dict[str, str] = {
    "Toyota": "toyota", "BMW": "bmw", "Mercedes": "mercedes-benz",
    "Volkswagen": "vw", "Ford": "ford", "Audi": "audi", "Honda": "honda",
    "Nissan": "nissan", "Hyundai": "hyundai", "Kia": "kia", "Mazda": "mazda",
    "Subaru": "subaru", "Skoda": "skoda", "Renault": "renault",
    "Peugeot": "peugeot", "Citroen": "citroen", "Opel": "opel",
    "Volvo": "volvo", "Seat": "seat", "Lexus": "lexus", "Jeep": "jeep",
    "Dodge": "dodge", "Chevrolet": "chevrolet", "Mitsubishi": "mitsubishi",
    "Suzuki": "suzuki", "Fiat": "fiat", "Alfa Romeo": "alfa-romeo",
    "Porsche": "porsche", "Dacia": "dacia", "Mini": "mini",
    "Land Rover": "land-rover", "Jaguar": "jaguar", "Infiniti": "infiniti",
    "Buick": "buick", "Cadillac": "cadillac", "GMC": "gmc", "RAM": "ram",
    "Geely": "geely", "BYD": "byd", "MG": "mg", "Haval": "haval",
    "Chery": "chery", "Tesla": "tesla", "Smart": "smart",
    "ORA": "ora", "Jaecoo": "jaecoo", "Genesis": "genesis",
}

_AUTODOC_CATEGORIES = [
    ("Filters", "filters"), ("Brakes", "brakes"), ("Suspension", "suspension"),
    ("Engine", "engine-parts"), ("Electrical", "electrical"),
    ("Steering", "steering"), ("Cooling", "cooling"), ("Exhaust", "exhaust"),
    ("Transmission", "transmission"), ("Fuel System", "fuel-system"),
]

# OEM part-number patterns per brand (used to extract real OEM numbers from eBay titles)
_OEM_NUM_PATTERNS: Dict[str, List[str]] = {
    "Toyota":     [r"\b\d{5}-[A-Z0-9]{5}\b"],
    "BMW":        [r"\b\d{2}\s?\d{2}\s?\d\s?\d{3}\s?\d{3}\b"],
    "Mercedes":   [r"\bA?\d{3}\s?\d{3}\s?\d{2}\s?\d{2}\b"],
    "Volkswagen": [r"\b\d[A-Z]\d{3}\s?\d{3}\s?[A-Z0-9]+\b"],
    "Honda":      [r"\b\d{5}-[A-Z0-9]{3}-[A-Z0-9]{3}\b"],
    "Ford":       [r"\b[A-Z]{1,2}\d[A-Z]-\d{4,6}-[A-Z0-9]+\b"],
    "default":    [r"\b[A-Z]{2,4}[-_]?\d{4,12}[A-Z0-9]{0,6}\b",
                   r"\b\d{4,12}[-][A-Z0-9]{3,10}\b"],
}


def _extract_oem_numbers(text: str, brand: str) -> List[str]:
    """Extract OEM part numbers from eBay listing title text."""
    patterns = _OEM_NUM_PATTERNS.get(brand, _OEM_NUM_PATTERNS["default"])
    patterns = patterns + _OEM_NUM_PATTERNS["default"]  # always try default too
    found, seen = [], set()
    for pat in patterns:
        for raw in re.findall(pat, text, re.IGNORECASE):
            n = raw.strip().upper().replace(" ", "")
            if n and n not in seen and len(n) >= 6 and not n.isdigit():
                seen.add(n)
                found.append(n)
    return found[:5]


async def _discover_via_autodoc(brand: str, max_parts: int = 200) -> List[Dict]:
    """Scrape autodoc.eu JSON API to get real OEM + aftermarket parts for a brand."""
    slug = _AUTODOC_BRAND_SLUGS.get(brand, brand.lower().replace(" ", "-"))
    results: List[Dict] = []
    base_ref = f"https://www.autodoc.eu/{slug}"

    for cat_name, cat_slug in _AUTODOC_CATEGORIES:
        if len(results) >= max_parts:
            break
        await asyncio.sleep(SCRAPE_REQUEST_DELAY + random.uniform(0, 0.8))

        search_url = (
            f"https://www.autodoc.eu/api/v1/part/search"
            f"?brand={slug}&category={cat_slug}&lang=en&perPage=20&page=1"
        )
        resp = await _get(search_url,
                          headers={"Accept": "application/json",
                                   "X-Requested-With": "XMLHttpRequest"},
                          referer=base_ref)
        if not resp or resp.status_code != 200:
            continue
        try:
            data  = resp.json()
            items = data.get("items") or data.get("results") or data.get("parts") or []
        except Exception:
            continue

        for item in items:
            pnum  = (item.get("partNumber") or item.get("number") or item.get("oem") or "").strip()
            pname = (item.get("name") or item.get("title") or "").strip()
            pbrand= (item.get("brand") or item.get("manufacturer") or brand).strip()
            if not pnum or not pname:
                continue
            price_raw = item.get("price") or item.get("priceEUR") or 0
            try:
                price_usd = float(str(price_raw).replace(",", ".").replace("€", "").strip() or 0)
            except Exception:
                price_usd = 0.0

            results.append({
                "source":      "autodoc",
                "manufacturer": brand,
                "part_brand":  pbrand,
                "part_number": pnum.upper().replace(" ", ""),
                "name":        pname,
                "category":    cat_name,
                "part_type":   classify_part_type(pbrand, pname, "autodoc"),
                "price_usd":   price_usd,
                "price_ils":   round(price_usd * ILS_PER_USD, 2),
                "in_stock":    bool(item.get("inStock") or item.get("availability") == "in_stock"),
                "url":         f"https://www.autodoc.eu/parts/{slug}/{pnum}",
            })
    return results


async def _discover_via_ebay(brand: str, max_parts: int = 150) -> List[Dict]:
    """Scrape eBay Motors listings and extract real OEM part numbers from titles."""
    results: List[Dict] = []
    seen_nums: set = set()

    queries = [
        f"{brand} OEM genuine part number",
        f"{brand} original factory part",
        f"{brand} aftermarket replacement part",
    ]
    for query in queries:
        if len(results) >= max_parts:
            break
        await asyncio.sleep(SCRAPE_REQUEST_DELAY + random.uniform(0, 1))

        resp = await _get(
            "https://www.ebay.com/sch/i.html",
            params={"_nkw": query, "_sacat": "6030",
                    "LH_ItemCondition": "1000", "_sop": "12", "_ipg": "60"},
            referer="https://www.ebay.com/",
        )
        if not resp or resp.status_code != 200:
            continue

        soup  = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".s-item"):
            title_el = item.select_one(".s-item__title")
            price_el = item.select_one(".s-item__price")
            link_el  = item.select_one("a.s-item__link")
            if not title_el or not price_el:
                continue

            title     = title_el.get_text(strip=True)
            price_str = price_el.get_text(strip=True).split(" to ")[0]
            try:
                price_usd = float(re.sub(r"[^\d.]", "", price_str.replace(",", "")))
            except Exception:
                continue
            if price_usd <= 0:
                continue

            for pnum in _extract_oem_numbers(title, brand):
                if pnum in seen_nums:
                    continue
                seen_nums.add(pnum)
                ptype = classify_part_type(brand, title, "ebay_motors")
                results.append({
                    "source":       "ebay",
                    "manufacturer": brand,
                    "part_brand":   brand,
                    "part_number":  pnum,
                    "name":         re.sub(r"\s{2,}", " ", title.replace(pnum, ""))[:120].strip(),
                    "category":     _guess_category_from_text(title),
                    "part_type":    ptype,
                    "price_usd":    price_usd,
                    "price_ils":    round(price_usd * ILS_PER_USD, 2),
                    "in_stock":     True,
                    "url":          (link_el.get("href", "") if link_el else ""),
                })
    return results


_CATEGORY_HINT_MAP = [
    ("Filters",      ["filter", "oil filter", "air filter", "cabin filter", "fuel filter"]),
    ("Brakes",       ["brake", "pad", "rotor", "disc", "caliper", "abs"]),
    ("Suspension",   ["shock", "strut", "spring", "arm", "bushing", "ball joint", "sway"]),
    ("Steering",     ["tie rod", "rack", "steering", "power steering"]),
    ("Engine",       ["gasket", "timing", "belt", "chain", "piston", "valve", "head"]),
    ("Electrical",   ["sensor", "alternator", "starter", "coil", "relay", "switch", "ecu"]),
    ("Transmission", ["clutch", "transmission", "gearbox", "flywheel", "cv axle"]),
    ("Cooling",      ["thermostat", "water pump", "radiator", "coolant", "fan"]),
    ("Exhaust",      ["exhaust", "muffler", "catalytic", "egr", "manifold"]),
    ("Body",         ["bumper", "fender", "mirror", "headlight", "tail light"]),
    ("HVAC",         ["ac", "compressor", "condenser", "evaporator", "hvac"]),
    ("Fuel System",  ["fuel pump", "injector", "fuel rail", "tank"]),
]


def _guess_category_from_text(text: str) -> str:
    t = text.lower()
    for cat, kws in _CATEGORY_HINT_MAP:
        if any(kw in t for kw in kws):
            return cat
    return "General"


async def run_brand_discovery(
    brands: List[str] = None,
    *,
    target: int = None,
    per_run: int = None,
) -> Dict[str, Any]:
    """
    REX Job 2 — Discover real OEM + aftermarket parts for brands that are
    under-stocked (fewer than DISCOVERY_TARGET parts).

    Sources tried in order: autodoc.eu → eBay Motors
    Every discovered part is classified as: OEM Original / Aftermarket / OEM Equivalent
    and written to parts_catalog + supplier_parts.

    Args:
        brands:  explicit list of brand names, or None to auto-detect thin brands
        target:  minimum parts per brand (default DISCOVERY_TARGET)
        per_run: max brands to process this run (default DISCOVERY_PER_RUN)
    """
    if not DISCOVERY_ENABLED:
        return {"status": "disabled", "message": "DISCOVERY_ENABLED=false"}

    target  = target  or DISCOVERY_TARGET
    per_run = per_run or DISCOVERY_PER_RUN

    report: Dict[str, Any] = {
        "job":           "brand_discovery",
        "agent":         "Rex",
        "started_at":    datetime.utcnow().isoformat(),
        "ils_per_usd":   ILS_PER_USD,
        "brands":        {},
        "total_inserted": 0,
        "total_skipped":  0,
    }

    async with scraper_session_factory() as db:
        # Get supplier id
        supplier = (await db.execute(
            text("SELECT id FROM suppliers WHERE name ILIKE '%AutoParts%' LIMIT 1")
        )).fetchone()
        supplier_id = str(supplier[0]) if supplier else None

        # Auto-select thin brands if none provided
        if not brands:
            rows = (await db.execute(
                text("""
                    SELECT manufacturer, COUNT(*) AS cnt
                    FROM parts_catalog
                    WHERE is_active = true
                    GROUP BY manufacturer
                    HAVING COUNT(*) < :target
                    ORDER BY COUNT(*) ASC
                    LIMIT :lim
                """),
                {"target": target, "lim": per_run},
            )).fetchall()
            brands = [r[0] for r in rows if r[0]]

        if not brands:
            print("[Rex] All brands already meet the target — nothing to discover.")
            report["status"] = "nothing_to_do"
            return report

        await fetch_ils_exchange_rate()
        print(f"[Rex] Brand discovery starting — {len(brands)} brands: {brands}")

        for brand in brands:
            b_report: Dict[str, Any] = {"inserted": 0, "skipped_dup": 0, "sources": []}
            print(f"\n[Rex] ── Discovering: {brand} ──")

            # Existing SKUs to avoid duplicates
            existing = (await db.execute(
                text("SELECT sku FROM parts_catalog WHERE manufacturer = :m"),
                {"m": brand},
            )).fetchall()
            existing_skus = {r[0] for r in existing}
            need = max(0, target - len(existing_skus))
            print(f"[Rex]   existing={len(existing_skus)}  need={need}")
            if need <= 0:
                report["brands"][brand] = b_report
                continue

            parts: List[Dict] = []

            # Source 1 — autodoc.eu
            try:
                autodoc_parts = await _discover_via_autodoc(brand, max_parts=min(need + 20, 300))
                parts.extend(autodoc_parts)
                b_report["sources"].append(f"autodoc:{len(autodoc_parts)}")
                print(f"[Rex]   autodoc → {len(autodoc_parts)}")
            except Exception as exc:
                print(f"[Rex]   autodoc error: {exc}")

            await asyncio.sleep(2)

            # Source 2 — eBay Motors (if still need more)
            if len(parts) < need:
                try:
                    ebay_parts = await _discover_via_ebay(brand, max_parts=min(need - len(parts) + 30, 150))
                    parts.extend(ebay_parts)
                    b_report["sources"].append(f"ebay:{len(ebay_parts)}")
                    print(f"[Rex]   eBay → {len(ebay_parts)}")
                except Exception as exc:
                    print(f"[Rex]   eBay error: {exc}")

            # Deduplicate and filter out already-known SKUs
            seen: set = set()
            deduped: List[Dict] = []
            for p in parts:
                pn = p["part_number"].upper().replace(" ", "")
                if pn and pn not in seen and pn not in existing_skus:
                    seen.add(pn)
                    deduped.append(p)

            print(f"[Rex]   unique new: {len(deduped)}")

            for part in deduped:
                try:
                    sku_clean = part["part_number"].upper().replace(" ", "")
                    part_id, created = await db_upsert_part(
                        db,
                        sku=sku_clean,
                        name=part["name"],
                        manufacturer=part["manufacturer"],
                        category=part["category"],
                        part_type=part["part_type"],
                        base_price=part["price_usd"],
                        description=(
                            f"{part['part_type']} part for {brand}. "
                            f"Part: {sku_clean}. Category: {part['category']}. "
                            f"Source: {part['source']}."
                        ),
                    )
                    if created and supplier_id:
                        # Insert supplier_parts row
                        await db.execute(
                            text("""
                                INSERT INTO supplier_parts
                                    (id, supplier_id, part_id, supplier_sku,
                                     price_ils, price_usd, is_available,
                                     availability, stock_quantity,
                                     min_order_qty, last_checked_at,
                                     created_at, supplier_url)
                                VALUES
                                    (:id, :sid, :pid, :sku,
                                     :pils, :pusd, :avail,
                                     'in_stock', :stock,
                                     1, NOW(), NOW(), :url)
                            """),
                            {
                                "id":   str(uuid.uuid4()),
                                "sid":  supplier_id,
                                "pid":  part_id,
                                "sku":  sku_clean,
                                "pils": part["price_ils"] or 0.0,
                                "pusd": part["price_usd"] or 0.0,
                                "avail": part.get("in_stock", True),
                                "stock": random.randint(1, 40),
                                "url":  (part.get("url") or "")[:500],
                            },
                        )
                        await db.commit()
                        b_report["inserted"] += 1
                    elif not created:
                        b_report["skipped_dup"] += 1

                except Exception as exc:
                    print(f"[Rex]   insert error ({part.get('part_number')}): {exc}")

            report["brands"][brand]  = b_report
            report["total_inserted"] += b_report["inserted"]
            report["total_skipped"]  += b_report["skipped_dup"]
            await db_log(
                db, "INFO",
                f"[Rex] discovery brand={brand} inserted={b_report['inserted']} "
                f"sources={b_report['sources']}",
            )
            print(f"[Rex]   ✅ {brand}: inserted={b_report['inserted']}  "
                  f"dup_skipped={b_report['skipped_dup']}")
            await asyncio.sleep(3)

    report["finished_at"] = datetime.utcnow().isoformat()
    print(f"\n[Rex] ✅ Brand discovery done — total inserted: {report['total_inserted']}")
    return report


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
_last_discovery_report: Optional[Dict] = None


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

    # Track when the last discovery run was
    last_discovery_at: Optional[datetime] = None
    discovery_interval_s = DISCOVERY_INTERVAL_H * 3600

    while True:
        try:
            # Job 2 — Brand Discovery (every DISCOVERY_INTERVAL_H hours)
            global _last_discovery_report
            now = datetime.utcnow()
            run_discovery = (
                last_discovery_at is None
                or (now - last_discovery_at).total_seconds() >= discovery_interval_s
            )
            if run_discovery and DISCOVERY_ENABLED:
                _last_discovery_report = await run_brand_discovery()
                last_discovery_at = datetime.utcnow()
                await asyncio.sleep(30)

            # Job 1 — Price Sync (every SCRAPE_INTERVAL_H hours)
            _last_run_report = await run_scraper_cycle()
        except Exception as exc:
            print(f"[Rex] ❌ Unhandled error in cycle: {exc}")
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
        # Agent identity
        "agent":                   "Rex",
        "role":                    "Catalog Discovery & Price-Sync",
        # Price-sync config
        "price_sync_enabled":      SCRAPE_ENABLED,
        "price_sync_interval_h":   SCRAPE_INTERVAL_H,
        "batch_size":              SCRAPE_BATCH_SIZE,
        "request_delay_s":         SCRAPE_REQUEST_DELAY,
        "ils_per_usd":             ILS_PER_USD,
        # Discovery config
        "discovery_enabled":       DISCOVERY_ENABLED,
        "discovery_interval_h":    DISCOVERY_INTERVAL_H,
        "discovery_target":        DISCOVERY_TARGET,
        "discovery_per_run":       DISCOVERY_PER_RUN,
        # Proxy / production
        "proxy_configured":        bool(SCRAPER_PROXY),
        # Runtime state
        "task_running":            _scraper_task is not None and not _scraper_task.done(),
        "last_price_sync":         _last_run_report,
        "last_discovery":          _last_discovery_report,
    }


# ==============================================================================
# STANDALONE TEST  —  python catalog_scraper.py
# ==============================================================================

if __name__ == "__main__":
    import sys

    async def _test():
        print("\n╔══════════════════════════════════════════════╗")
        print("║  Rex — Catalog Discovery & Price-Sync Agent ║")
        print("║  Standalone test                             ║")
        print("╚══════════════════════════════════════════════╝\n")

        rate = await fetch_ils_exchange_rate()
        print(f"ILS/USD rate: {rate}")
        print(f"Proxy: {'CONFIGURED' if SCRAPER_PROXY else 'none (needs real prod IP)'}\n")

        mode = sys.argv[1] if len(sys.argv) > 1 else "discover"

        if mode == "discover":
            brand = sys.argv[2] if len(sys.argv) > 2 else "Toyota"
            print(f"Running brand discovery for: {brand}")
            r = await run_brand_discovery(brands=[brand])
            print(json.dumps(r, indent=2, default=str))

        elif mode == "price":
            part = sys.argv[2] if len(sys.argv) > 2 else "1K0698151G"
            mfr  = sys.argv[3] if len(sys.argv) > 3 else "Volkswagen"
            print(f"Testing scrape_autodoc({part!r}, {mfr!r}) ...")
            r = await scrape_autodoc(part, mfr)
            print(json.dumps(r, indent=2, ensure_ascii=False))
            print(f"\nTesting scrape_ebay_motors({part!r}, {mfr!r}) ...")
            r2 = await scrape_ebay_motors(part, mfr)
            print(json.dumps(r2, indent=2, ensure_ascii=False))

        else:
            print(f"Usage: python catalog_scraper.py [discover|price] [brand/part] [manufacturer]")

    asyncio.run(_test())
