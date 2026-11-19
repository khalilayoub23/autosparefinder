from price_sync_report import price_sync_breakdown


def test_zero_checked_says_no_eligible_work():
    out = price_sync_breakdown({"parts_checked": 0, "errors": []}, {"status": "disabled"})
    assert "no eligible work found" in out and "aliexpress: disabled" in out


def test_checked_but_unchanged_is_explained():
    out = price_sync_breakdown({"parts_checked": 4400, "parts_updated": 0, "parts_not_found": 4300,
                                "blocked_non_il_shipping": 100, "errors": []}, None)
    assert "checked=4400 updated=0 no_listing=4300 blocked_non_il=100" in out
    assert "work found but nothing changed" in out


def test_errors_without_work_is_failure_not_empty():
    out = price_sync_breakdown({"parts_checked": 0, "errors": ["x"]}, {})
    assert "no work processed (errors)" in out


def test_updated():
    assert "[updated]" in price_sync_breakdown({"parts_checked": 5, "parts_updated": 2}, {"parts_checked": 1})


def test_guard_verdicts_quota_and_budget_are_in_the_breakdown():
    from price_sync_report import price_sync_breakdown
    line = price_sync_breakdown({"parts_checked": 3, "parts_updated": 1, "refresh_verdicts": {"exact_match": 1, "missing_listing": 2},
                                 "candidate_verdicts": {"product_mismatch": 4}, "calls_used": 7, "call_budget": 4500,
                                 "ebay_quota_remaining_at_start": 4800}, None)
    assert "refresh[exact_match=1,missing_listing=2]" in line and "discovery[product_mismatch=4]" in line
    assert "calls=7/4500" in line and "quota_at_start=4800" in line
