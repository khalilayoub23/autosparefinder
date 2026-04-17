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

        Discovery sources (tried in order):
            1. Official manufacturer websites / parts portals
            2. autodoc.eu JSON API
            3. eBay Motors search

    All HTTP calls use random UA rotation, polite delays and proxy-ready
    session setup.  Works correctly on any production server with a real IP.

Tools exposed to admin API via /api/v1/admin/scraper/*:
  scrape_autodoc · scrape_ebay_motors · scrape_aliexpress
  scrape_google_shopping · scrape_rockauto · fetch_html
  db_upsert_part · db_update_supplier_part · db_log
==============================================================================
"""
# CATALOG PIPELINE OWNER: Rex — writes parts_catalog + supplier_parts (real scraped prices).
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from external_fitment_providers import (
    build_external_provider_attempts,
    classify_external_payload,
)

from resilience import (
    retry_with_backoff,
    check_supplier_rate_limit,
    get_supplier_domain_from_url,
    job_registry_start,
    job_registry_finish,
)

load_dotenv()
logger = logging.getLogger(__name__)

# ── DB ─────────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")
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
DISCOVERY_USE_OFFICIAL_SITES = os.getenv("DISCOVERY_USE_OFFICIAL_SITES", "true").lower() == "true"
DISCOVERY_OFFICIAL_ONLY = os.getenv("DISCOVERY_OFFICIAL_ONLY", "false").lower() == "true"
DISCOVERY_OFFICIAL_MAX_REQUESTS = max(1, int(os.getenv("DISCOVERY_OFFICIAL_MAX_REQUESTS", "18")))
DISCOVERY_OFFICIAL_MAX_DOMAINS = max(1, int(os.getenv("DISCOVERY_OFFICIAL_MAX_DOMAINS", "18")))
DISCOVERY_OFFICIAL_MAX_URLS = max(6, int(os.getenv("DISCOVERY_OFFICIAL_MAX_URLS", "72")))
DISCOVERY_OFFICIAL_MAX_PRICE_USD = float(os.getenv("DISCOVERY_OFFICIAL_MAX_PRICE_USD", "10000"))
_DISCOVERY_OFFICIAL_SUFFIXES_RAW = os.getenv(
    "DISCOVERY_OFFICIAL_SUFFIXES",
    "com,co.il,de,co.uk,fr,it,es,nl,be,ch,at,pl,cz,pt,se,no,fi,dk,com.au,co.jp,co.kr,com.br,mx,ae,sa,tr",
)
DISCOVERY_OFFICIAL_SUFFIXES = [
    s.strip().lower()
    for s in _DISCOVERY_OFFICIAL_SUFFIXES_RAW.split(",")
    if s.strip()
]
if not DISCOVERY_OFFICIAL_SUFFIXES:
    DISCOVERY_OFFICIAL_SUFFIXES = ["com"]

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
_OEM_DISCOVERY_SOURCES = {"epc-data", "partsouq", "realoem"}
_AFTERMARKET_BRAND_CACHE: Dict[str, Tuple[Optional[uuid.UUID], str]] = {}


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


@retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=60.0, retry_on=(429, 503, 504), jitter=True)
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
     • Exponential back-off on 429 / 5xx (via @retry_with_backoff decorator)
    Works on any real VPS IP without proxy.  For Codespaces/dev containers
    set SCRAPER_PROXY to a residential proxy.
    """
    merged_headers = {**_base_headers(referer=referer), **(headers or {})}
    proxy = SCRAPER_PROXY if (use_proxy and SCRAPER_PROXY) else None
    jitter = random.uniform(0.2, 0.8)
    
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers=merged_headers,
            proxy=proxy,
        ) as client:
            resp = await client.get(url, params=params)
            return resp
    except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
        print(f"[Rex] GET failed: {url[:80]} — {exc}")
        raise  # Let decorator handle retry


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


def _normalize_oem_candidate(raw_value: Any) -> Optional[str]:
    if raw_value is None:
        return None
    text_val = str(raw_value).strip().upper()
    if not text_val:
        return None
    text_val = re.sub(r"\s+", "", text_val)
    text_val = text_val.strip(";,|")
    if len(text_val) < 5:
        return None
    if not re.search(r"[A-Z0-9]", text_val):
        return None
    return text_val


def _extract_oem_numbers_from_autodoc_item(item: Dict[str, Any], manufacturer: str) -> List[str]:
    """Extract OEM numbers from known autodoc payload keys plus regex fallback."""
    found: List[str] = []
    seen: set = set()

    def _push(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for entry in value:
                _push(entry)
            return
        normalized = _normalize_oem_candidate(value)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        found.append(normalized)

    _push(item.get("oemNumbers"))
    _push(item.get("OEMNumber"))

    for bucket_name in ("references", "applicability"):
        bucket = item.get(bucket_name)
        if not isinstance(bucket, list):
            continue
        for entry in bucket:
            if isinstance(entry, dict):
                entry_type = str(
                    entry.get("type")
                    or entry.get("referenceType")
                    or entry.get("kind")
                    or ""
                ).strip().lower()
                is_oem_type = entry_type in ("oem", "oe")
                if is_oem_type or bucket_name == "applicability":
                    _push(entry.get("number"))
                    _push(entry.get("value"))
                    _push(entry.get("reference"))
                    _push(entry.get("ref"))
                    _push(entry.get("oemNumber"))
                    _push(entry.get("OEMNumber"))
                    _push(entry.get("oemNumbers"))
            else:
                _push(entry)

    regex_blob = json.dumps(item, ensure_ascii=False, default=str)
    for extracted in _extract_oem_numbers(regex_blob, manufacturer):
        _push(extracted)

    return found


async def scrape_autodoc(
    part_number: str,
    manufacturer: str = "",
    *,
    rate_limit_per_minute: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Search autodoc.co.il (Autodoc's Israeli presence) for a part number.
    Falls back to autodoc.eu JSON API which is publicly accessible.
    Returns list of {name, price_ils, currency, url, availability, brand}
    """
    # Rate-limit check (Gap 4)
    import redis.asyncio as _aioredis
    try:
        redis_client = _aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
        limit = rate_limit_per_minute or 30
        if not await check_supplier_rate_limit(redis_client, "autodoc.co.il", limit_per_minute=limit):
            logger.warning("Autodoc rate limit exceeded, skipping scrape")
            return {"results": [], "skipped": True, "reason": "rate_limit"}
    except Exception as e:
        logger.warning(f"Rate limit check failed for autodoc: {e}")
    
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
                oem_numbers = _extract_oem_numbers_from_autodoc_item(item, manufacturer)
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
                    "oem_numbers": oem_numbers,
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
                    oem_numbers = _extract_oem_numbers(page["text"], manufacturer)
                    results.append({
                        "source": "autodoc_html",
                        "part_number": part_number,
                        "name": f"חלק {part_number}",
                        "brand": manufacturer,
                        "price_ils": price,
                        "availability": "unknown",
                        "url": page["url"],
                        "oem_numbers": oem_numbers,
                    })
                except Exception:
                    pass

    return {"tool": "scrape_autodoc", "part_number": part_number, "results": results}


async def scrape_ebay_motors(
    part_number: str,
    manufacturer: str = "",
    *,
    rate_limit_per_minute: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Search eBay Motors for a part number and return sold/listed price range.
    Uses eBay's public search endpoint (no API key required for basic results).
    """
    # Rate-limit check (Gap 4)
    import redis.asyncio as _aioredis
    try:
        redis_client = _aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
        limit = rate_limit_per_minute or 30
        if not await check_supplier_rate_limit(redis_client, "ebay.com", limit_per_minute=limit):
            logger.warning("eBay Motors rate limit exceeded, skipping scrape")
            return {"results": [], "skipped": True, "reason": "rate_limit"}
    except Exception as e:
        logger.warning(f"Rate limit check failed for eBay: {e}")
    
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


async def scrape_rockauto(
    part_number: str,
    manufacturer: str = "",
    *,
    rate_limit_per_minute: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Scrape RockAuto for a part number.  RockAuto uses a React/JSON structure;
    we extract prices from the embedded JSON data payload.
    """
    # Rate-limit check (Gap 4)
    import redis.asyncio as _aioredis
    try:
        redis_client = _aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
        limit = rate_limit_per_minute or 30
        if not await check_supplier_rate_limit(redis_client, "rockauto.com", limit_per_minute=limit):
            logger.warning("RockAuto rate limit exceeded, skipping scrape")
            return {"results": [], "skipped": True, "reason": "rate_limit"}
    except Exception as e:
        logger.warning(f"Rate limit check failed for RockAuto: {e}")
    
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

async def _meili_sync_part(part_id: str, doc: dict) -> None:
    """Fire-and-forget: push one updated part document to Meilisearch."""
    _meili_url = os.getenv("MEILI_URL", "")
    if not _meili_url:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.put(
                f"{_meili_url}/indexes/parts/documents",
                headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                json=[{**doc, "id": part_id}],
            )
    except Exception:
        pass  # non-critical — bulk re-sync will catch stragglers


def _normalize_brand_name(raw_name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (raw_name or "").lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


async def resolve_aftermarket_brand(
    brand_name: str,
    db: Optional[AsyncSession] = None,
) -> Tuple[Optional[uuid.UUID], str]:
    """Resolve aftermarket brand ID+tier via exact match then fuzzy fallback."""
    normalized = _normalize_brand_name(brand_name)
    if not normalized:
        return None, "generic"

    cached = _AFTERMARKET_BRAND_CACHE.get(normalized)
    if cached is not None:
        return cached

    if db is None:
        async with scraper_session_factory() as temp_db:
            return await resolve_aftermarket_brand(brand_name, db=temp_db)

    exact = (await db.execute(
        text(
            """
            SELECT id, tier
            FROM aftermarket_brands
            WHERE is_active = TRUE
              AND lower(name) = :exact_name
            LIMIT 1
            """
        ),
        {"exact_name": normalized},
    )).fetchone()

    if exact:
        resolved = (exact[0], (exact[1] or "generic"))
        _AFTERMARKET_BRAND_CACHE[normalized] = resolved
        return resolved

    first_token = normalized.split(" ")[0]
    fuzzy = (await db.execute(
        text(
            """
            SELECT id, tier
            FROM aftermarket_brands
            WHERE is_active = TRUE
              AND (
                    lower(name) LIKE :contains_match
                 OR :normalized_name LIKE '%' || lower(name) || '%'
                 OR lower(name) LIKE :prefix_match
              )
            ORDER BY
              CASE
                WHEN lower(name) = :normalized_name THEN 0
                WHEN lower(name) LIKE :prefix_match THEN 1
                ELSE 2
              END,
              length(name) ASC
            LIMIT 1
            """
        ),
        {
            "contains_match": f"%{normalized}%",
            "normalized_name": normalized,
            "prefix_match": f"{first_token}%",
        },
    )).fetchone()

    if fuzzy:
        resolved = (fuzzy[0], (fuzzy[1] or "generic"))
    else:
        resolved = (None, "generic")

    _AFTERMARKET_BRAND_CACHE[normalized] = resolved
    return resolved


async def db_upsert_part(db: AsyncSession, *, sku: str, name: str, manufacturer: str,
                          category: str, part_type: str = "Aftermarket",
                          base_price: float = 0.0, description: str = "",
                          compatible_vehicles: list = None,
                          aftermarket_brand_id: Optional[uuid.UUID] = None,
                          aftermarket_tier: Optional[str] = None,
                          apply_aftermarket: bool = False,
                          is_oem_source: bool = False,
                          oem_number: Optional[str] = None) -> Tuple[str, bool]:
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
                    description = :description,
                    part_condition = CASE
                        WHEN :is_oem_source THEN 'OEM'
                        ELSE part_condition
                    END,
                    aftermarket_tier = CASE
                        WHEN :is_oem_source THEN NULL
                        WHEN :apply_aftermarket THEN :aftermarket_tier
                        ELSE aftermarket_tier
                    END,
                    aftermarket_brand_id = CASE
                        WHEN :is_oem_source THEN NULL
                        WHEN :apply_aftermarket THEN CAST(:aftermarket_brand_id AS uuid)
                        ELSE aftermarket_brand_id
                    END,
                    oem_number = CASE
                        WHEN :is_oem_source AND :oem_number IS NOT NULL THEN COALESCE(oem_number, :oem_number)
                        ELSE oem_number
                    END,
                    needs_oem_lookup = CASE
                        WHEN :is_oem_source AND :oem_number IS NOT NULL THEN FALSE
                        ELSE needs_oem_lookup
                    END,
                    updated_at = NOW()
                WHERE sku = :sku
            """),
            {"name": name, "manufacturer": manufacturer, "category": category,
             "part_type": part_type, "base_price": base_price,
             "description": description, "sku": sku,
             "is_oem_source": is_oem_source,
             "apply_aftermarket": apply_aftermarket,
             "aftermarket_tier": aftermarket_tier,
             "aftermarket_brand_id": str(aftermarket_brand_id) if aftermarket_brand_id else None,
             "oem_number": oem_number},
        )
        await db.commit()
        asyncio.create_task(_meili_sync_part(
            str(existing[0]),
            {"sku": sku, "name": name, "manufacturer": manufacturer,
             "category": category, "part_type": part_type, "base_price": base_price},
        ))
        try:
            await db.execute(
                text("""
                    INSERT INTO catalog_versions
                        (id, version_tag, description, parts_added, parts_updated,
                         source, status, created_at)
                    VALUES
                        (gen_random_uuid(), :vtag, :desc, 0, 1,
                         'catalog_scraper', 'completed', NOW())
                    ON CONFLICT (version_tag) DO NOTHING
                """),
                {"vtag": f"scraper-upd-{uuid.uuid4().hex[:16]}", "desc": f"Updated: {sku}"},
            )
            await db.commit()
        except Exception:
            pass
        return str(existing[0]), False

    part_id = str(uuid.uuid4())
    insert_part_condition = "OEM" if is_oem_source else "New"
    insert_aftermarket_tier = aftermarket_tier if apply_aftermarket and not is_oem_source else None
    insert_aftermarket_brand = str(aftermarket_brand_id) if (apply_aftermarket and aftermarket_brand_id and not is_oem_source) else None
    insert_oem_number = oem_number if is_oem_source and oem_number else None
    insert_needs_oem_lookup = not bool(insert_oem_number)
    await db.execute(
        text("""
            INSERT INTO parts_catalog
                (id, sku, name, manufacturer, category, part_type, base_price,
                 description, specifications, compatible_vehicles,
                 part_condition, aftermarket_tier, aftermarket_brand_id,
                 oem_number, needs_oem_lookup,
                 is_active, created_at, updated_at)
            VALUES
                (:id, :sku, :name, :manufacturer, :category, :part_type, :base_price,
                 :description, '{}', :compat,
                 :part_condition, :aftermarket_tier, CAST(:aftermarket_brand_id AS uuid),
                 :oem_number, :needs_oem_lookup,
                 true, NOW(), NOW())
        """),
        {
            "id": part_id, "sku": sku, "name": name,
            "manufacturer": manufacturer, "category": category,
            "part_type": part_type, "base_price": base_price,
            "description": description,
            "compat": json.dumps(compatible_vehicles or []),
            "part_condition": insert_part_condition,
            "aftermarket_tier": insert_aftermarket_tier,
            "aftermarket_brand_id": insert_aftermarket_brand,
            "oem_number": insert_oem_number,
            "needs_oem_lookup": insert_needs_oem_lookup,
        },
    )
    await db.commit()
    asyncio.create_task(_meili_sync_part(
        part_id,
        {"sku": sku, "name": name, "manufacturer": manufacturer,
         "category": category, "part_type": part_type, "base_price": base_price,
         "is_active": True},
    ))
    try:
        await db.execute(
            text("""
                INSERT INTO catalog_versions
                    (id, version_tag, description, parts_added, parts_updated,
                     source, status, created_at)
                VALUES
                    (gen_random_uuid(), :vtag, :desc, 1, 0,
                     'catalog_scraper', 'completed', NOW())
                ON CONFLICT (version_tag) DO NOTHING
            """),
            {"vtag": f"scraper-add-{uuid.uuid4().hex[:16]}", "desc": f"Added: {sku}"},
        )
        await db.commit()
    except Exception:
        pass
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
        values: Dict[str, Any] = {
            "price_ils": price_ils,
            "price_usd": price_usd,
            "last_checked_at": text("NOW()"),
        }

        if availability is not None:
            values["availability"] = availability
            values["is_available"] = (availability == "in_stock")

        if stock_quantity is not None:
            values["stock_quantity"] = stock_quantity
            if stock_quantity > 0:
                values["last_in_stock_at"] = text("NOW()")

        if supplier_url:
            values["supplier_url"] = supplier_url[:1000]

        if express_available is not None:
            values["express_available"] = express_available
            values["express_last_checked"] = text("NOW()")

        if express_price_ils is not None:
            values["express_price_ils"] = express_price_ils

        if express_delivery_days is not None:
            values["express_delivery_days"] = express_delivery_days

        await db.execute(
            update(SupplierPart)
            .where(SupplierPart.id == supplier_part_id)
            .values(**values)
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
    "PartsPro USA":     scrape_rockauto,     # USA → RockAuto (USD)
    "AutoZone Direct":  scrape_ebay_motors,  # USA → eBay Motors
    "Hyundai Mobis":    scrape_google_shopping,  # Korean OEM → Google Shopping
    "Kia Parts Direct": scrape_google_shopping,  # Korean OEM → Google Shopping
    "Bosch Direct":     scrape_autodoc,          # Bosch → autodoc
    "Toyota Genuine":   scrape_google_shopping,  # Toyota OEM → Google Shopping
}

# Fallback priority: if primary returns nothing, try these in order
FALLBACK_TOOLS = [scrape_ebay_motors, scrape_google_shopping]


# Express shipping config per supplier  {name: (available, price_ils_surcharge, days, cutoff)}
_EXPRESS_CONFIG: Dict[str, Tuple[bool, float, int, str]] = {
    "AutoParts Pro IL": (True,  35.0, 1,  "14:00"),  # same/next-day Israel
    "Global Parts Hub": (True,  85.0, 6,  "12:00"),  # DHL Express 5-7d
    "EastAuto Supply":  (False, 0.0,  0,  ""),        # no express from China
    "PartsPro USA":     (True,  95.0, 5,  "13:00"),  # FedEx Express USA→IL
    "AutoZone Direct":  (True, 105.0, 6,  "12:00"),  # UPS Express USA→IL
    "Hyundai Mobis":    (True,  90.0, 5,  "13:00"),  # Korean Air Cargo
    "Kia Parts Direct": (True,  90.0, 5,  "13:00"),  # Korean Air Cargo
    "Bosch Direct":     (True,  80.0, 5,  "12:00"),  # DHL from Germany
    "Toyota Genuine":   (True,  99.0, 6,  "12:00"),  # Japan Air Express
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
    parts_catalog.online_price_ils (incl. 18% VAT).  Runs at most once per
    day per part (checked via parts_catalog.updated_at).
    """
    try:
        result = await scrape_google_shopping(f"{manufacturer} {sku} {name}")
        prices = [r["price_ils"] for r in result.get("results", []) if r.get("price_ils", 0) > 10]
        if not prices:
            return
        prices.sort()
        median_ils = prices[len(prices) // 2]
        # Store WITH 18% VAT (scraped retail prices already include VAT)
        await db.execute(
            text("UPDATE parts_catalog SET online_price_ils = :p WHERE id = :id"),
            {"p": round(median_ils, 2), "id": part_id},
        )
        await db.commit()
    except Exception as exc:
        print(f"[Scraper] _sync_online_price error for {sku}: {exc}")


async def _persist_oem_numbers_from_autodoc(
    db: AsyncSession,
    part_id: str,
    sku: str,
    manufacturer: str,
    oem_numbers: List[str],
) -> None:
    cleaned: List[str] = []
    seen: set = set()
    for raw in oem_numbers:
        normalized = _normalize_oem_candidate(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)

    if not cleaned:
        return

    logger.info(f"[REX] OEM extracted: {sku} → {cleaned}")

    await db.execute(
        text(
            """
            UPDATE parts_catalog
            SET
                oem_number = CASE
                    WHEN oem_number IS NULL THEN :first_oem
                    ELSE oem_number
                END,
                needs_oem_lookup = FALSE,
                updated_at = NOW()
            WHERE id = :pid
            """
        ),
        {"pid": part_id, "first_oem": cleaned[0][:100]},
    )

    for oem_number in cleaned:
        await db.execute(
            text(
                """
                INSERT INTO part_cross_reference
                    (id, part_id, ref_number, manufacturer, ref_type, created_at)
                SELECT
                    gen_random_uuid(), :pid, :num, :mfr, 'OEM', NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM part_cross_reference
                    WHERE part_id = :pid AND ref_number = :num
                )
                """
            ),
            {
                "pid": part_id,
                "num": oem_number[:100],
                "mfr": (manufacturer or "")[:100],
            },
        )

    await db.commit()


async def _sync_cross_references(
    db: AsyncSession,
    part_id: str,
    oem_number: str,
    manufacturer: str,
    source: str = "autodoc",
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
            ref_type = "OEM" if (source or "").lower() in _OEM_DISCOVERY_SOURCES else "aftermarket"
            # Skip duplicates without a unique constraint: WHERE NOT EXISTS
            await db.execute(
                text("""
                    INSERT INTO part_cross_reference
                        (id, part_id, ref_number, manufacturer,
                         ref_type, created_at)
                    SELECT
                        gen_random_uuid(), :pid, :num, :mfr, :ref_type, NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM part_cross_reference
                        WHERE part_id = :pid AND ref_number = :num
                    )
                """),
                {
                    "pid": part_id,
                    "num": str(ref_num).strip()[:100],
                    "mfr": str(ref_mfr).strip()[:100],
                    "ref_type": ref_type,
                },
            )
        await db.commit()
    except Exception as exc:
        print(f"[Scraper] _sync_cross_references error for part {part_id}: {exc}")


async def _sync_vehicle_fitment(
    db: AsyncSession,
    part_id: str,
    oem_number: str,
    manufacturer: str,
    provider_attempts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Fetch vehicle fitment data from autodoc and insert into
    part_vehicle_fitment.  Skips rows already present.  Runs at most
    once per week per part.
    """
    if not oem_number:
        return {
            "selected_provider": None,
            "provider_attempts": [],
            "items_count": 0,
            "upserted_rows": 0,
            "reason": "missing_oem_number",
        }

    def _build_fitment_provider_attempts(part_number: str, brand: str) -> List[Dict[str, Any]]:
        return build_external_provider_attempts(part_number=str(part_number or ""), brand=str(brand or ""))

    attempts = provider_attempts or _build_fitment_provider_attempts(oem_number, manufacturer)
    provider_trace: List[Dict[str, Any]] = []
    selected_provider: Optional[str] = None
    items: List[Dict[str, Any]] = []

    try:
        for attempt in attempts:
            source_kind = str(attempt.get("source_kind") or "autodoc_like")
            supports_fitment = bool(attempt.get("supports_fitment", True))
            skip_reason = str(attempt.get("skip_reason") or "").strip()

            if skip_reason or not str(attempt.get("url") or "").strip():
                provider_trace.append(
                    {
                        "provider": attempt.get("provider"),
                        "source_kind": source_kind,
                        "supports_fitment": supports_fitment,
                        "use_proxy": bool(attempt.get("use_proxy", True)),
                        "status_code": None,
                        "payload_kind": "skipped",
                        "items_count": 0,
                        "fitment_usable": False,
                        "skip_reason": skip_reason or "empty_url",
                    }
                )
                continue

            request_headers = {"Accept": "application/json"}
            for h_key, h_value in (attempt.get("headers") or {}).items():
                if h_key and h_value is not None:
                    request_headers[str(h_key)] = str(h_value)

            resp = await _get(
                str(attempt.get("url") or ""),
                headers=request_headers,
                timeout=10,
                use_proxy=bool(attempt.get("use_proxy", True)),
            )
            status_code = None if resp is None else int(resp.status_code)
            payload_meta = classify_external_payload(
                resp,
                source_kind=source_kind,
                default_brand=manufacturer,
                supports_fitment=supports_fitment,
            )
            candidate_items: List[Dict[str, Any]] = payload_meta.get("fitment_items", []) or []

            provider_trace.append(
                {
                    "provider": attempt.get("provider"),
                    "source_kind": source_kind,
                    "supports_fitment": supports_fitment,
                    "use_proxy": bool(attempt.get("use_proxy", True)),
                    "status_code": status_code,
                    "payload_kind": payload_meta.get("payload_kind"),
                    "items_count": int(payload_meta.get("items_count") or 0),
                    "fitment_usable": bool(payload_meta.get("fitment_usable", False)),
                    "content_type": payload_meta.get("content_type"),
                }
            )

            if candidate_items:
                items = candidate_items
                selected_provider = str(attempt.get("provider") or "") or None
                break

        upserted_rows = 0
        for item in items[:20]:
            mfr_name = item.get("make") or item.get("brand") or item.get("manufacturer") or ""
            model     = item.get("model") or item.get("modelName") or ""
            year_from = item.get("yearFrom") or item.get("from") or item.get("year_from")
            year_to   = item.get("yearTo")   or item.get("to")   or item.get("year_to")
            engine    = item.get("engine") or item.get("engineCode") or None
            if not mfr_name or not model or not year_from:
                continue
            try:
                year_from_int = int(year_from)
            except Exception:
                continue

            gov_match = await db.execute(
                text(
                    """
                    SELECT tozeret_cd, degem_cd
                    FROM vehicle_market_il
                    WHERE manufacturer ILIKE :manufacturer
                      AND kinuy_mishari ILIKE :model
                      AND shnat_yitzur = :year
                    LIMIT 1
                    """
                ),
                {
                    "manufacturer": str(mfr_name)[:100],
                    "model": str(model)[:100],
                    "year": year_from_int,
                },
            )
            gov_row = gov_match.fetchone()
            tozeret_cd = int(gov_row[0]) if gov_row and gov_row[0] is not None else None
            degem_cd = int(gov_row[1]) if gov_row and gov_row[1] is not None else None

            await db.execute(
                text("""
                    INSERT INTO part_vehicle_fitment
                        (
                            id,
                            part_id,
                            manufacturer,
                            model,
                            year_from,
                            year_to,
                            engine_type,
                            tozeret_cd,
                            degem_cd,
                            shnat_yitzur,
                            created_at,
                            updated_at
                        )
                    VALUES
                        (
                            gen_random_uuid(),
                            :pid,
                            :mfr,
                            :model,
                            :yf,
                            :yt,
                            :eng,
                            :tozeret_cd,
                            :degem_cd,
                            :shnat_yitzur,
                            NOW(),
                            NOW()
                        )
                    ON CONFLICT (part_id, manufacturer, model, year_from)
                    DO UPDATE SET
                        year_to = EXCLUDED.year_to,
                        engine_type = COALESCE(EXCLUDED.engine_type, part_vehicle_fitment.engine_type),
                        tozeret_cd = EXCLUDED.tozeret_cd,
                        degem_cd = EXCLUDED.degem_cd,
                        shnat_yitzur = EXCLUDED.shnat_yitzur,
                        updated_at = NOW()
                """),
                {
                    "pid": part_id,
                    "mfr": str(mfr_name)[:100],
                    "model": str(model)[:100],
                    "yf": year_from_int,
                    "yt": int(year_to) if year_to else None,
                    "eng": str(engine)[:50] if engine else None,
                    "tozeret_cd": tozeret_cd,
                    "degem_cd": degem_cd,
                    "shnat_yitzur": year_from_int,
                },
            )
            upserted_rows += 1
        await db.commit()
        return {
            "selected_provider": selected_provider,
            "provider_attempts": provider_trace,
            "items_count": len(items),
            "upserted_rows": upserted_rows,
        }
    except Exception as exc:
        print(f"[Scraper] _sync_vehicle_fitment error for part {part_id}: {exc}")
        return {
            "selected_provider": selected_provider,
            "provider_attempts": provider_trace,
            "items_count": len(items),
            "upserted_rows": 0,
            "error": str(exc),
        }


async def get_fitment_by_gov_codes(
    tozeret_cd: int,
    degem_cd: int,
    shnat_yitzur: int,
    db: AsyncSession,
) -> List[Dict]:
    """
    Returns matching parts for a vehicle identified by Israeli gov codes.
    Falls back to manufacturer/model text search if no gov code match exists.
    """
    rows = await db.execute(
        text(
            """
            SELECT
                pc.id,
                pc.name,
                pc.part_condition,
                pc.oem_number,
                pc.online_price_ils,
                ab.name AS brand_name,
                ab.tier AS brand_tier,
                ab.logo_url AS brand_logo,
                vm.manufacturer,
                vm.kinuy_mishari AS model,
                vm.shnat_yitzur AS year,
                vm.nefah_manoa AS engine
            FROM part_vehicle_fitment pvf
            JOIN parts_catalog pc ON pc.id = pvf.part_id
            LEFT JOIN aftermarket_brands ab ON ab.id = pc.aftermarket_brand_id
            JOIN vehicle_market_il vm
              ON vm.tozeret_cd = pvf.tozeret_cd
             AND vm.degem_cd = pvf.degem_cd
             AND vm.shnat_yitzur = pvf.shnat_yitzur
            WHERE pvf.tozeret_cd = :tozeret_cd
              AND pvf.degem_cd = :degem_cd
              AND pvf.shnat_yitzur = :shnat_yitzur
              AND pc.is_active = TRUE
            ORDER BY
                CASE pc.part_condition
                    WHEN 'OEM' THEN 1
                    WHEN 'aftermarket' THEN 2
                    WHEN 'used' THEN 3
                    ELSE 4
                END,
                CASE ab.tier
                    WHEN 'OE_equivalent' THEN 1
                    WHEN 'economy' THEN 2
                    WHEN 'generic' THEN 3
                    ELSE 4
                END
            """
        ),
        {
            "tozeret_cd": tozeret_cd,
            "degem_cd": degem_cd,
            "shnat_yitzur": shnat_yitzur,
        },
    )
    results = rows.fetchall()

    if not results:
        vehicle = await db.execute(
            text(
                """
                SELECT manufacturer, kinuy_mishari, shnat_yitzur, nefah_manoa
                FROM vehicle_market_il
                WHERE tozeret_cd = :tozeret_cd
                  AND degem_cd = :degem_cd
                LIMIT 1
                """
            ),
            {"tozeret_cd": tozeret_cd, "degem_cd": degem_cd},
        )
        v = vehicle.fetchone()
        if v:
            fallback_rows = await db.execute(
                text(
                    """
                    SELECT
                        pc.id,
                        pc.name,
                        pc.part_condition,
                        pc.oem_number,
                        pc.online_price_ils,
                        ab.name AS brand_name,
                        ab.tier AS brand_tier,
                        ab.logo_url AS brand_logo,
                        :manufacturer AS manufacturer,
                        :model AS model,
                                                CAST(:year AS INTEGER) AS year,
                        CAST(:engine AS TEXT) AS engine
                    FROM part_vehicle_fitment pvf
                    JOIN parts_catalog pc ON pc.id = pvf.part_id
                    LEFT JOIN aftermarket_brands ab ON ab.id = pc.aftermarket_brand_id
                    WHERE pvf.manufacturer ILIKE :manufacturer
                      AND pvf.model ILIKE :model
                                            AND pvf.year_from <= CAST(:year AS INTEGER)
                                            AND (pvf.year_to IS NULL OR pvf.year_to >= CAST(:year AS INTEGER))
                      AND pc.is_active = TRUE
                    ORDER BY
                        CASE pc.part_condition
                            WHEN 'OEM' THEN 1
                            WHEN 'aftermarket' THEN 2
                            WHEN 'used' THEN 3
                            ELSE 4
                        END,
                        CASE ab.tier
                            WHEN 'OE_equivalent' THEN 1
                            WHEN 'economy' THEN 2
                            WHEN 'generic' THEN 3
                            ELSE 4
                        END
                    """
                ),
                {
                    "manufacturer": v[0],
                    "model": v[1],
                    "year": int(v[2]) if v[2] is not None else shnat_yitzur,
                    "engine": str(v[3]) if v[3] is not None else None,
                },
            )
            results = fallback_rows.fetchall()

    return [dict(r._mapping) for r in results]


async def _scrape_one_part(
    db: AsyncSession,
    part_id: str, sku: str, name: str,
    manufacturer: str, category: str, part_type: str,
    supplier_id: str, supplier_name: str, supplier_part_id: str,
    rate_limit_per_minute: Optional[int],
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
            data = await primary_fn(cat_num, manufacturer, rate_limit_per_minute=rate_limit_per_minute)
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

    raw_results: List[Dict] = list(data.get("results", []))

    scraped_results: List[Dict] = [
        r for r in raw_results if r.get("price_ils", 0) > 10
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
                raw_results = list(fb_data.get("results", []))
                scraped_results = [
                    r for r in raw_results if r.get("price_ils", 0) > 10
                ]
                scraped_prices = [r["price_ils"] for r in scraped_results]
                if scraped_prices:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

    autodoc_oem_numbers: List[str] = []
    for row in raw_results:
        source_name = str(row.get("source") or "").lower()
        if source_name.startswith("autodoc"):
            autodoc_oem_numbers.extend(row.get("oem_numbers") or [])
    if autodoc_oem_numbers:
        try:
            await _persist_oem_numbers_from_autodoc(
                db,
                part_id=part_id,
                sku=sku,
                manufacturer=manufacturer,
                oem_numbers=autodoc_oem_numbers,
            )
        except Exception as exc:
            print(f"[Scraper] OEM persist error for {sku}: {exc}")

    if not scraped_prices:
        return result  # nothing found — leave price as-is

    # Use median scraped price as reference (ignores outliers)
    scraped_prices.sort()
    median_ils = scraped_prices[len(scraped_prices) // 2]

    # Business rules:
    # - Customer price = cost × 1.45 + 18% VAT + 91₪ shipping
    # - Scraped market price ≈ final customer price at competitors
    # - Derive our cost from scraped reference / 1.45 / 1.18 (rough)
    derived_cost_ils = median_ils / 1.18 / 1.45

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
                        MIN(COALESCE(price_ils, price_usd * :rate)) * 1.18 AS min_p,
                        MAX(COALESCE(price_ils, price_usd * :rate)) * 1.18 AS max_p
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
            await _sync_cross_references(db, part_id, cat_num, manufacturer, source="autodoc")
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

# Manufacturer official websites / parts portals.
# These are used by Rex discovery before marketplace sources.
_OFFICIAL_SITE_SEARCH_URLS: Dict[str, List[str]] = {
    "Toyota": ["https://autoparts.toyota.com/search?search_str={q}"],
    "Lexus": ["https://parts.lexus.com/search?search_str={q}"],
    "Ford": ["https://parts.ford.com/shop/en/us/search?q={q}"],
    "Volkswagen": ["https://parts.vw.com/search?searchTerm={q}"],
    "Audi": ["https://parts.audiusa.com/search?searchTerm={q}"],
    "Subaru": ["https://parts.subaru.com/search?searchTerm={q}"],
    "Mazda": ["https://parts.mazdausa.com/search?searchTerm={q}"],
    "Nissan": ["https://parts.nissanusa.com/search?searchTerm={q}"],
    "Honda": ["https://dreamshop.honda.com/s/search?q={q}"],
}

_OFFICIAL_BRAND_DOMAINS: Dict[str, str] = {
    "Toyota": "toyota.com",
    "Lexus": "lexus.com",
    "BMW": "bmw.com",
    "Mercedes": "mercedes-benz.com",
    "Volkswagen": "vw.com",
    "Ford": "ford.com",
    "Audi": "audi.com",
    "Honda": "honda.com",
    "Nissan": "nissanusa.com",
    "Hyundai": "hyundai.com",
    "Kia": "kia.com",
    "Mazda": "mazdausa.com",
    "Subaru": "subaru.com",
    "Skoda": "skoda-auto.com",
    "Renault": "renaultgroup.com",
    "Peugeot": "peugeot.com",
    "Citroen": "citroen.com",
    "Opel": "opel.com",
    "Volvo": "volvocars.com",
    "Seat": "seat.com",
    "Jeep": "jeep.com",
    "Dodge": "dodge.com",
    "Chevrolet": "chevrolet.com",
    "Mitsubishi": "mitsubishicars.com",
    "Suzuki": "suzuki.com",
    "Fiat": "fiat.com",
    "Alfa Romeo": "alfaromeo.com",
    "Porsche": "porsche.com",
    "Dacia": "dacia.com",
    "Mini": "mini.com",
    "Land Rover": "landrover.com",
    "Jaguar": "jaguar.com",
    "Infiniti": "infinitiusa.com",
    "Buick": "buick.com",
    "Cadillac": "cadillac.com",
    "GMC": "gmc.com",
    "RAM": "ramtrucks.com",
    "Geely": "geely.com",
    "BYD": "byd.com",
    "MG": "mg.co.uk",
    "Haval": "haval.com",
    "Chery": "cheryinternational.com",
    "Tesla": "tesla.com",
    "Smart": "smart.com",
    "ORA": "ora.co.uk",
    "Jaecoo": "jaecoo-global.com",
    "Genesis": "genesis.com",
}

# Alternate official brand domains seen across regions.
_OFFICIAL_BRAND_DOMAIN_ALIASES: Dict[str, List[str]] = {
    "Volkswagen": ["volkswagen.com"],
    "Mercedes": ["mercedes.com", "mercedes-benz.co.uk", "mercedes-benz.de"],
    "Nissan": ["nissan.com", "nissan.co.uk", "nissan.de"],
    "Mazda": ["mazda.com", "mazda.co.uk", "mazda.de"],
    "Renault": ["renault.com", "renault.fr"],
    "Skoda": ["skoda.com", "skoda-auto.de"],
    "Volvo": ["volvo.com", "volvocars.de", "volvocars.co.uk"],
    "Mitsubishi": ["mitsubishi-motors.com", "mitsubishi-motors.co.uk"],
    "Infiniti": ["infiniti.com", "infiniti.co.uk"],
    "RAM": ["ram.com", "ramtrucks.com"],
}

_OFFICIAL_SEARCH_PATH_TEMPLATES: List[str] = [
    "/search?q={q}",
    "/search?query={q}",
    "/search?searchTerm={q}",
    "/search?search_str={q}",
    "/search?keyword={q}",
    "/parts/search?q={q}",
    "/parts/search?query={q}",
    "/parts?query={q}",
    "/catalog/search?q={q}",
    "/shop/search?q={q}",
    "/s/search?q={q}",
]

_OFFICIAL_DISCOVERY_QUERIES: List[str] = [
    "{brand} genuine parts",
    "{brand} oem part number",
    "{brand} spare parts",
]

_OFFICIAL_DISCOVERY_QUERIES_BY_SUFFIX: Dict[str, List[str]] = {
    "co.il": ["{brand} oem spare parts israel", "{brand} genuine parts il"],
    "de": ["{brand} ersatzteile", "{brand} oem teilenummer"],
    "fr": ["{brand} pieces detachees", "{brand} numero de piece oem"],
    "it": ["{brand} ricambi originali", "{brand} codice oem"],
    "es": ["{brand} recambios originales", "{brand} referencia oem"],
    "co.uk": ["{brand} genuine parts uk", "{brand} oem part number uk"],
    "com.au": ["{brand} genuine parts australia"],
    "co.jp": ["{brand} genuine parts japan"],
}

_OFFICIAL_PART_TOKEN_BLACKLIST = {
    "HTTP", "HTTPS", "WWW", "COM", "ORG", "NET", "HTML", "JSON", "COOKIE",
    "SEARCH", "RESULT", "RESULTS", "LOGIN", "SIGNIN", "REGISTER", "ACCOUNT",
    "BUTTON", "SCRIPT", "STYLE", "CLASS", "PRODUCT", "PARTS", "SPARE", "GENUINE",
    "USD", "EUR", "ILS", "NIS", "MILE", "MILES", "MPG", "LEASE", "APR",
}

_OFFICIAL_PART_CONTEXT_KEYWORDS = {
    "part", "parts", "oem", "sku", "mpn", "genuine", "replacement",
    "filter", "brake", "rotor", "pad", "sensor", "pump", "gasket",
    "alternator", "starter", "radiator", "clutch", "transmission",
    "suspension", "bearing", "injector", "spark", "wiper", "coolant",
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


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return 0.0


def _price_to_usd(amount: float, currency: str) -> float:
    if amount <= 0:
        return 0.0
    cur = (currency or "USD").upper().strip()
    if cur in ("USD", "US$"):
        return amount
    if cur in ("ILS", "NIS", "₪"):
        return amount / max(ILS_PER_USD, 0.0001)
    if cur in ("EUR", "EURO", "€"):
        return amount * 1.08
    return amount


def _extract_price_from_text(text: str) -> Tuple[float, float]:
    source = text or ""
    patterns = [
        ("ILS", r"(?:₪|NIS|ILS)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"),
        ("USD", r"(?:US\$|USD|\$)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"),
        ("EUR", r"(?:EUR|€)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"),
    ]
    for cur, pat in patterns:
        match = re.search(pat, source, re.IGNORECASE)
        if not match:
            continue
        amount = _coerce_float(match.group(1))
        usd = round(_price_to_usd(amount, cur), 2)
        if usd <= 0:
            continue
        if usd > DISCOVERY_OFFICIAL_MAX_PRICE_USD:
            continue
        return usd, round(usd * ILS_PER_USD, 2)
    return 0.0, 0.0


def _extract_generic_part_numbers(text: str, brand: str) -> List[str]:
    found: List[str] = []
    seen: set = set()

    for pn in _extract_oem_numbers(text, brand):
        if pn not in seen:
            seen.add(pn)
            found.append(pn)

    for raw in re.findall(r"\b[A-Z0-9][A-Z0-9\-_/]{5,24}\b", (text or "").upper()):
        token = raw.strip("-_/ ").replace("_", "-").replace("/", "-")
        if not token:
            continue
        if token in _OFFICIAL_PART_TOKEN_BLACKLIST:
            continue
        if token.isdigit() or len(token) < 6 or len(token) > 24:
            continue
        digit_count = sum(ch.isdigit() for ch in token)
        letter_count = sum(ch.isalpha() for ch in token)
        if digit_count < 2:
            continue
        if re.search(r"(MILE|MILES|MPG|LEASE|APR)$", token):
            continue
        if re.fullmatch(r"\d{2,5}-(MILE|MILES|MPG|YEAR|YEARS)", token):
            continue
        # Accept all-alphanumeric mixed OEM-like tokens, or strict numeric hyphen formats.
        if letter_count == 0 and not re.fullmatch(r"\d{4,6}-\d{3,6}", token):
            continue
        if token not in seen:
            seen.add(token)
            found.append(token)

    return found[:10]


def _iter_json_ld_objects(soup: BeautifulSoup):
    for script in soup.select("script[type='application/ld+json']"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    yield item
            continue

        if not isinstance(payload, dict):
            continue

        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                if isinstance(item, dict):
                    yield item
            continue

        yield payload


def _dedupe_keep_order(items: List[str], limit: int = 0) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for raw in items:
        item = (raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def _normalize_official_domain(raw_host: str) -> str:
    host = (raw_host or "").strip().lower().strip("/")
    if not host:
        return ""
    if "://" in host:
        try:
            host = (urlparse(host).hostname or host).lower()
        except Exception:
            pass
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_domain_from_search_url_template(url_t: str) -> str:
    candidate = (url_t or "").strip()
    if not candidate:
        return ""
    if "{q}" in candidate:
        candidate = candidate.replace("{q}", "oem+parts")
    try:
        host = urlparse(candidate).hostname or ""
    except Exception:
        host = ""
    return _normalize_official_domain(host)


def _get_domain_suffix(domain: str) -> str:
    host = _normalize_official_domain(domain)
    if not host:
        return ""

    compound_suffixes = ("co.il", "co.uk", "com.au", "com.br", "co.jp", "co.kr", "com.mx")
    for suffix in compound_suffixes:
        if host == suffix or host.endswith(f".{suffix}"):
            return suffix

    labels = host.split(".")
    return labels[-1] if len(labels) >= 2 else ""


def _domain_root_label(domain: str) -> str:
    host = _normalize_official_domain(domain)
    if not host:
        return ""

    labels = host.split(".")
    if len(labels) == 1:
        return labels[0]

    compound_suffixes = {"co.il", "co.uk", "com.au", "com.br", "co.jp", "co.kr", "com.mx"}
    suffix2 = ".".join(labels[-2:]) if len(labels) >= 2 else ""
    if suffix2 in compound_suffixes and len(labels) >= 3:
        return labels[-3]

    return labels[-2]


def _expand_regional_domains(seed_domain: str) -> List[str]:
    base = _normalize_official_domain(seed_domain)
    if not base:
        return []

    expanded: List[str] = [base]
    root = _domain_root_label(base)
    if not root:
        return expanded

    for suffix in DISCOVERY_OFFICIAL_SUFFIXES:
        expanded.append(f"{root}.{suffix}")

    return _dedupe_keep_order(expanded)


def _build_official_brand_domains(brand: str) -> List[str]:
    seed_domains: List[str] = []

    primary = _OFFICIAL_BRAND_DOMAINS.get(brand)
    if primary:
        seed_domains.append(primary)

    seed_domains.extend(_OFFICIAL_BRAND_DOMAIN_ALIASES.get(brand, []))

    for u in _OFFICIAL_SITE_SEARCH_URLS.get(brand, []):
        host = _extract_domain_from_search_url_template(u)
        if host:
            seed_domains.append(host)

    normalized_seed_domains = _dedupe_keep_order([
        _normalize_official_domain(d) for d in seed_domains if d
    ])

    # Keep explicit known domains first, then add regional variants.
    expanded_domains: List[str] = list(normalized_seed_domains)
    for domain in normalized_seed_domains:
        expanded_domains.extend(_expand_regional_domains(domain)[1:])

    return _dedupe_keep_order(expanded_domains, limit=DISCOVERY_OFFICIAL_MAX_DOMAINS)


def _build_official_queries(brand: str, domain: str) -> List[str]:
    query_templates = list(_OFFICIAL_DISCOVERY_QUERIES)
    suffix = _get_domain_suffix(domain)
    query_templates.extend(_OFFICIAL_DISCOVERY_QUERIES_BY_SUFFIX.get(suffix, []))

    queries: List[str] = []
    seen: set = set()
    for tpl in query_templates:
        try:
            query = (tpl or "").format(brand=brand).strip()
        except Exception:
            continue
        if query and query not in seen:
            seen.add(query)
            queries.append(query)
    return queries


def _build_official_search_urls(brand: str) -> List[str]:
    urls = list(_OFFICIAL_SITE_SEARCH_URLS.get(brand, []))

    for domain in _build_official_brand_domains(brand):
        subdomain = domain.split(".")[0] if domain else ""
        allow_www = subdomain not in {
            "parts", "autoparts", "shop", "dreamshop", "store", "catalog", "service", "accessories"
        }
        for path_t in _OFFICIAL_SEARCH_PATH_TEMPLATES:
            urls.append(f"https://{domain}{path_t}")
            if allow_www:
                urls.append(f"https://www.{domain}{path_t}")

    return _dedupe_keep_order(urls, limit=DISCOVERY_OFFICIAL_MAX_URLS)


def _looks_like_part_context(text: str) -> bool:
    blob = (text or "").lower()
    if not blob:
        return False
    return any(k in blob for k in _OFFICIAL_PART_CONTEXT_KEYWORDS)


async def _discover_via_official_sites(brand: str, max_parts: int = 220) -> List[Dict]:
    """Discover parts using official manufacturer domains and official parts portals."""
    if not DISCOVERY_USE_OFFICIAL_SITES:
        return []

    urls = _build_official_search_urls(brand)
    if not urls:
        return []

    results: List[Dict] = []
    seen_nums: set = set()
    blocked_hits = 0
    requests_used = 0
    brand_domains = _build_official_brand_domains(brand)
    default_referer = f"https://{brand_domains[0]}" if brand_domains else ""

    for url_t in urls:
        if len(results) >= max_parts or requests_used >= DISCOVERY_OFFICIAL_MAX_REQUESTS:
            break

        url_domain = _extract_domain_from_search_url_template(url_t)
        referer = f"https://{url_domain}" if url_domain else default_referer

        for q_t in _build_official_queries(brand, url_domain):
            if len(results) >= max_parts or requests_used >= DISCOVERY_OFFICIAL_MAX_REQUESTS:
                break

            query = quote_plus(q_t)
            try:
                url = url_t.format(q=query)
            except Exception:
                url = url_t.replace("{q}", query)
            await asyncio.sleep(SCRAPE_REQUEST_DELAY + random.uniform(0, 0.8))

            resp = await _get(url, referer=referer, timeout=25, use_proxy=False)
            requests_used += 1
            if not resp:
                continue

            if resp.status_code != 200:
                if resp.status_code in (403, 429, 503):
                    blocked_hits += 1
                continue

            if _is_bot_block_page(resp.text[:1800]):
                blocked_hits += 1
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # Structured products from JSON-LD blocks.
            for obj in _iter_json_ld_objects(soup):
                otype = str(obj.get("@type", "")).lower()
                if "product" not in otype:
                    continue

                name = str(obj.get("name") or "").strip()
                pnums: List[str] = []
                for key in ("sku", "mpn", "productID", "productId"):
                    raw = obj.get(key)
                    if isinstance(raw, str):
                        pnums.extend(_extract_generic_part_numbers(raw, brand))
                if name:
                    pnums.extend(_extract_generic_part_numbers(name, brand))

                offers = obj.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else None
                price_currency = "USD"
                price_raw = 0.0
                if isinstance(offers, dict):
                    price_currency = str(offers.get("priceCurrency") or "USD")
                    price_raw = _coerce_float(offers.get("price") or offers.get("lowPrice") or 0)

                price_usd = round(_price_to_usd(price_raw, price_currency), 2)
                if price_usd > DISCOVERY_OFFICIAL_MAX_PRICE_USD:
                    price_usd = 0.0
                price_ils = round(price_usd * ILS_PER_USD, 2) if price_usd > 0 else 0.0

                if not _looks_like_part_context(name) and not pnums:
                    continue

                candidate_url = str(obj.get("url") or str(resp.url))
                for pn in pnums[:4]:
                    if pn in seen_nums:
                        continue
                    seen_nums.add(pn)
                    pname = name or f"{brand} {pn}"
                    results.append({
                        "source": "official_site",
                        "manufacturer": brand,
                        "part_brand": brand,
                        "part_number": pn,
                        "name": pname[:120],
                        "category": _guess_category_from_text(pname),
                        "part_type": classify_part_type(brand, pname, "official_site"),
                        "price_usd": price_usd,
                        "price_ils": price_ils,
                        "in_stock": True,
                        "url": candidate_url[:500],
                    })
                    if len(results) >= max_parts:
                        break
                if len(results) >= max_parts:
                    break

            if len(results) >= max_parts:
                break

            nodes = soup.select("article, .product, .product-item, .search-result, .search-result-item, li")
            if not nodes:
                nodes = soup.select("a[href]")

            for node in nodes[:200]:
                text_blob = node.get_text(" ", strip=True)
                if len(text_blob) < 14:
                    continue
                if not _looks_like_part_context(text_blob):
                    continue

                pnums = _extract_generic_part_numbers(text_blob, brand)
                if not pnums:
                    continue

                price_usd, price_ils = _extract_price_from_text(text_blob)
                link = node if node.name == "a" and node.get("href") else node.find("a", href=True)
                candidate_url = str(resp.url)
                if link and link.get("href"):
                    candidate_url = urljoin(str(resp.url), link.get("href"))

                for pn in pnums[:3]:
                    if pn in seen_nums:
                        continue
                    seen_nums.add(pn)
                    pname = re.sub(r"\s{2,}", " ", text_blob.replace(pn, "")).strip()[:120] or f"{brand} {pn}"
                    results.append({
                        "source": "official_site",
                        "manufacturer": brand,
                        "part_brand": brand,
                        "part_number": pn,
                        "name": pname,
                        "category": _guess_category_from_text(text_blob),
                        "part_type": classify_part_type(brand, pname, "official_site"),
                        "price_usd": price_usd,
                        "price_ils": price_ils,
                        "in_stock": True,
                        "url": candidate_url[:500],
                    })
                    if len(results) >= max_parts:
                        break

                if len(results) >= max_parts:
                    break

    if blocked_hits and not results:
        print(
            f"[Rex] official sites blocked or challenge pages for '{brand}' "
            f"(hits={blocked_hits}, urls={len(urls)})."
        )

    return results


def _is_bot_block_page(body: str) -> bool:
    """Best-effort detection of anti-bot challenge pages."""
    text_l = (body or "").lower()
    markers = (
        "just a moment",
        "pardon our interruption",
        "verify you are human",
        "are you a robot",
        "captcha",
        "cloudflare",
        "detected unusual traffic",
        "access denied",
    )
    return any(marker in text_l for marker in markers)


async def _discover_via_autodoc(brand: str, max_parts: int = 200) -> List[Dict]:
    """Scrape autodoc.eu JSON API to get real OEM + aftermarket parts for a brand."""
    slug = _AUTODOC_BRAND_SLUGS.get(brand, brand.lower().replace(" ", "-"))
    results: List[Dict] = []
    base_ref = f"https://www.autodoc.eu/{slug}"
    blocked = False

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
        if not resp:
            continue

        if resp.status_code != 200:
            if resp.status_code in (403, 429, 503):
                print(
                    f"[Rex] autodoc blocked for '{brand}' (HTTP {resp.status_code}) "
                    "- set SCRAPER_PROXY or run from a residential/prod IP."
                )
                blocked = True
                break
            continue

        content_type = (resp.headers.get("content-type") or "").lower()
        if "json" not in content_type and _is_bot_block_page(resp.text[:1200]):
            print(
                f"[Rex] autodoc anti-bot page for '{brand}' "
                "- discovery will return 0 from this source."
            )
            blocked = True
            break

        try:
            data  = resp.json()
            items = data.get("items") or data.get("results") or data.get("parts") or []
        except Exception:
            if _is_bot_block_page(resp.text[:1200]):
                print(
                    f"[Rex] autodoc anti-bot page for '{brand}' "
                    "- discovery will return 0 from this source."
                )
                blocked = True
                break
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

    if blocked and not results:
        print(f"[Rex] autodoc yielded 0 parts for '{brand}' due to source blocking.")
    return results


async def _discover_via_ebay(brand: str, max_parts: int = 150) -> List[Dict]:
    """Scrape eBay Motors listings and extract real OEM part numbers from titles."""
    results: List[Dict] = []
    seen_nums: set = set()
    blocked = False

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
        if not resp:
            continue

        if resp.status_code != 200:
            if resp.status_code in (403, 429, 503):
                print(
                    f"[Rex] eBay blocked for '{brand}' (HTTP {resp.status_code}) "
                    "- set SCRAPER_PROXY or run from a residential/prod IP."
                )
                blocked = True
                break
            continue

        if _is_bot_block_page(resp.text[:1200]):
            print(
                f"[Rex] eBay anti-bot page for '{brand}' "
                "- discovery will return 0 from this source."
            )
            blocked = True
            break

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

    if blocked and not results:
        print(f"[Rex] eBay yielded 0 parts for '{brand}' due to source blocking.")
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

        Sources tried in order:
            1) official manufacturer sites / portals
            2) autodoc.eu (optional fallback)
            3) eBay Motors (optional fallback)
    Every discovered part is classified as: OEM Original / Aftermarket / OEM Equivalent
    and written to parts_catalog + supplier_parts.

    Args:
        brands:  explicit list of brand names, or None to auto-detect thin brands
        target:  minimum parts per brand (default DISCOVERY_TARGET)
        per_run: max brands to process this run (default DISCOVERY_PER_RUN)
    """
    if not DISCOVERY_ENABLED:
        return {"status": "disabled", "message": "DISCOVERY_ENABLED=false"}

    from BACKEND_AUTH_SECURITY import get_redis
    from distributed_lock import acquire_lock
    _disc_lock = await acquire_lock(await get_redis(), "brand_discovery", ttl_seconds=86400)
    if not _disc_lock:
        return {"status": "skipped", "reason": "brand_discovery already running on another worker"}

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

    try:
        async with scraper_session_factory() as db:
            job_id: Optional[str] = None
            try:
                try:
                    job_id = await job_registry_start(db, "run_brand_discovery", ttl_seconds=int(DISCOVERY_INTERVAL_H * 3600))
                except Exception as exc:
                    print(f"[Rex] job_registry_start error: {exc}")
                    try:
                        await db.rollback()
                    except Exception:
                        pass

                # Get/create supplier id used for discovery inserts.
                supplier = (await db.execute(
                    text("SELECT id FROM suppliers WHERE name ILIKE '%AutoParts%' LIMIT 1")
                )).fetchone()
                if not supplier:
                    supplier = (await db.execute(
                        text("SELECT id FROM suppliers WHERE name = 'Official Manufacturer Sites' LIMIT 1")
                    )).fetchone()

                if not supplier:
                    created_supplier = (await db.execute(
                        text("""
                            INSERT INTO suppliers
                                (id, name, country, website, is_active, priority,
                                 reliability_score, rate_limit_per_minute,
                                 supports_express, is_manufacturer,
                                 created_at, updated_at)
                            VALUES
                                (:id, :name, :country, :website, TRUE, :priority,
                                 :reliability_score, :rate_limit_per_minute,
                                 FALSE, FALSE,
                                 NOW(), NOW())
                            ON CONFLICT (name) DO UPDATE
                                SET updated_at = NOW()
                            RETURNING id
                        """),
                        {
                            "id": str(uuid.uuid4()),
                            "name": "Official Manufacturer Sites",
                            "country": "Global",
                            "website": "https://www.toyota.com",
                            "priority": 50,
                            "reliability_score": 0.70,
                            "rate_limit_per_minute": 30,
                        },
                    )).fetchone()
                    await db.commit()
                    supplier = created_supplier

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
                    print("[Rex] All brands already meet the target - nothing to discover.")
                    report["status"] = "nothing_to_do"
                    if job_id:
                        await job_registry_finish(db, job_id, status="completed")
                    return report

                await fetch_ils_exchange_rate()
                print(f"[Rex] Brand discovery starting - {len(brands)} brands: {brands}")

                for brand in brands:
                    b_report: Dict[str, Any] = {"inserted": 0, "skipped_dup": 0, "sources": []}
                    print(f"\n[Rex] -- Discovering: {brand} --")

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

                    # Source 1 - official manufacturer sites
                    if DISCOVERY_USE_OFFICIAL_SITES:
                        try:
                            official_parts = await _discover_via_official_sites(brand, max_parts=min(need + 30, 260))
                            parts.extend(official_parts)
                            b_report["sources"].append(f"official:{len(official_parts)}")
                            print(f"[Rex]   official -> {len(official_parts)}")
                        except Exception as exc:
                            print(f"[Rex]   official error: {exc}")
                    else:
                        b_report["sources"].append("official:disabled")

                    await asyncio.sleep(1.5)

                    # Source 2/3 fallbacks - autodoc + eBay
                    if not DISCOVERY_OFFICIAL_ONLY:
                        try:
                            autodoc_parts = await _discover_via_autodoc(brand, max_parts=min(max(need - len(parts), 0) + 20, 300))
                            parts.extend(autodoc_parts)
                            b_report["sources"].append(f"autodoc:{len(autodoc_parts)}")
                            print(f"[Rex]   autodoc -> {len(autodoc_parts)}")
                        except Exception as exc:
                            print(f"[Rex]   autodoc error: {exc}")

                        await asyncio.sleep(2)

                        if len(parts) < need:
                            try:
                                ebay_parts = await _discover_via_ebay(brand, max_parts=min(need - len(parts) + 30, 150))
                                parts.extend(ebay_parts)
                                b_report["sources"].append(f"ebay:{len(ebay_parts)}")
                                print(f"[Rex]   eBay -> {len(ebay_parts)}")
                            except Exception as exc:
                                print(f"[Rex]   eBay error: {exc}")
                    else:
                        b_report["sources"].append("fallbacks:skipped_official_only")
                        print("[Rex]   official-only mode: skipped autodoc + eBay")

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
                            source_name = (part.get("source") or "").lower()
                            part_type_name = (part.get("part_type") or "").lower()
                            part_brand_name = part.get("part_brand") or part.get("manufacturer") or ""

                            is_oem_source = source_name in _OEM_DISCOVERY_SOURCES
                            apply_aftermarket = source_name == "autodoc" and "aftermarket" in part_type_name
                            resolved_aftermarket_brand_id: Optional[uuid.UUID] = None
                            resolved_aftermarket_tier: Optional[str] = None

                            if apply_aftermarket:
                                resolved_aftermarket_brand_id, resolved_aftermarket_tier = await resolve_aftermarket_brand(
                                    part_brand_name,
                                    db=db,
                                )

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
                                aftermarket_brand_id=resolved_aftermarket_brand_id,
                                aftermarket_tier=resolved_aftermarket_tier,
                                apply_aftermarket=apply_aftermarket,
                                is_oem_source=is_oem_source,
                                oem_number=sku_clean if is_oem_source else None,
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
                    print(f"[Rex]   SUCCESS {brand}: inserted={b_report['inserted']}  "
                          f"dup_skipped={b_report['skipped_dup']}")
                    await asyncio.sleep(3)

                if job_id:
                    try:
                        await job_registry_finish(db, job_id, status="completed")
                    except Exception as exc:
                        print(f"[Rex] job_registry_finish error: {exc}")

            except Exception as exc:
                if job_id:
                    try:
                        await job_registry_finish(db, job_id, status="dead", error_message=str(exc)[:500])
                    except Exception:
                        pass
                raise

    finally:
        try:
            await _disc_lock.release()
        except Exception as lock_exc:
            print(f"[Rex] lock release warning: {lock_exc}")

    # Immediately normalise newly discovered parts — don't wait for the 6h timer
    if report["total_inserted"] > 0:
        try:
            from db_update_agent import run_all_tasks as _run_norm
            async with scraper_session_factory() as _norm_db:
                await _run_norm(_norm_db)
            print("[Rex] ✅ Post-discovery normalization complete")
        except Exception as _e:
            print(f"[Rex] ⚠ Post-discovery normalization failed: {_e}")

    # Write job-level catalog_versions audit row
    try:
        async with scraper_session_factory() as _cv_db:
            await _cv_db.execute(
                text("""
                    INSERT INTO catalog_versions
                        (id, version_tag, description, parts_added, parts_updated,
                         source, status, created_at)
                    VALUES
                        (gen_random_uuid(), :vtag, :desc, :added, 0,
                         'scraper_brand_discovery', 'completed', NOW())
                    ON CONFLICT (version_tag) DO NOTHING
                """),
                {
                    "vtag": f"discovery-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
                    "desc": (
                        f"Brand discovery: {report['total_inserted']} parts added "
                        f"across {len(report['brands'])} brands"
                    ),
                    "added": report["total_inserted"],
                },
            )
            await _cv_db.commit()
    except Exception:
        pass

    report["finished_at"] = datetime.utcnow().isoformat()
    print(f"\n[Rex] Brand discovery done - total inserted: {report['total_inserted']}")
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

    from BACKEND_AUTH_SECURITY import get_redis
    from distributed_lock import acquire_lock
    _cycle_lock = await acquire_lock(await get_redis(), "scraper_cycle", ttl_seconds=7200)
    if not _cycle_lock:
        return {"status": "skipped", "reason": "scraper_cycle already running on another worker"}

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
        job_id = None
        try:
            try:
                job_id = await job_registry_start(db, "run_scraper_cycle", ttl_seconds=int(SCRAPE_INTERVAL_H * 3600))
            except Exception as exc:
                print(f"[Scraper] job_registry_start error: {exc}")
                try:
                    await db.rollback()
                except Exception:
                    pass

            # Pull oldest-checked supplier_parts joined to parts_catalog
            rows = (await db.execute(
                text("""
                    SELECT
                        sp.id            AS supplier_part_id,
                        sp.supplier_id,
                        sp.price_ils,
                        sp.availability,
                        s.name           AS supplier_name,
                        s.rate_limit_per_minute,
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
                        rate_limit_per_minute=row.rate_limit_per_minute,
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

            # Write job-level catalog_versions audit row
            try:
                await db.execute(
                    text("""
                        INSERT INTO catalog_versions
                            (id, version_tag, description, parts_added, parts_updated,
                             source, status, created_at)
                        VALUES
                            (gen_random_uuid(), :vtag, :desc, 0, :updated,
                             'scraper_price_sync', 'completed', NOW())
                        ON CONFLICT (version_tag) DO NOTHING
                    """),
                    {
                        "vtag": f"price-sync-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
                        "desc": (
                            f"Price sync: {report['prices_updated']} updated, "
                            f"{report['availability_changes']} availability changes"
                        ),
                        "updated": report["prices_updated"],
                    },
                )
                await db.commit()
            except Exception:
                pass

            if job_id:
                await job_registry_finish(db, job_id, status="completed")

        except Exception as exc:
            if job_id:
                try:
                    await job_registry_finish(db, job_id, status="dead", error_message=str(exc)[:500])
                except Exception:
                    pass
            raise

    print(
        f"[Scraper] ✅ Cycle done — "
        f"checked={report['parts_checked']:,}  "
        f"updated={report['prices_updated']:,}  "
        f"errors={report['errors']}  "
        f"elapsed={report['elapsed_s']}s"
    )
    await _cycle_lock.release()
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
    from resilience import log_job_failure
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
                try:
                    _last_discovery_report = await run_brand_discovery()
                except Exception as exc:
                    error_msg = str(exc)[:500]
                    print(f"[Scraper] Brand discovery error: {error_msg}")
                    # Log failure to DLQ (Gap 2b)
                    try:
                        from BACKEND_DATABASE_MODELS import pii_session_factory
                        async with pii_session_factory() as pii_db:
                            await log_job_failure(
                                pii_db,
                                job_name="run_brand_discovery",
                                error=error_msg,
                                payload={},
                                attempts=1,
                            )
                    except Exception as dlq_err:
                        print(f"[Scraper] Failed to log brand_discovery to DLQ: {dlq_err}")
                
                last_discovery_at = datetime.utcnow()
                await asyncio.sleep(30)

            # Job 1 — Price Sync (every SCRAPE_INTERVAL_H hours)
            try:
                _last_run_report = await run_scraper_cycle()
            except Exception as exc:
                error_msg = str(exc)[:500]
                print(f"[Scraper] Scraper cycle error: {error_msg}")
                # Log failure to DLQ (Gap 2b)
                try:
                    from BACKEND_DATABASE_MODELS import pii_session_factory
                    async with pii_session_factory() as pii_db:
                        await log_job_failure(
                            pii_db,
                            job_name="run_scraper_cycle",
                            error=error_msg,
                            payload={},
                            attempts=1,
                        )
                except Exception as dlq_err:
                    print(f"[Scraper] Failed to log scraper_cycle to DLQ: {dlq_err}")
        
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
        "discovery_use_official_sites": DISCOVERY_USE_OFFICIAL_SITES,
        "discovery_official_only": DISCOVERY_OFFICIAL_ONLY,
        "discovery_official_max_requests": DISCOVERY_OFFICIAL_MAX_REQUESTS,
        "discovery_official_max_domains": DISCOVERY_OFFICIAL_MAX_DOMAINS,
        "discovery_official_max_urls": DISCOVERY_OFFICIAL_MAX_URLS,
        "discovery_official_suffixes": DISCOVERY_OFFICIAL_SUFFIXES,
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
