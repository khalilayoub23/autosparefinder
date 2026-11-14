"""The ONE parts-identity policy (part_identity.classify) is FAIL CLOSED: only a proven SAME may be merged.
Adversarial + regression table for the 2026-11-14 wrong-merge incident (522 pairs unmerged in production)."""
import json
from pathlib import Path

import pytest

import part_identity as pi

ROOT = Path(__file__).resolve().parent.parent
S, D, U = pi.SAME, pi.DIFFERENT, pi.UNCERTAIN


def row(name, num, pt="aftermarket", mfr="m1", make="Toyota", **kw):
    return dict({"name": name, "oem_number": num, "part_type": pt, "manufacturer_id": mfr, "manufacturer": make}, **kw)


CASES = [
    # --- the reported failures -------------------------------------------------------------------------------
    ("119272 vs 119 272, wiper vs breather pipe", row("SWF 119272 SKODA Scala Hatchback windshield wipers", "119272"),
     row("TOPRAN 119 272 Rocker cover breather pipe", "119 272"), D, "different_brands"),
    ("K2 T104 vs STEINHOF T-104", row("K2 T104 Brake Fluid Capacity: 0,5l", "Article №: T104"),
     row("STEINHOF T-104 Towbar", "Article №: T-104"), D, "different_brands"),
    ("REMSA pads vs TOPRAN cable", row("REMSA 1187.02 Brake pad set incl. wear warning", "1187.02"),
     row("TOPRAN 118 702 Gear selector cable", "118 702"), D, "different_brands"),
    ("K2 twins", row("K2 T104 Brake Fluid Capacity: 0,5l", "Article №: T104"),
     row("K2 T104 Brake Fluid Capacity: 0,5l", "article №: t104"), S, "same_brand_same_number"),
    ("label spelling №: vs Article №:", row("MAXGEAR 70-0122 Gasket Set", "№: 70-0122"),
     row("MAXGEAR 70-0122 Gasket Set, cylinder head cover", "Article №: 70-0122"), S, "same_brand_same_number"),
    # --- same number, different brands (identical raw number is NOT proof) ----------------------------------
    ("same raw number, two brands", row("ERA 550348 Camshaft position sensor", "550348"),
     row("NRF 550348 Jaguar XE X760 engine radiator", "550348"), D, "different_brands"),
    ("METZGER / QUICK BRAKE are two brands, not an alias", row("METZGER 12053021 Brake pad fitting kit", "12053021"),
     row("QUICK BRAKE 12053021 Brake pad fitting kit", "12053021"), D, "different_brands"),
    ("COFLE / METZGER are two brands", row("COFLE S07048 Clutch cable", "S07048"), row("METZGER S07048 Clutch cable", "S07048"),
     D, "different_brands"),
    # --- punctuation is a candidate, never proof ---------------------------------------------------------------
    ("same brand, punctuation-only difference", row("BREMBO 08.D045.11 Brake disc", "08.D045.11"),
     row("BREMBO 08D04511 Brake disc", "08D04511"), U, "same_brand_punctuation_variant"),
    ("vehicle-first, same brand, space-only difference", row("Nissan Navara Platform Tappet FRECCIA PI06-0019", "PI06-0019"),
     row("Nissan X Trail t30 Tappet FRECCIA PI 06-0019", "PI 06-0019"), U, "same_brand_punctuation_variant"),
    # --- vehicle-first titles ---------------------------------------------------------------------------------
    ("vehicle-first vs brand-first, same brand + number", row("Ford Focus 4 Turnier Tappet FRECCIA PI 06-0019", "PI 06-0019"),
     row("FRECCIA PI 06-0019 Tappet Hydraulic", "PI 06-0019"), S, "same_brand_same_number"),
    ("vehicle-first, two brands", row("OPEL Corsa F Van Fuel filter febi bilstein 32399", "32399"),
     row("Mercedes E Class W124 Middle silencer POLMO 32399", "32399"), D, "different_brands"),
    ("vehicle word is not a brand (Transit Connect)", row("Ford Transit Connect 51234 brake hose", "51234"),
     row("Ford Tourneo Connect 51234 wheel hub", "51234"), U, "brand_unknown"),
    ("vehicle make is not a suffix brand", row("Brake disc for Suzuki 51234", "51234"), row("Wiper for Suzuki 51234", "51234"),
     U, "brand_unknown"),
    # --- unbranded / unknown brand ------------------------------------------------------------------------------
    ("unbranded titles", row("Heater Hose", "BD215/1"), row("Heater Hose", "BD215/1"), U, "brand_unknown"),
    ("brand after the number is not evidence", row("ADJUSTER, DRUM BRAKE 192030 MAX", "192030"),
     row("MAXGEAR 19-2030 Adjuster, drum brake", "19-2030"), U, "brand_unknown"),
    ("rare brand not in the vocabulary", row("Sinuslive 11205 Fuse", "11205"), row("NEO TOOLS 11-205 Socket", "11-205"), U, "brand_unknown"),
    ("Hebrew unbranded vs branded", row("צלחת בלם אחורי", "08D04511"), row("BREMBO 08D04511 Sprinter brake discs", "08D04511"),
     U, "brand_unknown"),
    ("Hebrew unbranded twins (aftermarket)", row("צינור בלם A75", "000000000060522545"), row("צינור בלם", "000000000060522545"),
     U, "brand_unknown"),
    # --- product type -------------------------------------------------------------------------------------------
    ("missing product type", row("K2 T104 Brake Fluid", "T104", pt=None), row("K2 T104 Brake Fluid", "T104"), U, "missing_product_type"),
    ("unknown product type label", row("K2 T104 Brake Fluid", "T104", pt="replacement"), row("K2 T104 Brake Fluid", "T104"),
     U, "unknown_product_type"),
    ("genuine vs aftermarket, same text", row("Connector", "T4A10791", pt="oem"), row("Connector", "T4A10791", pt="aftermarket"),
     U, "product_type_conflict"),
    ("OE-equivalent vs aftermarket (compatible)", row("BOSCH 0986494 Brake pad", "0986494", pt="oe_equivalent"),
     row("BOSCH 0986494 Brake pad", "0986494", pt="aftermarket"), U, "product_type_conflict"),
    ("genuine vs aftermarket AND two brands", row("VAG 8E5827505B Tailgate lock", "8E5827505B", pt="oem", make="Audi"),
     row("TOPRAN 8E5827505B Tailgate lock", "8E5827505B", pt="aftermarket", make="Audi"), D, "different_brands"),
    # --- genuine (OEM) numbers ----------------------------------------------------------------------------------
    ("OEM punctuation variant, Hebrew + English", row("מפריד שמן", "06Q103495H", pt="oem"),
     row("Vapor Canister Purge Valve Assembly", "06Q-103-495-H", pt="oem"), S, "same_manufacturer_oem_number"),
    ("OEM with the maker's own name in the title", row("PORSCHE 9J0807442OK1", "9J0807442 OK1", pt="oem", make="Porsche"),
     row("Tow Eye Cap", "9J0807442OK1", pt="original", make="Porsche"), S, "same_manufacturer_oem_number"),
    ("OEM rows of two manufacturers", row("Bolt", "12345-6789", pt="oem", mfr="m1"), row("Bolt", "123456789", pt="oem", mfr="m2"),
     U, "manufacturer_missing_or_different"),
    ("OEM rows, manufacturer missing", row("Bolt", "12345-6789", pt="oem", mfr=None), row("Bolt", "123456789", pt="oem"),
     U, "manufacturer_missing_or_different"),
    ("OEM-typed row carrying a foreign brand vs unbranded", row("METZGER 2420301 Radiator hose", "2420301", pt="oem", make="Ford"),
     row("GRILLE-BUMPER", "2420301", pt="oem", make="Ford"), U, "branded_vs_unbranded"),
    ("OEM-typed row with an unknown title prefix", row("Sinuslive 11205 Fuse", "11205", pt="oem"), row("Fuse", "11205", pt="oem"),
     U, "unrecognised_title_prefix"),
    ("verified alias inside a vehicle/position-first title", row("Front FERDINAND BILSTEIN 116361 Brake Pad Set, disc brake", "116361"),
     row("Chrysler Neon 2 Brake pad set febi bilstein 116361", "116361"), S, "same_brand_same_number"),
    # --- not even a candidate -----------------------------------------------------------------------------------
    ("different numbers", row("K2 T104 Brake Fluid", "T104"), row("K2 T105 Brake Fluid", "T105"), D, "different_number"),
    ("empty number", row("K2 Brake Fluid", ""), row("K2 Brake Fluid", ""), D, "different_number"),
]


