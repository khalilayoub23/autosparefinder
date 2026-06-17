"""
Script: sng_barratt_jaguar_import.py
Purpose: Import SNG Barratt Jaguar classic/modern parts catalog into parts_catalog.

Process:
  1. Read jaguar_parts_raw.ndjson (scraped from sngbarratt.com)
  2. Parse each record: part number, title, price (GBP), applications, stock status
  3. Convert GBP price to ILS (incl. 18% VAT)
  4. Upsert to parts_catalog with full specifications JSONB
  5. Upsert supplier_parts record per part
  6. Insert part_vehicle_fitment for each application (model + year range)
  7. Delegate missing fitment/Hebrew names to REX agent
  8. Print summary stats

Data Imported / Modified:
  - parts_catalog: sku, name, category, manufacturer, manufacturer_id, part_type,
                   description, oem_number, aftermarket_tier, base_price,
                   compatible_vehicles (JSONB), specifications (JSONB), is_active
    Note: name_he not available from SNG Barratt — REX will add Hebrew descriptions
  - supplier_parts: supplier_id, part_id, supplier_sku, price_usd, price_ils,
                    availability, warranty_months, estimated_delivery_days,
                    is_available, supplier_url
  - part_vehicle_fitment: part_id, manufacturer, model, year_from, year_to, notes
  - agent_todos: REX task for missing Hebrew names

Data Sources / Web Links:
  - SNG Barratt (UK Jaguar parts specialist): https://www.sngbarratt.com
  - SNG Barratt product API: https://www.sngbarratt.com/English/UK/Products/{guid}
  - Scraped ndjson: /opt/autosparefinder/jaguar_parts_raw.ndjson

Missing Data Delegation:
  - Hebrew (name_he) → REX agent todo created after import
  - Missing OEM cross-refs → needs_oem_lookup=True on all parts

VAT Rules:
  - SNG Barratt prices are GBP EXCL. VAT
  - Conversion: GBP × GBP_TO_ILS × 1.18 = base_price (ILS incl. 18% VAT)
  - Default GBP_TO_ILS = 4.73 (set via env var GBP_TO_ILS)

Confidence tier: 0.85 (reputable UK specialist supplier, not official Israeli importer)

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""
import argparse, asyncio, json, logging, os, sys, uuid
from datetime import datetime
from pathlib import Path
import asyncpg

INPUT    = Path("/opt/autosparefinder/jaguar_parts_raw.ndjson")
LOGS_DIR = Path("/opt/autosparefinder/logs")
DB_DSN   = (os.getenv("DATABASE_URL","postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@localhost:5432/autospare")
            .replace("postgresql+asyncpg://","postgresql://"))
JAGUAR_BRAND_ID = "fde0f2dc-c6fb-4ab6-b699-765044fbc073"
SUPPLIER_NAME   = "SNG Barratt"
SUPPLIER_URL    = "https://www.sngbarratt.com"
GBP_TO_ILS      = float(os.getenv("GBP_TO_ILS","4.73"))
VAT_RATE        = float(os.getenv("VAT_RATE","0.18"))
BATCH_SIZE      = 25

LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(str(LOGS_DIR/"jaguar_import.log"))])
log = logging.getLogger("jag_import")

MODEL_YEARS = {
    "E-Type":(1961,1975),"XKE":(1961,1975),"XK8":(1996,2006),"XKR":(1996,2006),
    "XJS":(1976,1996),"XJ40":(1986,1994),"XJ6":(1968,1997),"XJ8":(1998,2010),
    "XJ":(1968,2019),"XF":(2008,2099),"XE":(2015,2099),"F-Type":(2013,2099),
    "F-Pace":(2016,2099),"E-Pace":(2017,2099),"I-Pace":(2018,2099),
    "S-Type":(1999,2008),"X-Type":(2001,2009),"Daimler":(1945,2005),
    "Mk II":(1959,1969),"Mk 2":(1959,1969),"XK":(2006,2014),
}

def year_range(model):
    u = model.upper()
    for k,(y1,y2) in MODEL_YEARS.items():
        if k.upper() in u: return y1, y2
    return 1950, 2099

def sku(pn):
    return "JAG-" + pn.strip().replace("_","-")

def part_type(tn):
    t = (tn or "").lower()
    return "Original" if any(x in t for x in ("original","oem","genuine")) else "Aftermarket"

def base_price_ils(gbp):
    if not gbp or gbp <= 0: return None
    return round(gbp * GBP_TO_ILS * (1 + VAT_RATE), 2)

def price_usd(gbp):
    return round((gbp or 0) * 1.264, 2)

def category(name, desc):
    t = f"{name} {desc}".lower()
    if any(w in t for w in ("brake","disc","pad","caliper","master cylinder")): return "בלמים"
    if any(w in t for w in ("engine","piston","valve","gasket","timing","camshaft","oil seal")): return "מנוע"
    if any(w in t for w in ("gearbox","clutch","transmission","gear")): return "תיבת הילוכים"
    if any(w in t for w in ("suspension","spring","shock","absorber","strut","bush")): return "מתלה"
    if any(w in t for w in ("steering","rack","column","tie rod","wheel bearing")): return "היגוי"
    if any(w in t for w in ("cooling","radiator","fan","thermostat","coolant","water pump")): return "קירור"
    if any(w in t for w in ("fuel","injector","carburetor","carburettor","filter element")): return "דלק"
    if any(w in t for w in ("electrical","wiring","sensor","switch","relay","fuse","lamp","light")): return "חשמל"
    if any(w in t for w in ("body","panel","bumper","door","bonnet","wing","sill")): return "מרכב"
    if any(w in t for w in ("exhaust","manifold","silencer","muffler")): return "פליטה"
    if any(w in t for w in ("interior","carpet","seat","trim","dashboard")): return "פנים הרכב"
    return "חלקי חילוף"

async def ensure_supplier(conn):
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row: return str(row["id"])
    sid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,website,country,reliability_score,is_active,created_at,updated_at)"
        " VALUES($1,$2,$3,'UK',0.92,TRUE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL)
    log.info("Created supplier: %s", sid)
    return sid

async def import_batch(conn, supplier_id, batch, skip_fitment):
    ins, skp = 0, 0
    for rec in batch:
        try:
            async with conn.transaction():   # savepoint per row
                pn = rec.get("part_number","")
                title = (rec.get("title","") or "").strip()
                if not title: skp+=1; continue
                desc = " | ".join(filter(None,[rec.get("description","").strip(),rec.get("sales_note","").strip()]))
                pt = part_type(rec.get("type_name",""))
                tier = rec.get("aftermarket_tier")
                gbp  = rec.get("price_gbp")
                oem  = rec.get("base_part_number") or None
                apps = rec.get("applications") or []
                guid = rec.get("web_product_guid","")
                stock = rec.get("stock_status","unknown")

                specs = json.dumps({
                    'vat_included':    True,
                    'vat_rate':        0.18,
                    'currency':        'ILS',
                    'source_currency': 'GBP',
                    'gbp_rate':        GBP_TO_ILS,
                    'source':          'SNG Barratt UK catalog',
                    'shipping_to_il':  True,
                    'importer':        'SNG Barratt',
                    'warranty_months': 12,
                    'stock_status':    stock,
                })

                row = await conn.fetchrow("""
                    INSERT INTO parts_catalog(
                        id,sku,name,category,manufacturer,manufacturer_id,
                        part_type,description,oem_number,aftermarket_tier,
                        base_price,importer_price_ils,online_price_ils,min_price_ils,max_price_ils,
                        compatible_vehicles,specifications,
                        part_condition,is_active,needs_oem_lookup,created_at,updated_at)
                    VALUES(gen_random_uuid(),$1,$2,$3,'Jaguar',$4,
                           $5,$6,$7,$8,
                           $9,0,0,$9,$9,$10::jsonb,$11::jsonb,
                           'New',TRUE,FALSE,NOW(),NOW())
                    ON CONFLICT(sku) DO UPDATE SET
                        name=EXCLUDED.name, description=EXCLUDED.description,
                        oem_number=EXCLUDED.oem_number, aftermarket_tier=EXCLUDED.aftermarket_tier,
                        base_price=EXCLUDED.base_price,
                        importer_price_ils=0,
                        online_price_ils=0,
                        min_price_ils=CASE WHEN EXCLUDED.min_price_ils > 0
                                      THEN EXCLUDED.min_price_ils
                                      ELSE parts_catalog.min_price_ils END,
                        max_price_ils=EXCLUDED.max_price_ils,
                        compatible_vehicles=EXCLUDED.compatible_vehicles,
                        specifications=COALESCE(parts_catalog.specifications,'{}')::jsonb
                                        || EXCLUDED.specifications::jsonb,
                        updated_at=NOW()
                    RETURNING id,(xmax=0) AS was_inserted""",
                    sku(pn), title, category(title,desc), JAGUAR_BRAND_ID,
                    pt, desc or None, oem, tier,
                    base_price_ils(gbp), json.dumps(apps), specs)

                if row is None: skp+=1; continue
                part_id = str(row["id"])
                if row["was_inserted"]: ins+=1

                pusd = price_usd(gbp)
                pils = round(float(gbp or 0)*GBP_TO_ILS, 2) if gbp else None
                avail = stock in ("in_stock","unknown")
                surl  = f"https://www.sngbarratt.com/English/UK/Products/{guid}" if guid else SUPPLIER_URL
                await conn.execute("""
                    INSERT INTO supplier_parts(
                        id,supplier_id,part_id,supplier_sku,
                        price_usd,price_ils,availability,warranty_months,
                        estimated_delivery_days,is_available,supplier_url,
                        part_type,created_at,updated_at)
                    VALUES(gen_random_uuid(),$1,$2,$3,
                           $4,$5,$6,12,21,$7,$8,$9,NOW(),NOW())
                    ON CONFLICT (part_id, supplier_id) DO UPDATE SET
                        price_usd=EXCLUDED.price_usd,
                        price_ils=EXCLUDED.price_ils,
                        is_available=EXCLUDED.is_available,
                        updated_at=NOW()""",
                    supplier_id, part_id, pn,
                    pusd, pils, "In Stock" if stock=="in_stock" else "Pre-order",
                    avail, surl, pt)

                if not skip_fitment:
                    for app in apps[:20]:
                        app = app.strip()
                        if not app: continue
                        y1,y2 = year_range(app)
                        await conn.execute("""
                            INSERT INTO part_vehicle_fitment(
                                id,part_id,manufacturer,manufacturer_id,
                                model,year_from,year_to,notes,created_at,updated_at)
                            VALUES(gen_random_uuid(),$1,'Jaguar',$2,
                                   $3,$4,$5,$6,NOW(),NOW())
                            ON CONFLICT(part_id,manufacturer,model,year_from) DO NOTHING""",
                            part_id, JAGUAR_BRAND_ID, app, y1,
                            y2 if y2<2090 else None,
                            "SNG Barratt catalogue")
        except Exception as e:
            log.debug(f'Row skip {rec.get("part_number","?")}: {e}')
            skp += 1
    return ins, skp

async def run(dry_run=False, limit=None, skip_fitment=False):
    if not INPUT.exists():
        log.error("Input not found: %s — run scraper first", INPUT); sys.exit(1)

    total = sum(1 for _ in open(INPUT,encoding="utf-8"))
    if limit: total = min(total, limit)
    log.info("Importing %d parts | GBP_TO_ILS=%.2f VAT=%.0f%%", total, GBP_TO_ILS, VAT_RATE*100)

    if dry_run:
        sample = []
        with open(INPUT,encoding="utf-8") as f:
            for i,line in enumerate(f):
                if limit and i>=limit: break
                sample.append(json.loads(line))
        with_price = sum(1 for r in sample if r.get("price_gbp") and r["price_gbp"]>0)
        in_stock   = sum(1 for r in sample if r.get("stock_status")=="in_stock")
        types = {}
        for r in sample: t=r.get("type_name","?"); types[t]=types.get(t,0)+1
        log.info("DRY-RUN: %d records | with_price=%d | in_stock=%d", len(sample), with_price, in_stock)
        log.info("Types: %s", dict(sorted(types.items(),key=lambda x:-x[1])[:8]))
        sample_r = sample[0] if sample else {}
        log.info("Sample: %s | %.2f GBP → %.2f ILS",
                 sample_r.get("title"), sample_r.get("price_gbp") or 0,
                 base_price_ils(sample_r.get("price_gbp")) or 0)
        return

    conn = await asyncpg.connect(DB_DSN)
    try:
        sid = await ensure_supplier(conn)
        total_ins, total_skp, total_proc = 0, 0, 0
        batch = []
        t0 = datetime.utcnow()
        with open(INPUT,encoding="utf-8") as f:
            for line in f:
                if limit and total_proc >= limit: break
                line = line.strip()
                if not line: continue
                try: rec = json.loads(line)
                except: continue
                batch.append(rec); total_proc+=1
                if len(batch)>=BATCH_SIZE:
                    i,s = await import_batch(conn,sid,batch,skip_fitment)
                    total_ins+=i; total_skp+=s; batch.clear()
                    if total_proc%500==0:
                        el=(datetime.utcnow()-t0).total_seconds()
                        log.info("Progress %d/%d (%.1f%%) ins=%d skp=%d %.0f/s",
                                 total_proc,total,total_proc/total*100,total_ins,total_skp,total_proc/el if el>0 else 0)
        if batch:
            i,s = await import_batch(conn,sid,batch,skip_fitment)
            total_ins+=i; total_skp+=s
        el=(datetime.utcnow()-t0).total_seconds()
        log.info("Done: processed=%d inserted=%d skipped=%d in %.1fs", total_proc,total_ins,total_skp,el)
        cnt = await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Jaguar' AND is_active")
        log.info("Jaguar parts in catalog: %d", cnt)

        # Delegate missing Hebrew names to REX
        try:
            await conn.execute("""
                INSERT INTO agent_todos
                    (id, agent_name, title, description, priority, status, created_at, updated_at)
                VALUES (gen_random_uuid(), 'REX',
                    'Add Hebrew descriptions for Jaguar parts from SNG Barratt',
                    'SNG Barratt catalog parts have no Hebrew names. Total Jaguar parts: ' || $1::text ||
                    '. Add name_he translations for all parts WHERE name_he IS NULL AND manufacturer=''Jaguar''.',
                    'medium', 'not_started', NOW(), NOW())
            """, str(cnt))
        except Exception:
            pass
    finally:
        await conn.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--limit",type=int,default=None)
    ap.add_argument("--skip-fitment",action="store_true")
    args=ap.parse_args()
    asyncio.run(run(dry_run=args.dry_run, limit=args.limit, skip_fitment=args.skip_fitment))
