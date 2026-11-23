"""Regression (2026-11-23): an ASAP MSRP/MAP/jobber price can never become an orderable offer or an advertised price.
Pure source/SQL-fragment checks - no DB."""
import pathlib
import price_policy as pp

SRC = pathlib.Path(__file__).resolve().parent.parent


def test_orderable_requires_active_supplier_and_available_offer():
    sql = pp.ORDERABLE_OFFER_SQL
    assert "s.is_active = TRUE" in sql and "sp.is_available = TRUE" in sql
    assert "s.is_active = TRUE" in pp.VISIBLE_OFFER_SQL  # advertised price basis inherits both gates


def test_inactive_supplier_offer_is_not_orderable():
    assert pp.offer_is_orderable(True, False, "ASAP Network") is False
    assert pp.offer_is_orderable(False, True, "Anyone") is False


def test_importer_never_reads_jobber_pricing_as_cost():
    src = (SRC / "importers" / "asap_import.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    # jobber_pricing is mentioned only in docs/comments; it is never indexed from a row
    assert "row[\"jobber" not in code and "row.get(\"jobber" not in code and "['jobber" not in code
