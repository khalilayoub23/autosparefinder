#!/usr/bin/env python3
"""
Script: catalogs_parts_importer.py
Purpose: Import Kia vehicle fitment data scraped from kia.catalogs-parts.com into
         part_vehicle_fitment table. Must be run after kia_import.py.

Process:
  1. Read TSV file at /tmp/kia_catalog_fitment.tsv (columns: oem, model, year_from, year_to)
  2. Match OEM numbers against active parts_catalog records
  3. Insert/update part_vehicle_fitment rows

Data Imported / Modified:
  - part_vehicle_fitment: part_id, manufacturer, model, year_from, year_to, manufacturer_id

Data Sources / Web Links:
  - kia.catalogs-parts.com (scrape output as TSV)

Missing Data Delegation:
  - Fitment year ranges must be in TSV — nothing fetched dynamically
  - No supplier_parts written — this is fitment-only

Author: AutoSpareFinder Agent
Last Updated: 2026-06-01
"""
import csv, sys, uuid, psycopg2

DSN = "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog:5432/autospare"
TSV_PATH = "/tmp/kia_catalog_fitment.tsv"
KIA_MANUFACTURER_ID = "626947bf-be3f-4dd1-a52e-fbcff8168cfc"

def main():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    rows = []
    with open(TSV_PATH) as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            rows.append(row)
    print(f"Loaded {len(rows)} fitment rows")
    oems = list({r['oem'] for r in rows})
    cur.execute("SELECT oem_number, id FROM parts_catalog WHERE oem_number = ANY(%s) AND is_active = TRUE", (oems,))
    oem_to_parts = {}
    for oem, pid in cur.fetchall():
        oem_to_parts.setdefault(oem, []).append(pid)
    print(f"Matched {len(oem_to_parts)}/{len(oems)} OEMs to parts")
    inserted = updated = skipped = 0
    for row in rows:
        oem, model = row['oem'], row['model']
        year_from, year_to = int(row['year_from']), int(row['year_to'])
        part_ids = oem_to_parts.get(oem, [])
        if not part_ids:
            skipped += 1
            continue
        for part_id in part_ids:
            cur.execute("""
                INSERT INTO part_vehicle_fitment (id, part_id, manufacturer, model, year_from, year_to, manufacturer_id)
                VALUES (gen_random_uuid(), %s, 'Kia', %s, %s, %s, %s)
                ON CONFLICT (part_id, manufacturer, model, year_from) DO UPDATE SET year_to = EXCLUDED.year_to
                RETURNING (xmax = 0)
            """, (part_id, model, year_from, year_to, KIA_MANUFACTURER_ID))
            is_new = cur.fetchone()[0]
            if is_new: inserted += 1
            else: updated += 1
    conn.commit()
    cur.close(); conn.close()
    print(f"Inserted: {inserted}, Updated: {updated}, Skipped: {skipped}")

if __name__ == '__main__':
    main()
