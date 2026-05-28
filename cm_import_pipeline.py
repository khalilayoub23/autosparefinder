"""
Champion Motors Import Pipeline
================================
Processes champion_motors_parts.json → normalizes → imports to DB.

Handles:
  - VW Group: Volkswagen, Audi, Skoda, SEAT, VW Commercial → each as separate brand
  - Multi-brand entries (e.g. "אודי / סיאט / VW") → expanded to one row per brand
  - BMW: 32,307 parts
  - All valid aftermarket_tier values (NULL / OE_equivalent / economy / generic)
  - Hebrew → English model name hints
  - Model year extraction from strings like "2019-2014 גולף VW"

Usage:
  python cm_import_pipeline.py --brand VW       # import all VW Group brands
  python cm_import_pipeline.py --brand BMW
  python cm_import_pipeline.py --brand ALL
  python cm_import_pipeline.py --dry-run --brand VW
  python cm_import_pipeline.py --brand VW --limit 500
"""
import argparse, asyncio, json, logging, os, re, sys, uuid
from datetime import datetime
from pathlib import Path
import asyncpg

# ── Config ────────────────────────────────────────────────────────────────────
INPUT    = Path("/opt/autosparefinder/champion_motors_parts.json")
LOGS_DIR = Path("/opt/autosparefinder/logs")
DB_DSN   = (os.getenv("DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@localhost:5432/autospare")
    .replace("postgresql+asyncpg://", "postgresql://"))

SUPPLIER_NAME = "Champion Motors IL"
SUPPLIER_URL  = "https://www.championmotors.co.il"
BATCH_SIZE    = 25

# VAT already included in price_ils_vat; price_ils is ex-VAT
# We store base_price WITH VAT (per DB doc)
USE_VAT_PRICE = True   # use price_ils_vat

# ── Brand registry ────────────────────────────────────────────────────────────
# car_brands.id values confirmed from DB
BRAND_IDS = {
    "BMW":        "caa6ba39-02aa-4394-969d-a15f3f19104c",
    "Audi":       "4a718e3c-5b47-478d-9c62-0b6b5135593e",
    "Volkswagen": "04877cea-0889-4b57-978a-cff0a8f1ed25",
    "Skoda":      "e062ba07-930c-489f-b43e-48bf90a42d11",
    "SEAT":       "ebb4521b-6742-4cc2-b1d0-207903ea085a",
    "Toyota":     "01954786-65c7-4ff4-a6ad-4836b31da9f4",
    "MINI":       "47a433bf-4f6f-4f8f-a686-a8c02f7727a8",
}

SKU_PREFIX = {
    "BMW": "BMW-CM", "Audi": "AUDI-CM", "Volkswagen": "VW-CM",
    "Skoda": "SKODA-CM", "SEAT": "SEAT-CM", "Toyota": "TOYOTA-CM",
    "MINI": "MINI-CM",
}

# Hebrew brand name → canonical English brand
HE_BRAND_MAP = {
    "BMW":           "BMW",
    "אודי":          "Audi",
    "VW":            "Volkswagen",
    "סקודה":         "Skoda",
    "סיאט":          "SEAT",
    "מסחריות VW":   "Volkswagen",   # commercial VW = same brand
    "טויוטה":        "Toyota",
    "מיני":          "MINI",
}

# Which brands belong to each group filter
GROUP_BRANDS = {
    "VW":    {"Volkswagen", "Audi", "Skoda", "SEAT"},
    "BMW":   {"BMW"},
    "Toyota":{"Toyota"},
    "MINI":  {"MINI"},
    "ALL":   set(BRAND_IDS.keys()),
}

# ── Part type mapping ─────────────────────────────────────────────────────────
# part_type_he → (part_type str, aftermarket_tier str|None)
# aftermarket_tier MUST be NULL or one of: OE_equivalent, economy, generic
PTYPE_MAP = {
    "מקורי":   ("Original",    None),           # genuine OEM
    "תחליפי":  ("Aftermarket", "OE_equivalent"), # OE-equivalent aftermarket
    "חליפי":   ("Aftermarket", "economy"),       # economy aftermarket
}
PTYPE_DEFAULT = ("Aftermarket", "generic")

