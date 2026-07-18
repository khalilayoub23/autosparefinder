#!/usr/bin/env python3
"""
KGM (kgm.co.il) SsangYong parts importer.

Fetches the full parts pricelist via POST to the mchron-chlfim page (% wildcard),
parses the HTML table, and imports ~11,000 parts into parts_catalog as SsangYong.

Prices are already in ILS (Israeli New Shekel).
Run inside the backend container: python3 /app/importers/kgm_ssangyong_importer.py
"""
from __future__ import annotations

import asyncio
import logging
import re
import httpx
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)

KGM_URL = "https://kgm.co.il/%D7%9E%D7%97%D7%99%D7%A8%D7%95%D7%9F-%D7%97%D7%9C%D7%A4%D7%99%D7%9D/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/html,application/xhtml+xml",
    "Referer": KGM_URL,
}

# Hebrew keywords → DB category
# Checked against real descriptions from kgm.co.il
CAT_RULES: list[tuple[list[str], str]] = [
    # brakes
    (["בלם", "קליפר", "ABS", "abs", "דיסק בלם", "רפידת"], "brakes"),
    # gearbox / transmission
    (["תיבת הילוכים", "תי'ה", "גיר", "הילוך", "מצמד", "A/T", "M/T", "CVT", "טרנסאקסל"], "gearbox"),
    # suspension / steering
    (["הגה", "בולם זעזועים", "קפיץ", "מסב", "מיסב", "זרוע", "מנהרה", "סרן", "ג'וינט", "פלנג'"], "suspension-steering"),
    # engine
    (["מנוע", "בוכנה", "שסתום", "אטם ראש", "גלגל שיניים", "פין", "קשת", "גל ארכובה"], "engine"),
    # cooling
    (["קירור", "נוזל קירור", "מקרן", "ת'רמוסטט", "משאבת מים", "תרמוסטט"], "cooling"),
    # filters
    (["מסנן", "פילטר", "אוויר", "שמן מנוע"], "filters"),
    # electrical / sensors
    (["חישן", "חיישן", "מתג", "חשמל", "פיוז", "ממסר", "רלה", "פנס", "תאורה", "מנוע מקפיא"], "electrical-sensors"),
    # body / exterior
    (["פגוש", "גוף", "ספוילר", "דלת", "זכוכית", "מראה", "מכסה", "ידית"], "body-exterior"),
    # exhaust
    (["פליטה", "אגזוז", "קטליזטור", "מנקאי פליטה"], "exhaust"),
    # fuel / air
    (["משאבת דלק", "מזרק", "מייצב לחץ", "דלק", "צינור דלק"], "fuel-air"),
    # belts / chains
    (["רצועה", "שרשרת תזמון", "מתח רצועה", "תזמון", "מותח"], "belts-chains"),
    # AC / heating
    (["מזגן", "קומפרסור", "אוורור", "מפוח", "תנור"], "air-conditioning-heating"),
    # steering fluid / oil  (after gearbox to not conflict)
    (["שמן הגה", "שמן הילוכים"], "suspension-steering"),
]

# Model name hints extracted from Hebrew abbreviations in descriptions
MODEL_MAP: list[tuple[list[str], str]] = [
    (["רקסטון", "רקס'", "רק'", "G4", "Rexton"], "Rexton"),
    (["קורנדו", "קור'", "Korando"], "Korando"),
    (["מוסו", "מו'", "Musso"], "Musso"),
    (["אקטיון", "אק'", "Actyon"], "Actyon"),
    (["טיבולי", "ט'", "Tivoli"], "Tivoli"),
    (["רודיוס", "רוד'", "Rodius"], "Rodius"),
    (["קיורון", "Kyron"], "Kyron"),
]

HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", HTML_TAG_RE.sub(" ", s)).strip()


def categorize(desc: str) -> str:
    for keywords, cat in CAT_RULES:
        for kw in keywords:
            if kw in desc:
                return cat
    return "accessories"


def extract_model(desc: str) -> str:
    for triggers, model in MODEL_MAP:
        for t in triggers:
            if t in desc:
                return model
    return "SsangYong"


