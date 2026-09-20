"""Content-length hardening (Phase 18) — live Sandbox verification
(2026-09-11, Phase 17) bisected the exact Eurosender content-field boundary
to 17 characters. This module never invents a truncation/abbreviation
scheme; it only falls back item_content -> category -> manual_review."""
from services.shipping.eurosender_content import (
    EUROSENDER_CONTENT_MAX_LEN,
    resolve_package_content,
)


def test_verified_boundary_constant_is_17():
    assert EUROSENDER_CONTENT_MAX_LEN == 17


def test_short_item_content_used_verbatim():
    r = resolve_package_content("brake pads", "brakes")
    assert r.ok is True
    assert r.content == "brake pads"


def test_item_content_exactly_17_chars_fits():
    content = "1234567890123456X"[:17]
    assert len(content) == 17
    r = resolve_package_content(content, "brakes")
    assert r.ok is True
    assert r.content == content


def test_item_content_18_chars_falls_back_to_category():
    long_content = "x" * 18
    r = resolve_package_content(long_content, "brakes")
    assert r.ok is True
    assert r.content == "brakes"  # short category slug used instead


def test_long_content_and_long_category_both_fail_goes_manual_review():
    long_content = "Automotive Brake Component Extended Description"
    r = resolve_package_content(long_content, "suspension-steering")  # 20 chars, itself too long
    assert r.ok is False
    assert r.content is None
    assert "business decision" in r.reason


def test_no_item_content_falls_back_to_short_category():
    r = resolve_package_content(None, "filters")
    assert r.ok is True
    assert r.content == "filters"


def test_empty_string_content_falls_back_to_category():
    r = resolve_package_content("   ", "filters")
    assert r.ok is True
    assert r.content == "filters"


def test_both_none_goes_manual_review():
    r = resolve_package_content(None, None)
    assert r.ok is False


def test_unicode_hebrew_content_no_crash():
    r = resolve_package_content("רפידות בלם", "brakes")  # 10 chars
    assert r.ok is True
    assert r.content == "רפידות בלם"


def test_unicode_hebrew_content_too_long_falls_back():
    long_hebrew = "רפידות בלם קדמיות מקוריות למכונית"  # well over 17 chars
    r = resolve_package_content(long_hebrew, "brakes")
    assert r.ok is True
    assert r.content == "brakes"


def test_deterministic_same_input_same_output():
    a = resolve_package_content("brake pads", "brakes")
    b = resolve_package_content("brake pads", "brakes")
    assert a == b


def test_never_returns_empty_content_on_success():
    r = resolve_package_content("x", "brakes")
    assert r.ok is True
    assert r.content  # truthy, non-empty