# ── Hebrew → English model hints ─────────────────────────────────────────────
MODEL_HE_EN = {
    "גולף": "Golf", "פולו": "Polo", "פאסאט": "Passat", "טיגואן": "Tiguan",
    "טוארג": "Touareg", "שירוקו": "Scirocco", "אאוטלנדר": "Outlander",
    "טוראן": "Touran", "קאדי": "Caddy", "קראפטר": "Crafter",
    "מולטיואן": "Multivan", "אמארוק": "Amarok", "בורה": "Bora",
    "לופו": "Lupo", "פוקס": "Fox", "איאוס": "Eos",
    "חיפושית": "Beetle", "אאפ": "Up", "אי אפ": "e-Up",
    "יטי": "Yeti", "סופרב": "Superb", "אוקטביה": "Octavia",
    "פביה": "Fabia", "קודיאק": "Kodiaq", "קאמיק": "Kamiq",
    "קארוק": "Karoq", "רפיד": "Rapid", "סקאלה": "Scala",
    "לאון": "Leon", "איביזה": "Ibiza", "ארונה": "Arona",
    "פרמו": "Formentor", "אטקה": "Ateca",
    "A1": "A1", "A3": "A3", "A4": "A4", "A5": "A5", "A6": "A6",
    "A7": "A7", "A8": "A8", "Q2": "Q2", "Q3": "Q3", "Q5": "Q5",
    "Q7": "Q7", "Q8": "Q8", "R8": "R8", "TT": "TT",
    "מרובה דגמים": "Multiple Models",
}

def translate_model(he_str: str) -> str:
    """Best-effort Hebrew → English model hint."""
    s = he_str.strip()
    for he, en in MODEL_HE_EN.items():
        if he in s:
            s = s.replace(he, en)
    return s.strip()

def parse_model_years(model_str: str) -> tuple[str, int, int]:
    """
    Parse model strings like:
      "2019-2014 גולף VW"  → ("Golf VW", 2014, 2019)
      "VW ID4"             → ("VW ID4", 0, 0)
      "מרובה דגמים"        → ("Multiple Models", 0, 0)
    Returns (model_name_en, year_from, year_to)  (0 = unknown)
    """
    m = re.match(r"^(\d{4})-(\d{4})\s+(.+)$", model_str.strip())
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        year_from, year_to = min(y1, y2), max(y1, y2)
        model_name = translate_model(m.group(3))
        return model_name, year_from, year_to
    return translate_model(model_str), 0, 0

def expand_brands(vehicle_make: str) -> list[str]:
    """
    Expand multi-brand strings to a list of canonical English brand names.
    "אודי / סיאט / סקודה / VW" → ["Audi", "SEAT", "Skoda", "Volkswagen"]
    "" → [] (skip)
    """
    if not vehicle_make or not vehicle_make.strip():
        return []
    parts = [p.strip() for p in vehicle_make.split("/")]
    brands = []
    for p in parts:
        en = HE_BRAND_MAP.get(p)
        if en and en not in brands:
            brands.append(en)
    return brands

def make_sku(brand: str, oem: str) -> str:
    prefix = SKU_PREFIX.get(brand, brand.upper() + "-CM")
    clean  = re.sub(r"[^A-Za-z0-9\-_]", "", (oem or "NOREF").strip().replace(" ", "-"))
    return f"{prefix}-{clean}"

def map_category(name_he: str, name: str) -> str:
    t = f"{name_he} {name}".lower()
    if any(w in t for w in ("בלם","דיסק","רפידה","ברקס")): return "בלמים"
    if any(w in t for w in ("מנוע","בוכנה","שסתום","תמסורת","ראש צילינדר")): return "מנוע"
    if any(w in t for w in ("גיר","קלאץ","תיבת")): return "תיבת הילוכים"
    if any(w in t for w in ("מתלה","קפיץ","בולם","זרוע")): return "מתלה"
    if any(w in t for w in ("הגה","גלגל")): return "היגוי"
    if any(w in t for w in ("קירור","רדיאטור","מאוורר","תרמוסטט")): return "קירור"
    if any(w in t for w in ("דלק","מסנן","משאבת")): return "דלק"
    if any(w in t for w in ("חשמל","חיישן","חוט","פיוז","מתג","נורה")): return "חשמל"
    if any(w in t for w in ("פגוש","דלת","מכסה","מכסה מנוע","תא מטען","פנס","פלאס")): return "מרכב"
    if any(w in t for w in ("פליטה","מניפולד")): return "פליטה"
    if any(w in t for w in ("פנים","ריפוד","שטיח","דשבורד")): return "פנים הרכב"
    if any(w in t for w in ("שמן","מסנן שמן","פילטר")): return "מסנן"
    return "חלקי חילוף"

# ── Normalize ─────────────────────────────────────────────────────────────────

