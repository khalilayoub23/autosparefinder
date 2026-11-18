"""C1 (2026-11-18): ICE-only products never fit pure-BEV vehicles - shared helper + inventory of every fitment writer."""
import pathlib
import re

import cpie_fitment_guard as g

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"tests", "devtests", "legacy", "alembic", "alembic_pii", "candidates", "node_modules", "state"}

# Writers that call the C1 helper (must keep calling it).
GUARDED = {"importers/car_parts_ie_import_generic.py": "fitment_allowed", "ebay_fitment_backfill.py": "bev_ice_conflict",
           "catalog_scraper.py": "bev_ice_conflict",
           "opel_car_parts_ie_import.py": "bev_ice_conflict"}
# Writers that create fitment but do NOT enforce C1 yet. Documented limitation (FIXES_TRACKER 2026-11-18): none has been measured
# to write ICE-on-BEV rows (remaining ICE-named rows on BEV models are car-parts.ie era rows), and in-process writers
# (db_update_agent, catalog_scraper, routes/admin) only pick a change up after a restart, which needs owner approval.
# A NEW writer must either call the guard or be added here deliberately.
KNOWN_UNGUARDED = {
    "BACKEND_DATABASE_MODELS.py", "db_update_agent.py", "harvesters/spareto_harvester.py", "importers/asap_import.py",
    "importers/bmw_il_import.py", "importers/catalogs_parts_importer.py", "importers/delek_multi_importer.py",
    "importers/ev_vag_lexus_import.py", "importers/fridayparts_seed_import.py", "importers/import_champion_motors.py",
    "importers/import_delek_brands.py", "importers/import_parts_db.py", "importers/isuzu_excel_import.py",
    "importers/kia_fitment_importer.py", "importers/kia_new_models_import.py", "importers/kiapartsnow_fitment_importer.py",
    "importers/lexus_pdf_import.py", "importers/lr_import.py", "importers/mazda_il_importer.py", "importers/porsche_import.py",
    "importers/post_import_fitment.py", "importers/samelet_fitment.py", "importers/sng_barratt_jaguar_import.py",
    "importers/subaru_il_importer.py", "importers/toyota_il_importer.py", "importers/zeekr_full_import.py",
    "maintenance/backfill_vw_fitment.py", "maintenance/cpie_fitment_cleanup_20261117.py", "maintenance/jaguar_fitment_from_oem_prefix.py",
    "maintenance/jaguar_fitment_prefix.py", "maintenance/kia_fitment_from_names.py", "maintenance/merge_master_parts.py",
    "maintenance/repair_brand_ids_150.py", "maintenance/run_brand_fitment_merge.py", "oempartsonline_importer.py",
    "routes/admin.py", "scrapers/bmw_spare_parts_scraper.py", "scrapers/bydil_scraper.py",
    "scrapers/eliteparts_scraper.py", "scrapers/febest_scraper.py", "scripts/import_merged_catalog.py", "workbook_normalizer.py",
}
_WRITE = re.compile(r"INSERT\s+INTO\s+part_vehicle_fitment|PartVehicleFitment\(|SET\s+compatible_vehicles|compatible_vehicles\s*=", re.I)


def _writers():
    out = set()
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if SKIP_DIRS & set(rel.split("/")[:-1]):
            continue
        try:
            if _WRITE.search(p.read_text(encoding="utf-8", errors="ignore")):
                out.add(rel)
        except OSError:
            pass
    return out


def test_every_fitment_writer_is_guarded_or_documented():
    unknown = _writers() - set(GUARDED) - KNOWN_UNGUARDED
    assert not unknown, f"new fitment writer(s) without the C1 guard or a documented exception: {sorted(unknown)}"


def test_guarded_writers_call_the_guard():
    for rel, fn in GUARDED.items():
        assert fn in (ROOT / rel).read_text(encoding="utf-8"), rel
        assert "cpie_fitment_guard" in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_bev_ice_conflict_rule():
    bev = sorted(g.BEV_MODELS)[0]
    assert g.bev_ice_conflict("Engine oil filter ACME", *bev)                # ICE-only on a pure BEV
    assert not g.bev_ice_conflict("Brake pads ACME", *bev)                   # valid BEV part
    assert not g.bev_ice_conflict("Engine oil filter ACME", "Toyota", "Corolla")   # non-BEV unaffected
    assert not g.bev_ice_conflict("Engine oil filter ACME", "Volkswagen", "Golf")
    assert not g.bev_ice_conflict("Engine oil filter ACME", "BYD", "Seal U")        # PHEV/DM-i stays allowed
    assert not g.bev_ice_conflict("Cabin air filter ACME", *bev)             # not ICE-only


def test_bev_match_is_case_and_punctuation_insensitive_but_exact():
    assert g.bev_ice_conflict("Engine oil filter ACME", "BMW", "i4 M50")
    assert g.bev_ice_conflict("Engine oil filter ACME", "Volkswagen", "ID.4")
    assert g.bev_ice_conflict("Engine oil filter ACME", "VW", "Id4")
    assert not g.bev_ice_conflict("Engine oil filter ACME", "BMW", "i4")          # unlisted spelling/model: no prefix match
    assert not g.bev_ice_conflict("Engine oil filter ACME", "Kia", "Niro Plus")   # ambiguous HEV/PHEV stays allowed


def test_sump_plug_washer_is_not_ice_only_and_new_bev_slugs():
    assert not g.is_ice_only("Sump plug washer (RIDEX, 135O0004)")
    assert g.is_ice_only("Oil sump ACME")
    assert g.bev_ice_conflict("Engine oil filter ACME", "Citroen", "Ami")
    assert g.bev_ice_conflict("Spark plug ACME", "Opel", "Rocks E Hatchback")
    assert not g.bev_ice_conflict("Engine oil filter ACME", "Opel", "Corsa")   # ICE Corsa stays allowed
    assert not g.bev_ice_conflict("Engine oil filter ACME", "Opel", "Ampera")  # PHEV Ampera stays allowed
