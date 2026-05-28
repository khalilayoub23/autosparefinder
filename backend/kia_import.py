#!/usr/bin/env python3
"""
KIA Genuine Parts Import — kia-israel.co.il מחירון חלפים
Source: https://kia-israel.co.il/מחירון-חלפים (POST search for 'אטם')
Prices: ex-VAT ILS (column: מחיר ללא מע"מ)
Name:   Full Hebrew description INCLUDING embedded model abbreviation — do NOT strip it
Part type: original (genuine KIA parts, suffix 'K')
manufacturer_id: 626947bf-be3f-4dd1-a52e-fbcff8168cfc (car_brands)
"""
import asyncio
import asyncpg
import urllib.request
import urllib.parse
import re
import sys
from html.parser import HTMLParser

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
KIA_MANUFACTURER_ID = "626947bf-be3f-4dd1-a52e-fbcff8168cfc"
PARTS_URL = "https://kia-israel.co.il/%d7%9e%d7%97%d7%99%d7%a8%d7%95%d7%9f-%d7%97%d7%9c%d7%a4%d7%99%d7%9d"
BATCH_SIZE = 25


def map_category(desc: str) -> str:
    d = desc.lower()
    if any(k in desc for k in ["מכשיר", "כלי", "חולץ", "מתאם", "להתקנת", "להסרת"]):
        return "tools-equipment"
    if any(k in desc for k in ["בלם", "קליפר", "בוכנה בלם", "ABS", "צינור בלם"]):
        return "brakes-clutch"
    if any(k in desc for k in ["מצמד", "גלגל תנופה"]):
        return "brakes-clutch"
    if any(k in desc for k in ["פליטה", "אגזוז", "קטליזטור", "סעפת פל"]):
        return "exhaust"
    if "EGR" in desc or "egr" in d:
        return "engine"
    if any(k in desc for k in ["טורבו", "מגדש", "מצנן בין", "intercooler"]):
        return "engine"
    if any(k in desc for k in ["מים", "תרמוסטט", "טרמוסטט", "קירור", "מאוורר", "רדיאטור"]):
        return "cooling-system"
    if any(k in desc for k in ["דלק", "מרסס", "מזרק", "שסתום דלק", "גז", "דיזל"]):
        return "fuel-system"
    if any(k in desc for k in ["הגה", "היגוי", 'תה"\u05dc', "רחפן", "מתלה", "קפיץ", "בולם"]):
        return "suspension-steering"
    if any(k in desc for k in ["תיבת הילוכים", "גיר", "ממיר", "דיפרנציאל", "גל ארכובה", "גל הינע"]):
        return "gearbox"
    if any(k in desc for k in ["שמן", "ראש מנוע", "שסתום", "ארכובה", "כרבולת", "בוכנה", "גל קמי", "טבעת"]):
        return "engine"
    return "engine"


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row: list[str] = []
        self.current_cell: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            self.current_row.append(" ".join(self.current_cell).strip())

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)

    def handle_entityref(self, name):
        import html as _html
        if self.in_cell:
            self.current_cell.append(_html.unescape(f"&{name};"))

    def handle_charref(self, name):
        import html as _html
        if self.in_cell:
            self.current_cell.append(_html.unescape(f"&#{name};"))


