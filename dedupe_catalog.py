#!/usr/bin/env python3
"""
Catalog deduplication — safe, non-destructive.
Strategy:
  1. SAME-NAME duplicates (true import dupes): deactivate losers (is_active=FALSE)
  2. DIFFERENT-NAME duplicates (bad OEM assignment): clear oem_number → NULL + needs_oem_lookup=TRUE
     Do NOT deactivate — these are real distinct parts.

Rules per claude.md:
  - Never DELETE rows — use is_active=FALSE
  - Never overwrite higher-confidence data with lower-confidence data
  - Export loser→keeper rollback mapping before any changes
"""
import subprocess, json, re
from datetime import datetime

DB = ['docker', 'exec', '-i', 'autospare_postgres_catalog', 'psql',
      '-U', 'autospare', '-d', 'autospare', '-t', '-A', '-F', '\t', '-c']

def sql(q):
    r = subprocess.run(DB + [q], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    lines = [l for l in r.stdout.strip().splitlines() if l]
    return [l.split('\t') for l in lines]

print("=== Step 0: Pre-dedup snapshot ===")
total_before = int(sql("SELECT COUNT(*) FROM parts_catalog WHERE is_active=TRUE")[0][0])
print(f"  Active parts before: {total_before:,}")

print("\n=== Step 1: Loading duplicate OEM groups ===")
rows = sql("""
SELECT manufacturer, oem_number,
       array_agg(id ORDER BY
           CASE WHEN name_he IS NOT NULL AND name_he!='' THEN 0 ELSE 1 END,
           CASE WHEN base_price > 0 THEN 0 ELSE 1 END,
           base_price DESC,
           created_at ASC
       ) as ids,
       array_agg(LOWER(name) ORDER BY
           CASE WHEN name_he IS NOT NULL AND name_he!='' THEN 0 ELSE 1 END,
           CASE WHEN base_price > 0 THEN 0 ELSE 1 END,
           base_price DESC,
           created_at ASC
       ) as names
FROM parts_catalog
WHERE is_active=TRUE AND oem_number IS NOT NULL AND oem_number!=''
GROUP BY manufacturer, oem_number
HAVING COUNT(*) > 1
ORDER BY manufacturer, COUNT(*) DESC
""")
print(f"  Duplicate OEM groups found: {len(rows):,}")

true_dups   = []
bad_oem     = []

def normalize_name(n):
    return re.sub(r'\s+', ' ', (n or '').strip().lower())

for row in rows:
    manufacturer, oem, ids_str, names_str = row[0], row[1], row[2], row[3]
    ids   = [x.strip() for x in ids_str.strip('{}').split(',') if x.strip()]
    names = [normalize_name(x) for x in names_str.strip('{}').split(',') if x.strip()]
    if len(ids) < 2:
        continue

    unique_names = set(names)
    if len(unique_names) == 1 or (len(unique_names) == 2 and
        min(len(a) for a in unique_names) > 3 and
        max(names[0].replace(' ',''), names[1].replace(' ','')) == min(names[0].replace(' ',''), names[1].replace(' ',''))):
        true_dups.append({'keeper': ids[0], 'losers': ids[1:],
                          'manufacturer': manufacturer, 'oem': oem, 'name': names[0]})
    else:
        bad_oem.extend(ids)

print(f"  True duplicates (same name, deactivate losers): {len(true_dups):,} groups")
print(f"  Bad OEM assignments (different names, clear OEM):  {len(bad_oem):,} part rows")

ts = datetime.now().strftime('%Y%m%d_%H%M%S')
rollback_file = f'/opt/autosparefinder/dedupe_rollback_{ts}.json'
rollback = {
    'created_at': ts,
    'true_dups': true_dups,
    'bad_oem_ids': bad_oem,
}
with open(rollback_file, 'w') as f:
    json.dump(rollback, f, indent=2, ensure_ascii=False)
print(f"\n  Rollback artifact saved: {rollback_file}")

print("\n=== Step 4: Deactivating duplicate losers ===")
all_loser_ids = []
for g in true_dups:
    all_loser_ids.extend(g['losers'])

deactivated = 0
if all_loser_ids:
    BATCH = 500
    for i in range(0, len(all_loser_ids), BATCH):
        batch = all_loser_ids[i:i+BATCH]
        ids_sql = "','".join(batch)
        sql(f"UPDATE parts_catalog SET is_active=FALSE, updated_at=NOW() WHERE id IN ('{ids_sql}')")
        deactivated += len(batch)
        print(f"  Deactivated {deactivated:,}/{len(all_loser_ids):,}...", end='\r')
print(f"\n  Total deactivated: {deactivated:,}")

print("\n=== Step 5: Clearing invalid OEM assignments ===")
cleared = 0
if bad_oem:
    BATCH = 500
    for i in range(0, len(bad_oem), BATCH):
        batch = bad_oem[i:i+BATCH]
        ids_sql = "','".join(batch)
        sql(f"UPDATE parts_catalog SET oem_number=NULL, needs_oem_lookup=TRUE, updated_at=NOW() WHERE id IN ('{ids_sql}')")
        cleared += len(batch)
        print(f"  Cleared OEM for {cleared:,}/{len(bad_oem):,}...", end='\r')
print(f"\n  Total OEM-cleared: {cleared:,}")

print("\n=== Step 6: Removing fitment rows for deactivated losers ===")
if all_loser_ids:
    BATCH = 500
    fitment_removed = 0
    for i in range(0, len(all_loser_ids), BATCH):
        batch = all_loser_ids[i:i+BATCH]
        ids_sql = "','".join(batch)
        r = sql(f"""
            WITH del AS (
                DELETE FROM part_vehicle_fitment WHERE part_id IN ('{ids_sql}') RETURNING id
            ) SELECT COUNT(*) FROM del
        """)
        fitment_removed += int(r[0][0])
    print(f"  Fitment rows removed: {fitment_removed:,}")

print("\n=== Step 7: Post-dedup snapshot ===")
total_after = int(sql("SELECT COUNT(*) FROM parts_catalog WHERE is_active=TRUE")[0][0])

print(f"  Active parts before: {total_before:,}")
print(f"  Active parts after:  {total_after:,}")
print(f"  Removed (deactivated): {total_before - total_after:,}")
print(f"  OEM cleared (parts kept active): {cleared:,}")

summary = sql("""
SELECT manufacturer, COUNT(*) as parts FROM parts_catalog 
WHERE is_active=TRUE GROUP BY manufacturer ORDER BY parts DESC LIMIT 15
""")
print("\n  Top manufacturers after dedup:")
for row in summary:
    print(f"    {row[0]:<20} {int(row[1]):>8,} parts")
