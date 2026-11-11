"""il_consumer_price_import: the VAT basis is explicit, never assumed (2026-11-11 closure of #151/#130 for CHEV)."""
import subprocess, sys, pathlib
import price_policy as pp

SCRIPT = str(pathlib.Path(__file__).resolve().parent.parent / "importers" / "il_consumer_price_import.py")
sys.path.insert(0, str(pathlib.Path(SCRIPT).parent))
import il_consumer_price_import as imp


def test_inclusive_strips_vat_once_and_applies_margin_once():
    cost, retail, base = imp.price_fields(118.0, "inclusive")
    assert (cost, retail, base) == (100.0, 118.0, round(100.0 * pp.MARGIN, 2))


def test_exclusive_adds_vat_once_to_the_reference_only():
    cost, retail, base = imp.price_fields(100.0, "exclusive")
    assert (cost, retail, base) == (100.0, 118.0, 145.0)


def test_unknown_or_missing_basis_is_refused():
    for bad in (None, "", "unknown", "maybe"):
        try:
            imp.price_fields(100.0, bad)
        except ValueError:
            continue
        raise AssertionError(f"basis {bad!r} must be refused")


def test_cli_refuses_without_basis_and_evidence():
    r = subprocess.run([sys.executable, SCRIPT, "--file", "/nonexistent.xlsx", "--brand", "X"], capture_output=True, text=True)
    assert r.returncode != 0 and "--vat-basis" in r.stderr
    r = subprocess.run([sys.executable, SCRIPT, "--file", "/nonexistent.xlsx", "--brand", "X", "--vat-basis", "inclusive", "--evidence", "x"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "evidence" in (r.stderr + r.stdout)


def test_no_hardcoded_17_percent_left():
    src = open(SCRIPT, encoding="utf8").read()
    assert "1.17" not in src.split('"""', 2)[2]
