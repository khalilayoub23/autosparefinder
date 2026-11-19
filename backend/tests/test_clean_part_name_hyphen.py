"""clean_part_names must not cut a name at an in-word hyphen (2026-11-19: '2007-2013 GM 1500 ...' became '2007')."""
import pytest

import db_update_agent as d


@pytest.mark.parametrize("name", [
    '2007-2013 GM 1500 4wd 6" Suspension Lift Kit 5.5 Inch Rear Block/AAL 2.5 PES C/O Front  2.5 PES P/B Rear BDS Suspension',
    "4 Inch Lift Kit 4-Link Conversion Ford F250/F350 Super Duty 17-19 4WD Diesel 5 Inch Block 3 Leaf Main Fox 2.5 Performance  Elite Shocks",
])
def test_in_word_hyphen_never_truncates(name):
    out = d._clean_part_name_value(name)
    assert len(out) > 0.8 * len(" ".join(name.split())) and out.startswith(name.split()[0][:4])


def test_a_real_vehicle_suffix_is_still_stripped():
    assert d._clean_part_name_value("Brake Pad - Toyota Corolla 2010") == "Brake Pad"


def test_complete_year_range_keeps_its_dash():
    assert d._clean_part_name_value("2007-2013 GM 1500 Lift Kit") == "2007-2013 GM 1500 Lift Kit"
    assert d._clean_part_name_value("Pad Corolla 2007-") == "Pad Corolla 2007"


def test_dangling_year_dash_before_text_does_not_glue_words():
    assert d._clean_part_name_value("Pad 2007- sedan") == "Pad 2007 sedan"
