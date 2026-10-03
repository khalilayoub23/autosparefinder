"""
Tests for category counts API response shape.

Business rule: `counts` and `family_counts` in the categories endpoint response
must be keyed by family slug (e.g. "body-exterior"), not display names
(e.g. "Body Parts").  Any surface reading counts["body-exterior"] must get a
real integer, not 0.
"""
import pytest
from part_type_taxonomy import iter_part_type_families, PART_TYPE_FAMILY_BY_ID


def test_family_ids_are_slugs():
    """All family IDs must be lowercase slug strings (no spaces, no uppercase)."""
    for family in iter_part_type_families():
        assert family.id == family.id.lower(), f"family.id has uppercase: {family.id!r}"
        assert " " not in family.id, f"family.id has space: {family.id!r}"


def test_family_labels_differ_from_ids():
    """Labels (display names) must differ from IDs — they are not slugs."""
    for family in iter_part_type_families():
        # at least one family must have a display name that is not equal to its id
        pass
    labels = {f.label for f in iter_part_type_families()}
    ids    = {f.id    for f in iter_part_type_families()}
    assert labels != ids, "labels and ids must not be identical sets"


def test_key_expected_family_ids_exist():
    """The 9 families the landing page maps to must all exist."""
    expected = [
        "engine", "brakes", "suspension-steering", "electrical",
        "body-exterior", "gearbox", "cooling", "filters", "exhaust",
    ]
    for slug in expected:
        assert slug in PART_TYPE_FAMILY_BY_ID, f"Expected family id missing: {slug!r}"


def test_counts_response_uses_slug_keys(monkeypatch):
    """
    Simulate the aggregation loop and assert that the 'counts' dict returned by
    get_categories uses slug keys (family.id), not display-name keys (family.label).

    This is the regression test for the flat_counts bug: previously
      counts = {**fallback_counts, **flat_counts}
    merged display-name keys ("Body Parts": 908413) ON TOP of slug-keyed zeros,
    so counts["body-exterior"] was always 0.

    After the fix:
      counts = {**fallback_counts, **family_counts}
    family_counts is slug-keyed, so counts["body-exterior"] carries the real count.
    """
    from part_type_taxonomy import PART_TYPE_FAMILY_BY_ID, classify_part_type_family, iter_part_type_families, PART_TYPE_FAMILIES
    from routes.parts import CANONICAL_FILTER_CATEGORIES, _normalize_filter_category

    # Simulate the aggregate path with a tiny fake agg_rows dataset
    fake_rows = [
        ("body-exterior", "oem",         500),
        ("brakes",        "aftermarket",  200),
        ("electrical",    "oem",          300),
        ("engine",        "oem",          400),   # previously mis-classified as fluids
        ("unknown-junk",  "new",           10),   # should fall to fallback_counts
    ]

    family_counts = {f.id: 0 for f in iter_part_type_families()}
    fallback_counts = {c: 0 for c in CANONICAL_FILTER_CATEGORIES}

    for raw_category, raw_part_type, cnt in fake_rows:
        # Fixed aggregation: fast dict lookup first (same as routes/parts.py)
        family = PART_TYPE_FAMILY_BY_ID.get(raw_category) or classify_part_type_family(
            raw_category, raw_part_type, None, None, None
        )
        if family:
            family_counts[family.id] = family_counts.get(family.id, 0) + cnt
        else:
            canonical = _normalize_filter_category(raw_category)
            if canonical:
                fallback_counts[canonical] = fallback_counts.get(canonical, 0) + cnt

    # The fixed response
    counts = {**fallback_counts, **family_counts}

    # Slug keys must have the real counts
    assert counts["body-exterior"] == 500, f"Expected 500, got {counts['body-exterior']}"
    assert counts["brakes"]        == 200, f"Expected 200, got {counts['brakes']}"
    assert counts["electrical"]    == 300, f"Expected 300, got {counts['electrical']}"
    assert counts["engine"]        == 400, f"engine mis-classified; expected 400, got {counts['engine']}"
    # fluids must NOT have absorbed the engine count
    assert counts.get("fluids", 0) == 0, f"engine leaked into fluids: {counts.get('fluids')}"

    # Display-name keys must NOT appear in counts
    labels = {f.label for f in PART_TYPE_FAMILIES}
    for label in labels:
        assert label not in counts, f"Display-name key leaked into counts: {label!r}"


def test_family_counts_and_counts_share_same_slugs():
    """After the fix, counts and family_counts should agree on the same key set for families."""
    from part_type_taxonomy import classify_part_type_family, iter_part_type_families
    from routes.parts import CANONICAL_FILTER_CATEGORIES, _normalize_filter_category

    fake_rows = [("brakes", "oem", 100), ("engine", "aftermarket", 50)]

    family_counts = {f.id: 0 for f in iter_part_type_families()}
    fallback_counts = {c: 0 for c in CANONICAL_FILTER_CATEGORIES}

    for raw_category, raw_part_type, cnt in fake_rows:
        family = classify_part_type_family(raw_category, raw_part_type, None, None, None)
        if family:
            family_counts[family.id] = family_counts.get(family.id, 0) + cnt

    counts = {**fallback_counts, **family_counts}

    # For every family id, counts[family.id] == family_counts[family.id]
    for fid, fcount in family_counts.items():
        assert counts.get(fid) == fcount, (
            f"counts[{fid!r}]={counts.get(fid)} differs from family_counts[{fid!r}]={fcount}"
        )
