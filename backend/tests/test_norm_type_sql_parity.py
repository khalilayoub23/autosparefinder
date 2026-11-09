"""norm_type_sql (SQL) must resolve every label exactly as normalize_type (Python) does - Hebrew included."""
import offer_classification as oc
import variant_identity as vi


def test_every_known_label_appears_in_the_sql_branch_of_its_class():
    sql = oc.norm_type_sql("x")
    for key in ("oem", "oe_equivalent", "aftermarket", "remanufactured", "used"):
        branch = f"THEN '{key}'"
        assert branch in sql
        for lab in vi.sql_labels(key):
            assert "'" + lab.replace("'", "''") + "'" in sql, (key, lab)


def test_hebrew_aftermarket_is_not_left_as_raw_text():
    sql = oc.norm_type_sql("x")
    assert "'חליפי'" in sql and oc.normalize_type("חליפי") == "aftermarket"
    assert "'מקורי'" in sql and oc.normalize_type("מקורי") == "oem"


def test_hebrew_and_english_resolve_to_same_class():
    assert oc.normalize_type("מקורי") == oc.normalize_type("Original") == oc.normalize_type("genuine") == "oem"
    assert oc.normalize_type("חליפי") == oc.normalize_type("Aftermarket") == "aftermarket"


def test_distinct_variants_not_collapsed():
    cls = {oc.normalize_type(x) for x in ("oem", "oe equivalent", "aftermarket", "remanufactured", "used")}
    assert len(cls) == 5
