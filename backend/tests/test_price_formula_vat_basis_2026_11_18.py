"""Pins the approved customer-price formula and the supplier VAT-basis evidence rule (mission 2026-11-18).

Approved order (price_policy.unit_price): ex-VAT cost -> net = round(cost x 1.45, 2) -> vat = round(net x rate, 2) -> total = net + vat.
The 45% is a MARKUP ON COST (net = 1.45 x cost, gross margin 31.0%), applied to the EX-VAT cost; VAT is added on the net, once.
"""
import pathlib
import re

import pytest

import price_policy as pp

BACKEND = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_registry():
    pp.load_vat_bases(None)
    yield
    pp.load_vat_bases(None)


def test_israeli_exclusive_cost():
    u = pp.unit_price(100, "X", "IL", "exclusive")
    assert (u.net, u.vat, u.total) == (145.00, 26.10, 171.10)


def test_israeli_inclusive_cost_vat_not_applied_twice():
    # 118 incl. VAT -> ex-VAT 100 -> same customer price as an exclusive 100: VAT once, markup once
    u = pp.unit_price(118, "X", "IL", "inclusive")
    assert (u.net, u.vat, u.total) == (145.00, 26.10, 171.10)
    assert pp.unit_price(118, "X", "IL", "inclusive").total == pp.unit_price(100, "X", "IL", "exclusive").total


def test_foreign_supplier_markup_only():
    u = pp.unit_price(100, "Y", "IE", "exclusive")
    assert (u.net, u.vat, u.total) == (145.00, 0.0, 145.00)
    # basis is irrelevant for a foreign supplier (no Israeli VAT stripped or added)
    assert pp.unit_price(100, "Y", "IE", "inclusive").total == 145.00


def test_unknown_basis_israeli_not_priced_151():
    assert pp.unit_price(100, "X", "IL", "unknown") is None
    assert pp.vat_basis_blocks("X", "IL", "unknown")
    pp.load_vat_bases({"Some New Importer": None})          # NULL in suppliers -> registry 'unknown' -> blocked
    assert pp.unit_price(100, "Some New Importer", "IL") is None
    assert pp.unit_price(100, "Not In Registry", "IL") is None   # unregistered local supplier is blocked too
    assert pp.unit_price(100, "Foreign Co", "DE") is not None     # foreign unaffected


def test_rounding_points():
    # net is rounded BEFORE vat is taken from it (stored/displayed net is the taxable base)
    u = pp.unit_price(33.33, "X", "IL", "exclusive")
    assert u.net == round(33.33 * 1.45, 2) == 48.33
    assert u.vat == round(48.33 * 0.18, 2) == 8.70
    assert u.total == round(48.33 + 8.70, 2) == 57.03
    # inclusive: ex-VAT cost rounded first
    assert pp.cost_ex_vat(100, "X", "IL", "inclusive") == 84.75


def test_line_vat_is_per_unit_times_qty():
    u = pp.unit_price(33.33, "X", "IL", "exclusive")
    assert u.line(3) == (round(48.33 * 3, 2), round(8.70 * 3, 2), round(48.33 * 3 + 8.70 * 3, 2))


def test_markup_is_not_gross_margin():
    u = pp.unit_price(100, "Y", "IE", "exclusive")
    assert u.net == 145.00                   # markup on cost; a 45% GROSS MARGIN would be 100/0.55 = 181.82
    assert u.net != round(100 / 0.55, 2)


def test_base_price_is_net_not_customer_total():
    import base_price_rule as bpr
    from types import SimpleNamespace as NS
    row = NS(offer_ils=100, offer_usd=None, offer_supplier="X", offer_country="IL", has_visible=True, other_class_only=False,
             importer_price_ils=None, online_price_ils=None)
    pp.load_vat_bases({"X": "exclusive"})
    net, src = bpr.target(row, 3.7)
    assert (net, src) == (145.00, "offer")                  # base_price = net, never the 171.10 customer total
    assert net != pp.unit_price(100, "X", "IL").total


