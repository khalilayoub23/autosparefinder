#!/usr/bin/env python3
"""
Name quality fixer for parts imported in the last 7 days.
Fixes:
  1. Surrounding double-quotes
  2. Trailing bracket option codes: ", [DHP]" / ", [XK9] OR [XK8]"
  3. HTML entity unescaping: &amp; -> &
  4. Double spaces -> single space
  5. Title-case fully all-caps English names (> 4 chars, no Hebrew)
"""
import subprocess, re, html

PSQL = ['docker', 'exec', '-i', 'autospare_postgres_catalog', 'psql',
        '-U', 'autospare', '-d', 'autospare', '-t', '-A', '-F', '\t', '-c']

def q(sql):
    r = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    return r.stdout.strip()

HEBREW_RE  = re.compile(r'[\u0590-\u05FF]')
BRACKET_RE = re.compile(r'(,?\s*\[[A-Z0-9\-/ ]+\](\s+OR\s+\[[A-Z0-9\-/ ]+\])*\s*)+$')
# Preserve known all-caps acronyms (skip title-casing if they appear alone)
ACRONYM_ONLY = re.compile(r'^[A-Z0-9]{1,4}$')

def fix_name(name: str) -> str:
    if not name:
        return name
    new = name

    # 1. HTML entity unescape
    new = html.unescape(new)

    # 2. Strip surrounding double quotes
    if new.startswith('"') and new.endswith('"') and len(new) > 2:
        new = new[1:-1].strip()

    # 3. Strip trailing bracket option codes
    new = BRACKET_RE.sub('', new).rstrip(', ').strip()

    # 4. Collapse double spaces
    new = re.sub(r'  +', ' ', new).strip()

    # 5. Title-case fully all-caps English-only names longer than 4 chars
    if (len(new) > 4
            and not HEBREW_RE.search(new)
            and new == new.upper()
            and re.search(r'[A-Z]{4,}', new)):
        new = new.title()

    return new

# Load candidate parts — use simple conditions, filter in Python
print("Loading candidate parts...")
raw = q("""
SELECT id, name FROM parts_catalog
WHERE is_active=TRUE AND created_at >= NOW() - INTERVAL '7 days'
""")

fixes = []
skipped = 0
for row in raw.split('\n'):
    if not row.strip() or '\t' not in row:
        continue
    pid, name = row.split('\t', 1)
    pid = pid.strip()
    new_name = fix_name(name)
    if new_name != name:
        fixes.append((pid, name, new_name))
    else:
        skipped += 1

print(f"Loaded {skipped + len(fixes)} parts | Needs fix: {len(fixes)} | Unchanged: {skipped}")
if not fixes:
    print("Nothing to fix.")
    exit(0)

print("\nSample (first 20 changes):")
for pid, old, new in fixes[:20]:
    print(f"  {old!r:70s}")
    print(f"  -> {new!r}")
    print()

# Apply in batches
updated = 0
batch_size = 200
for i in range(0, len(fixes), batch_size):
    batch = fixes[i:i+batch_size]
    # Build CASE using dollar-quoting won't work easily; escape single quotes
    cases = '\n'.join(
        "WHEN id='{}' THEN '{}'".format(p, n.replace("'", "''"))
        for p, _, n in batch
    )
    ids = ','.join("'{}'".format(p) for p, _, _ in batch)
    sql = "UPDATE parts_catalog SET name = CASE {} END, updated_at = NOW() WHERE id IN ({})".format(cases, ids)
    q(sql)
    updated += len(batch)
    print(f"  Updated {updated}/{len(fixes)}...", end='\r', flush=True)

print(f"\nDone. {updated} names fixed.")
