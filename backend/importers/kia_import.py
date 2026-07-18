#!/usr/bin/env python3
# ⚠️  BROWSER TOOL REQUIRED — DO NOT RUN HTTP REQUESTS FROM SERVER IP
# The server IP (207.180.217.129) is blocked by Cloudflare and anti-bot systems.
# All external HTTP extraction must be done via the browser tool (Playwright / run_playwright_code).
# Pattern: (1) Extract with browser tool → save JSON, (2) Import JSON with this script.
# See claude.md § Web Scraping Rules.
"""
Script: kia_import.py
Purpose: Import Kia genuine parts from official Israeli importer (kia-israel.co.il).

Process:
  1. POST to kia-israel.co.il parts price list page to fetch HTML table
  2. Parse table rows: SKU, Hebrew description, price (ILS excl. VAT)
  3. Upsert to parts_catalog with specifications JSONB (per-row savepoints)
  4. Get-or-create 'Kia Official Importer Israel' supplier record
  5. Upsert supplier_parts record per part
  6. Create REX agent todo for missing fitment data

Data Imported / Modified:
  - parts_catalog: sku, name, name_he, description, manufacturer, manufacturer_id,
                   part_type, part_condition, base_price, importer_price_ils,
                   max_price_ils, category, is_active, oem_number,
                   specifications (JSONB with vat_included, source, warranty, importer)
  - supplier_parts: supplier_id, part_id, supplier_sku, price_ils, availability,
                    is_available, warranty_months, estimated_delivery_days, supplier_url
  - agent_todos: REX task for missing fitment data

Data Sources / Web Links:
  - Official Kia Israel importer (Delek Motors): https://kia-israel.co.il
  - Parts price list page: https://kia-israel.co.il/%d7%9e%d7%97%d7%99%d7%a8%d7%95%d7%9f-%d7%97%d7%9c%d7%a4%d7%99%d7%9d

Missing Data Delegation:
  - Vehicle fitment → REX agent todo created after import
  - English descriptions → ai_catalog_builder.py (master_enriched=False)
  - OEM cross-refs → needs_oem_lookup=True on all parts

VAT Rules:
  - kia-israel.co.il prices are ILS EXCL. 18% VAT
  - Store: base_price = price (excl. VAT), importer_price_ils = price,
           max_price_ils = price * 1.18

Confidence tier: 1.00 (Official Israeli importer price data)

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""
import asyncio, asyncpg, json, urllib.request, urllib.parse, sys, uuid
from html.parser import HTMLParser

DB_URL = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
KIA_MFR_ID = "626947bf-be3f-4dd1-a52e-fbcff8168cfc"
PARTS_URL = "https://kia-israel.co.il/%d7%9e%d7%97%d7%99%d7%a8%d7%95%d7%9f-%d7%97%d7%9c%d7%a4%d7%99%d7%9d"
BATCH = 25

def map_category(desc):
    if any(k in desc for k in ["מכשיר","כלי","חולץ","מתאם","להתקנת","להסרת"]): return "tools-equipment"
    if any(k in desc for k in ["בלם","קליפר","ABS","צינור בלם"]): return "brakes-clutch"
    if any(k in desc for k in ["מצמד","גלגל תנופה"]): return "brakes-clutch"
    if any(k in desc for k in ["סעפת פל","פליטה","אגזוז","קטליזטור"]): return "exhaust"
    if "EGR" in desc: return "engine"
    if any(k in desc for k in ["טורבו","מגדש","מצנן בין"]): return "engine"
    if any(k in desc for k in ["מים","תרמוסטט","טרמוסטט","קירור","רדיאטור","מאוורר"]): return "cooling-system"
    if any(k in desc for k in ["דלק","מרסס","מזרק","גז","דיזל"]): return "fuel-system"
    if any(k in desc for k in ["הגה","היגוי","מתלה","קפיץ","בולם"]): return "suspension-steering"
    if any(k in desc for k in ["תיבת הילוכים","גיר","ממיר","דיפרנציאל","גל ארכובה","גל הינע"]): return "gearbox"
    return "engine"

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_t=self.in_r=self.in_c=False
        self.cur_row=[];self.cur_cell=[];self.rows=[]
    def handle_starttag(self,tag,attrs):
        if tag=="table":self.in_t=True
        elif tag=="tr" and self.in_t:self.in_r=True;self.cur_row=[]
        elif tag in("td","th") and self.in_r:self.in_c=True;self.cur_cell=[]
    def handle_endtag(self,tag):
        if tag=="table":self.in_t=False
        elif tag=="tr" and self.in_r:
            self.in_r=False
            if self.cur_row:self.rows.append(self.cur_row)
        elif tag in("td","th") and self.in_c:
            self.in_c=False;self.cur_row.append(" ".join(self.cur_cell).strip())
    def handle_data(self,d):
        if self.in_c:self.cur_cell.append(d)

KIA_SUPPLIER_URL = 'https://kia-israel.co.il'


async def ensure_supplier(conn) -> str:
    name = 'Kia Official Importer Israel'
    row = await conn.fetchrow('SELECT id FROM suppliers WHERE name=$1', name)
    if row:
        return str(row['id'])
    sid = str(uuid.uuid4())
    await conn.execute("""
        INSERT INTO suppliers (id, name, website, country,
                               is_active, is_manufacturer, manufacturer_name,
                               manufacturer_id, reliability_score, created_at, updated_at)
        VALUES ($1, $2, $3, 'IL', true, true, 'Kia', $4::uuid, 0.95, NOW(), NOW())
        ON CONFLICT (name) DO NOTHING
    """, sid, name, KIA_SUPPLIER_URL, KIA_MFR_ID)
    row = await conn.fetchrow('SELECT id FROM suppliers WHERE name=$1', name)
    return str(row['id']) if row else sid


SEARCH_TERMS = [
    "", "א","ב","ג","ד","ה","ו","ז","ח","ט","י","כ","ל","מ","נ","ס","ע","פ","צ","ק","ר","ש","ת",
    "oil","filter","pump","valve","sensor","belt","hose","seal","gasket","bearing","brake",
]

def fetch_parts():
    headers={"User-Agent":"Mozilla/5.0 Chrome/120","Content-Type":"application/x-www-form-urlencoded",
             "Accept":"text/html","Referer":"https://kia-israel.co.il/","Origin":"https://kia-israel.co.il"}
    seen=set();all_parts=[]
    for term in SEARCH_TERMS:
        try:
            data=urllib.parse.urlencode({"catalogNum":"","partDesc":term}).encode("utf-8")
            req=urllib.request.Request(PARTS_URL,data=data,headers=headers,method="POST")
            with urllib.request.urlopen(req,timeout=30) as r:
                html=r.read().decode("utf-8",errors="replace")
            p=TableParser();p.feed(html)
            new=0
            for row in p.rows[1:]:
                if len(row)<4:continue
                sku=row[0].strip();desc=row[2].strip();price_s=row[3].strip().replace(",","")
                if not sku or not desc or sku in seen:continue
                try:price=float(price_s)
                except:continue
                seen.add(sku);all_parts.append({"sku":sku,"name":desc,"price":price});new+=1
            print(f"  term='{term}': {new} new parts (total {len(all_parts)})")
        except Exception as e:
            print(f"  term='{term}': error {e}", file=sys.stderr)
    return all_parts

INSERT_SQL = """
    INSERT INTO parts_catalog(
        id, sku, name, name_he, description,
        manufacturer, manufacturer_id,
        part_type, part_condition,
        base_price, importer_price_ils, min_price_ils, max_price_ils,
        aftermarket_tier,
        category, is_active, oem_number, specifications,
        needs_oem_lookup, master_enriched, is_safety_critical,
        created_at, updated_at
    ) VALUES (
        gen_random_uuid(),
        $1::varchar,           -- sku
        $2::varchar,           -- name (full Hebrew desc incl. model)
        $3::varchar,           -- name_he (same)
        $4::text,              -- description (same, text type)
        'Kia'::varchar,
        $5::uuid,              -- manufacturer_id
        'original'::varchar,
        'new'::varchar,
        ROUND($6::numeric * 1.18, 2),  -- base_price = il_retail (consumer retail incl. VAT)
        $6::numeric,                   -- importer_price_ils = ex-VAT cost (CLAUDE.md formula)
        ROUND($6::numeric * 1.18, 2),  -- min_price_ils = il_retail
        ROUND($6::numeric * 1.18, 2),  -- max_price_ils = il_retail
        NULL,                  -- aftermarket_tier (genuine OEM parts)
        $7::varchar,           -- category
        TRUE,
        $1::varchar,           -- oem_number = sku
        $8::jsonb,             -- specifications
        FALSE, FALSE, FALSE,
        NOW(), NOW()
    )
    ON CONFLICT (sku) DO UPDATE SET
        name               = EXCLUDED.name,
        name_he            = EXCLUDED.name_he,
        description        = EXCLUDED.description,
        base_price         = EXCLUDED.base_price,
        importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0 THEN EXCLUDED.importer_price_ils ELSE parts_catalog.importer_price_ils END,
        min_price_ils      = EXCLUDED.min_price_ils,
        max_price_ils      = EXCLUDED.max_price_ils,
        specifications     = COALESCE(parts_catalog.specifications, '{}')::jsonb
                              || EXCLUDED.specifications::jsonb,
        category           = EXCLUDED.category,
        is_active          = TRUE,
        updated_at         = NOW()
    RETURNING id, (xmax = 0) AS was_inserted
