#!/usr/bin/env python3
"""Kia new model parts importer — kia-israel.co.il"""
import asyncio, asyncpg, urllib.request, urllib.parse
from html.parser import HTMLParser

DB_URL   = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
KIA_MFR  = "626947bf-be3f-4dd1-a52e-fbcff8168cfc"
PARTS_URL= "https://kia-israel.co.il/%d7%9e%d7%97%d7%99%d7%a8%d7%95%d7%9f-%d7%97%d7%9c%d7%a4%d7%99%d7%9d"
BATCH    = 25

MODELS = [
    ("Carens",    "קרנס"),
    ("Cadenza",   "קדנזה"),
    ("Pregio",    "פרגיו"),
    ("Sephia II", "ספיה"),
    ("Pride",     "פרייד"),
    ("Shuma",     "שומה"),
    ("Clarus",    "קלרוס"),
    ("Mentor",    "מנטור"),
    ("Opirus",    "אופירוס"),
    ("Joice",     "ג'וייס"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/120",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/html",
    "Referer": "https://kia-israel.co.il/",
    "Origin": "https://kia-israel.co.il"
}

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

def map_cat(desc):
    if any(k in desc for k in ["בלם","קליפר","ABS"]): return "brakes-clutch"
    if any(k in desc for k in ["מצמד","גלגל תנופה"]): return "brakes-clutch"
    if any(k in desc for k in ["טורבו","מגדש","EGR"]): return "engine"
    if any(k in desc for k in ["מים","קירור","רדיאטור","תרמוסטט"]): return "cooling-system"
    if any(k in desc for k in ["דלק","מרסס","מזרק"]): return "fuel-system"
    if any(k in desc for k in ["הגה","היגוי","מתלה","קפיץ","בולם"]): return "suspension-steering"
    if any(k in desc for k in ["תיבת הילוכים","גיר","דיפרנציאל","גל ארכובה"]): return "gearbox"
    if any(k in desc for k in ["סעפת","פליטה","אגזוז","קטליזטור"]): return "exhaust"
    if any(k in desc for k in ["מנוע","אטם","שסתום","בוכנה","קמשאפ"]): return "engine"
    if any(k in desc for k in ["פנס","אור","נורה","מראה","שמשה"]): return "electrical-lighting"
    if any(k in desc for k in ["מסנן","שמן","אוויר"]): return "filters-oils"
    return "engine"

def fetch_model(search_term):
    data = urllib.parse.urlencode({"catalogNum":"","partDesc": search_term}).encode("utf-8")
    req  = urllib.request.Request(PARTS_URL, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", errors="replace")
    p = TableParser(); p.feed(html)
    parts = []
    for row in p.rows[1:]:
        if len(row) < 4: continue
        sku  = row[0].strip()
        desc = row[2].strip()
        price_s = row[3].strip().replace(",","")
        if not sku or not desc: continue
        try: price = float(price_s)
        except: price = 0.0
        parts.append({"sku": sku, "name": desc, "price": price, "category": map_cat(desc)})
    return parts

async def run():
    conn = await asyncpg.connect(DB_URL)
    total_parts = 0
    total_fitment = 0

    for model_en, model_he in MODELS:
        print(f"\n[{model_en}] Fetching '{model_he}'...", flush=True)
        try:
            parts = fetch_model(model_he)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            continue
        print(f"  Got {len(parts)} parts", flush=True)

        parts_added = 0
        fit_added   = 0

        for i in range(0, len(parts), BATCH):
            batch = parts[i:i+BATCH]
            async with conn.transaction():
                for p in batch:
                    sku  = ("KIA-" + p["sku"])[:100]
                    name = p["name"][:255]
                    cat  = p["category"]

                    # gen_random_uuid() supplies the id
                    part_id = await conn.fetchval("""
                        INSERT INTO parts_catalog
                          (id, sku, name, category, base_price, oem_number,
                           manufacturer, manufacturer_id, is_active,
                           part_condition, needs_oem_lookup)
                        VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,'Kia',$6,TRUE,'New',FALSE)
                        ON CONFLICT (sku) DO UPDATE
                          SET name=EXCLUDED.name,
                              base_price=EXCLUDED.base_price,
                              category=EXCLUDED.category,
                              updated_at=NOW()
                        RETURNING id
                    """, sku, name, cat, p["price"], p["sku"], KIA_MFR)

                    if part_id:
                        parts_added += 1
                        await conn.execute("""
                            INSERT INTO part_vehicle_fitment
                              (part_id, manufacturer, model, year_from, manufacturer_id)
                            VALUES ($1,'Kia',$2,0,$3)
                            ON CONFLICT (part_id, manufacturer, model, year_from) DO NOTHING
                        """, part_id, model_en, KIA_MFR)
                        fit_added += 1

        print(f"  {model_en}: {parts_added} parts upserted, {fit_added} fitment records", flush=True)
        total_parts   += parts_added
        total_fitment += fit_added

    await conn.close()
    print(f"\nDONE. Total: {total_parts} parts, {total_fitment} fitment records", flush=True)

asyncio.run(run())
