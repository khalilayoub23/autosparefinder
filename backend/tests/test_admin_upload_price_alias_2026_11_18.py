"""Admin parts upload: max_price_ils is the documented price header; base_price stays a legacy alias; both -> max_price_ils."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "routes" / "admin.py").read_text(encoding="utf-8")


def _price_aliases():
    m = re.search(r"_PRICE\s*=\s*\{([^}]*)\}", SRC)
    assert m
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_both_headers_accepted():
    assert {"max_price_ils", "base_price"} <= _price_aliases()


def test_price_column_still_stored_as_max_price_ils_only():
    assert "max_price_ils=price" in SRC and "existing.max_price_ils = price" in SRC
    assert "price_raw = _get(row, 'base_price')" in SRC  # internal key unchanged
