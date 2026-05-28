"""
Delek Motors Brands Importer
============================
Imports BMW, JLR, Ford, MINI, NIO, M-HERO, VOYAH from harvested JSON files.

Run inside backend container:
  docker exec autospare_backend python /app/import_delek_brands.py

Or run all brands:
  docker exec autospare_backend python /app/import_delek_brands.py --all
"""
import asyncio, json, os, re, sys, uuid, argparse
from pathlib import Path
import asyncpg
import urllib.parse as up

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
)
BATCH_SIZE = 25
DATA_DIR   = Path("/opt/autosparefinder")

# Brand config: make_name → (sku_prefix, json_file, supplier_name)
BRAND_CONFIG = {
    "BMW":              ("BMW",  "bmw_parts.json",    "BMW Delek Motors"),
    "Mazda":("MAZDA","jlr_parts.json","Mazda Delek Motors"),
    "Ford":             ("FORD", "ford_parts.json",   "Ford Delek Motors"),
    "MINI":             ("MINI", "mini_parts.json",   "MINI Delek Motors"),
    "NIO":              ("NIO",  "nio_parts.json",    "NIO Delek Motors"),
    "M-HERO":           ("MHR",  "mhero_parts.json",  "M-HERO Delek Motors"),
    "VOYAH":            ("VYH",  "voyah_parts.json",  "VOYAH Delek Motors"),
}

OEM_RE = re.compile(r"^[A-Z0-9][\w\-./]{1,49}$", re.IGNORECASE)


def make_sku(prefix, oem):
    clean = re.sub(r"[^A-Za-z0-9]", "", oem).upper()[:50]
    return f"{prefix}-{clean}"


def categorise(name_he):
    t = (name_he or "").lower()
    if any(w in t for w in ["מנוע","שמן","מסנן","מחזור","טורבו","בוכנה","שסתום"]):
        return "Engine Parts"
    if any(w in t for w in ["ברקס","בלם","brake","דיסק","pad","רפידה"]):
        return "Brakes"
    if any(w in t for w in ["suspension","שלדה","קפיץ","מוט","bearing","מסב","זרוע","מתלה"]):
        return "Suspension"
    if any(w in t for w in ["אטם","gasket","seal","צינור"]):
        return "Engine Parts"
    if any(w in t for w in ["חשמל","sensor","חיישן","electrical","מחשב","פתיל","חוטים"]):
        return "Electrical"
    if any(w in t for w in ["תיבת","מצמד","clutch","gearbox","גיר"]):
        return "Transmission"
    if any(w in t for w in ["פגוש","מכסה","דלת","body","מרכב","כנף","מגן"]):
        return "Body Parts"
    if any(w in t for w in ["קירור","radiator","מאוורר","מצנן","cooling"]):
        return "Cooling System"
    if any(w in t for w in ["היגוי","steering","הגה"]):
        return "Steering"
    if any(w in t for w in ["דלק","fuel","משאבת דלק","מכל"]):
        return "Fuel System"
    return "General Parts"


async def ensure_car_brand(conn, name):
    row = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)=$1", name.lower())
    if row:
        return str(row["id"])
    # Try partial match
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) LIKE $1 LIMIT 1",
        f"%{name.lower().split()[0]}%"
    )
    if row:
        return str(row["id"])
    # Create new car_brand entry
    new_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO car_brands(id, name, created_at) VALUES($1, $2, NOW()) ON CONFLICT DO NOTHING",
        new_id, name
    )
    row = await conn.fetchrow("SELECT id FROM car_brands WHERE LOWER(name)=$1", name.lower())
    return str(row["id"]) if row else new_id


async def ensure_supplier(conn, name):
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", name)
    if row:
        return str(row["id"])
    new_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,country,is_active,created_at) VALUES($1,$2,'IL',TRUE,NOW()) ON CONFLICT DO NOTHING",
        new_id, name
    )
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", name)
    return str(row["id"]) if row else new_id