@pytest.mark.parametrize("label,a,b,decision,reason", CASES, ids=[c[0] for c in CASES])
def test_identity_table(label, a, b, decision, reason):
    for x, y in ((a, b), (b, a)):                                   # the verdict is symmetric
        v = pi.classify(x, y)
        assert (v.decision, v.reason) == (decision, reason), label
    assert pi.distinct_articles(a, b) is (decision == D)


@pytest.mark.parametrize("a,b", [("119272", "119 272"), ("T104", "T-104"), ("1187.02", "118 702"), ("08.D045.11", "08D04511"),
                                 ("PI06-0019", "pi 06-0019"), ("Article №: 70-0122", "№: 700122")])
def test_normalisation_makes_candidates_but_never_authorises(a, b):
    assert pi.norm_number(a) == pi.norm_number(b)                    # they ARE candidates of one group
    for pt in ("aftermarket", "oe_equivalent", "remanufactured", "used"):
        for name_a, name_b in (("Part", "Part"), ("TOPRAN %s Part" % a, "SWF %s Part" % b), ("Opel Corsa part %s" % a, "Opel Corsa part %s" % b)):
            assert not pi.classify(row(name_a, a, pt=pt), row(name_b, b, pt=pt)).same


def test_mixed_group_is_classified_per_article():
    k2a, k2b = row("K2 T104 Brake Fluid", "Article №: T104", id="a"), row("K2 T104 Brake Fluid Capacity: 0,5l", "Article №: T104", id="b")
    tow = row("STEINHOF T-104 Towbar", "Article №: T-104", id="c")
    veh = row("Toyota Yaris towbar T104", "T104", id="d")
    clusters, held = pi.cluster([tow, k2a, veh, k2b])
    assert sorted(sorted(r["id"] for r in cl) for cl in clusters) == [["a", "b"], ["c"], ["d"]]
    assert held == {"different": 1, "uncertain": 1}
    ev = {e["id"]: e for e in pi.evidence([tow, k2a, veh, k2b])}
    assert ev["a"]["cluster"] == ev["b"]["cluster"] != ev["c"]["cluster"] and ev["d"]["vs_other_clusters"] == ["brand_unknown"]


