"""Different aftermarket brands' article numbers must never be merged on punctuation-stripped equality (2026-11-14)."""
import part_identity as pi


def _r(name, num):
    return {"name": name, "oem_number": num}


def test_real_wrong_merges_are_distinct():
    assert pi.distinct_articles(_r("REMSA 1187.02 Brake pad set incl. wear warning", "1187.02"),
                                _r("TOPRAN 118 702 Gear selector cable", "118 702"))
    assert pi.distinct_articles(_r("K2 T104 Brake Fluid Capacity: 0,5l", "Article №: T104"),
                                _r("STEINHOF T-104 Towbar", "Article №: T-104"))
    assert pi.distinct_articles(_r("ELSTOCK 15-1309 Power steering pump Hydraulic", "15-1309"),
                                _r("Hyundai Trajet Van Repair kit, suspension strut DACO Germany 151309", "151309"))


def test_oem_punctuation_variants_stay_mergeable():
    assert not pi.distinct_articles(_r("מפריד שמן", "06Q103495H"), _r("Vapor Canister Purge Valve Assembly", "06Q-103-495-H"))
    assert not pi.distinct_articles(_r("Brake Hose", "587321D000DS"), _r("צינור בלם קד R קרנס 06", "587321D000-DS"))


def test_same_raw_number_is_never_distinct_even_with_different_titles():
    assert not pi.distinct_articles(_r("ABS Sensor Front Left or Right ADA107108", "ADA107108"),
                                    _r("BLUE PRINT ADA107108 Chrysler 300c Estate", "ADA107108"))
    assert not pi.distinct_articles(_r("VAICO V60-0317 Brake Fluid", "Article №: V60-0317"),
                                    _r("VAICO V60-0317 Brake Fluid", "article №: v60-0317"))


def test_same_brand_punctuation_variant_is_mergeable():
    assert not pi.distinct_articles(_r("BREMBO 08.D045.11 Brake disc", "08.D045.11"), _r("BREMBO 08D04511 Brake disc", "08D04511"))


def test_unclassifiable_rows_are_not_called_distinct():
    assert not pi.distinct_articles(_r("צלחת בלם אחורי", "08D04511"), _r("BREMBO 08.D045.11 Sprinter", "08.D045.11"))
    assert pi.brand_prefix("1187.02 pads", "1187.02") is None and pi.brand_prefix("", "x") is None