async def import_brand(conn, make, sku_prefix, json_file, supplier_name):
    path = DATA_DIR / json_file
    if not path.exists():
        print(f"  [SKIP] {json_file} not found"); return {"inserted": 0, "errors": 0, "skipped": 0}

    raw = json.loads(path.read_text(encoding="utf-8"))
    parts_raw = raw.get("parts", raw) if isinstance(raw, dict) else raw
    print(f"\n[{make}] {len(parts_raw)} parts from {json_file}")

    brand_id    = await ensure_car_brand(conn, make)
    supplier_id = await ensure_supplier(conn, supplier_name)
    print(f"  brand_id={brand_id}  supplier_id={supplier_id}")

    stats = {"inserted": 0, "errors": 0, "skipped": 0}
    batch = []

    async def flush():
        if not batch: return
        async with conn.transaction():
            for row in batch:
                try:
                    await conn.execute("""
                        INSERT INTO parts_catalog(
                            id, sku, name, name_he, manufacturer, manufacturer_id,
                            oem_number, category, part_type, part_condition,
                            base_price, is_active, aftermarket_tier,
                            needs_oem_lookup, master_enriched, updated_at
                        ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,NOW())
                        ON CONFLICT(sku) DO UPDATE SET
                            oem_number   = EXCLUDED.oem_number,
                            name_he      = EXCLUDED.name_he,
                            base_price   = CASE WHEN EXCLUDED.base_price > 0
                                           THEN EXCLUDED.base_price
                                           ELSE parts_catalog.base_price END,
                            is_active    = TRUE,
                            updated_at   = NOW()
                    """,
                    row["id"], row["sku"], row["name"], row["name_he"],
                    row["manufacturer"], row["brand_id"],
                    row["oem_number"], row["category"], row["part_type"],
                    "New", row["base_price"], True,
                    row["tier"], False, False)
                    stats["inserted"] += 1
                except Exception as e:
                    print(f"    [ERR] {row['sku']}: {e}")
                    stats["errors"] += 1
        batch.clear()

    for p in parts_raw:
        oem = (p.get("oem_number") or "").strip()
        if not oem or not OEM_RE.match(oem):
            stats["skipped"] += 1; continue

        is_orig  = bool(p.get("is_original", False))
        name_he  = (p.get("name_he") or p.get("name") or oem).strip()
        name_en  = (p.get("name")    or "").strip()
        price    = float(p.get("price_ils") or p.get("price_ils_vat") or 0)
        if price > 0:
            price = round(price / 1.18, 2) if p.get("price_ils_vat") and not p.get("price_ils") else price

        batch.append({
            "id":           str(uuid.uuid4()),
            "sku":          make_sku(sku_prefix, oem),
            "name":         name_en or name_he,
            "name_he":      name_he,
            "manufacturer": make,
            "brand_id":     brand_id,
            "oem_number":   oem,
            "category":     categorise(name_he),
            "part_type":    "original" if is_orig else "oe_equivalent",
            "base_price":   price,
            "tier":         None if is_orig else "OE_equivalent",
        })
        if len(batch) >= BATCH_SIZE:
            await flush()

    await flush()
    print(f"  [{make}] inserted={stats['inserted']}  errors={stats['errors']}  skipped={stats['skipped']}")
    return stats


async def verify(conn, brands):
    print("\n[Verify] Parts per brand in DB:")
    total = 0
    for make, (prefix, _, _) in brands.items():
        row = await conn.fetchrow(
            "SELECT COUNT(*) as n FROM parts_catalog WHERE sku LIKE $1 AND is_active=TRUE",
            f"{prefix}-%"
        )
        n = row["n"] if row else 0
        print(f"  {make:25s}  {n:6d}  (SKU: {prefix}-*)")
        total += n
    print(f"  {'TOTAL':25s}  {total:6d}")


async def main(brands_to_run):
    p = up.urlparse(DATABASE_URL)
    conn = await asyncpg.connect(
        host=p.hostname, port=p.port or 5432,
        database=p.path.lstrip("/"), user=p.username, password=p.password, timeout=30
    )
    print(f"[DB] Connected to {p.hostname}/{p.path.lstrip('/')}")

    totals = {"inserted": 0, "errors": 0, "skipped": 0}
    for make, (prefix, jfile, supplier) in brands_to_run.items():
        s = await import_brand(conn, make, prefix, jfile, supplier)
        for k in totals: totals[k] += s[k]

    await verify(conn, brands_to_run)
    await conn.close()
    print(f"\n[DONE] total inserted={totals['inserted']}  errors={totals['errors']}  skipped={totals['skipped']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", help="Single brand name to import (default: all)")
    ap.add_argument("--all",   action="store_true", help="Import all brands")
    args = ap.parse_args()

    if args.brand:
        key = args.brand.upper()
        chosen = {k: v for k, v in BRAND_CONFIG.items() if k.upper() == key or v[0] == key}
        if not chosen:
            print(f"Unknown brand '{args.brand}'. Options: {list(BRAND_CONFIG.keys())}")
            sys.exit(1)
    else:
        chosen = BRAND_CONFIG

    asyncio.run(main(chosen))