def normalize(raw: dict) -> list[dict]:
    """
    Normalize one raw record → list of brand-specific records (1 per expanded brand).
    Handles multi-brand entries, fixes part type, generates SKU.
    """
    oem        = (raw.get("oem_number") or "").strip()
    name_he    = (raw.get("name_he") or "").strip()
    name_en    = (raw.get("name") or "").strip()
    vehicle_make = raw.get("vehicle_make", "")
    model_str  = (raw.get("model") or "").strip()
    ptype_he   = (raw.get("part_type_he") or "").strip()
    stock      = (raw.get("stock") or "").strip()
    price_vat  = raw.get("price_ils_vat")
    price_ex   = raw.get("price_ils")
    is_original= raw.get("is_original", False)
    supplier_sku = (raw.get("supplier_sku") or "").strip()

    if not oem and not name_he:
        return []  # skip empty rows

    # Price
    price = price_vat if USE_VAT_PRICE else price_ex
    if price is not None:
        try:
            price = round(float(price), 2)
            if price <= 0:
                price = None
        except (TypeError, ValueError):
            price = None

    # Part type + aftermarket_tier
    pt, tier = PTYPE_MAP.get(ptype_he, PTYPE_DEFAULT)
    # Override: if is_original flag set but type not "מקורי"
    if is_original and tier is not None:
        pt, tier = "Original", None

    # Model
    model_en, year_from, year_to = parse_model_years(model_str)
    is_multi_model = (model_str == "מרובה דגמים" or not model_str)

    # Stock
    in_stock = stock in ("יש", "yes", "in_stock", "true")

    # Name: prefer English if non-empty and not just whitespace, else use Hebrew
    display_name = name_en if name_en.strip() else name_he

    # Expand brands
    brands = expand_brands(vehicle_make)
    if not brands:
        return []  # skip unknown brands

    results = []
    for brand in brands:
        sku = make_sku(brand, oem)
        results.append({
            "sku":          sku,
            "brand":        brand,
            "brand_id":     BRAND_IDS[brand],
            "oem_number":   oem or None,
            "name":         display_name,
            "name_he":      name_he,
            "name_en":      name_en if name_en.strip() else None,
            "part_type":    pt,
            "aftermarket_tier": tier,
            "category":     map_category(name_he, name_en),
            "price_ils":    price,
            "model_str":    model_en,
            "year_from":    year_from if year_from > 0 else None,
            "year_to":      year_to   if year_to   > 0 else None,
            "is_multi_model": is_multi_model,
            "in_stock":     in_stock,
            "supplier_sku": supplier_sku if supplier_sku and supplier_sku != " " else None,
            "source_brand_he": vehicle_make,
        })
    return results

# ── DB helpers ────────────────────────────────────────────────────────────────