def parse_parts_table(html: str) -> list[dict]:
    """Extract parts from the KGM HTML table response."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
    parts: list[dict] = []
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        if len(tds) != 5:
            continue
        pn    = strip_tags(tds[0])
        suf   = strip_tags(tds[1])
        desc  = strip_tags(tds[2])
        price = strip_tags(tds[3])
        stock = strip_tags(tds[4])

        # Skip header / date rows
        if not re.search(r"[A-Z0-9]{4,}", pn):
            continue
        if not re.match(r"^\d+(\.\d+)?$", price):
            continue

        price_ils = float(price)
        if price_ils <= 0:
            continue

        parts.append({
            "part_number": pn,
            "suffix":      suf,
            "desc_he":     desc,
            "price_ils":   price_ils,
            "in_stock":    stock == "יש",
        })
    return parts


async def fetch_all_parts(client: httpx.AsyncClient) -> list[dict]:
    log.info("Fetching full KGM parts catalog via POST …")
    for attempt in range(3):
        try:
            r = await client.post(
                KGM_URL,
                data={"catalogNum": "", "partDesc": "%"},
                timeout=60,
            )
            r.raise_for_status()
            log.info("Response: %d bytes", len(r.text))
            parts = parse_parts_table(r.text)
            log.info("Parsed %d valid parts from table", len(parts))
            return parts
        except Exception as exc:
            log.warning("Attempt %d failed: %s", attempt + 1, exc)
            await asyncio.sleep(3)
    return []


async def upsert_parts(
    conn: asyncpg.Connection,
    parts: list[dict],
    brand_id: str,
) -> dict:
    inserted = updated = skipped = 0

    for p in parts:
        pn = p["part_number"]
        desc_he = p["desc_he"]
        price_ils = p["price_ils"]
        in_stock = p["in_stock"]

        # Clean OEM / part number: strip leading zeros only if >8 chars
        oem = pn.lstrip("0") or pn
        if len(oem) < 4:
            oem = pn

        sku = f"KGM-{re.sub(r'[^A-Z0-9]', '-', pn.upper())}"
        category = categorize(desc_he)
        model = extract_model(desc_he)
        suffix = p["suffix"]
        suffix_note = f" (suffix: {suffix})" if suffix else ""

        name = f"SsangYong {model} - {desc_he}"[:255]
        description = (
            f"{desc_he}{suffix_note}. "
            f"SsangYong {model} OEM part. "
            f"Part number: {pn}. "
            f"Israeli price: ₪{price_ils:.2f}. "
            f"Stock: {'in stock' if in_stock else 'out of stock'}. "
            f"Source: kgm.co.il."
        )[:500]

        # Pricing policy: cost stored excl. VAT; max_price normalized to current 18% IL VAT
        # base_price = max_price × 1.45 (45% margin, hidden from customers)
        price_ex_vat        = round(price_ils / 1.18, 2)         # excl. VAT (our cost)
        max_price_normalized = round(price_ex_vat * 1.18, 2)     # consumer price incl. 18% VAT
        base_price_computed  = round(price_ex_vat * 1.45, 2)     # our selling price = cost × 1.45

        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, description, specifications,
                        online_price_ils, min_price_ils, max_price_ils,
                        importer_price_ils, base_price,
                        part_type, is_safety_critical, needs_oem_lookup,
                        master_enriched, is_active, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, 'SsangYong', $4::uuid,
                        $5, $6, '{}'::jsonb,
                        $7, $8, $9,
                        $8, $10,
                        'oem', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        online_price_ils   = EXCLUDED.online_price_ils,
                        min_price_ils      = EXCLUDED.min_price_ils,
                        max_price_ils      = EXCLUDED.max_price_ils,
                        importer_price_ils = EXCLUDED.importer_price_ils,
                        base_price         = EXCLUDED.base_price,
                        name               = EXCLUDED.name,
                        description        = EXCLUDED.description,
                        updated_at         = NOW()
                    RETURNING xmax
                    """,
                    sku, oem, name, brand_id,
                    category, description,
                    price_ils, price_ex_vat, max_price_normalized, base_price_computed,
                )
                if row:
                    if row["xmax"] == 0:
                        inserted += 1
                    else:
                        updated += 1
        except Exception as exc:
            log.warning("Failed %s: %s", sku, exc)
            skipped += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


async def main() -> None:
    conn = await asyncpg.connect(DB_DSN)
    try:
        brand_id = await conn.fetchval(
            "SELECT id::text FROM car_brands WHERE lower(name)='ssangyong' AND is_active=TRUE LIMIT 1"
        )
        if not brand_id:
            log.error("SsangYong not found in car_brands — aborting")
            return
        log.info("SsangYong brand ID: %s", brand_id)

        async with httpx.AsyncClient(headers=HEADERS) as client:
            parts = await fetch_all_parts(client)

        if not parts:
            log.error("No parts fetched — aborting")
            return

        log.info("Starting DB import of %d parts…", len(parts))
        result = await upsert_parts(conn, parts, brand_id)

        total = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='SsangYong' AND is_active=TRUE"
        )
        log.info(
            "Done: inserted=%d  updated=%d  skipped=%d | DB total SsangYong=%d",
            result["inserted"], result["updated"], result["skipped"], total,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