def test_cluster_needs_same_with_every_member_and_is_idempotent():
    a, b = row("K2 T104 Brake Fluid", "T104", id="a"), row("K2 T104 Brake Fluid", "T104", id="b")
    c = row("K2 T104 Brake Fluid", "T104", pt="oe_equivalent", id="c")          # same text, other product class
    first = pi.cluster([a, b, c])
    assert [len(cl) for cl in first[0]] == [2, 1] and first == pi.cluster([a, b, c])
    assert [len(cl) for cl in pi.cluster([a])[0]] == [1] and pi.cluster([])[0] == []


def test_brand_alias_needs_written_evidence(tmp_path):
    f = tmp_path / "aliases.json"
    f.write_text(json.dumps({"aliases": [
        {"alias": "LEMFOERDER", "brand": "LEMFÖRDER", "evidence": "manufacturer catalogue: one company, two spellings of its name"},
        {"alias": "QUICK BRAKE", "brand": "METZGER", "evidence": ""},                # no evidence -> ignored
        {"alias": "COFLE", "brand": "METZGER"}]}), encoding="utf-8")                 # no evidence -> ignored
    assert pi.load_aliases(f) == {"LEMFOERDER": "LEMFÖRDER"}
    assert pi.load_aliases(tmp_path / "missing.json") == {}
    a, b = row("LEMFOERDER 12345 01 Ball joint", "12345 01"), row("LEMFÖRDER 12345 01 Ball joint", "12345 01")
    try:
        pi.configure(brands={"LEMFÖRDER": 500, "METZGER": 500, "QUICK BRAKE": 500}, aliases={})
        assert pi.classify(a, b).decision == U                                       # unverified spelling: not the same brand
        pi.configure(brands={"LEMFÖRDER": 500, "METZGER": 500, "QUICK BRAKE": 500}, aliases=pi.load_aliases(f))
        assert pi.classify(a, b).same                                                # verified alias
        assert pi.classify(row("QUICK BRAKE 109-1757 kit", "109-1757"), row("METZGER 109-1757 kit", "109-1757")).decision == D
    finally:
        pi.configure()


