"""2026-11-18 resolver gaps: brands the generated vocabulary could not use as a vehicle-first SUFFIX are accepted only through
article_brand_extensions.json (measured evidence + article-shape regex); everything else stays fail-closed."""
import json
import pytest

import part_identity as pi


@pytest.fixture(autouse=True)
def reload_data():
    pi.configure(); yield; pi.configure()


@pytest.mark.parametrize("name,number,brand", [
    ("FIAT 130 Saloon Oil filter CHAMPION COF100270S", "COF100270S", "CHAMPION"),
    ("VAUXHALL Cresta Saloon Spark plug CHAMPION OE009/T10", "OE009/T10", "CHAMPION"),
    ("Ford Sierra MK1 Steering rack gaiter FAST FT20064", "FT20064", "FAST"),
    ("DACIA timing chain motive TCK304", "TCK304", "MOTIVE"),
    ("Hyundai Kona OS Lambda sensor NTK 94002", "94002", "NTK"),
    ("CHEVROLET Rezzo U100 brake system EBC Brakes DP42150R", "DP42150R", "EBC BRAKES"),
    ("FORD GT Mk1 Coupe Brake pad set Hi-Q SP2119", "SP2119", "HI-Q"),
    ("PEUGEOT 405 II Estate (4E) Tailgate strut FA KROSNO 23540", "23540", "FA KROSNO"),
    ("OPEL Rekord E2 Saloon Tappet AE FOL38", "FOL38", "AE"),
    ("DACIA catalytic converter AS 74176", "74176", "AS"),
    ("Volvo 240 Estate Indicator bulb XL 132325", "132325", "XL"),
    ("Toyota Prius 2 Anti-roll bar link 555 SL-3920-M", "SL-3920-M", "555"),
    ("Daewoo Lanos - Fram PH3387A", "PH3387A", "FRAM"),
])
def test_extension_brands_resolve_in_vehicle_first_titles(name, number, brand):
    assert pi.title_brand(name, number) == brand


def test_brand_first_titles_still_resolve():
    assert pi.title_brand("CHAMPION CET11P VW Touareg 7p spark plug", "CET11P") == "CHAMPION"
    assert pi.title_brand("555 SA-N612L NISSAN Kicks (P15) control arm", "SA-N612L") == "555"


def test_article_shape_mismatch_stays_unresolved():
    # CHAMPION is on the generic-word deny-list: a number that is NOT a Champion article shape must not resolve
    assert pi.title_brand("FORD Transit Champion 123456789012", "123456789012") is None
    # a vehicle word ending a vehicle-first title never becomes a brand
    assert pi.title_brand("Ford Transit CONNECT 1234", "1234") is None


def test_extension_without_evidence_or_with_bad_regex_is_ignored(tmp_path):
    f = tmp_path / "ext.json"
    f.write_text(json.dumps({"brands": [
        {"brand": "NOEVID", "rows": 500, "suffix_article_regex": "^\\d+$", "evidence": "short"},
        {"brand": "BADRX", "rows": 500, "suffix_article_regex": "([", "evidence": "x" * 40},
        {"brand": "GOOD", "rows": 500, "suffix_article_regex": "^\\d+$", "evidence": "x" * 40}]}))
    ext = pi.load_extensions(f)
    assert set(ext) == {"GOOD"}
    assert pi.load_extensions(tmp_path / "missing.json") == {}      # unreadable file -> nothing extended (fail closed)


def test_extensions_only_add_support_never_lower_a_count():
    voc = pi._vocabulary()
    assert voc["FRAM"] >= 31 and voc["555"] >= 611
    assert voc.get("CHAMPION", 0) >= 2717       # the generated count is kept (max of the two)


def test_every_extension_entry_is_measured_and_wellformed():
    d = json.load(open(pi.EXTENSIONS_FILE))
    assert d["brands"]
    for b in d["brands"]:
        assert len(b["evidence"]) >= 20 and b["rows"] >= 30 and b["coverage"] >= 0.95, b["brand"]
        import re; re.compile(b["suffix_article_regex"])
