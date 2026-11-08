"""
Script: tests/test_max_price_writers_127.py
Purpose: #127 policy guard — `parts_catalog.max_price_ils` is the ISRAELI CONSUMER REFERENCE incl. 18% VAT and a foreign
         supplier must never write it. Every file that writes the column is classified here; a new writer fails the
         test until someone decides which list it belongs to. Foreign-source files must bind NULL for the column in every
         INSERT INTO parts_catalog and may not assign or copy it on conflict.
Process: static analysis of the backend source (no database).
Data Imported/Modified: none.
Last Updated: 2026-11-03
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = ("tests", "devtests", "legacy", "alembic", "candidates", "state", "backups", "__pycache__", "node_modules")

# FOREIGN supplier / marketplace sources: never write the Israeli reference.
FOREIGN = [
    'harvesters/rockauto_harvester.py',
    'harvesters/tecdoc_harvester.py',
    'importers/amayama_price_import.py',
    'importers/asap_import.py',
    'importers/car_parts_ie_import_generic.py',
    'importers/ebay_brand_importer.py',
    'importers/fridayparts_seed_import.py',
    'importers/geely_seed_import.py',
    'importers/gm_brands_seed_import.py',
    'importers/multi_brand_seed_import.py',
    'importers/rockauto_browser_import.py',
    'importers/rockauto_price_import.py',
    'importers/saab_partsforsaabs_importer.py',
    'importers/sng_barratt_jaguar_import.py',
    'oempartsonline_importer.py',
    'opel_car_parts_ie_import.py',
    'scrapers/bmw_spare_parts_scraper.py',
]
# Israeli sources (official importers' price lists, Israeli retailers) + maintenance tools that follow the policy.
ISRAELI = [
    'harvesters/kia_israel_harvester.py',
    'importers/bmw_il_direct_import.py',
    'importers/bmw_il_import.py',
    'importers/cadillac_israel_import.py',
    'importers/colmobil_import_v2.py',
    'importers/delek_json_price_update.py',
    'importers/delek_multi_importer.py',
    'importers/ev_vag_lexus_import.py',
    'importers/fitz_mb_suzuki_import.py',
    'importers/fitz_pdf_import.py',
    'importers/freesbe_importer.py',
    'importers/freesbe_recovery.py',
    'importers/geely_israel_import.py',
    'importers/gmc_buick_umi_import.py',
    'importers/hyundai_genesis_mitsubishi_import.py',
    'importers/il_consumer_price_import.py',
    'importers/il_importer_pdf_import.py',
    'importers/il_pdf_batch_import.py',
    'importers/import_champion_motors.py',
    'importers/import_delek_brands.py',
    'importers/import_from_excel.py',
    'importers/import_kia_israel.py',
    'importers/isuzu_excel_import.py',
    'importers/jaguar_batch_import.py',
    'importers/jaguar_ndjson_import.py',
    'importers/kgm_ssangyong_importer.py',
    'importers/kia_il_direct_import.py',
    'importers/kia_import.py',
    'importers/lexus_pdf_import.py',
    'importers/lr_import.py',
    'importers/lr_subaru_byd_import.py',
    'importers/mazda_il_importer.py',
    'importers/mct_importer.py',
    'importers/mixed_brands_import.py',
    'importers/porsche_import.py',
    'importers/renault_pdf_import.py',
    'importers/samelet_import_v2.py',
    'importers/subaru_il_importer.py',
    'importers/supplier_pdf_import.py',
    'importers/toyota_il_importer.py',
    'importers/xls_prices_import.py',
    'importers/xlsx_il_price_import.py',
    'importers/zeekr_full_import.py',
    'importers/zeekr_pdf_import.py',
    'scrapers/bdv_playwright_scraper.py',
    'scrapers/bydil_scraper.py',
    'scrapers/eliteparts_scraper.py',
    'scrapers/selected_parts_scraper.py',
]
MAINTENANCE = [
    'maintenance/repair_foreign_max_price_127.py',
    'maintenance/repair_oms_foreign_max_133.py',   # #133: clears provably-foreign max only, dry-run by default
    'maintenance/repair_inclusive_reference_131.py',   # #131: recomputes via refresh_min_max_for_parts, dry-run by default
    'catalog_scraper.py',
    'db_cleanup_agent.py',
    'db_update_agent.py',
    'maintenance/merge_master_parts.py',
    'maintenance/price_audit_fix.py',
    'maintenance/vat_fix_batched.py',
    'scripts/import_merged_catalog.py',
]
# mention the column only to read it / describe it
READERS = [
    'BACKEND_DATABASE_MODELS.py',
    'routes/parts.py',
]


def _writers():
    found = set()
    for d, ds, fs in os.walk(ROOT):
        ds[:] = [x for x in ds if x not in SKIP]
        for f in fs:
            if not f.endswith(".py"):
                continue
            p = os.path.relpath(os.path.join(d, f), ROOT)
            s = open(os.path.join(d, f), encoding="utf-8", errors="ignore").read()
            if "max_price_ils" in s and re.search(r"INSERT INTO parts_catalog|UPDATE parts_catalog|max_price_ils\s*=", s):
                found.add(p)
    return found


def _split_top(s):
    out, depth, cur, q = [], 0, "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur += ch
        elif ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def test_every_writer_of_max_price_ils_is_classified():
    known = set(FOREIGN) | set(ISRAELI) | set(MAINTENANCE) | set(READERS)
    unknown = _writers() - known
    assert not unknown, f"new writer(s) of max_price_ils - classify them in tests/test_max_price_writers_127.py: {sorted(unknown)}"


def test_foreign_sources_bind_null_and_never_copy_the_column():
    bad = []
    for rel in FOREIGN:
        s = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        if re.search(r"max_price_ils\s*=(?!=)", s) or "EXCLUDED.max_price_ils" in s:
            bad.append(f"{rel}: assigns / copies max_price_ils")
        for m in re.finditer(r"INSERT INTO parts_catalog\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*(?:ON CONFLICT|RETURNING|\"\"\"|$)", s, re.S):
            cols = [c.strip() for c in m.group(1).split(",")]
            if "max_price_ils" in cols:
                vals = _split_top(m.group(2))
                if len(vals) != len(cols) or vals[cols.index("max_price_ils")].upper() != "NULL":
                    bad.append(f"{rel}: INSERT binds {vals[cols.index('max_price_ils')] if len(vals) == len(cols) else '??'} to max_price_ils")
    assert not bad, bad


def test_no_inline_vat_formula_left_in_the_scrapers_min_max_update():
    s = open(os.path.join(ROOT, "catalog_scraper.py"), encoding="utf-8").read()
    assert "refresh_min_max_for_parts" in s
    assert not re.search(r"MAX\(COALESCE\(price_ils, price_usd", s), "second copy of the min/max formula is back"


def test_ebay_sync_no_longer_touches_max_price_ils():
    assert "max_price_ils" not in open(os.path.join(ROOT, "services", "ebay_price_sync.py"), encoding="utf-8").read()
