#!/usr/bin/env python3
"""
KIA Genuine Parts Import — kia-israel.co.il
Prices: ex-VAT ILS  |  Name: full Hebrew description INCLUDING embedded model (e.g. "אטם ראש מנוע,פיקנטו")
manufacturer_id: 626947bf-be3f-4dd1-a52e-fbcff8168cfc (car_brands)
"""
import asyncio, asyncpg, urllib.request, urllib.parse, sys
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

def fetch_parts():
    print("Fetching from kia-israel.co.il ...")
    data=urllib.parse.urlencode({"catalogNum":"","partDesc":"אטם"}).encode("utf-8")
    headers={"User-Agent":"Mozilla/5.0 Chrome/120","Content-Type":"application/x-www-form-urlencoded",
             "Accept":"text/html","Referer":"https://kia-israel.co.il/","Origin":"https://kia-israel.co.il"}
    req=urllib.request.Request(PARTS_URL,data=data,headers=headers,method="POST")
    with urllib.request.urlopen(req,timeout=30) as r:
        html=r.read().decode("utf-8",errors="replace")
    p=TableParser();p.feed(html)
    parts=[]
    for row in p.rows[1:]:
        if len(row)<4:continue
        sku=row[0].strip();desc=row[2].strip();price_s=row[3].strip().replace(",","")
        if not sku or not desc:continue
        try:price=float(price_s)
        except:print(f"  SKIP bad price '{price_s}' sku={sku}",file=sys.stderr);continue
        parts.append({"sku":sku,"name":desc,"price":price})
    print(f"  Parsed {len(parts)} parts");return parts

# Fix for asyncpg AmbiguousParameterError:
# Use separate $-params for name(varchar), name_he(varchar), description(text)
# Cast manufacturer_id and numeric fields explicitly
INSERT_SQL = """
    INSERT INTO parts_catalog(
        id, sku, name, name_he, description,
        manufacturer, manufacturer_id,
        part_type, part_condition,
        base_price, importer_price_ils,
        category, is_active, oem_number,
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
        $6::numeric,           -- base_price (ex-VAT)
        $6::numeric,           -- importer_price_ils
        $7::varchar,           -- category
        TRUE,
        $1::varchar,           -- oem_number = sku
        FALSE, FALSE, FALSE,
        NOW(), NOW()
    )
    ON CONFLICT (sku) DO UPDATE SET
        name               = EXCLUDED.name,
        name_he            = EXCLUDED.name_he,
        description        = EXCLUDED.description,
        base_price         = EXCLUDED.base_price,
        importer_price_ils = EXCLUDED.importer_price_ils,
        category           = EXCLUDED.category,
        is_active          = TRUE,
        updated_at         = NOW()
    RETURNING (xmax = 0) AS was_inserted
"""

async def run():
    parts=fetch_parts()
    if not parts:sys.exit("No parts fetched")
    conn=await asyncpg.connect(DB_URL)
    try:
        ex=await conn.fetchval("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer_id=$1::uuid",KIA_MFR_ID)
        print(f"Existing Kia parts in DB: {ex}")
        ins=upd=err=0;errs=[]
        for i in range(0,len(parts),BATCH):
            batch=parts[i:i+BATCH]
            async with conn.transaction():
                for p in batch:
                    cat=map_category(p["name"])
                    try:
                        r=await conn.fetchrow(INSERT_SQL,
                            p["sku"], p["name"], p["name"], p["name"],
                            KIA_MFR_ID, p["price"], cat)
                        if r and r["was_inserted"]:ins+=1
                        else:upd+=1
                    except Exception as e:
                        err+=1;errs.append(f"sku={p['sku']}:{type(e).__name__}:{e}")
            print(f"  Batch {i//BATCH+1}/{-(-len(parts)//BATCH)} done (+{ins}ins ~{upd}upd {err}err)")
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
