#!/usr/bin/env python3
import json, re, subprocess, sys, uuid
from pathlib import Path

JSON_FILE    = "/opt/autosparefinder/land_rover_parts.json"
MANUFACTURER = "Land Rover"
MFR_ID       = "7f060acf-2382-42e1-8413-f9b045cb0836"
BATCH        = 100

CATEGORY_MAP = {
    "brake":"Brakes & Clutch","clutch":"Brakes & Clutch",
    "filter":"Filters","fuel":"Fuel System",
    "engine":"Engine & Drivetrain","piston":"Engine & Drivetrain","gasket":"Engine & Drivetrain",
    "belt":"Engine & Drivetrain","timing":"Engine & Drivetrain","seal":"Engine & Drivetrain",
    "suspension":"Suspension & Steering","strut":"Suspension & Steering",
    "shock":"Suspension & Steering","bearing":"Suspension & Steering",
    "steering":"Suspension & Steering","rack":"Suspension & Steering",
    "exhaust":"Exhaust","muffler":"Exhaust",
    "light":"Lighting & Electrical","lamp":"Lighting & Electrical",
    "battery":"Lighting & Electrical","sensor":"Lighting & Electrical",
    "body":"Body & Interior","door":"Body & Interior","seat":"Body & Interior",
    "mirror":"Body & Interior","windscreen":"Body & Interior","wiper":"Body & Interior",
    "wheel":"Wheels & Tyres","tyre":"Wheels & Tyres","tire":"Wheels & Tyres",
    "transmission":"Gearbox & Transmission","gearbox":"Gearbox & Transmission",
    "cooling":"Cooling System","radiator":"Cooling System","thermostat":"Cooling System",
}

def guess_category(name, desc=""):
    text=(name+" "+desc).lower()
    for kw,cat in CATEGORY_MAP.items():
        if kw in text: return cat
    return "Other"

def fix_name(name):
    name=name.strip().strip('"')
    name=re.sub(r'\s*\[[A-Z0-9]{2,6}\]\s*$','',name)
    name=re.sub(r' {2,}',' ',name).strip()
    if name==name.upper() and re.search(r'[A-Z]{3,}',name):
        name=name.title()
    return name

def make_sku(raw):
    return "LR-"+re.sub(r'[^A-Z0-9\-]','',raw.upper())[:80]

def esc(s):
    return s.replace("'","''")

def run_sql(sql):
    r=subprocess.run(
        ["docker","exec","-i","autospare_postgres_catalog",
         "psql","-U","autospare","-d","autospare","-t","-A","-c",sql],
        capture_output=True,text=True,timeout=30)
    if r.returncode!=0: raise RuntimeError(f"psql error: {r.stderr.strip()}")
    return r.stdout.strip()

def run_sql_pipe(sql):
    r=subprocess.run(
        ["docker","exec","-i","autospare_postgres_catalog",
         "psql","-U","autospare","-d","autospare","-q"],
        input=sql,capture_output=True,text=True,timeout=60)
    if r.returncode!=0: raise RuntimeError(f"psql error: {r.stderr.strip()}")

def main():
    p=Path(JSON_FILE)
    if not p.exists(): print(f"ERROR: {JSON_FILE} not found"); sys.exit(1)
    raw=json.loads(p.read_text(encoding="utf-8"))
    parts=raw.get("parts",raw) if isinstance(raw,dict) else raw
    print(f"Loaded {len(parts)} parts from JSON")

    old=int(run_sql(f"SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Land Rover' AND is_active=TRUE"))
    print(f"Existing active LR parts: {old} -> deactivating...")
    run_sql(f"UPDATE parts_catalog SET is_active=FALSE,updated_at=NOW() WHERE manufacturer='Land Rover'")

    inserted=0; skipped=0; rows=[]
    for rp in parts:
        raw_sku=str(rp.get("sku") or rp.get("part_number") or "").strip()
        if not raw_sku: skipped+=1; continue
        name=fix_name(str(rp.get("name") or rp.get("title") or "").strip()[:255])
        if not name: skipped+=1; continue
        desc=esc(str(rp.get("description") or "").strip()[:2000])
        price=float(rp.get("price") or rp.get("retail_price") or 0)
        oem=esc(str(rp.get("oem_number") or raw_sku).strip()[:100])
        cat=guess_category(name,rp.get("description") or "")
        sku=make_sku(raw_sku)
        pid=str(uuid.uuid4())
        rows.append(f"('{pid}','{esc(sku)}','{esc(name)}','{esc(cat)}','Land Rover',{price},{price},'{oem}',TRUE,NOW(),NOW(),'{MFR_ID}'::uuid)")
        if len(rows)>=BATCH:
            run_sql_pipe("INSERT INTO parts_catalog(id,sku,name,category,manufacturer,base_price,importer_price_ils,oem_number,is_active,created_at,updated_at,manufacturer_id) VALUES "+",".join(rows)+" ON CONFLICT(sku) DO UPDATE SET name=EXCLUDED.name,category=EXCLUDED.category,base_price=EXCLUDED.base_price,importer_price_ils=EXCLUDED.importer_price_ils,oem_number=EXCLUDED.oem_number,is_active=TRUE,updated_at=NOW();")
            inserted+=len(rows); rows=[]
            if inserted%500==0: print(f"  {inserted} inserted...")
    if rows:
        run_sql_pipe("INSERT INTO parts_catalog(id,sku,name,category,manufacturer,base_price,importer_price_ils,oem_number,is_active,created_at,updated_at,manufacturer_id) VALUES "+",".join(rows)+" ON CONFLICT(sku) DO UPDATE SET name=EXCLUDED.name,category=EXCLUDED.category,base_price=EXCLUDED.base_price,importer_price_ils=EXCLUDED.importer_price_ils,oem_number=EXCLUDED.oem_number,is_active=TRUE,updated_at=NOW();")
        inserted+=len(rows)
    total=int(run_sql("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Land Rover' AND is_active=TRUE"))
    priced=int(run_sql("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Land Rover' AND is_active=TRUE AND base_price>0"))
    print(f"\n{'='*50}")
    print(f"IMPORT COMPLETE - Land Rover")
    print(f"  Inserted/updated : {inserted}")
    print(f"  Skipped          : {skipped}")
    print(f"  Active in DB     : {total}")
    print(f"  With price       : {priced}")
    print(f"{'='*50}")

if __name__=="__main__":
    main()
