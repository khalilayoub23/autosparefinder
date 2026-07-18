#!/usr/bin/env python3
"""
Catalog deduplication — safe, non-destructive.
  1. SAME-NAME duplicates: deactivate losers (is_active=FALSE)
  2. DIFFERENT-NAME duplicates (bad OEM): clear oem_number=NULL, needs_oem_lookup=TRUE
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
           base_price DESC, created_at ASC) as ids,
       array_agg(LOWER(TRIM(name)) ORDER BY
           CASE WHEN name_he IS NOT NULL AND name_he!='' THEN 0 ELSE 1 END,
           CASE WHEN base_price > 0 THEN 0 ELSE 1 END,
           base_price DESC, created_at ASC) as names
FROM parts_catalog
WHERE is_active=TRUE AND oem_number IS NOT NULL AND oem_number!=''
GROUP BY manufacturer, oem_number
HAVING COUNT(*) > 1
ORDER BY manufacturer, COUNT(*) DESC
""")
print(f"  Duplicate OEM groups: {len(rows):,}")

def norm(n):
    return re.sub(r'\s+', ' ', (n or '').strip().lower())

def parse_pg_array(s):
    s = s.strip()
    if s.startswith('{') and s.endswith('}'):
        s = s[1:-1]
    # Simple split by comma — handles plain text arrays
    return [x.strip().strip('"') for x in s.split(',') if x.strip()]

true_dups = []
bad_oem   = []

for row in rows:
    manufacturer, oem = row[0], row[1]
    ids   = parse_pg_array(row[2])
    names = [norm(x) for x in parse_pg_array(row[3])]
    if len(ids) < 2:
        continue
    unique = set(names)
    # Same-name check: collapse whitespace differences
    compact = set(re.sub(r'\s','',n) for n in names)
    if len(compact) == 1:
        true_dups.append({'keeper': ids[0], 'losers': ids[1:], 'mfr': manufacturer, 'oem': oem})
    else:
        bad_oem.extend(ids)

all_loser_ids = [lid for g in true_dups for lid in g['losers']]
print(f"  True dups (deactivate):  {len(true_dups):,} groups, {len(all_loser_ids):,} losers")
print(f"  Bad OEM (clear oem_number): {len(bad_oem):,} rows")

ts = datetime.now().strftime('%Y%m%d_%H%M%S')
rollback_file = f'/opt/autosparefinder/dedupe_rollback_{ts}.json'
with open(rollback_file, 'w') as f:
    json.dump({'ts': ts, 'true_dups': true_dups, 'bad_oem_ids': bad_oem}, f, indent=2, ensure_ascii=False)
print(f"\n  Rollback saved: {rollback_file}")

print("\n=== Step 4: Deactivating duplicate losers ===")
deactivated = 0
BATCH = 500
for i in range(0, len(all_loser_ids), BATCH):
    batch = all_loser_ids[i:i+BATCH]
    ids_sql = "','".join(batch)
    sql(f"UPDATE parts_catalog SET is_active=FALSE, updated_at=NOW() WHERE id IN ('{ids_sql}')")
    deactivated += len(batch)
print(f"  Deactivated: {deactivated:,}")

print("\n=== Step 5: Clearing invalid OEM assignments ===")
cleared = 0
for i in range(0, len(bad_oem), BATCH):
    batch = bad_oem[i:i+BATCH]
    ids_sql = "','".join(batch)
    sql(f"UPDATE parts_catalog SET oem_number=NULL, needs_oem_lookup=TRUE, updated_at=NOW() WHERE id IN ('{ids_sql}')")
    cleared += len(batch)
print(f"  OEM cleared: {cleared:,}")

print("\n=== Step 6: Removing fitment for deactivated losers ===")
fitment_removed = 0
for i in range(0, len(all_loser_ids), BATCH):
    batch = all_loser_ids[i:i+BATCH]
    ids_sql = "','".join(batch)
    r = sql(f"WITH del AS (DELETE FROM part_vehicle_fitment WHERE part_id IN ('{ids_sql}') RETURNING id) SELECT COUNT(*) FROM del")
    fitment_removed += int(r[0][0])
print(f"  Fitment rows removed: {fitment_removed:,}")

print("\n=== Step 7: Post-dedup snapshot ===")
total_after = int(sql("SELECT COUNT(*) FROM parts_catalog WHERE is_active=TRUE")[0][0])
print(f"  Before: {total_before:,}")
print(f"  After:  {total_after:,}")
print(f"  Removed:{total_before - total_after:,}")
print(f"  OEM cleared (still active): {cleared:,}")

summary = sql("SELECT manufacturer, COUNT(*) as parts FROM parts_catalog WHERE is_active=TRUE GROUP BY manufacturer ORDER BY parts DESC LIMIT 15")
print("\n  Top manufacturers after dedup:")
for row in summary:
    print(f"    {row[0]:<20} {int(row[1]):>8,}")
print(f"\n  Rollback: {rollback_file}")
print("=== DONE ===")
