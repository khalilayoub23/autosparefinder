"""Dimension policy (block/allow/manual_review) and weight ceiling policy."""
from services.shipping.eurosender_dimensions import (
    DimensionVerdict,
    classify_category,
    validate_dimensions,
)
from services.shipping.eurosender_weight import (
    WeightVerdict,
    aggregate_weight_kg,
    check_weight_ceiling,
)


def test_body_exterior_is_blocked():
    d = classify_category("body-exterior")
    assert d.verdict == DimensionVerdict.BLOCK
    assert d.estimate is None


def test_engine_is_blocked():
    assert classify_category("engine").verdict == DimensionVerdict.BLOCK


def test_gearbox_is_blocked():
    assert classify_category("gearbox").verdict == DimensionVerdict.BLOCK


def test_exhaust_is_blocked():
    assert classify_category("exhaust").verdict == DimensionVerdict.BLOCK


def test_brakes_is_allowed_with_estimate_flagged():
    d = classify_category("brakes")
    assert d.verdict == DimensionVerdict.ALLOW
    assert d.estimate is not None
    assert d.estimate.is_estimate is True
    assert d.estimate.length_cm > 0 and d.estimate.width_cm > 0 and d.estimate.height_cm > 0


def test_filters_is_allowed():
    assert classify_category("filters").verdict == DimensionVerdict.ALLOW


def test_lighting_is_manual_review_not_auto_allow():
    d = classify_category("lighting")
    assert d.verdict == DimensionVerdict.MANUAL_REVIEW
    assert d.estimate is None


def test_general_catchall_is_manual_review():
    assert classify_category("כללי").verdict == DimensionVerdict.MANUAL_REVIEW
    assert classify_category("general").verdict == DimensionVerdict.MANUAL_REVIEW


def test_unknown_category_defaults_to_manual_review_never_auto_allow():
    d = classify_category("some-totally-unmapped-category-xyz")
    assert d.verdict == DimensionVerdict.MANUAL_REVIEW


def test_category_matching_is_case_insensitive_for_block_and_allow():
    assert classify_category("BRAKES").verdict == DimensionVerdict.ALLOW
    assert classify_category("Engine").verdict == DimensionVerdict.BLOCK


# ---------------------------------------------------------------------------
# Weight policy
# ---------------------------------------------------------------------------

def test_aggregate_weight_uses_sendcloud_default_map():
    # brakes default is 1.5kg in sendcloud_shipping_sync._DEFAULT_WEIGHT_KG
    w = aggregate_weight_kg([{"category": "brakes", "quantity": 2}])
    assert w == 3.0


def test_aggregate_weight_multiple_items():
    w = aggregate_weight_kg([
        {"category": "brakes", "quantity": 1},   # 1.5
        {"category": "filters", "quantity": 2},  # 0.3 * 2 = 0.6
    ])
    assert abs(w - 2.1) < 0.001


def test_normal_order_within_ceiling_allowed():
    decision = check_weight_ceiling(5.0, ceiling_kg=25)
    assert decision.verdict == WeightVerdict.ALLOW


def test_over_25kg_blocks_and_flags_manual_review():
    decision = check_weight_ceiling(30.0, ceiling_kg=25)
    assert decision.verdict == WeightVerdict.BLOCK_MANUAL_REVIEW
    # Declared weight must NOT be altered to fit — the decision reports the
    # true 30.0kg, never a silently-capped 25.0kg.
    assert decision.weight_kg == 30.0


def test_weight_ceiling_uses_env_default_when_not_given(monkeypatch):
    monkeypatch.setenv("EUROSENDER_MAX_WEIGHT_KG", "10")
    decision = check_weight_ceiling(12.0)
    assert decision.verdict == WeightVerdict.BLOCK_MANUAL_REVIEW
    assert decision.ceiling_kg == 10.0


def test_unrecognized_category_uses_fallback_weight():
    # sendcloud_shipping_sync._DEFAULT_WEIGHT_FALLBACK = 1.0
    w = aggregate_weight_kg([{"category": "not-a-real-category", "quantity": 1}])
    assert w == 1.0


# ---------------------------------------------------------------------------
# Phase 18 hardening: application-side dimension validation. Live Sandbox
# verification (2026-09-11, Phase 17) proved the Eurosender API ACCEPTS a
# zero length/width/height without rejection — these tests prove the
# application does not rely on the API to catch that.
# ---------------------------------------------------------------------------

def test_validate_dimensions_valid_passes():
    v = validate_dimensions(20, 15, 10)
    assert v.valid is True


def test_validate_dimensions_zero_length_blocked():
    v = validate_dimensions(0, 15, 10)
    assert v.valid is False
    assert "length_cm" in v.reason


def test_validate_dimensions_zero_width_blocked():
    v = validate_dimensions(20, 0, 10)
    assert v.valid is False
    assert "width_cm" in v.reason


def test_validate_dimensions_zero_height_blocked():
    v = validate_dimensions(20, 15, 0)
    assert v.valid is False
    assert "height_cm" in v.reason


def test_validate_dimensions_negative_blocked():
    v = validate_dimensions(20, -5, 10)
    assert v.valid is False


def test_validate_dimensions_missing_blocked():
    v = validate_dimensions(20, None, 10)
    assert v.valid is False
    assert "missing" in v.reason.lower()


def test_validate_dimensions_non_numeric_blocked():
    v = validate_dimensions(20, "abc", 10)
    assert v.valid is False


# ---------------------------------------------------------------------------
# Phase 18 hardening: application-side weight validation extended onto the
# existing single enforcement point (check_weight_ceiling). Live Sandbox
# verification proved weight=0 is ACCEPTED by the API without rejection.
# ---------------------------------------------------------------------------

def test_weight_zero_blocked():
    decision = check_weight_ceiling(0)
    assert decision.verdict == WeightVerdict.BLOCK_MANUAL_REVIEW


def test_weight_negative_blocked():
    decision = check_weight_ceiling(-5)
    assert decision.verdict == WeightVerdict.BLOCK_MANUAL_REVIEW


def test_weight_missing_blocked():
    decision = check_weight_ceiling(None)
    assert decision.verdict == WeightVerdict.BLOCK_MANUAL_REVIEW


def test_weight_exactly_25kg_allowed():
    decision = check_weight_ceiling(25.0, ceiling_kg=25)
    assert decision.verdict == WeightVerdict.ALLOW


def test_weight_25_01kg_blocked():
    decision = check_weight_ceiling(25.01, ceiling_kg=25)
    assert decision.verdict == WeightVerdict.BLOCK_MANUAL_REVIEW


def test_weight_normal_value_allowed():
    decision = check_weight_ceiling(5.0, ceiling_kg=25)
    assert decision.verdict == WeightVerdict.ALLOW
