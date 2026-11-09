"""Every parts_catalog.part_type writer goes through variant_identity.catalog_part_type (class convention), never a raw label."""
from pathlib import Path

import pytest

import variant_identity as vi

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("label,expected", [
    ("OEM Original", "oem"), ("original", "oem"), ("Genuine", "oem"), ("מקורי", "oem"), ("OEM", "oem"),
    ("OEM Equivalent", "oe_equivalent"), ("oe_equivalent", "oe_equivalent"), ("שווה ערך", "oe_equivalent"),
    ("Aftermarket", "aftermarket"), ("חליפי", "aftermarket"), ("analog", "aftermarket"),
    ("remanufactured", "remanufactured"), ("used", "used"),
    (None, None), ("", None), ("unknown", None), ("weird-label", None), ("מקורי/חליפי", None),
])
def test_catalog_part_type(label, expected):
    assert vi.catalog_part_type(label) == expected


def test_variants_stay_distinct():
    assert len({vi.catalog_part_type(x) for x in ("oem", "oe equivalent", "aftermarket", "remanufactured", "used")}) == 5


WRITERS = ["catalog_scraper.py", "workbook_normalizer.py", "importers/import_parts_db.py", "importers/import_champion_motors.py",
           "importers/import_delek_brands.py", "importers/jaguar_ndjson_import.py", "importers/samelet_import_v2.py"]


@pytest.mark.parametrize("rel", WRITERS)
def test_dynamic_writers_call_the_central_function(rel):
    src = (ROOT / rel).read_text()
    assert "catalog_part_type" in src or "variant_key" in src, rel


@pytest.mark.parametrize("rel", ["importers/lexus_pdf_import.py", "importers/isuzu_excel_import.py", "scrapers/eliteparts_scraper.py",
                                 "importers/xlsx_il_price_import.py", "ai_catalog_builder.py"])
def test_literal_writers_use_the_catalog_class_not_original_or_uppercase(rel):
    src = (ROOT / rel).read_text()
    for bad in ("'original'", "'Original'", '"OEM",'):
        for line in src.splitlines():
            if "part_type" in line and bad in line and not line.strip().startswith("#"):
                pytest.fail(f"{rel}: non-canonical catalog part_type literal {bad}: {line.strip()}")
    assert "'oem'" in src or '"oem"' in src
