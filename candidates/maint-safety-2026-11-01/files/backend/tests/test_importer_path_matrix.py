"""Importer accessibility matrix - MISSING-source behaviour, per importer (2026-11-02).

For every active importer/harvester that reads a local file, the REAL script runs inside the production container layout
(candidate tree as /app, empty tmpfs /app/state + /app/uploads, no /opt/autosparefinder) with its source absent, against the
isolated sandbox DB. Required: non-zero exit, the resolved path named in the output, and no row written to the DB.
The present-source half of the matrix is test_importer_default_locations_e2e.py (default paths) and
test_importers_e2e_scripts.py (every importer, fixture through its documented entry); the static half is
test_importer_container_paths.py. Fixture-only: none of this touches a live source.

Needs `ASF_PATH_TEST_LAYOUT=1` (set by the scratch runner): in the shared sandbox_backend /app is the production tree."""
import os
from pathlib import Path

import asyncpg
import pytest

from importer_e2e_harness import dsn, run_script

pytestmark = pytest.mark.skipif(os.environ.get("ASF_PATH_TEST_LAYOUT") != "1",
                                reason="needs the scratch container layout (ASF_PATH_TEST_LAYOUT=1)")

# (script, args, [paths the failure must name]) - every path is container-absolute and absent in the layout.
MATRIX = [
    ("importers/amayama_price_import.py", [], ["/app/state/amayama_parts.json"]),
    ("importers/rockauto_price_import.py", [], ["/app/state/rockauto_parts.json"]),
    ("importers/bmw_il_direct_import.py", [], ["/app/state/champion_motors_parts.json"]),
    ("importers/bmw_il_import.py", [], ["/app/bmw_parts.json"]),
    ("importers/cadillac_israel_import.py", [], ["/tmp/cadillac_parts.xlsx"]),
    ("importers/car_parts_ie_import_generic.py", ["--brand", "toyota", "--file", "/app/state/none.json"], ["/app/state/none.json"]),
    ("importers/delek_json_price_update.py", [], ["/app/jlr_parts.json", "/app/ford_parts.json", "/app/mini_parts.json"]),
    ("importers/fitz_mb_suzuki_import.py", [], ["/app/uploads/MERCEDES-BENZ_20260518_154959.pdf", "/app/uploads/SUZUKI_20260518_170146.pdf"]),
    ("importers/fitz_pdf_import.py", [], ["/app/uploads/MITSUBISHI_20260518_161229.pdf"]),
    ("importers/hyundai_genesis_mitsubishi_import.py", [], ["/app/uploads/HYUNDAI_20260518_185642.pdf"]),
    ("importers/il_pdf_batch_import.py", [], ["/app/uploads/HYUNDAI_20260518_185642.pdf"]),
    ("importers/renault_pdf_import.py", [], ["/app/uploads/RENAULT_20260518_223327.pdf"]),
    ("importers/zeekr_pdf_import.py", [], ["/app/uploads/ZEEKER001_20260519_131930.pdf"]),
    ("importers/zeekr_full_import.py", [], ["/app/uploads/ZEEKER001_*.pdf"]),
    ("importers/lexus_pdf_import.py", [], ["/app/uploads/LEXUS_20260612_082412.pdf"]),
    ("importers/porsche_import.py", [], ["/app/porsche_prices.pdf"]),
    ("importers/gm_run_import.py", [], ["/tmp/gm_vehicle_parts.json"]),
    ("importers/gmc_buick_umi_import.py", [], ["/tmp/umi_full_text.txt"]),
    ("importers/il_consumer_price_import.py", ["--file", "/app/uploads/none.xlsx", "--brand", "Chevrolet"], ["/app/uploads/none.xlsx"]),
    ("importers/xlsx_il_price_import.py", ["--file", "/app/uploads/none.xlsx", "--brand", "Honda"], ["/app/uploads/none.xlsx"]),
    ("importers/mixed_brands_import.py", [], ["/app/uploads/MIXED CARS BRANDS.xlsx"]),
    ("importers/import_champion_motors.py", [], ["/app/state/champion_motors_parts.json"]),
    ("importers/import_kia_israel.py", [], ["/app/state/kia_israel_parts.json"]),
    ("importers/import_delek_brands.py", ["--all"], ["/app/nio_parts.json", "/app/mini_parts.json"]),
    ("importers/isuzu_excel_import.py", [], ["/tmp/isuzuFile.xlsx"]),
    ("importers/jaguar_ndjson_import.py", [], ["/app/state/jaguar_parts_raw.ndjson"]),
    ("importers/jaguar_batch_import.py", [], ["/app/state/jaguar_parts_raw.ndjson"]),
    ("importers/sng_barratt_jaguar_import.py", [], ["/app/state/jaguar_parts_raw.ndjson"]),
    ("importers/kia_il_direct_import.py", [], ["/tmp/Kia_oem.json"]),
    ("importers/lr_import.py", [], ["/app/land_rover_parts.json"]),
    ("importers/rockauto_browser_import.py", [], ["/tmp/rockauto_daewoo.json"]),
    ("harvesters/acura_browser_harvest.py", [], ["/app/state/acura_parts.json"]),
    ("maintenance/backfill_vw_fitment.py", [], ["/app/state/champion_motors_parts.json"]),
    ("importers/repair_manufacturer_import.py", ["ORA", "--dry-run"], ["/nonexistent_data_dir"]),
]
_ENV = {"importers/repair_manufacturer_import.py": {"REPAIR_MANUFACTURER_DATA_DIR": "/nonexistent_data_dir"}}


async def _counts(conn):
    return [await conn.fetchval(f"SELECT COUNT(*) FROM {t}") for t in ("suppliers", "parts_catalog", "supplier_parts", "car_brands", "part_vehicle_fitment")]


def _concrete(path):
    return path if "*" not in path else str(Path(path).parent)


@pytest.mark.parametrize("script,args,paths", MATRIX, ids=[m[0].split("/")[-1] for m in MATRIX])
async def test_missing_source_fails_loudly_names_the_path_and_writes_nothing(script, args, paths):
    for p in paths:
        if "*" not in p and p != "/nonexistent_data_dir":
            assert not Path(p).exists(), f"{p} exists in the layout: the run would prove nothing"
    conn = await asyncpg.connect(dsn())
    try:
        before = await _counts(conn)
        p = run_script(script, *args, env_extra=_ENV.get(script), timeout=120)
        out = p.stdout + p.stderr
        assert p.returncode != 0, f"{script} reported SUCCESS with no source:\n{out[-700:]}"
        assert any(x in out for x in paths), f"{script} failed without naming any of {paths}:\n{out[-700:]}"
        assert await _counts(conn) == before, f"{script} wrote rows although its source was missing"
    finally:
        await conn.close()
