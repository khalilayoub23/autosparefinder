#!/usr/bin/env python3
# ⚠️  BROWSER TOOL REQUIRED — DO NOT RUN HTTP REQUESTS FROM SERVER IP
# The server IP (94.130.150.23) is blocked by Cloudflare and anti-bot systems.
# All external HTTP extraction must be done via the browser tool (Playwright / run_playwright_code).
# Pattern: (1) Extract with browser tool → save JSON, (2) Import JSON with this script.
# See claude.md § Web Scraping Rules.
"""
saicmg_scraper.py — Multi-brand catalog scraper for saicmgautoparts.com
Covers: Tesla (Model 3 & Y), Chery, Jetour, MG additional models

Runs inside autospare_backend container:
  python3 /app/saicmg_scraper.py [--dry-run] [--brand Tesla|Chery|Jetour|MG]

Data notes:
- No prices on this site (wholesale inquiry-only)
- OEM numbers embedded in product titles / URLs
- Tesla products are aftermarket body parts with NO standard OEM numbers
- confidence_score = 0.50 (scraped web data per claude.md rules)
"""

import re
import sys
import os
import uuid
import time
import hashlib
import logging
import json
import argparse

import requests
from bs4 import BeautifulSoup
import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="[saicmg] %(levelname)s %(message)s",
)
log = logging.getLogger("saicmg")

# ── DB ─────────────────────────────────────────────────────────────────────────
_raw_url = os.environ.get(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare",
)
DSN = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

# ── Brand IDs (pre-verified in car_brands) ────────────────────────────────────
BRAND_IDS = {
    "Tesla": "96b02c99-9df4-4ff1-b72f-6ed1501d8b70",
    "Chery":  "3516329c-29a0-4bf8-aea8-4bc57979561a",
    "Jetour": "28abc228-69f1-4d52-aa69-4b7133dcac58",
    "Omoda":  "53a3f1fd-d516-48c4-8237-a34ec805892b",
    "MG":     "341be223-5852-4f29-bd96-085ef2c5d07b",
    "Maxus":  "a1c230df-849d-46ca-a80c-58478845ba24",
}

