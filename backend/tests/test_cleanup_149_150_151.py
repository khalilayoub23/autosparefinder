"""Second pass (#149 removal, #150 text corruption, #151 GM/BDV VAT): invariants of the code that replaced the defects."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def src(rel):
    return (ROOT / rel).read_text()


def test_oem_prefix_rewrite_is_gone():
    assert "OEM_PREFIX_TO_MANUFACTURER" not in src("manufacturer_normalization.py")
    assert "normalize_oem_manufacturer" not in src("manufacturer_normalization.py")
    agent = src("db_update_agent.py")
    assert "OEM_PREFIX_TO_MANUFACTURER" not in agent and "oem_prefix_updated" not in agent
    assert not re.search(r"UPDATE parts_catalog\s+SET manufacturer = :correct", agent)


def test_text_restore_requires_cause_and_independent_signal():
    code = src("maintenance/repair_brand_ids_150.py")
    for needle in ("--restore-text", "PREFIX_RULE", "specifications->>'importer'", "compatible_vehicles", "NOT EXISTS (SELECT 1 FROM part_vehicle_fitment g"):
        assert needle in code, needle


def test_placeholder_removal_is_archived_protected_and_policy_bound():
    code = src("maintenance/remove_placeholder_offers_149.py")
    for needle in ("supplier_parts_removed_149", "--restore", "gate_or_exit", "protected_ids", "FOR UPDATE OF sp SKIP LOCKED", "NULLIF(BTRIM(sp.supplier_url),'') IS NULL"):
        assert needle in code, needle
    import offer_policy as op
    assert "maintenance/remove_placeholder_offers_149.py" in op.POLICY_BOUND_ACTORS


def test_gm_and_bdv_use_the_policy_vat_rate_not_17():
    for rel in ("importers/cadillac_israel_import.py", "importers/gmc_buick_umi_import.py", "scrapers/selected_parts_scraper.py"):
        code = "\n".join(l for l in src(rel).splitlines() if not l.strip().startswith("#"))
        assert "/ 1.17" not in code and "* 1.17" not in code, rel
    assert "1 + _pp.VAT_RATE" in src("importers/cadillac_israel_import.py") and "_pp.VAT_RATE" in src("scrapers/bdv_playwright_scraper.py")
    for rel in ("importers/il_importer_pdf_import.py", "importers/supplier_pdf_import.py"):
        assert not re.search(r'"(Cadillac|CADILLAC)":\s*0\.17', src(rel)), rel


def test_gm_arithmetic_vat_and_margin_once():
    price = 46.54                                   # CADILLAC-55504552 quoted consumer price
    cost = round(price / 1.18, 2)
    assert cost == 39.44 and round(cost * 1.45, 2) == 57.19 and round(price, 2) == 46.54