def fetch_parts() -> list[dict]:
    print("Fetching parts from kia-israel.co.il ...")
    data = urllib.parse.urlencode({"catalogNum": "", "partDesc": "אטם"}).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
        "Referer": "https://kia-israel.co.il/",
        "Origin": "https://kia-israel.co.il",
    }
    req = urllib.request.Request(PARTS_URL, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", errors="replace")

    parser = TableParser()
    parser.feed(html)

    if not parser.rows:
        raise RuntimeError("No table rows found in response")

    parts = []
    for row in parser.rows[1:]:
        if len(row) < 4:
            continue
        sku = row[0].strip()
        suffix = row[1].strip()
        description = row[2].strip()
        price_str = row[3].strip().replace(",", "")
        stock = row[4].strip() if len(row) > 4 else ""

        if not sku or not description:
            continue

        try:
            price = float(price_str)
        except ValueError:
            print(f"  SKIP bad price '{price_str}' for sku={sku}", file=sys.stderr)
            continue

        parts.append({
            "sku": sku,
            "suffix": suffix,
            "name": description,
            "price": price,
            "in_stock": stock == "יש",
        })

    print(f"  Parsed {len(parts)} parts from table")
    return parts


async def import_parts(parts: list[dict]):
    conn = await asyncpg.connect(DB_URL)
    try:
        existing = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer_id = $1::uuid",
            KIA_MANUFACTURER_ID,
        )
        print(f"Existing Kia parts in DB: {existing}")

        inserted = 0
        updated = 0
        skipped = 0
        errors = []

        for batch_start in range(0, len(parts), BATCH_SIZE):
            batch = parts[batch_start : batch_start + BATCH_SIZE]
            async with conn.transaction():
                for p in batch:
                    category = map_category(p["name"])
                    try:
                        result = await conn.fetchrow(
                            """
                            INSERT INTO parts_catalog (
                                id, sku, name, name_he, description,
                                manufacturer, manufacturer_id,
                                part_type, part_condition,
                                base_price, importer_price_ils,
                                category, is_active, oem_number,
                                needs_oem_lookup, master_enriched,
                                is_safety_critical, created_at, updated_at
                            ) VALUES (
                                gen_random_uuid(),
                                $1, $2, $2, $2,
                                'Kia', $3::uuid,
                                'original', 'new',
                                $4, $4,
                                $5, TRUE, $1,
                                FALSE, FALSE, FALSE,
                                NOW(), NOW()
                            )
                            ON CONFLICT (sku) DO UPDATE SET
                                name          = EXCLUDED.name,
                                name_he       = EXCLUDED.name_he,
                                description   = EXCLUDED.description,
                                base_price    = EXCLUDED.base_price,
                                importer_price_ils = EXCLUDED.importer_price_ils,
                                category      = EXCLUDED.category,
                                is_active     = TRUE,
                                updated_at    = NOW()
                            RETURNING (xmax = 0) AS was_inserted
                            """,
                            p["sku"],
                            p["name"],
                            KIA_MANUFACTURER_ID,
                            p["price"],
                            category,
                        )
                        if result and result["was_inserted"]:
                            inserted += 1
                        else:
                            updated += 1
                    except Exception as e:
                        errors.append(f"sku={p['sku']}: {e}")
                        skipped += 1

            print(
                f"  Batch {batch_start // BATCH_SIZE + 1}/{-(-len(parts) // BATCH_SIZE)} done "
                f"(+{inserted} ins, ~{updated} upd so far)"
            )

        total_kia = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer_id = $1::uuid",
            KIA_MANUFACTURER_ID,
        )
        price_stats = await conn.fetchrow(
            "SELECT MIN(base_price), MAX(base_price), AVG(base_price) FROM parts_catalog WHERE manufacturer_id = $1::uuid",
            KIA_MANUFACTURER_ID,
        )

        print("\n=== IMPORT COMPLETE ===")
        print(f"  Inserted new : {inserted}")
        print(f"  Updated exist: {updated}")
        print(f"  Skipped/error: {skipped}")
        print(f"  Total Kia in DB: {total_kia}")
        print(f"  Price range: \u20aa{price_stats['min']:.2f} \u2013 \u20aa{price_stats['max']:.2f} (avg \u20aa{price_stats['avg']:.2f})")
        if errors:
            print(f"\n  Errors ({len(errors)}):")
            for e in errors[:10]:
                print(f"    {e}")

    finally:
        await conn.close()


async def main():
    parts = fetch_parts()
    if not parts:
        print("No parts fetched \u2014 aborting")
        sys.exit(1)
    await import_parts(parts)


if __name__ == "__main__":
    asyncio.run(main())
