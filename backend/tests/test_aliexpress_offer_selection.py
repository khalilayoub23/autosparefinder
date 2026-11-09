"""AliExpress sync: which stored row an incoming listing updates (exact typed match > untyped fallback > conflict)."""
from types import SimpleNamespace as N

from services.aliexpress_price_sync import select_existing_offer as pick


def R(i, t):
    return N(id=i, part_type=t)


def test_exact_typed_match_beats_earlier_untyped_row():
    assert pick([R(1, None), R(2, "aftermarket")], "aftermarket").id == 2


def test_untyped_is_fallback_when_no_typed_match():
    assert pick([R(1, None), R(2, "oe_equivalent")], "aftermarket").id == 1


def test_never_substitutes_one_known_variant_for_another():
    assert pick([R(2, "oe_equivalent")], "aftermarket") is None
    assert pick([R(2, "oem")], "oe_equivalent") is None
    assert pick([R(2, "oem"), R(3, "oe_equivalent")], "aftermarket") is None


def test_multiple_variants_pick_the_matching_one():
    rows = [R(1, "oem"), R(2, "oe_equivalent"), R(3, "aftermarket")]
    assert pick(rows, "oe_equivalent").id == 2
    assert pick(rows, "aftermarket").id == 3


def test_original_and_oem_spellings_are_one_identity():
    assert pick([R(1, "original")], "oem").id == 1


def test_hebrew_label_matches_canonical():
    assert pick([R(1, "מקורי")], "oem").id == 1


def test_no_rows_and_untyped_incoming():
    assert pick([], "aftermarket") is None
    assert pick([R(1, "aftermarket")], None) is None
    assert pick([R(1, None)], None).id == 1