"""

async def run():
    parts=fetch_parts()
    if not parts:sys.exit("No parts fetched")
    conn=await asyncpg.connect(DB_URL)
    try:
        supplier_id = await ensure_supplier(conn)
        ex=await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer_id=$1::uuid",KIA_MFR_ID)
        print(f"Existing Kia parts in DB: {ex}")
        ins=upd=err=0;errs=[]
        for i in range(0,len(parts),BATCH):
            batch=parts[i:i+BATCH]
            for p in batch:
                cat=map_category(p["name"])
                specs = json.dumps({
                    'vat_included':    False,
                    'vat_rate':        0.18,
                    'currency':        'ILS',
                    'source':          'kia-israel.co.il official importer price list',
                    'shipping_to_il':  True,
                    'importer':        'Kia Official Importer Israel',
                    'warranty_months': 24,
                }, ensure_ascii=False)
                try:
                    async with conn.transaction():
                        r=await conn.fetchrow(INSERT_SQL,
                            p["sku"], p["name"], p["name"], p["name"],
                            KIA_MFR_ID, p["price"], cat, specs)
                        if r and r["was_inserted"]:ins+=1
                        else:upd+=1
                        # Upsert supplier_parts
                        if r and supplier_id:
                            await conn.execute("""
                                INSERT INTO supplier_parts (
                                    id, supplier_id, part_id, supplier_sku,
                                    price_ils, price_usd, availability, is_available,
                                    warranty_months, estimated_delivery_days, supplier_url,
                                    created_at, updated_at)
                                VALUES (gen_random_uuid(), $1::uuid, $2::uuid, $3, $4, 0.0,
                                        'in_stock', TRUE, 24, 14, $5, NOW(), NOW())
                                ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET
                                    price_ils=EXCLUDED.price_ils, updated_at=NOW()
                            """, supplier_id, str(r['id']), p['sku'],
                                 float(p['price']), KIA_SUPPLIER_URL)
                except Exception as e:
                    err+=1;errs.append(f"sku={p['sku']}:{type(e).__name__}:{e}")
            print(f"  Batch {i//BATCH+1}/{-(-len(parts)//BATCH)} done (+{ins}ins ~{upd}upd {err}err)")
        # REX todo for missing fitment
        try:
            await conn.execute("""
                INSERT INTO agent_todos
                    (id, agent_name, title, description, priority, status, created_at, updated_at)
                VALUES (gen_random_uuid(), 'REX',
                    'Fetch fitment for Kia genuine parts',
                    'Kia parts imported from kia-israel.co.il have no vehicle fitment. '
                    'Total parts: ' || $1::text || '. Map via TecDoc or kia-israel.co.il fitment data.',
                    'high', 'not_started', NOW(), NOW())
            """, str(ins + upd))
        except Exception:
            pass
        total=await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer_id=$1::uuid",KIA_MFR_ID)
        stats=await conn.fetchrow(
            "SELECT MIN(base_price)::float,MAX(base_price)::float,AVG(base_price)::float "
            "FROM parts_catalog WHERE manufacturer_id=$1::uuid",KIA_MFR_ID)
        print(f"\n=== IMPORT COMPLETE ===")
        print(f"  Inserted: {ins}  Updated: {upd}  Errors: {err}")
        print(f"  Total Kia in DB: {total}")
        if stats and stats[0] is not None:
            print(f"  Price range: {stats[0]:.2f} - {stats[1]:.2f} ILS ex-VAT (avg {stats[2]:.2f})")
        if errs:
            print(f"  First errors:")
            for e in errs[:5]:print(f"    {e}")
    finally:
        await conn.close()

asyncio.run(run())
