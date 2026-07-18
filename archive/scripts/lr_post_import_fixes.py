#!/usr/bin/env python3
"""
Land Rover post-import fix pipeline:
  1. Name fix  — HTML entities, trim artefacts
  2. Category  — fix lowercase + re-classify "Other" with richer keyword map
  3. Fitment   — parse part names for sub-models, insert part_vehicle_fitment rows
"""
import subprocess, sys, re, json, uuid
from pathlib import Path

MFR       = "Land Rover"
MFR_ID    = "7f060acf-2382-42e1-8413-f9b045cb0836"
JSON_FILE = "/opt/autosparefinder/land_rover_parts.json"

# ── helpers ───────────────────────────────────────────────────────────────────
def run(sql, fetch=False):
    args = ["docker","exec","-i","autospare_postgres_catalog",
            "psql","-U","autospare","-d","autospare","-t","-A"]
    if fetch: args += ["-c", sql]
    else:     args += ["-q"]
    r = subprocess.run(args, input=None if fetch else sql,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0: raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()

def runq(sql): return run(sql, fetch=True)

# ── STEP 1: HTML entity fix ───────────────────────────────────────────────────
def fix_names():
    print("\n── STEP 1: HTML entity fix ──────────────────")
    sql = """
UPDATE parts_catalog SET
  name = regexp_replace(
           replace(replace(replace(replace(replace(
             name,
             '&amp;','&'), '&#38;','&'), '&nbsp;',' '),
             '&#39;',''''), '&quot;','"'),
           '  +',' ','g'),
  updated_at = NOW()
WHERE manufacturer='Land Rover' AND is_active=TRUE
  AND (name LIKE '%&amp;%' OR name LIKE '%&#%' OR name LIKE '%&nbsp;%');
"""
    run(sql)
    n = int(runq("SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='Land Rover' AND is_active=TRUE AND (name LIKE '%&amp;%' OR name LIKE '%&#%')"))
    print(f"  Remaining with HTML entities: {n}")

# ── STEP 2: Category fix ──────────────────────────────────────────────────────
SCRAPER_CAT_MAP = {
    # scraper slug → our category
    "brakes":"Brakes & Clutch", "brakes-clutch":"Brakes & Clutch",
    "clutch-drivetrain":"Engine & Drivetrain",
    "suspension-steering":"Suspension & Steering",
    "filters":"Filters", "service-general":"Filters",
    "fuel-air":"Fuel System",
    "engine":"Engine & Drivetrain",
    "gearbox":"Gearbox & Transmission",
    "exhausts":"Exhaust", "exhaust":"Exhaust",
    "cooling":"Cooling System",
    "electrical":"Lighting & Electrical", "lighting":"Lighting & Electrical",
    "wheels-bearings":"Wheels & Tyres",
    "air-conditioning-heating":"Cooling System",
    "body-chassis":"Body & Interior",
    "interior":"Body & Interior",
    "accessories":"Other",
}

# richer keyword map for re-classifying "Other"
KW = [
    # longer phrases first to avoid false matches
    ("master cylinder",    "Brakes & Clutch"),
    ("brake disc",         "Brakes & Clutch"),
    ("brake drum",         "Brakes & Clutch"),
    ("brake shoe",         "Brakes & Clutch"),
    ("brake pad",          "Brakes & Clutch"),
    ("brake caliper",      "Brakes & Clutch"),
    ("handbrake",          "Brakes & Clutch"),
    ("brake hose",         "Brakes & Clutch"),
    ("brake",              "Brakes & Clutch"),
    ("clutch",             "Brakes & Clutch"),
    ("air filter",         "Filters"),
    ("oil filter",         "Filters"),
    ("fuel filter",        "Filters"),
    ("pollen filter",      "Filters"),
    ("cabin filter",       "Filters"),
    ("filter",             "Filters"),
    ("fuel pump",          "Fuel System"),
    ("fuel tank",          "Fuel System"),
    ("injector",           "Fuel System"),
    ("injection relay",    "Fuel System"),
    ("carburettor",        "Fuel System"),
    ("carburetor",         "Fuel System"),
    ("throttle",           "Fuel System"),
    ("fuel",               "Fuel System"),
    ("water pump",         "Cooling System"),
    ("thermostat",         "Cooling System"),
    ("radiator",           "Cooling System"),
    ("coolant",            "Cooling System"),
    ("cooling",            "Cooling System"),
    ("heater matrix",      "Cooling System"),
    ("universal joint",    "Engine & Drivetrain"),
    (" uj ",               "Engine & Drivetrain"),
    ("crownwheel",         "Engine & Drivetrain"),
    ("pinion",             "Engine & Drivetrain"),
    ("differential",       "Engine & Drivetrain"),
    ("diff case",          "Engine & Drivetrain"),
    ("diff seal",          "Engine & Drivetrain"),
    ("propshaft",          "Engine & Drivetrain"),
    ("prop shaft",         "Engine & Drivetrain"),
    ("driveshaft",         "Engine & Drivetrain"),
    ("drive shaft",        "Engine & Drivetrain"),
    ("axle shaft",         "Engine & Drivetrain"),
    ("half shaft",         "Engine & Drivetrain"),
    ("transfer box",       "Gearbox & Transmission"),
    ("transfer case",      "Gearbox & Transmission"),
    ("gearbox",            "Gearbox & Transmission"),
    ("gear selector",      "Gearbox & Transmission"),
    ("transmission",       "Gearbox & Transmission"),
    ("torque",             "Gearbox & Transmission"),
    ("air suspension",     "Suspension & Steering"),
    ("coil spring",        "Suspension & Steering"),
    ("leaf spring",        "Suspension & Steering"),
    ("shock absorber",     "Suspension & Steering"),
    ("shock",              "Suspension & Steering"),
    ("spring",             "Suspension & Steering"),
    ("bush",               "Suspension & Steering"),
    ("ball joint",         "Suspension & Steering"),
    ("track rod",          "Suspension & Steering"),
    ("tie rod",            "Suspension & Steering"),
    ("panhard",            "Suspension & Steering"),
    ("radius arm",         "Suspension & Steering"),
    ("wishbone",           "Suspension & Steering"),
    ("anti-roll",          "Suspension & Steering"),
    ("sway bar",           "Suspension & Steering"),
    ("swivel",             "Suspension & Steering"),
    ("steering rack",      "Suspension & Steering"),
    ("power steering",     "Suspension & Steering"),
    ("steering pump",      "Suspension & Steering"),
    ("steering column",    "Suspension & Steering"),
    ("steering",           "Suspension & Steering"),
    ("suspension",         "Suspension & Steering"),
    ("bearing",            "Suspension & Steering"),
    ("exhaust manifold",   "Exhaust"),
    ("exhaust pipe",       "Exhaust"),
    ("exhaust",            "Exhaust"),
    ("silencer",           "Exhaust"),
    ("downpipe",           "Exhaust"),
    ("catalyst",           "Exhaust"),
    ("muffler",            "Exhaust"),
    ("headlamp",           "Lighting & Electrical"),
    ("headlight",          "Lighting & Electrical"),
    ("tail light",         "Lighting & Electrical"),
    ("tail lamp",          "Lighting & Electrical"),
    ("fog light",          "Lighting & Electrical"),
    ("indicator",          "Lighting & Electrical"),
    ("bulb",               "Lighting & Electrical"),
    ("relay",              "Lighting & Electrical"),
    ("fuse",               "Lighting & Electrical"),
    ("switch",             "Lighting & Electrical"),
    ("sensor",             "Lighting & Electrical"),
    ("alternator",         "Lighting & Electrical"),
    ("starter motor",      "Lighting & Electrical"),
    ("battery",            "Lighting & Electrical"),
    ("wiring",             "Lighting & Electrical"),
    ("light",              "Lighting & Electrical"),
    ("lamp",               "Lighting & Electrical"),
    ("alloy wheel",        "Wheels & Tyres"),
    ("steel wheel",        "Wheels & Tyres"),
    ("wheel nut",          "Wheels & Tyres"),
    ("wheel bolt",         "Wheels & Tyres"),
    ("wheel",              "Wheels & Tyres"),
    ("tyre",               "Wheels & Tyres"),
    ("tire",               "Wheels & Tyres"),
    ("tailgate",           "Body & Interior"),
    ("windscreen",         "Body & Interior"),
    ("wiper",              "Body & Interior"),
    ("mirror",             "Body & Interior"),
    ("door",               "Body & Interior"),
    ("bonnet",             "Body & Interior"),
    ("bumper",             "Body & Interior"),
    ("grille",             "Body & Interior"),
    ("seat",               "Body & Interior"),
    ("carpet",             "Body & Interior"),
    ("trim",               "Body & Interior"),
    ("headlining",         "Body & Interior"),
    ("insulation",         "Body & Interior"),
    ("window",             "Body & Interior"),
    ("weather strip",      "Body & Interior"),
    ("gasket",             "Engine & Drivetrain"),
    ("seal",               "Engine & Drivetrain"),
    ("o ring",             "Engine & Drivetrain"),
    ("piston",             "Engine & Drivetrain"),
    ("valve",              "Engine & Drivetrain"),
    ("camshaft",           "Engine & Drivetrain"),
    ("crankshaft",         "Engine & Drivetrain"),
    ("rocker",             "Engine & Drivetrain"),
    ("timing belt",        "Engine & Drivetrain"),
    ("timing chain",       "Engine & Drivetrain"),
    ("oil pump",           "Engine & Drivetrain"),
    ("engine mount",       "Engine & Drivetrain"),
    ("engine oil",         "Engine & Drivetrain"),
    ("engine",             "Engine & Drivetrain"),
    ("axle",               "Engine & Drivetrain"),
]

def cat_from_name(name):
    n = name.lower()
    for kw, cat in KW:
        if kw in n:
            return cat
    return None

def fix_categories():
    print("\n── STEP 2: Category fix ─────────────────────")

    # 2a — fix lowercase/slug categories from scraper
    for slug, full in SCRAPER_CAT_MAP.items():
        sql = f"UPDATE parts_catalog SET category='{full}',updated_at=NOW() WHERE manufacturer='{MFR}' AND is_active=TRUE AND category='{slug}';"
        run(sql)
    n_slug = int(runq(f"SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='{MFR}' AND is_active=TRUE AND category NOT LIKE '%&%' AND category NOT IN ('Other','Filters','Exhaust') AND LOWER(category)=category"))
    print(f"  Remaining lowercase slugs: {n_slug}")

    # 2b — re-classify "Other" using richer keyword map
    # Fetch all "Other" parts (id + name)
    rows = runq(f"SELECT id||'|'||name FROM parts_catalog WHERE manufacturer='{MFR}' AND is_active=TRUE AND category='Other' LIMIT 5000")
    if not rows:
        print("  No 'Other' parts to reclassify")
        return

    lines = rows.split('\n')
    updates = {}  # cat → list of ids
    stayed_other = 0
    for line in lines:
        if '|' not in line: continue
        pid, name = line.split('|', 1)
        new_cat = cat_from_name(name)
        if new_cat:
            updates.setdefault(new_cat, []).append(pid)
        else:
            stayed_other += 1

    total_reclassified = 0
    for cat, ids in updates.items():
        for i in range(0, len(ids), 200):
            chunk = ids[i:i+200]
            id_list = ",".join(f"'{x}'" for x in chunk)
            run(f"UPDATE parts_catalog SET category='{cat}',updated_at=NOW() WHERE id IN ({id_list});")
        total_reclassified += len(ids)

    print(f"  Reclassified: {total_reclassified} | Still Other: {stayed_other}")

    # Report final distribution
    dist = runq(f"SELECT category, COUNT(*) FROM parts_catalog WHERE manufacturer='{MFR}' AND is_active=TRUE GROUP BY category ORDER BY COUNT(*) DESC")
    print("  Final category distribution:")
    for row in dist.split('\n'):
        if row.strip(): print(f"    {row}")

# ── STEP 3: Fitment ───────────────────────────────────────────────────────────
# (model_keyword, canonical_model_name, year_from, year_to)
MODEL_PATTERNS = [
    ("Range Rover Sport",    "Range Rover Sport",   2005, 2013),
    ("Range Rover Evoque",   "Range Rover Evoque",  2011, 2019),
    ("Range Rover Velar",    "Range Rover Velar",   2017, 2021),
    ("Range Rover P38",      "Range Rover P38",     1994, 2002),
    ("Range Rover Classic",  "Range Rover Classic", 1970, 1995),
    ("Range Rover Vogue",    "Range Rover Vogue",   2002, 2012),
    ("Range Rover",          "Range Rover",         1970, 2013),
    ("Defender 90",          "Defender 90",         1983, 2016),
    ("Defender 110",         "Defender 110",        1983, 2016),
    ("Defender 130",         "Defender 130",        1983, 2016),
    ("Defender",             "Defender",            1983, 2016),
    ("Discovery Sport",      "Discovery Sport",     2014, 2020),
    ("Discovery 1",          "Discovery 1",         1989, 1998),
    ("Discovery 2",          "Discovery 2",         1998, 2004),
    ("Discovery 3",          "Discovery 3",         2004, 2009),
    ("Discovery 4",          "Discovery 4",         2009, 2016),
    ("Discovery",            "Discovery",           1989, 2016),
    ("Freelander 1",         "Freelander 1",        1997, 2006),
    ("Freelander 2",         "Freelander 2",        2006, 2014),
    ("Freelander",           "Freelander",          1997, 2014),
    ("Series",               "Series I/II/III",     1948, 1985),
]

def extract_models(name):
    """Return list of (canonical_model, year_from, year_to) matched in name."""
    n_low = name.lower()
    matched = []
    seen_models = set()
    for kw, canonical, yf, yt in MODEL_PATTERNS:
        if kw.lower() in n_low and canonical not in seen_models:
            # avoid adding generic "Range Rover" if a more specific variant already matched
            skip = False
            for sm in seen_models:
                if sm.startswith(canonical) and sm != canonical:
                    skip = True; break
                if canonical.startswith(sm) and sm != canonical:
                    seen_models.discard(sm)
            if not skip:
                matched.append((canonical, yf, yt))
                seen_models.add(canonical)
    return matched

def build_fitment():
    print("\n── STEP 3: Car fitment ──────────────────────")

    # Fetch all LR parts
    rows = runq(f"SELECT id||'|'||name FROM parts_catalog WHERE manufacturer='{MFR}' AND is_active=TRUE LIMIT 10000")
    if not rows:
        print("  No LR parts found"); return

    parts = []
    for line in rows.split('\n'):
        if '|' not in line: continue
        pid, name = line.split('|', 1)
        parts.append((pid, name))

    print(f"  Processing {len(parts)} parts...")

    # Delete existing LR fitment rows
    run(f"DELETE FROM part_vehicle_fitment WHERE manufacturer='{MFR}';")

    batch = []
    total_fitment = 0

    for pid, name in parts:
        models = extract_models(name)
        if not models:
            # Generic "Land Rover" fitment — applies to all models
            models = [("All Models", 1948, 2021)]

        for (model, yf, yt) in models:
            fid = str(uuid.uuid4())
            m_esc = model.replace("'","''")
            batch.append(
                f"('{fid}','{pid}','{MFR}','{m_esc}',{yf},{yt},"
                f"NULL,NULL,NULL,NOW(),NOW(),'{MFR_ID}'::uuid)"
            )

        if len(batch) >= 500:
            run("INSERT INTO part_vehicle_fitment(id,part_id,manufacturer,model,year_from,year_to,engine_type,transmission,notes,created_at,updated_at,manufacturer_id) VALUES "+",".join(batch)+";")
            total_fitment += len(batch); batch = []

    if batch:
        run("INSERT INTO part_vehicle_fitment(id,part_id,manufacturer,model,year_from,year_to,engine_type,transmission,notes,created_at,updated_at,manufacturer_id) VALUES "+",".join(batch)+";")
        total_fitment += len(batch)

    total_db = int(runq(f"SELECT COUNT(*) FROM part_vehicle_fitment WHERE manufacturer='{MFR}'"))
    print(f"  Fitment rows inserted: {total_fitment} | In DB: {total_db}")

    # Model distribution
    dist = runq(f"SELECT model, COUNT(*) as cnt FROM part_vehicle_fitment WHERE manufacturer='{MFR}' GROUP BY model ORDER BY cnt DESC")
    print("  Fitment by model:")
    for row in dist.split('\n'):
        if row.strip(): print(f"    {row}")

# ── main ──────────────────────────────────────────────────────────────────────
fix_names()
fix_categories()
build_fitment()

print("\n── SUMMARY ──────────────────────────────────")
total  = int(runq(f"SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='{MFR}' AND is_active=TRUE"))
priced = int(runq(f"SELECT COUNT(*) FROM parts_catalog WHERE manufacturer='{MFR}' AND is_active=TRUE AND base_price>0"))
fitment= int(runq(f"SELECT COUNT(*) FROM part_vehicle_fitment WHERE manufacturer='{MFR}'"))
print(f"  Active LR parts  : {total}")
print(f"  With price       : {priced}")
print(f"  Fitment rows     : {fitment}")
print("Done.")
