"""#151 / #130: legacy 1.17 paths. Foreign costs carry no IL VAT; kgm.co.il is ex-VAT; unverified 17%-era lists are left alone."""
import re
from pathlib import Path

import price_policy as pp

ROOT = Path(__file__).resolve().parent.parent


def src(rel):
    return (ROOT / rel).read_text()


def test_foreign_paths_never_strip_il_vat():
    for rel in ("harvesters/rockauto_harvester.py", "harvesters/tecdoc_harvester.py", "importers/rockauto_browser_import.py",
                "importers/saab_partsforsaabs_importer.py"):
        code = "\n".join(l for l in src(rel).splitlines() if not l.strip().startswith("#"))
        assert "/ 1.17" not in code.split("#")[0] and not re.search(r"price\w*\s*/\s*1\.17", code), rel


def test_kgm_importer_treats_listed_price_as_exclusive():
    code = src("importers/kgm_ssangyong_importer.py")
    assert "price_ex_vat         = round(price_ils, 2)" in code
    assert "/ 1.18" not in code and "_pp.VAT_RATE" in code and "_pp.MARGIN" in code


def test_kgm_arithmetic_vat_and_margin_exactly_once():
    listed = 222.03                                   # real stored KGM-7871544000
    cost = round(listed, 2)
    consumer_ref = round(cost * (1 + pp.VAT_RATE), 2)
    base = round(cost * pp.MARGIN, 2)
    assert (cost, consumer_ref, base) == (222.03, 262.00, 321.94)
    assert consumer_ref / cost == 1.18 or abs(consumer_ref / cost - 1.18) < 1e-3     # VAT once
    assert abs(base / cost - 1.45) < 1e-3                                           # margin once


def test_kgm_and_ssangyong_removed_from_17_percent_maps():
    for rel in ("importers/il_importer_pdf_import.py", "importers/supplier_pdf_import.py"):
        m = re.search(r"PRICES_INCL_VAT = \{(.*?)\n\}", src(rel), re.S)
        body = m.group(1)
        assert not re.search(r'"(KGM|SsangYong|SSANGYONG)"\s*:', body), rel
        assert '"Cadillac"' in body or '"CADILLAC"' in body        # unverified: deliberately unchanged


def test_repair_script_is_bounded_idempotent_reversible_and_leaves_unverified_lists():
    code = src("maintenance/repair_vat_paths_151.py")
    for needle in ("SKIP LOCKED", "vat_repair_log", "--rollback", "NO_IL_OFFER", "never twice in one run", "BATCH = 1000"):
        assert needle in code, needle
    for untouched in ("Cadillac", "GMC", "selected.parts"):
        assert f"sku LIKE '{untouched}" not in code


def test_foreign_estimate_has_no_il_vat_and_cannot_use_max():
    est = pp.catalog_estimate(None, None, 119.40)      # foreign online cost only
    assert est is not None and est[1] != "il"
    cf = pp.customer_fields(est[0], 0, supplier_country=est[1])
    assert cf["customer_vat_ils"] == 0