def test_base_price_rule_never_reads_max_price_ils():
    import base_price_rule as bpr
    from types import SimpleNamespace as NS
    row = NS(offer_ils=None, offer_usd=None, has_visible=False, other_class_only=False, importer_price_ils=None,
             online_price_ils=None, max_price_ils=999)
    assert bpr.target(row, 3.7) == (None, "no_basis")


# ── supplier creation: no unsupported VAT-basis default ──────────────────────────────────────────────────────────
def _supplier_inserts():
    for p in BACKEND.rglob("*.py"):
        rel = p.relative_to(BACKEND).parts
        if rel[0] in ("tests", "devtests", "legacy") or "node_modules" in rel:
            continue
        txt = p.read_text(errors="ignore")
        for m in re.finditer(r"INSERT\s+INTO\s+suppliers\b(.{0,900}?)(?:ON CONFLICT|RETURNING|\"\"\"|\"\s*\)|$)", txt, re.I | re.S):
            yield p.relative_to(BACKEND).as_posix(), m.group(0)


def test_no_creation_path_assigns_unverified_basis():
    """A new supplier gets NULL (no column written) or 'unknown'. 'exclusive'/'inclusive' only together with an evidence column."""
    seen = 0
    for rel, sql in _supplier_inserts():
        seen += 1
        if "vat_price_basis" not in sql:
            continue                                        # NULL == unknown (blocked for a local supplier, see test above)
        if re.search(r"'(exclusive|inclusive)'", sql):
            assert "vat_basis_evidence" in sql, f"{rel}: basis assigned without evidence"
    assert seen >= 10


def test_pdf_importer_creates_suppliers_as_unknown():
    src = (BACKEND / "importers" / "supplier_pdf_import.py").read_text()
    body = src.split("async def _ensure_supplier", 1)[1].split("OFFER_OK", 1)[0]
    assert "'unknown'" in body and "'exclusive'" not in body.replace("'exclusive'` ", "").split('"""', 2)[2]


def test_bdv_basis_has_evidence_constant():
    src = (BACKEND / "scrapers" / "bdv_playwright_scraper.py").read_text()
    assert "BDV_VAT_EVIDENCE = {" in src and "vat_basis_evidence" in src


def test_admin_parts_import_file_is_a_multipart_upload_not_a_query_param():
    """2026-11-18 live finding: `file,` (no annotation) made FastAPI expose `file` as a REQUIRED QUERY parameter, so every real XLSX
    upload to /api/v1/admin/parts/import was a 422. Every upload route must declare its file as UploadFile = File(...)."""
    import ast
    tree = ast.parse((BACKEND / "routes" / "admin.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "import_parts_excel")
    arg = next(a for a in fn.args.args if a.arg == "file")
    assert arg.annotation is not None and ast.unparse(arg.annotation) == "UploadFile"


def test_admin_parts_import_new_part_has_manufacturer_id_lowercase_condition_and_savepoint():
    """2026-11-18 live finding #2: a NEW part was inserted without manufacturer_id (NOT NULL) -> 500 and nothing stored."""
    src = (BACKEND / "routes" / "admin.py").read_text()
    body = src.split("async def import_parts_excel", 1)[1].split("# JOB FAILURES", 1)[0]
    assert "manufacturer_id=_mfr_id" in body
    assert "rejected_manufacturer" in body and "LOWER(name) = LOWER(:n)" in body      # exact brand match, no create, no LIKE
    assert "part_condition='new'" in body
    assert "db.begin_nested()" in body
    assert "base_price=" not in body                                                  # the upload never writes base_price


def test_admin_frontend_import_overrides_json_content_type():
    f = BACKEND.parent / "frontend" / "src" / "pages" / "Admin.jsx"
    if not f.exists():
        pytest.skip("frontend sources are not mounted in the backend container (runs on the host)")
    src = f.read_text()
    assert "api.post('/admin/parts/import', fd, { headers: { 'Content-Type': undefined } })" in src