# ── Scrape targets: (listing_url, brand, vehicle_model, year_from, year_to) ───
SCRAPE_TARGETS = [
    # --- TESLA ---
    ("https://www.saicmgautoparts.com/saic-tesla-auto-parts-model-3/",  "Tesla", "Model 3", 2019, 2025),
    ("https://www.saicmgautoparts.com/saic-tesla-auto-parts-model-y/",  "Tesla", "Model Y", 2020, 2025),
    # --- CHERY ---
    ("https://www.saicmgautoparts.com/chery-arrizo-5-auto-parts/",      "Chery", "Arrizo 5",   2016, 2024),
    ("https://www.saicmgautoparts.com/chery-arrizo-8-auto-parts/",      "Chery", "Arrizo 8",   2021, 2025),
    ("https://www.saicmgautoparts.com/chery-tiggo4-pro-auto-parts/",    "Chery", "Tiggo 4 Pro",2021, 2025),
    ("https://www.saicmgautoparts.com/chery-tiggo7pro-auto-parts/",     "Chery", "Tiggo 7 Pro",2021, 2025),
    ("https://www.saicmgautoparts.com/chery-tiggo8pro-auto-parts/",     "Chery", "Tiggo 8 Pro",2021, 2025),
    ("https://www.saicmgautoparts.com/chery-tiggo8plus-auto-parts/",    "Chery", "Tiggo 8 Plus",2020, 2025),
    # --- JETOUR ---
    ("https://www.saicmgautoparts.com/jetour-x70-auto-parts/",          "Jetour", "X70",     2019, 2024),
    ("https://www.saicmgautoparts.com/jetour-x70plus-auto-parts/",      "Jetour", "X70 Plus", 2021, 2024),
    ("https://www.saicmgautoparts.com/jetour-x90plus-auto-parts/",      "Jetour", "X90 Plus", 2022, 2025),
    ("https://www.saicmgautoparts.com/jetour-x95-auto-parts/",          "Jetour", "X95",      2023, 2025),
    # --- MG ADDITIONAL MODELS (not in Lubinski 2022 Excel) ---
    ("https://www.saicmgautoparts.com/mg-hs-auto-parts/",               "MG", "HS",       2019, 2025),
    ("https://www.saicmgautoparts.com/saic-mg-4-ev-auto-parts/",        "MG", "MG4 EV",   2022, 2025),
    ("https://www.saicmgautoparts.com/mg-gs-auto-parts/",               "MG", "GS",       2016, 2022),
    ("https://www.saicmgautoparts.com/mg-rx5-auto-parts/",              "MG", "RX5",      2017, 2023),
    ("https://www.saicmgautoparts.com/mg-750-auto-parts-2/",            "MG", "MG 750",   2012, 2018),
    ("https://www.saicmgautoparts.com/saic-mg-gt-auto-parts/",          "MG", "GT",       2015, 2021),
    ("https://www.saicmgautoparts.com/mg-hs-24-auto-parts/",            "MG", "HS 2024",  2024, 2025),
    # --- MAXUS ---
    ("https://www.saicmgautoparts.com/saic-maxus-auto-parts/", "Maxus", "MAXUS",    2018, 2025),
    ("https://www.saicmgautoparts.com/maxus-d90-auto-parts/",  "Maxus", "D90",      2018, 2024),
    ("https://www.saicmgautoparts.com/maxus-g10-auto-parts/",  "Maxus", "G10",      2017, 2023),
    ("https://www.saicmgautoparts.com/maxus-g50-auto-parts/",  "Maxus", "G50",      2019, 2025),
    ("https://www.saicmgautoparts.com/maxus-v80-auto-parts/",  "Maxus", "V80",      2017, 2024),
    ("https://www.saicmgautoparts.com/maxus-t60-auto-parts/",  "Maxus", "T60",      2017, 2023),
    # --- OMODA ---
    ("https://www.saicmgautoparts.com/chery-omoda-auto-parts/", "Omoda", "Omoda 5", 2022, 2025),
    # --- JETOUR ADDITIONAL ---
    ("https://www.saicmgautoparts.com/jetour-dasheng-auto-parts/",  "Jetour", "Dasheng",  2023, 2025),
    ("https://www.saicmgautoparts.com/jetour-traveler-auto-parts/", "Jetour", "Traveler", 2023, 2025),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# ── English → category slug ────────────────────────────────────────────────────
EN_CATEGORY_KEYWORDS: list[tuple[frozenset[str], str]] = [
    (frozenset({"brake pad", "brake disc", "rotor", "caliper", "master cylinder",
                "brake", "braking"}), "brakes"),
    (frozenset({"shock absorber", "strut", "spring", "control arm", "tie rod",
                "stabilizer", "knuckle", "swing arm", "suspension",
                "cushion", "connecting rod", "ball pin", "ball joint",
                "lower arm", "upper arm", "triangular arm"}), "suspension-steering"),
    (frozenset({"headlight", "headlamp", "tail light", "taillight",
                "fog lamp", "fog light", "daytime running", "drl",
                "turn signal", "blinker", "lamp assembly", "brake light",
                "high brake", "light", "lamp"}), "lighting"),
    (frozenset({"radiator", "water pump", "thermostat", "cooling fan",
                "intercooler", "fan assembly", "condenser"}), "cooling"),
    (frozenset({"oil filter", "air filter", "fuel filter", "cabin filter",
                "air conditioner filter", "filter"}), "filters"),
    (frozenset({"timing chain", "timing belt", "timing sprocket", "belt",
                "chain", "tensioner", "idler", "sprocket"}), "belts-chains"),
    (frozenset({"engine", "piston", "camshaft", "crankshaft", "valve",
                "cylinder", "oil pump", "oil pan", "oil seal", "gasket",
                "turbocharger", "turbine", "alternator", "starter",
                "ignition coil", "spark plug", "injector", "throttle",
                "intake manifold", "intake", "oil", "torsion"}), "engine"),
    (frozenset({"gearbox", "transmission", "differential"}), "gearbox"),
    (frozenset({"clutch", "cv joint", "driveshaft", "axle",
                "universal joint", "bearing hub"}), "clutch-drivetrain"),
    (frozenset({"mirror", "bumper", "fender", "door", "hood", "trunk",
                "body", "grille", "spoiler", "wing", "beam", "panel",
                "cover", "lining", "liner", "frame", "bracket",
                "deflector", "eyebrow"}), "body-exterior"),
    (frozenset({"sensor", "module", "control unit", "ecu", "relay",
                "wiring", "abs", "radar", "height sensor",
                "phase sensor", "oxygen sensor", "airbag", "balloonet"}), "electrical-sensors"),
    (frozenset({"fuel pump", "fuel", "exhaust pipe", "exhaust manifold",
                "pcv valve", "oxygen"}), "fuel-air"),
    (frozenset({"seat", "dashboard", "console", "seat belt",
                "interior", "trim", "clamp", "switch", "trunk switch",
                "door trim", "door handle", "steering wheel",
                "astern radar", "battery", "seat bag"}), "interior-comfort"),
    (frozenset({"wiper", "washer"}), "wipers-washers"),
    (frozenset({"wheel bearing", "hub", "rim", "steel ring",
                "wheel pump"}), "wheels-bearings"),
    (frozenset({"exhaust", "muffler", "catalytic",
                "exhaust pipe decoration"}), "exhaust"),
    (frozenset({"air conditioning", "ac compressor",
                "evaporator", "hvac", "warm air tank"}), "air-conditioning-heating"),
]


def guess_category_en(text: str) -> str:
    """Guess category from English part name."""
    t = text.lower()
    for keywords, cat in EN_CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in t:
                return cat
    # Try the backend categories.py if available
    try:
        sys.path.insert(0, "/app")
        from categories import guess_category_by_text  # type: ignore
        result = guess_category_by_text(text)
        if result:
            return result
    except Exception:
        pass
    return "service-general"


# ── OEM extraction ─────────────────────────────────────────────────────────────
# OEM prefix code: 2–8 alphanumeric chars, must have at least one letter AND one digit
# Matches: E4G16, T15, J20, F18, J68, QR523, 481H, E4T15B
# Rejects: DOOR, TAIL, RH, LH, DY (letters only), 1307010 (digits only)
OEM_PREFIX_RE = re.compile(
    r"^(?=[A-Z0-9]{2,8}$)(?=.*[A-Z])(?=.*\d)[A-Z0-9]+$"
)

# Two title formats on this site (Chery/Jetour):
#
# Format B (old): "Brand Model Auto parts {Partcode} supplier wholesale..."
#   e.g. "Chery Tiggo4 pro Auto parts Water-pump-assembly-E4G16-1307010 supplier..."
#
# Format C (new): "Brand Model series new auto parts Auto {PARTCODE} Parts supplier..."
#   e.g. "Chery Tiggo 7PRO series new auto parts Auto LWR-RADIATOR-CROSSBEAM-T15-5300810-DY Parts..."
#
# Both: strip the brand/model prefix and the trailing "supplier/Parts supplier" garbage,
# then parse the clean PARTCODE for OEM + name.

_BRAND_PREFIX_RE = re.compile(
    # non-greedy: match "Chery/Jetour/SAIC anything auto parts [Auto] "
    r"^(?:Chery|Jetour|SAIC)\s+.+?auto\s+parts\s+(?:Auto\s+)?",
    re.IGNORECASE,
)
_SUPPLIER_SUFFIX_RE = re.compile(
    # stop at first occurrence of "supplier" or "Parts supplier" (with or without wholesale)
    r"\s+(?:parts\s+)?supplier\b.*$",
    re.IGNORECASE,
)


def _parse_partcode(partcode: str) -> tuple[str, str]:
    """
    Extract (oem, name) from a clean partcode string like:
      Water-pump-assembly-E4G16-1307010
      LWR-RADIATOR-CROSSBEAM-T15-5300810-DY
      TAIL-DOOR-552000148AADYJ
      Warm-air-tank-301000819AA
      front fog light 605000852-53AA
    """
    tokens = partcode.split("-")

    # 1. Alphanumeric OEM prefix: find token matching [A-Z]+[0-9]+ (e.g. E4G16, T15)
    #    followed by a token starting with 4+ digits
    for i, tok in enumerate(tokens):
        t = tok.strip()
        if OEM_PREFIX_RE.match(t) and i > 0:
            if i + 1 < len(tokens) and re.match(r"^\d{4,}", tokens[i + 1].strip()):
                name = " ".join(
                    t.strip() for t in tokens[:i]
                    if t.strip() not in ("L", "R", "LR", "l", "r")
                ).strip()
                oem_tokens = list(tokens[i:])
                # Drop trailing pure-letter direction/variant codes (DY, LH, RH, etc.)
                while oem_tokens and re.match(r"^[A-Z]{1,3}$", oem_tokens[-1].strip()):
                    oem_tokens.pop()
                oem = "-".join(t.strip() for t in oem_tokens).upper()
                # Keep only first OEM if L/R pair (J60-4435010-J60-4435020 → J60-4435010)
                m = re.match(r"([A-Z0-9]+-\d+[A-Z0-9]*)-[A-Z]", oem)
                if m:
                    oem = m.group(1)
                return oem, name or partcode.replace("-", " ")

    # 2. OEM at end of partcode: find the last occurrence of a digit-heavy token
    #    Covers:
    #      "TAIL-DOOR-552000148AADYJ"        → last token is OEM
    #      "front fog light 605000852-53AA"  → last two tokens form OEM (digits-suffix)
    last = tokens[-1].strip()
    second_last = tokens[-2].strip() if len(tokens) >= 2 else ""

    # Case 2a: last token is 6+ digits optionally followed by letters
    if re.match(r"^\d{6,}[A-Z]*$", last):
        name = " ".join(t.strip() for t in tokens[:-1]
                        if t.strip() not in ("L", "R", "LR")).strip()
        return last, name or last

    # Case 2b: second-to-last ends with 6+ digits, last is short alphanumeric suffix
    #   e.g. second_last "605000852", last "53AA" or "1AA"
    m2 = re.search(r"(\d{6,})$", second_last)
    if m2 and re.match(r"^[A-Z0-9]{1,6}$", last):
        # Combine: everything in second_last from the digits start, plus "-" + last
        prefix_end = m2.start()
        name_part = " ".join(t.strip() for t in tokens[:-2]
                             if t.strip() not in ("L", "R", "LR")).strip()
        name_part_extra = second_last[:prefix_end].strip()
        name = (name_part + " " + name_part_extra).strip()
        oem = m2.group(1) + "-" + last.upper()
        return oem, name or partcode.replace("-", " ")

    # 3. Fallback
    return "", partcode.replace("-", " ").strip()


def extract_oem_and_name(raw_title: str, brand: str) -> tuple[str, str]:
    """
    Split a product title into (oem_number, english_name).
    Returns ('', cleaned_name) if no OEM found.
    """
    # --- Tesla: no OEM numbers ---
    if brand == "Tesla":
        name = re.sub(
            r"^SAIC TESLA MODEL\s+\d+\s+Y?\s+European car PARTS\s*",
            "", raw_title, flags=re.I,
        ).strip()
        name = re.sub(
            r"\s*(exterior system|interior system|chassis system|power system)"
            r"\s*(body kits\s*)?MG CATALOG.*$",
            "", name, flags=re.I,
        ).strip()
        name = re.sub(r"\s*MG CATALOG.*$", "", name, flags=re.I).strip()
        name = name.strip(" –-").strip()
        return "", name or raw_title

    # --- MG: numeric OEM embedded in title ---
    if brand == "MG":
        # Clean up long-form titles from div.global_product pages:
        #   "SAIC MG GT AUTO Parts <name>-<OEM> zhuo meng China..."
        #   "SAIC MG 750 NEW AUTO PARTS CAR SPARE AUTO <name>-<OEM> PARTS SUPPLIER..."
        #   "SAIC MG350/360/550/750 AUTO PARTS CAR SPARE <name>-<OEM> ..."
        title_clean = re.sub(
            r"^SAIC\s+MG\S*(?:/\S+)*\s+(?:NEW\s+)?AUTO\s+PARTS?(?:\s+CAR\s+SPARE(?:\s+AUTO)?)?\s+",
            "", raw_title, flags=re.I,
        ).strip()
        title_clean = re.sub(r"\s+zhuo\s+meng\s+China.*$", "", title_clean, flags=re.I).strip()
        title_clean = re.sub(r"\s+(?:parts\s+)?supplier\b.*$", "", title_clean, flags=re.I).strip()
        search_in = title_clean or raw_title
        m = re.search(r"\b(\d{7,10})\b", search_in)
        if m:
            oem = m.group(1)
            name = search_in.replace(oem, "").strip(" -–_").strip()
            name = re.sub(r"\s{2,}", " ", name).strip()
            return oem, name or raw_title

    # --- Chery / Jetour: detect and strip brand prefix + supplier suffix ---
    if brand in ("Chery", "Jetour"):
        # Strip "Brand Model [series new] Auto parts [Auto] " prefix
        stripped = _BRAND_PREFIX_RE.sub("", raw_title).strip()
        # Strip " [Parts] supplier wholesale ..." suffix
        stripped = _SUPPLIER_SUFFIX_RE.sub("", stripped).strip()
        if stripped and stripped != raw_title:
            # Successfully isolated the partcode
            return _parse_partcode(stripped)

    # --- Generic dash-separated format: "Part-Name-Words-OEM_PREFIX-digits" ---
    return _parse_partcode(raw_title)


def title_to_sku(brand: str, oem: str, name: str) -> str:
    """Generate a unique, reproducible SKU."""
    brand_short = brand[:4].upper()
    if oem:
        return f"{brand_short}-{oem.upper()}"
    # For parts without OEM (Tesla), hash the FULL cleaned name for uniqueness
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    h = hashlib.md5(slug.encode()).hexdigest()[:8].upper()
    return f"{brand_short}-SCRAPE-{h}"


# ── HTTP helpers ───────────────────────────────────────────────────────────────
def fetch_html(url: str, retries: int = 3) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 404:
                log.debug("404: %s", url)
                return None
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            log.warning("Attempt %d/%d failed for %s: %s", attempt, retries, url, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None


def get_next_page_url(soup: BeautifulSoup, current_url: str) -> str | None:
    """Extract the 'next page' URL from pagination, or None."""
    # Standard WooCommerce / WordPress pagination
    nxt = soup.select_one("a.next.page-numbers, .pagination a.next, a[aria-label='Next']")
    if nxt and nxt.get("href"):
        return nxt["href"]
    # Fallback: look for "Next >" text link
    for a in soup.find_all("a"):
        txt = a.get_text(strip=True).lower()
        if txt in ("next", "next >", "next»", ">", "»"):
            href = a.get("href", "")
            if href and href.startswith("http"):
                return href
    return None


def scrape_listing_pages(base_url: str) -> list[dict]:
    """
    Fetch a listing page and collect all product dicts.
    The site uses li.product_item inside .items_list — all products on one page,
    no standard WooCommerce pagination present.
    Returns list of {title, url, image}.
    """
    products: list[dict] = []
    url = base_url
    page_num = 1

    while url:
        log.info("Fetching page %d: %s", page_num, url)
        html = fetch_html(url)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")

        # Site uses li.product_item (custom theme, not WooCommerce)
        # Title is in img[alt], URL in a[href], image in img[src]
        items = soup.find_all("li", class_="product_item")
        use_h2_title = False

        if not items:
            # Fallback for MG GT / MG 750 pages that use div.global_product layout
            items = soup.select("div.global_product")
            if items:
                use_h2_title = True
                log.info("  Using div.global_product fallback (%d found)", len(items))
            else:
                log.warning("No product items found on %s", url)
                break

        page_count = 0
        for item in items:
            # Title — prefer h2 text for global_product layout, img alt for product_item
            img_el = item.find("img")
            link_el = item.find("a", href=True)

            title = ""
            if use_h2_title:
                h2 = item.find("h2")
                if h2:
                    title = h2.get_text(" ", strip=True)
            if not title and img_el:
                title = img_el.get("alt", "").strip()
            if not title and link_el:
                title = link_el.get("title", "").strip()
            if not title:
                continue

            prod_url = link_el.get("href", "").strip() if link_el else ""

            img_url = ""
            if img_el:
                img_url = (
                    img_el.get("data-src")
                    or img_el.get("data-lazy-src")
                    or img_el.get("src")
                    or ""
                )
                if "placeholder" in img_url.lower():
                    img_url = ""

            products.append({"title": title, "url": prod_url, "image": img_url})
            page_count += 1

        log.info("  Found %d products on page %d", page_count, page_num)
        if page_count == 0:
            break

        # Check for pagination (some pages may have it)
        next_url = get_next_page_url(soup, url)
        if next_url and next_url != url:
            url = next_url
            page_num += 1
            time.sleep(1.5)
        else:
            break

    log.info("Total collected from %s: %d", base_url, len(products))
    return products


# ── DB operations ──────────────────────────────────────────────────────────────
def ensure_jetour_brand(conn) -> str:
    """Create Jetour brand if missing, return its UUID."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM car_brands WHERE name = 'Jetour'")
        row = cur.fetchone()
        if row:
            return row[0]
        brand_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO car_brands (id, name, name_he, created_at, updated_at)
            VALUES (%s, 'Jetour', 'ג׳טור', NOW(), NOW())
            ON CONFLICT (name) DO UPDATE SET updated_at = NOW()
            RETURNING id
            """,
            (brand_id,),
        )
        row = cur.fetchone()
        conn.commit()
        actual_id = row[0]
        log.info("Created Jetour brand with id %s", actual_id)
        return actual_id


UPSERT_PART_SQL = """
INSERT INTO parts_catalog (
    id, sku, name, name_he, category,
    manufacturer, manufacturer_id,
    oem_number, base_price, importer_price_ils, online_price_ils,
    part_type, part_condition,
    is_active, is_safety_critical, needs_oem_lookup, master_enriched,
    specifications, updated_at
) VALUES (
    %(id)s, %(sku)s, %(name)s, %(name_he)s, %(category)s,
    %(manufacturer)s, %(manufacturer_id)s,
    %(oem_number)s, NULL, NULL, NULL,
    %(part_type)s, 'New',
    TRUE, FALSE, FALSE, FALSE,
    %(specifications)s::jsonb, NOW()
)
ON CONFLICT (sku) DO UPDATE SET
    name         = EXCLUDED.name,
    category     = EXCLUDED.category,
    oem_number   = COALESCE(EXCLUDED.oem_number, parts_catalog.oem_number),
    specifications = parts_catalog.specifications || EXCLUDED.specifications,
    is_active    = TRUE,
    updated_at   = NOW()
RETURNING id, sku
"""

UPSERT_FITMENT_SQL = """
INSERT INTO part_vehicle_fitment (
    id, part_id, manufacturer, manufacturer_id, model,
    year_from, year_to, notes, created_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, NOW()
)
ON CONFLICT DO NOTHING
"""


def upsert_batch(conn, parts: list[dict]) -> int:
    """Upsert up to 25 parts in one transaction. Returns count inserted/updated."""
    inserted = 0
    batch_size = 25
    for i in range(0, len(parts), batch_size):
        batch = parts[i : i + batch_size]
        with conn.cursor() as cur:
            for p in batch:
                cur.execute(UPSERT_PART_SQL, p)
                if cur.rowcount:
                    inserted += 1
        conn.commit()
    return inserted


def upsert_fitment_batch(conn, fitment_rows: list[tuple]) -> int:
    """Upsert fitment rows. Each row: (id, part_id, manufacturer, manufacturer_id, model, year_from, year_to, notes)"""
    inserted = 0
    batch_size = 25
    for i in range(0, len(fitment_rows), batch_size):
        batch = fitment_rows[i : i + batch_size]
        with conn.cursor() as cur:
            for row in batch:
                cur.execute(UPSERT_FITMENT_SQL, row)
                if cur.rowcount:
                    inserted += 1
        conn.commit()
    return inserted


# ── Main import logic ──────────────────────────────────────────────────────────
def process_target(
    conn,
    listing_url: str,
    brand: str,
    vehicle_model: str,
    year_from: int,
    year_to: int,
    brand_ids: dict[str, str],
    dry_run: bool,
) -> dict:
    """
    Scrape one model category, deduplicate against existing SKUs, and upsert.
    Returns stats dict.
    """
    brand_id = brand_ids[brand]
    raw_products = scrape_listing_pages(listing_url)

    if not raw_products:
        return {"scraped": 0, "inserted": 0, "fitment": 0, "skipped": 0}

    # Deduplicate within this batch by SKU
    seen_sku: dict[str, dict] = {}
    for prod in raw_products:
        title = prod["title"]
        if not title:
            continue

        oem, part_name = extract_oem_and_name(title, brand)
        if not part_name:
            continue

        sku = title_to_sku(brand, oem, part_name)
        if sku in seen_sku:
            continue   # already have this part from another listing on same page

        category = guess_category_en(part_name)

        # Tesla: aftermarket body/interior parts
        # Chery/Jetour/MG: OEM equivalent or aftermarket
        if brand == "Tesla":
            part_type = "Aftermarket"
        elif oem:
            part_type = "OE Equivalent"
        else:
            part_type = "Aftermarket"

        image_url = prod.get("image", "") or ""
        # Normalise image URL (sometimes thumbnail, sometimes full-size)
        image_url = re.sub(r"-\d+x\d+(\.\w+)$", r"\1", image_url)

        seen_sku[sku] = {
            "id": str(uuid.uuid4()),
            "sku": sku,
            "name": part_name,
            "name_he": None,
            "category": category,
            "manufacturer": brand,
            "manufacturer_id": brand_id,
            "oem_number": oem if oem else None,
            "part_type": part_type,
            "specifications": json.dumps({
                "source": "saicmgautoparts.com",
                "vehicle_model": vehicle_model,
                "scraped_title": title,
                "image_url": image_url or None,
                "source_url": prod.get("url") or None,
            }),
        }

    parts = list(seen_sku.values())
    log.info(
        "[%s %s] scraped=%d unique=%d",
        brand, vehicle_model, len(raw_products), len(parts),
    )

    if dry_run:
        log.info("[dry-run] Would insert %d parts", len(parts))
        return {
            "scraped": len(raw_products),
            "inserted": 0,
            "fitment": 0,
            "skipped": len(raw_products) - len(parts),
        }

    inserted = upsert_batch(conn, parts)

    # Resolve actual DB IDs (ON CONFLICT DO UPDATE returns existing ID, not the new one)
    sku_list = [p["sku"] for p in parts]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, sku FROM parts_catalog WHERE sku = ANY(%s)",
            (sku_list,),
        )
        sku_to_id = {row[1]: str(row[0]) for row in cur.fetchall()}
    for p in parts:
        p["id"] = sku_to_id.get(p["sku"], p["id"])

    # Build fitment rows
    fitment_rows = []
    for p in parts:
        fitment_rows.append((
            str(uuid.uuid4()),
            p["id"],
            brand,
            brand_id,
            vehicle_model,
            year_from,
            year_to,
            "Source: saicmgautoparts.com",
        ))

    # Special case: Tesla products marked "MODEL 3 Y" fit both models
    if brand == "Tesla" and "model" in vehicle_model.lower():
        other_model = "Model Y" if vehicle_model == "Model 3" else "Model 3"
        other_year_from = 2020 if other_model == "Model Y" else 2019
        for p in parts:
            fitment_rows.append((
                str(uuid.uuid4()),
                p["id"],
                brand,
                brand_id,
                other_model,
                other_year_from,
                2025,
                "Source: saicmgautoparts.com (shared Model 3/Y part)",
            ))

    fitment_inserted = upsert_fitment_batch(conn, fitment_rows)

    return {
        "scraped": len(raw_products),
        "inserted": inserted,
        "fitment": fitment_inserted,
        "skipped": len(raw_products) - len(parts),
    }


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="saicmgautoparts.com multi-brand scraper")
    parser.add_argument("--dry-run", action="store_true", help="Scrape only, do not write to DB")
    parser.add_argument("--brand", default="all",
                        help="Brand to scrape: Tesla|Chery|Jetour|MG|Maxus|Omoda|all")
    args = parser.parse_args()

    # Filter targets
    target_brand = args.brand.lower()
    if target_brand == "all":
        targets = SCRAPE_TARGETS
    else:
        targets = [t for t in SCRAPE_TARGETS if t[1].lower() == target_brand]
    if not targets:
        log.error("No matching targets for brand=%s", args.brand)
        sys.exit(1)

    conn = psycopg2.connect(DSN)
    conn.autocommit = False

    brand_ids = dict(BRAND_IDS)


    totals = {"scraped": 0, "inserted": 0, "fitment": 0, "skipped": 0}

    for (listing_url, brand, vehicle_model, year_from, year_to) in targets:
        if brand not in brand_ids or not brand_ids[brand]:
            log.error("Brand %s has no ID — skipping", brand)
            continue

        log.info("=" * 60)
        log.info("Processing: %s %s", brand, vehicle_model)
        log.info("URL: %s", listing_url)

        try:
            stats = process_target(
                conn, listing_url, brand, vehicle_model,
                year_from, year_to, brand_ids, args.dry_run,
            )
            for k in totals:
                totals[k] += stats.get(k, 0)
            log.info(
                "  → scraped=%d inserted=%d fitment=%d skipped=%d",
                stats["scraped"], stats["inserted"],
                stats["fitment"], stats["skipped"],
            )
        except Exception as e:
            log.exception("Error processing %s %s: %s", brand, vehicle_model, e)
            conn.rollback()

        time.sleep(2)  # polite delay between brand categories

    conn.close()

    log.info("=" * 60)
    log.info("TOTAL: scraped=%d inserted/updated=%d fitment=%d skipped=%d",
             totals["scraped"], totals["inserted"], totals["fitment"], totals["skipped"])

    # Final counts from DB
    try:
        conn2 = psycopg2.connect(DSN)
        with conn2.cursor() as cur:
            for brand in ["Tesla", "Chery", "Jetour", "MG"]:
                cur.execute(
                    "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=%s AND is_active=TRUE",
                    (brand,),
                )
                cnt = cur.fetchone()[0]
                log.info("DB count for %s: %d active parts", brand, cnt)
        conn2.close()
    except Exception as e:
        log.warning("Could not get final DB counts: %s", e)


if __name__ == "__main__":
    main()
