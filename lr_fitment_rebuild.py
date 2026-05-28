#!/usr/bin/env python3
"""
LR fitment rebuild — scoped 100% to manufacturer='Land Rover'.
Deletes only WHERE manufacturer='Land Rover', inserts only LR parts.
Other brands are never touched.
"""
import subprocess, uuid
from collections import defaultdict

LR_BRAND_ID = "7f060acf-2382-42e1-8413-f9b045cb0836"

def runq(sql):
    r = subprocess.run(
        ["docker","exec","-i","autospare_postgres_catalog",
         "psql","-U","autospare","-d","autospare","-t","-A","-c",sql],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0: raise RuntimeError(r.stderr[:300])
    return r.stdout.strip()

def runw(sql):
    r = subprocess.run(
        ["docker","exec","-i","autospare_postgres_catalog",
         "psql","-U","autospare","-d","autospare","-q"],
        input=sql, capture_output=True, text=True, timeout=120)
    if r.returncode != 0: raise RuntimeError(r.stderr[:300])

# ── 1. Load LR parts ──────────────────────────────────────────────────────────
print("Loading LR parts...")
raw = runq("SELECT id||'|'||name FROM parts_catalog WHERE manufacturer='Land Rover' AND is_active=TRUE LIMIT 10000")
parts = [(r.split('|',1)[0], r.split('|',1)[1]) for r in raw.split('\n') if '|' in r]
print(f"  {len(parts)} parts")

# ── 2. Load LR vehicles from Israeli registry ─────────────────────────────────
print("Loading LR vehicles from registry...")
raw_v = runq("""
SELECT tozeret_cd||'|'||COALESCE(degem_cd::text,'NULL')||'|'||COALESCE(kinuy_mishari,'')||'|'||COALESCE(shnat_yitzur::text,'0')
FROM vehicle_market_il
WHERE manufacturer_id='7f060acf-2382-42e1-8413-f9b045cb0836'
ORDER BY tozeret_cd, kinuy_mishari, shnat_yitzur
""")
vehicles = []
for line in raw_v.split('\n'):
    p = line.split('|')
    if len(p) < 4: continue
    vehicles.append({
        'tozeret_cd': int(p[0]),
        'degem_cd':   p[1] if p[1] != 'NULL' else None,   # integer string or None
        'kinuy':      p[2].upper(),
        'year':       int(p[3]) if p[3].isdigit() else None,
    })
print(f"  {len(vehicles)} vehicles")

# ── 3. Kinuy → vehicles lookup ────────────────────────────────────────────────
kinuy_map = defaultdict(list)
for v in vehicles:
    kinuy_map[v['kinuy']].append(v)

# ── 4. Model → kinuy patterns & year bounds ───────────────────────────────────
# (model_keyword_in_partname, kinuy_substrings, year_from, year_to)
# Ordered from most specific → least specific (first match wins)
MODELS = [
    ("Range Rover Sport",  ["R ROVER SPORT","RANGE R SPORT","RANGE R.SPORT","RANGE R. SPORT",
                             "R. ROVER SPORT","R.ROVER SPORT","RAN.ROVER SPORT","RANGE ROVER SPO",
                             "RANGE  R.SPORT","ROVER SPORT","SPORT","RR SPORT","R ROVER P400E",
                             "RANGR OVER SPORT"],                                    2005, 2026),
    ("Range Rover Evoque", ["R ROVER EVOQUE","R.ROVER EVOQUE","RANGE R EVOQUE","RANGE ROVER EVO",
                             "RANGE RO EVOQUE","R.ROVER EVOQE","R ROVER EVOQE","EVOQUE",
                             "EVOQUE P300E","RR EVOQUE P300E","1A2BW","EVOUQE"],     2011, 2026),
    ("Range Rover Velar",  ["R.ROVER VELAR","R ROVER VELAR","ANGEROVER VELAR"],      2017, 2026),
    ("Range Rover Vogue",  ["R.ROVER VOGUE"],                                        2002, 2012),
    ("Range Rover",        ["RANGE ROVER","RANGE  ROVER","RANG-ROVER","RANGE  ROVER.",
                             "KA9BW","KA9B4"],                                       1970, 2026),
    ("Defender 110",       ["DEFENDER 110"],                                         1983, 2026),
    ("Defender 90",        ["DEFENDER 90"],                                          1983, 2026),
    ("Defender 130",       ["DEFENDER 130"],                                         1983, 2026),
    ("Defender",           ["DEFENDER"],                                             1983, 2026),
    ("Discovery Sport",    ["DISCOVERY SPORT","DISCOVRY SPORT"],                     2014, 2026),
    ("Discovery 4",        ["DISCOVERY 4","DISCOVERY"],                              2009, 2016),
    ("Discovery 3",        ["DISCOVERY 3","DISCOVERY"],                              2004, 2009),
    ("Discovery 2",        ["DISCOVERY"],                                            1998, 2004),
    ("Discovery 1",        ["DISCOVERY"],                                            1989, 1998),
    ("Discovery",          ["DISCOVERY","DISCOVRY SPORT","DISCOVERY SPORT"],         1989, 2026),
    ("Freelander 2",       ["FREELANDER 2","FREELANDER"],                            2006, 2014),
    ("Freelander 1",       ["FREELANDER"],                                           1997, 2006),
    ("Freelander",         ["FREELANDER","FREELANDER 2"],                            1997, 2016),
]

def find_vehicles(part_name):
    """Return list of (model_name, vehicle_dict) for matching vehicles."""
    n = part_name.upper()
    results = []
    seen_keys = set()
    for (model_kw, kinuy_pats, yf, yt) in MODELS:
        if model_kw.upper() not in n:
            continue
        for pat in kinuy_pats:
            for kinuy_key, vlist in kinuy_map.items():
                if pat in kinuy_key or kinuy_key in pat:
                    for v in vlist:
                        if v['year'] and not (yf <= v['year'] <= yt):
                            continue
                        k = (v['tozeret_cd'], v['degem_cd'], v['year'])
                        if k not in seen_keys:
                            seen_keys.add(k)
                            results.append((model_kw, v))
    return results

# ── 5. Safety: scope check before delete ──────────────────────────────────────
other_count = int(runq("SELECT COUNT(*) FROM part_vehicle_fitment WHERE manufacturer != 'Land Rover'"))
lr_count    = int(runq("SELECT COUNT(*) FROM part_vehicle_fitment WHERE manufacturer = 'Land Rover'"))
print(f"\nBefore rebuild:  LR fitment={lr_count}  |  Other brands fitment={other_count}  (will NOT be touched)")

# ── 6. Delete LR fitment ONLY ─────────────────────────────────────────────────
print("Deleting old LR fitment (scoped to manufacturer='Land Rover')...")
runw("DELETE FROM part_vehicle_fitment WHERE manufacturer='Land Rover';")

# ── 7. Insert new fitment ─────────────────────────────────────────────────────
print("Building new fitment...")
batch, total_fits, no_match = [], 0, 0
INSERT_SQL = (
    "INSERT INTO part_vehicle_fitment"
    "(id,part_id,manufacturer,model,year_from,year_to,engine_type,transmission,"
    "notes,created_at,updated_at,manufacturer_id,tozeret_cd,degem_cd,shnat_yitzur) VALUES "
)

def flush(b):
    runw(INSERT_SQL + ",".join(b) + ";")

for (pid, name) in parts:
    matched = find_vehicles(name)
    if matched:
        for (model_name, v) in matched:
            fid = str(uuid.uuid4())
            yr   = v['year'] or 0
            tz   = v['tozeret_cd']
            dc   = v['degem_cd']          # integer string like "765" or None
            dc_s = dc if dc else "NULL"   # SQL NULL literal
            m_esc = model_name[:100].replace("'","''")
            batch.append(
                f"('{fid}','{pid}','Land Rover','{m_esc}',{yr},{yr},"
                f"NULL,NULL,NULL,NOW(),NOW(),'{LR_BRAND_ID}'::uuid,{tz},{dc_s},{yr})"
            )
    else:
        # Generic — no specific vehicle match found
        fid = str(uuid.uuid4())
        batch.append(
            f"('{fid}','{pid}','Land Rover','All Models',1970,2026,"
            f"NULL,NULL,NULL,NOW(),NOW(),'{LR_BRAND_ID}'::uuid,NULL,NULL,NULL)"
        )
        no_match += 1

    if len(batch) >= 500:
        flush(batch); total_fits += len(batch); batch = []
        if total_fits % 5000 == 0: print(f"  {total_fits} rows inserted...")

if batch:
    flush(batch); total_fits += len(batch)

# ── 8. Verify ─────────────────────────────────────────────────────────────────
db_lr     = int(runq("SELECT COUNT(*) FROM part_vehicle_fitment WHERE manufacturer='Land Rover'"))
db_other  = int(runq("SELECT COUNT(*) FROM part_vehicle_fitment WHERE manufacturer!='Land Rover'"))

print(f"\n{'='*55}")
print(f"  LR fitment rows:         {db_lr:,}")
print(f"  Other brands unchanged:  {db_other:,}  (was {other_count:,})")
print(f"  Parts with no match:     {no_match}")
print(f"{'='*55}")

if db_other != other_count:
    print("WARNING: other-brand count changed — INVESTIGATE")
else:
    print("  OK — other brands unaffected.")

print("\nDistribution by model:")
dist = runq("""
SELECT model, COUNT(*) cnt FROM part_vehicle_fitment
WHERE manufacturer='Land Rover'
GROUP BY model ORDER BY cnt DESC LIMIT 20
""")
for r in dist.split('\n'):
    if r.strip(): print(f"  {r}")