def test_shipped_alias_file_is_valid_and_never_aliases_the_reviewed_pairs():
    doc = json.loads(pi.ALIASES_FILE.read_text(encoding="utf-8"))
    assert all(len(str(e.get("evidence") or "")) >= 20 for e in doc["aliases"])
    al = pi.load_aliases()
    for x, y in (("METZGER", "QUICK BRAKE"), ("COFLE", "METZGER")):
        assert al.get(x) != y and al.get(y) != x


def test_missing_brand_file_fails_closed(tmp_path):
    try:
        pi.configure(brands=pi.load_brands(tmp_path / "nope.json"), aliases={})
        assert pi.load_brands(tmp_path / "nope.json") == {}
        v = pi.classify(row("K2 T104 Brake Fluid", "T104"), row("K2 T104 Brake Fluid", "T104"))
        assert v.decision == U                                                        # nothing is a known brand -> no merge
        assert pi.classify(row("מפריד שמן", "06Q103495H", pt="oem"), row("Oil separator", "06Q-103-495-H", pt="oem")).same
    finally:
        pi.configure()


def test_shipped_vocabulary_is_sane():
    brands = pi.load_brands()
    assert len(brands) > 300 and min(brands.values()) >= pi.MIN_BRAND_ROWS
    for b in ("TOPRAN", "FEBI BILSTEIN", "K2", "STEINHOF", "REMSA", "SWF", "FRECCIA", "METZGER", "QUICK BRAKE", "COFLE"):
        assert b in brands
    for junk in ("CAR", "MAX", "LTD", "2X BRAKE DISC"):
        assert junk not in brands
    assert all(len(b.split()) <= 3 and "(" not in b for b in brands)


def test_every_merge_capable_script_asks_the_one_policy():
    """A write path that merges / deactivates duplicates must call part_identity; none may keep its own identity rule."""
    for rel, needle in (("maintenance/merge_master_parts.py", "part_identity.cluster("),
                        ("maintenance/merge_master_parts.py", "part_identity.classify(canon, l)"),
                        ("maintenance/bmw_oem_dedup.py", "part_identity.classify("),
                        ("maintenance/pipeline_parity_check.py", "part_identity.cluster("),
                        ("maintenance/unmerge_distinct_articles.py", "part_identity.distinct_articles(")):
        assert needle in (ROOT / rel).read_text(encoding="utf-8"), (rel, needle)
    writers = []
    for p in ROOT.rglob("*.py"):
        parts = p.relative_to(ROOT).parts
        if parts[0] in ("tests", "devtests", "legacy", "alembic", "state", "data") or "__pycache__" in parts:
            continue
        if "dedup_merged_into" in p.read_text(encoding="utf-8", errors="ignore"):
            writers.append(str(p.relative_to(ROOT)))
    # every file that knows the merge marker is one of the reviewed ones (a new merge path must be added here AND use the policy)
    assert sorted(writers) == sorted(["job_queue.py", "maintenance/bmw_oem_dedup.py", "maintenance/merge_master_parts.py",
                                      "maintenance/pipeline_parity_check.py", "maintenance/restore_merged_offers_133.py",
                                      "maintenance/unmerge_distinct_articles.py"]), writers
    assert "distinct_articles" not in (ROOT / "maintenance/merge_master_parts.py").read_text(encoding="utf-8")