async def ensure_supplier(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,"
        "reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'IL',0.95,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL)
    logging.getLogger("cm").info("Created supplier: %s", sid)
    return sid

async def import_batch(conn, supplier_id, batch, skip_fitment=False):
    """Insert one batch. Each item is a normalized record. Returns (inserted, updated, errors)."""
    inserted = updated = errors = 0
    for rec in batch:
        try:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    INSERT INTO parts_catalog(
                        id, sku, name, name_he, category,
                        manufacturer, manufacturer_id,
                        part_type, description, oem_number, aftermarket_tier,
                        base_price, part_condition, is_active, needs_oem_lookup,
                        created_at, updated_at)
                    VALUES(
                        gen_random_uuid(), $1, $2, $3, $4,
                        $5, $6,
                        $7, $8, $9, $10,
                        $11, 'New', TRUE, FALSE,
                        NOW(), NOW())
                    ON CONFLICT (sku) DO UPDATE SET
                        name=EXCLUDED.name, name_he=EXCLUDED.name_he,
                        oem_number=EXCLUDED.oem_number, aftermarket_tier=EXCLUDED.aftermarket_tier,
                        base_price=EXCLUDED.base_price, category=EXCLUDED.category,
                        updated_at=NOW()
                    RETURNING id, (xmax=0) AS was_inserted""",
                    rec["sku"],
                    rec["name"] or rec["name_he"],
                    rec["name_he"],
                    rec["category"],
                    rec["brand"],
                    rec["brand_id"],
                    rec["part_type"],
                    rec.get("name_en"),
                    rec["oem_number"],
                    rec["aftermarket_tier"],
                    rec["price_ils"],
                )
                if row is None:
                    errors += 1
                    continue
                part_id   = str(row["id"])
                was_new   = row["was_inserted"]
                if was_new:
                    inserted += 1
                else:
                    updated += 1

                # Upsert supplier_parts
                price_usd = round(float(rec["price_ils"] or 0) / 3.65, 2) if rec["price_ils"] else 0.0
                await conn.execute("""
                    INSERT INTO supplier_parts(
                        id, supplier_id, part_id, supplier_sku,
                        price_usd, price_ils, availability,
                        warranty_months, estimated_delivery_days,
                        is_available, supplier_url, part_type,
                        created_at, updated_at)
                    VALUES(
                        gen_random_uuid(), $1, $2, $3,
                        $4, $5, $6,
                        24, 7, $7, $8, $9,
                        NOW(), NOW())
                    ON CONFLICT (supplier_id, supplier_sku) DO UPDATE SET
                        price_usd=EXCLUDED.price_usd, price_ils=EXCLUDED.price_ils,
                        is_available=EXCLUDED.is_available, updated_at=NOW()
                    """,
                    supplier_id, part_id,
                    rec["supplier_sku"] or rec["sku"],
                    price_usd,
                    rec["price_ils"],
                    "In Stock" if rec["in_stock"] else "Out of Stock",
                    rec["in_stock"],
                    SUPPLIER_URL,
                    rec["part_type"],
                )

                # Fitment
                if not skip_fitment and not rec["is_multi_model"] and rec.get("model_str") and rec.get("year_from"):
                    await conn.execute("""
                        INSERT INTO part_vehicle_fitment(
                            id, part_id, manufacturer, manufacturer_id,
                            model, year_from, year_to, notes, created_at, updated_at)
                        VALUES(gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
                        ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING""",
                        part_id, rec["brand"], rec["brand_id"],
                        rec["model_str"], rec["year_from"], rec["year_to"],
                        "Champion Motors IL pricelist",
                    )

        except Exception as e:
            errors += 1
            logging.getLogger("cm").debug("ERR %s: %s", rec.get("sku"), e)

    return inserted, updated, errors

# ── Main ──────────────────────────────────────────────────────────────────────

async def run(brand_filter: str, dry_run=False, limit=None, skip_fitment=False):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("cm")

    target_brands = GROUP_BRANDS.get(brand_filter.upper(), {brand_filter})
    log.info("Target brands: %s", target_brands)

    raw_parts = json.loads(INPUT.read_text(encoding="utf-8"))["parts"]
    log.info("Raw parts in file: %d", len(raw_parts))

    # Normalize → expand multi-brand entries
    normalized = []
    for p in raw_parts:
        normalized.extend(normalize(p))

    # Filter to target brands
    filtered = [r for r in normalized if r["brand"] in target_brands]
    log.info("Normalized rows for %s: %d", brand_filter, len(filtered))

    if limit:
        filtered = filtered[:limit]

    # Stats by brand
    brand_counts = {}
    for r in filtered:
        brand_counts[r["brand"]] = brand_counts.get(r["brand"], 0) + 1
    for b, c in sorted(brand_counts.items()):
        log.info("  %s: %d parts", b, c)

    # Aftermarket tier validation check
    bad_tiers = [r for r in filtered if r["aftermarket_tier"] not in (None, "OE_equivalent", "economy", "generic")]
    if bad_tiers:
        log.error("VALIDATION FAIL: %d rows with invalid aftermarket_tier!", len(bad_tiers))
        for r in bad_tiers[:3]:
            log.error("  sku=%s tier=%s", r["sku"], r["aftermarket_tier"])
        sys.exit(1)

    if dry_run:
        log.info("DRY-RUN — no DB writes.")
        with_price = sum(1 for r in filtered if r.get("price_ils"))
        in_stock   = sum(1 for r in filtered if r["in_stock"])
        with_fitment = sum(1 for r in filtered if not r["is_multi_model"] and r.get("year_from"))
        log.info("  with_price=%d  in_stock=%d  with_fitment=%d", with_price, in_stock, with_fitment)
        sample = filtered[0] if filtered else {}
        log.info("  sample: %s | %s | ₪%.2f", sample.get("sku"), sample.get("name"), sample.get("price_ils") or 0)
        return

    conn = await asyncpg.connect(DB_DSN)
    try:
        sid = await ensure_supplier(conn)
        total_ins = total_upd = total_err = 0
        t0 = datetime.utcnow()

        for i in range(0, len(filtered), BATCH_SIZE):
            batch = filtered[i:i+BATCH_SIZE]
            ins, upd, err = await import_batch(conn, sid, batch, skip_fitment)
            total_ins += ins; total_upd += upd; total_err += err

            done = i + len(batch)
            if done % 500 == 0 or done == len(filtered):
                el = (datetime.utcnow()-t0).total_seconds()
                log.info("Progress %d/%d | ins=%d upd=%d err=%d | %.0f/s",
                         done, len(filtered), total_ins, total_upd, total_err,
                         done/el if el > 0 else 0)

        log.info("DONE brand=%s | inserted=%d updated=%d errors=%d", brand_filter, total_ins, total_upd, total_err)

        # Final count per brand
        for brand in sorted(target_brands):
            prefix = SKU_PREFIX.get(brand, brand)
            cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active", brand)
            log.info("  DB count %s: %d", brand, cnt)

    finally:
        await conn.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="ALL",
                    help="VW | BMW | Toyota | MINI | ALL or exact brand name")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip-fitment", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(LOGS_DIR / "cm_import.log")),
        ]
    )
    asyncio.run(run(args.brand, dry_run=args.dry_run, limit=args.limit, skip_fitment=args.skip_fitment))
