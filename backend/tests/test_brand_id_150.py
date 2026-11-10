"""#150: no fuzzy brand resolution in importers; the repair script keeps its safety rules."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_LIKE = {"importers/lr_import.py"}      # fixed literal '%land rover%', not derived from the incoming brand text


def test_no_importer_resolves_a_brand_by_substring_of_incoming_text():
    bad = []
    for sub in ("importers", "harvesters", "scrapers"):
        for p in (ROOT / sub).glob("*.py"):
            rel = f"{sub}/{p.name}"
            src = p.read_text(errors="ignore")
            for m in re.finditer(r"FROM car_brands WHERE[^\"']*LIKE", src):
                if rel not in ALLOWED_LIKE:
                    bad.append(rel)
    assert not bad, bad


def test_resolver_replaced_the_fixed_dictionary():
    src = (ROOT / "importers" / "car_parts_ie_import_generic.py").read_text()
    assert "MANUFACTURER_IDS" not in src and "hashlib.md5" not in src and "resolve_source_brand" in src


def test_repair_script_rules():
    src = (ROOT / "maintenance" / "repair_brand_ids_150.py").read_text()
    for needle in ("MIN_GROUP = 100", "brand_id_repair_log", "FOR UPDATE", "SKIP LOCKED", "pc.manufacturer_id = x.prev",
                   "f.manufacturer_id = x.prev", "--rollback", "ALIAS_TEXTS"):
        assert needle in src, needle
    assert "updated_at" not in src.split("UPDATE parts_catalog pc SET manufacturer_id")[1].split("\n")[0]   # id repair: no Meilisearch churn (the text repair bumps it on purpose)
