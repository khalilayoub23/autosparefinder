"""Generates files/docs/BASE_PRICE_WRITERS.json (the ONE authoritative writer inventory) from the AST scan + the
curated status tables below. Run: python3 evidence/gen_registry.py <merged-backend-tree>"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "files", "backend", "tests"))
import base_price_inventory as b

TREE = sys.argv[1]
CAND = os.path.join(os.path.dirname(__file__), "..", "files", "backend")
REPO = "/opt/autosparefinder"

E2E = {  # file -> (tests, what was proven)
    "catalog_scraper.py": ("test_base_price_writers.py::test_new_discovered_part_*, test_rediscovery_*, test_part_without_any_cost_*", "db_upsert_part insert / update / re-run on the isolated DB"),
    "importers/car_parts_ie_import_generic.py": ("test_base_price_writers.py::test_car_parts_ie_reharvest_*", "the importer's own INSERT..ON CONFLICT statement, new / owned / emptied / repeated"),
    "importers/freesbe_importer.py": ("test_base_price_writers.py::test_freesbe_normalizer_*", "normalize_base_price_batched (real function) on rows with and without base_price"),
    "importers/delek_json_price_update.py": ("test_importers_e2e.py::test_delek_json_update_end_to_end", "JSON file -> update_from_json -> persisted base_price, owned value, 2 runs"),
    "importers/ev_vag_lexus_import.py": ("test_importers_e2e.py::test_ev_vag_lexus_end_to_end", "NIO update + Voyah/M-Hero insert/conflict; load_json fixture; NOT the VAG/Lexus update paths (same helper, statement prepared only)"),
    "importers/geely_israel_import.py": ("test_importers_e2e.py::test_geely_israel_end_to_end", "embedded PARTS -> import_parts -> insert, owned value on re-run"),
    "importers/sng_barratt_jaguar_import.py": ("test_importers_e2e.py::test_sng_barratt_end_to_end", "record batch -> import_batch; foreign supplier net, owned value"),
    "importers/lr_subaru_byd_import.py": ("test_importers_e2e.py::test_subaru_update_end_to_end", "Subaru path end to end; Land Rover / BYD paths: statement prepared only"),
    "importers/amayama_price_import.py": ("test_importers_e2e.py::test_price_fill_importers_end_to_end[amayama]", "relay JSON -> main(path) -> price fill, owned untouched, 2 runs"),
    "oempartsonline_importer.py": ("test_importers_e2e.py::test_oempartsonline_import_products_end_to_end", "product dicts -> import_products -> insert, owned value on re-import at another rate"),
    "importers/rockauto_price_import.py": ("test_importers_e2e.py::test_price_fill_importers_end_to_end[rockauto]", "relay JSON -> main(path) -> price fill, owned untouched, 2 runs"),
}

T = "tests/test_importers_e2e_scripts.py::"
SAVED_SOURCE = {"importers/import_parts_db.py": "the REAL saved workbook data/parts_database.xlsx (sheet 'Porsche', 1,496 rows)"}
# file -> (test, entry point exercised, input fixture). All run the script as a subprocess against the isolated sandbox database
# (python3 /app/<file>), then read the persisted base_price back; each asserts: new part, existing EMPTY base (filled by price_policy),
# existing OWNED base (kept), and a second run (idempotent). Foreign/IL VAT is NOT_APPLICABLE for importers whose source is IL-only.
E2E_SCRIPTS = {
    "importers/kia_il_direct_import.py": ("test_kia_il_direct_script_end_to_end", "script main", "synthetic Kia IL JSON"),
    "importers/bmw_il_direct_import.py": ("test_bmw_il_direct_script_end_to_end", "script main", "synthetic BMW IL JSON"),
    "importers/jaguar_batch_import.py": ("test_jaguar_scripts_end_to_end[batch]", "script main", "synthetic JSON batch"),
    "importers/jaguar_ndjson_import.py": ("test_jaguar_scripts_end_to_end[ndjson]", "script main", "synthetic NDJSON"),
    "importers/xlsx_il_price_import.py": ("test_xlsx_il_price_import_script_end_to_end", "script main", "generated .xlsx"),
    "importers/il_consumer_price_import.py": ("test_il_consumer_price_import_script_end_to_end", "script main", "generated .xlsx"),
    "importers/mixed_brands_import.py": ("test_mixed_brands_import_script_end_to_end", "script --file", "generated .xlsx"),
    "importers/import_kia_israel.py": ("test_import_kia_israel_script_end_to_end", "script main (KIA_JSON)", "synthetic harvest JSON"),
    "importers/import_champion_motors.py": ("test_import_champion_motors_script_end_to_end", "script main (CM_JSON)", "synthetic harvest JSON"),
    "importers/lr_import.py": ("test_lr_import_script_end_to_end", "script main (JSON_FILE)", "synthetic JSON"),
    "importers/toyota_il_importer.py": ("test_toyota_il_importer_script_end_to_end", "script main (/app/state/toyota_il_parts.json)", "synthetic harvest JSON"),
    "importers/mazda_il_importer.py": ("test_mazda_il_importer_with_synthetic_delek_api_response", "script main, HTTP transport replaced", "synthetic Delek API JSON"),
    "importers/mct_importer.py": ("test_mct_importer_with_synthetic_mct_api_response", "script main, HTTP transport replaced", "synthetic MCT API JSON (base64)"),
    "importers/bmw_il_import.py": ("test_bmw_il_import_script_end_to_end", "script main (/app/bmw_parts.json)", "synthetic JSON"),
    "importers/delek_multi_importer.py": ("test_delek_multi_importer_with_synthetic_delek_api_response", "script --brands 3, HTTP transport replaced", "synthetic Delek API JSON"),
    "importers/asap_import.py": ("test_asap_import_script_end_to_end", "script CSV --brand-name", "generated CSV"),
    "importers/subaru_il_importer.py": ("test_subaru_il_importer_with_synthetic_samelet_response", "script main, HTTP transport replaced", "synthetic samelet JSON"),
    "importers/hyundai_genesis_mitsubishi_import.py": ("test_colmobil_format_pdf_importers_end_to_end[hyundai_genesis_mitsubishi]", "script main", "generated PDF (Colmobil layout)"),
    "importers/fitz_pdf_import.py": ("test_colmobil_format_pdf_importers_end_to_end[fitz_pdf]", "script main", "generated PDF (Colmobil layout)"),
    "importers/fitz_mb_suzuki_import.py": ("test_fitz_mb_suzuki_pdf_importer_end_to_end", "script main", "generated MB + Suzuki PDFs"),
    "importers/renault_pdf_import.py": ("test_renault_pdf_importer_end_to_end", "script main", "generated PDF"),
    "importers/zeekr_pdf_import.py": ("test_zeekr_pdf_importer_end_to_end", "script main", "generated PDF"),
    "importers/il_pdf_batch_import.py": ("test_il_pdf_batch_importer_end_to_end", "script main", "generated PDF"),
    "importers/il_importer_pdf_import.py": ("test_il_importer_pdf_import_script_end_to_end", "script main", "generated PDF"),
    "importers/lexus_pdf_import.py": ("test_lexus_pdf_importer_end_to_end", "script main", "generated ruled-table PDF"),
    "importers/porsche_import.py": ("test_porsche_pdf_importer_end_to_end", "script main", "generated ruled-table PDF"),
    "importers/cadillac_israel_import.py": ("test_cadillac_israel_importer_end_to_end", "script main", "generated .xlsx"),
    "importers/isuzu_excel_import.py": ("test_isuzu_excel_importer_end_to_end", "script main", "generated .xlsx"),
    "importers/xls_prices_import.py": ("test_xls_prices_import_end_to_end", "main() with XLS_PATH -> fixture", "generated .xlsx"),
    "importers/gmc_buick_umi_import.py": ("test_gmc_buick_umi_importer_end_to_end", "script main", "generated PDF"),
    "importers/import_delek_brands.py": ("test_import_delek_brands_script_end_to_end", "script main", "synthetic Delek JSON"),
    "harvesters/acura_browser_harvest.py": ("test_acura_browser_harvest_import_end_to_end", "script <parts.json>", "synthetic browser-harvest JSON"),
    "importers/import_parts_db.py": ("test_import_parts_db_with_the_real_saved_workbook_porsche_sheet", "script Porsche", "the real saved workbook"),
    "importers/kia_new_models_import.py": ("test_kia_new_models_import_with_synthetic_kia_israel_page", "script main, HTTP transport replaced", "synthetic kia-israel HTML"),
    "importers/kia_import.py": ("test_kia_import_with_synthetic_kia_israel_page", "script main, HTTP transport replaced", "synthetic kia-israel HTML"),
    "scrapers/eliteparts_scraper.py": ("test_eliteparts_scraper_with_synthetic_shopify_catalog", "script --scrape/--import, HTTP transport replaced", "synthetic Shopify JSON"),
    "importers/kgm_ssangyong_importer.py": ("test_kgm_ssangyong_importer_with_synthetic_kgm_page", "script main, HTTP transport replaced", "synthetic kgm.co.il HTML"),
    "importers/samelet_import_v2.py": ("test_samelet_import_v2_with_synthetic_samelet_api", "script main, HTTP transport replaced", "synthetic samelet API JSON"),
    "scrapers/selected_parts_scraper.py": ("test_selected_parts_scraper_with_synthetic_site_pages", "script main, HTTP transport replaced", "synthetic site HTML"),
    "importers/zeekr_full_import.py": ("test_zeekr_full_pdf_importer_end_to_end", "script main", "generated PDF"),
    "scrapers/bydil_scraper.py": ("test_bydil_scraper_scrape_and_import_with_synthetic_byd_catalog", "script --scrape --import-db, httpx replaced", "synthetic bydauto table HTML"),
    "harvesters/spareto_harvester.py": ("test_spareto_harvester_end_to_end_with_synthetic_listing_and_product_pages", "script --makes TOYOTA --pages 3, httpx replaced", "synthetic listing + product HTML"),
    "importers/colmobil_import_v2.py": ("test_colmobil_import_v2_end_to_end_from_a_generated_mitsubishi_price_list", "script --brands mitsubishi", "generated /app/state/MIT.PDF"),
    "importers/freesbe_recovery.py": ("test_freesbe_recovery_dry_run_then_apply_end_to_end_against_the_isolated_database", "script dry-run, then --apply --from-report", "synthetic FREESBE API (pages + targeted lookups)"),
    "importers/repair_manufacturer_import.py": ("test_repair_manufacturer_import_end_to_end_without_pandas", "script <MFR> --dry-run, then --apply twice (REPAIR_MANUFACTURER_DATA_DIR)", "generated .xlsx workbooks (manufacturer sheet + parts_catalog_import)"),
    "importers/supplier_pdf_import.py": ("test_supplier_pdf_import_apply_end_to_end_from_a_ruled_table_pdf", "script --pdf --manufacturer ORA --apply", "generated ruled-table PDF"),
}
DEFECTS_FIXED = {
    "importers/repair_manufacturer_import.py": "died at `import pandas` (not installed); workbook paths were host-absolute so every source was skipped in the container; the INSERT omitted the NOT NULL manufacturer_id",  # real defects the end-to-end run exposed (each pinned by the test named above)
    "importers/subaru_il_importer.py": "NameError `importer_price` (the supplier offer was never written)",
    "importers/kia_new_models_import.py": "missing `import json`",
    "importers/samelet_import_v2.py": "executemany argument count did not match the statement",
    "importers/il_importer_pdf_import.py": "insert casts",
    "harvesters/spareto_harvester.py": "UPDATE passed 7 args to 6 placeholders and INSERT 8 to 9: no part was ever written",
    "importers/supplier_pdf_import.py": "price update overwrote a stored importer_price_ils with the 0.0 an IL-importer row carries",
}
BLOCKED_E2E = {}
COMPLIANT = {"maintenance/base_price_snapshot.py": "data-recovery writer: restores a verified snapshot only, sha256-confirmed (test_base_price_snapshot.py)",
             "db_update_agent.py": "apply_base_price_rule IS the policy writer (test_maintenance_policy_alignment.py, test_maintenance_guard.py)",
             "db_cleanup_agent.py": "four healers call apply_base_price_rule only (test_base_price_classification.py, test_maintenance_policy_alignment.py)"}
BLOCKER = {  # input type that has no fixture generator in the sandbox
    "pdf": "input is a PDF price list (PyMuPDF / pdfplumber / pdftotext); no representative PDF fixture generator",
    "xlsx": "input is an Excel workbook; no representative workbook fixture built",
    "http": "input is a live website / API / browser session (urllib, httpx, requests, playwright); the sandbox has no outbound access",
    "json": "input is a fixed-path JSON list whose schema fixture was not built in this pass; statement prepares on the real schema",
}
KIND = {
    "pdf": "importers/fitz_mb_suzuki_import.py importers/fitz_pdf_import.py importers/hyundai_genesis_mitsubishi_import.py importers/il_importer_pdf_import.py importers/il_pdf_batch_import.py importers/lexus_pdf_import.py importers/porsche_import.py importers/renault_pdf_import.py importers/supplier_pdf_import.py importers/zeekr_full_import.py importers/zeekr_pdf_import.py importers/colmobil_import_v2.py importers/gmc_buick_umi_import.py",
    "xlsx": "importers/cadillac_israel_import.py importers/il_consumer_price_import.py importers/isuzu_excel_import.py importers/mixed_brands_import.py importers/xls_prices_import.py importers/xlsx_il_price_import.py importers/import_parts_db.py importers/repair_manufacturer_import.py",
    "http": "importers/kgm_ssangyong_importer.py importers/kia_import.py importers/kia_new_models_import.py importers/samelet_import_v2.py importers/subaru_il_importer.py importers/mazda_il_importer.py importers/toyota_il_importer.py importers/delek_multi_importer.py importers/mct_importer.py scrapers/bydil_scraper.py scrapers/eliteparts_scraper.py scrapers/selected_parts_scraper.py harvesters/spareto_harvester.py harvesters/acura_browser_harvest.py",
    "json": "importers/asap_import.py importers/bmw_il_direct_import.py importers/bmw_il_import.py importers/import_champion_motors.py importers/import_delek_brands.py importers/import_kia_israel.py importers/jaguar_batch_import.py importers/jaguar_ndjson_import.py importers/kia_il_direct_import.py importers/lr_import.py importers/freesbe_recovery.py importers/fitz_mb_suzuki_import.py",
}
kind_of = {f: k for k, v in KIND.items() for f in v.split()}
kind_of["importers/fitz_mb_suzuki_import.py"] = "pdf"

def callers(f):
    stem = os.path.splitext(os.path.basename(f))[0]
    out = subprocess.run(["grep", "-rlE", "--include=*.py", "--include=*.sh", "--include=*.yml", stem, REPO + "/backend", REPO + "/docker-compose.yml"], capture_output=True, text=True).stdout.split()
    c = [os.path.relpath(x, REPO + "/backend") if x.startswith(REPO + "/backend") else "docker-compose.yml" for x in out]
    return [x for x in c if not x.startswith(("tests/", "devtests/", "legacy/", "maintenance/audit_importers.py")) and os.path.basename(x) != os.path.basename(f)][:4]

scan = b.scan(TREE)
rows = []
for f, v in sorted(scan.items()):
    modified = os.path.exists(os.path.join(CAND, f))
    if f in COMPLIANT:
        st, ev, lim = "VERIFIED_COMPLIANT", COMPLIANT[f], ""
    elif f in E2E:
        st, ev, lim = "FIXED_AND_VERIFIED", E2E[f][0] + " -- " + E2E[f][1], ""
    elif f in E2E_SCRIPTS:
        t, entry, fix = E2E_SCRIPTS[f]
        st = "E2E_VERIFIED_SAVED_SOURCE" if f in SAVED_SOURCE else "E2E_VERIFIED_FIXTURE"
        ev = f"{T}{t} -- entry point: {entry}; input: {SAVED_SOURCE.get(f, fix)}; persisted base_price read back from the isolated DB (new / empty / owned / repeat run)"
        lim = "not a live-source run (no live source reachable from the isolation)" if st.endswith("FIXTURE") else "saved source file, not a live source"
        if f in DEFECTS_FIXED:
            ev += f"; DEFECT FOUND+FIXED: {DEFECTS_FIXED[f]}"
    elif f in BLOCKED_E2E:
        st, ev, lim = "BLOCKED_WITH_EVIDENCE", "fixed in code (no literal formula; statement prepares on the real schema: test_importers_e2e.py::test_every_asyncpg_writer_statement_prepares_on_the_real_schema)", "E2E blocked: " + BLOCKED_E2E[f][0] + " | next action: " + BLOCKED_E2E[f][1]
    else:
        print("UNCLASSIFIED", f); continue
    rows.append({"file": f, "kinds": sorted({w[0] for w in v["writes"]}), "status": st, "changed_in_candidate": modified,
                 "callers": callers(f), "evidence": ev, "limitation": lim, "formula_lines_remaining": v["formulas"]})
out = {"generated": "2026-11-01", "writers": rows,
       "shell_writers": [{"file": "backend/scripts/fix_oem_price_gaps.sh", "status": "FIXED_AND_VERIFIED",
                          "evidence": "test_importers_e2e.py::test_fix_oem_price_gaps_copies_cost_but_never_a_stale_base_price (embedded python executed against the isolated DB)"}],
       "reviewed_non_writers": [
           {"file": "maintenance/price_audit_fix.py", "note": "rewritten: no price formula, dry-run by default, --apply only via the guarded tasks", "tests": "test_price_audit_fix.py"},
           {"file": "maintenance/margin_fix_batched.py", "note": "superseded: delegates to price_audit_fix", "tests": "test_price_audit_fix.py"},
           {"file": "maintenance/vat_fix_batched.py", "note": "no longer writes base_price (importer cost repair only)", "tests": "test_price_audit_fix.py (static)"},
           {"file": "importers/import_from_excel.py", "note": "base_price assignment removed (workbook price is not a base_price)", "tests": "static inventory"},
           {"file": "ai_catalog_builder.py", "note": "never writes base_price", "tests": "test_base_price_writers.py::test_ai_catalog_builder_*"},
           {"file": "routes/admin.py", "note": "import_parts_excel reports the price column as unsupported", "tests": "test_base_price_writers.py::test_admin_upload_*; adminImportWarnings.node-test.mjs"},
           {"file": "harvesters/rockauto_harvester.py harvesters/tecdoc_harvester.py importers/ebay_brand_importer.py importers/fridayparts_seed_import.py importers/geely_seed_import.py importers/gm_brands_seed_import.py importers/multi_brand_seed_import.py importers/rockauto_browser_import.py importers/saab_partsforsaabs_importer.py opel_car_parts_ie_import.py scrapers/bmw_spare_parts_scraper.py scripts/import_merged_catalog.py",
            "note": "INSERT INTO parts_catalog without base_price in the column list or SET clause (AST proof): NOT writers", "tests": "inventory scan"},
           {"file": "services/aliexpress_price_sync.py services/ebay_price_sync.py", "note": "read base_price only (SELECT / WHERE)", "tests": "inventory scan"}]}
json.dump(out, open(os.path.join(os.path.dirname(__file__), "..", "files", "docs", "BASE_PRICE_WRITERS.json"), "w"), indent=1, ensure_ascii=False)
from collections import Counter
print(len(rows), Counter(r["status"] for r in rows))
