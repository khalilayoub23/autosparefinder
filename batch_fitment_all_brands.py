#!/usr/bin/env python3
"""Batch fitment builder for all unconnected manufacturers.
Uses vehicle_market_il (gov.il data) → vehicle_hierarchy_xls → part_vehicle_fitment.
Strategy: top 5 models per manufacturer (by active Israeli cars).
"""
import subprocess, sys

PSQL = ['docker', 'exec', '-i', 'autospare_postgres_catalog', 'psql',
        '-U', 'autospare', '-d', 'autospare', '-t', '-A', '-F', '\t', '-c']

def psql(sql):
    r = subprocess.run(PSQL + [sql], capture_output=True)
    return r.stdout.decode().strip()

def psql_rows(sql):
    out = psql(sql)
    rows = []
    for line in out.split('\n'):
        if not line.strip():
            continue
        cols = line.split('\t')
        rows.append([c.strip() for c in cols])
    return rows

# (parts_catalog.manufacturer, vehicle_market_il.manufacturer or None)
MFRS = [
    ('Nissan',        'Nissan'),
    ('Honda',         'Honda'),
    ('Mercedes-Benz', 'Mercedes-Benz'),
    ('Volvo',         'Volvo'),
    ('Suzuki',        'Suzuki'),
    ('Hyundai',       'Hyundai'),
    ('WEY',           'WEY'),
    ('Hongqi',         None),       # no VMI data — use hierarchy
    ('Renault',       'Renault'),
    ('RAM',            None),       # no VMI data — use hierarchy
    ('Jeep',          'Jeep'),
    ('Citroen',       'Citroen'),
    ('Abarth',         None),       # no VMI data — use hierarchy
    ('Fiat',          'Fiat'),
    ('Alfa Romeo',    'Alfa Romeo'),
    ('Mitsubishi',    'Mitsubishi'),
    ('Dacia',         'Dacia'),
    ('Kia',           'Kia'),
    ('Genesis',        None),       # no VMI data — use hierarchy
    ('Peugeot',       'Peugeot'),
    ('Chery',         'Chery'),
    ('Subaru',        'Subaru'),
    ('Xpeng',         'XPeng'),
    ('ZEEKER001',     'Zeekr'),
    ('Smart',         'Smart'),
    ('Porsche',       'Porsche'),
    ('Chevrolet',     'Chevrolet'),
    ('ORA',           'ORA'),
    ('Jaecoo',        'Jaecoo'),
    ('JAC',           'JAC'),
]

grand_total = 0

for parts_mfr, vmi_mfr in MFRS:
    # ── 1. Get top 5 models ──────────────────────────────────────────────────
    if vmi_mfr:
        mfr_safe = vmi_mfr.replace("'", "''")
        rows = psql_rows(f"""
            SELECT kinuy_mishari,
                   MIN(shnat_yitzur),
                   MAX(shnat_yitzur)
            FROM vehicle_market_il
            WHERE manufacturer = '{mfr_safe}'
            GROUP BY kinuy_mishari
            ORDER BY SUM(COALESCE(mispar_rechavim_pailim,0)) DESC
            LIMIT 5
        """)
    else:
        mfr_safe = parts_mfr.replace("'", "''")
        rows = psql_rows(f"""
            SELECT model, year_from, year_to
            FROM vehicle_hierarchy_xls
            WHERE manufacturer = '{mfr_safe}'
            ORDER BY year_from DESC NULLS LAST
            LIMIT 5
        """)

    models = []
    for row in rows:
        if len(row) >= 3:
            name = row[0]
            try:   yr_from = int(row[1]) if row[1] else 2000
            except: yr_from = 2000
            try:   yr_to   = int(row[2]) if row[2] else 2025
            except: yr_to   = 2025
            if name:
                models.append((name, yr_from, yr_to))

    if not models:
        print(f"  SKIP {parts_mfr}: no models found in {'VMI' if vmi_mfr else 'hierarchy'}")
        continue

    print(f"\n[{parts_mfr}] top {len(models)} models: {[m[0] for m in models]}")

    # ── 2. Upsert models into vehicle_hierarchy_xls ──────────────────────────
    mfr_safe = parts_mfr.replace("'", "''")
    for model, yr_from, yr_to in models:
        ms = model.replace("'", "''")
        psql(f"""
            INSERT INTO vehicle_hierarchy_xls (manufacturer, model, year_from, year_to, source_tag)
            SELECT '{mfr_safe}', '{ms}', {yr_from}, {yr_to}, 'gov_il'
            WHERE NOT EXISTS (
                SELECT 1 FROM vehicle_hierarchy_xls
                WHERE manufacturer = '{mfr_safe}' AND model = '{ms}'
            )
        """)

    # ── 3. Broad fitment: all active parts of this mfr → each model ─────────
    model_inserted = 0
    for model, yr_from, yr_to in models:
        ms = model.replace("'", "''")
        out = psql(f"""
            INSERT INTO part_vehicle_fitment (id, part_id, manufacturer, model, year_from, year_to)
            SELECT gen_random_uuid(), pc.id,
                   '{mfr_safe}', '{ms}', {yr_from}, {yr_to}
            FROM parts_catalog pc
            WHERE pc.manufacturer = '{mfr_safe}'
              AND pc.is_active = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM part_vehicle_fitment pvf
                  WHERE pvf.part_id = pc.id
                    AND pvf.manufacturer = '{mfr_safe}'
                    AND pvf.model = '{ms}'
              )
        """)
        try:    n = int(out.split()[-1])
        except: n = 0
        model_inserted += n
        print(f"    {ms}: +{n:,} records")

    grand_total += model_inserted

    # ── 4. Summary ───────────────────────────────────────────────────────────
    count = psql(f"""
        SELECT COUNT(DISTINCT part_id)
        FROM part_vehicle_fitment
        WHERE manufacturer = '{mfr_safe}'
    """)
    print(f"  [{parts_mfr}] DONE: {count} parts connected, +{model_inserted:,} records")

# ── Final stats ───────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"GRAND TOTAL new fitment records: {grand_total:,}")

remaining = psql("""
    SELECT COUNT(*) FROM parts_catalog
    WHERE is_active = TRUE
      AND NOT EXISTS (SELECT 1 FROM part_vehicle_fitment pvf WHERE pvf.part_id = id)
""")
print(f"Parts still without any fitment: {remaining}")

covered = psql("""
    SELECT COUNT(DISTINCT manufacturer) FROM part_vehicle_fitment
""")
print(f"Manufacturers now with fitment: {covered}")
