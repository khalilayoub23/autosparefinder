"""ASAP gives MSRP, never acquisition cost (2026-11-20). Static guards for the importer and the quarantine script."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMPORTER = (ROOT / "importers" / "asap_import.py").read_text()
QUAR = (ROOT / "maintenance" / "asap_msrp_quarantine.py").read_text()


def test_importer_never_writes_msrp_as_cost_or_available_offer():
    code = re.sub(r'""".*?"""', "", IMPORTER, flags=re.S)           # ignore docstrings
    code = re.sub(r"#.*", "", code)
    # the only place importer_price_ils may appear is not an assignment of a derived value
    assert not re.search(r"importer_price_ils\s*=\s*(CASE|\$|cost)", code)
    assert "importer_price_ils = CASE" not in IMPORTER
    assert "in_stock = False" in IMPORTER                         # offers never available without a verified cost
    assert "0.0, 0, TRUE, NOW(), NOW())" in IMPORTER               # new parts start with importer cost 0 / base 0


def test_importer_insert_placeholders_match_arguments():
    start = IMPORTER.index("INSERT INTO parts_catalog")
    ins = IMPORTER[start:IMPORTER.index("RETURNING id", start)]
    nums = {int(n) for n in re.findall(r"\$(\d+)", ins)}
    assert nums == set(range(1, max(nums) + 1))                   # no unused / skipped parameter number (asyncpg rejects it)


def test_quarantine_is_reversible_non_destructive_and_policy_gated():
    assert "DELETE" not in QUAR.upper().replace("DELETED", "")
    assert "gate_or_exit" in QUAR and "msrp_quarantine" in QUAR and "--restore" in QUAR
    assert "ABS(pc.importer_price_ils - sp.price_ils) <= 1" in QUAR   # MSRP derivation proven by equality (rounding <= 1 ILS)
    assert "o.supplier_id <> sp.supplier_id" in QUAR               # never touches a part another supplier also offers
    import offer_policy
    assert "maintenance/asap_msrp_quarantine.py" in offer_policy.POLICY_BOUND_ACTORS


def test_quarantine_targets_ignore_null_specifications_and_rounding():
    """2026-11-23: `NOT (specifications ? 'k')` is NULL for a part with NULL specifications, which hid 1,140 MSRP-derived
    Fox/Banks parts from the first run; the cost also differs from the offer price by importer rounding (<= 1 ILS)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "maintenance" / "asap_msrp_quarantine.py").read_text()
    assert "COALESCE(pc.specifications, '{}'::jsonb) ? 'msrp_quarantine'" in src
    assert "ABS(pc.importer_price_ils - sp.price_ils) <= 1" in src
    assert "prev_max" in src
