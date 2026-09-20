"""Timeout/idempotency state machine — no API idempotency key exists, so
customerInternalReference + webhook reconciliation is the only safe pattern.
The one hard invariant: a second POST /v1/orders must never be attempted
from timeout_pending_reconciliation."""
from services.shipping.eurosender_timeout import (
    EurosenderStatus,
    can_attempt_creation,
    reconcile,
)


def test_never_attempted_can_create():
    assert can_attempt_creation(None) is True


def test_timeout_pending_cannot_retry():
    assert can_attempt_creation(EurosenderStatus.TIMEOUT_PENDING_RECONCILIATION.value) is False


def test_created_cannot_retry():
    # already succeeded — a second POST would create a duplicate shipment
    assert can_attempt_creation(EurosenderStatus.CREATED.value) is False


def test_failed_can_retry_after_human_review():
    assert can_attempt_creation(EurosenderStatus.FAILED.value) is True


def test_cancelled_cannot_auto_retry():
    assert can_attempt_creation(EurosenderStatus.CANCELLED.value) is False


def test_unrecognized_legacy_value_treated_as_never_attempted():
    assert can_attempt_creation("some-legacy-value") is True


def test_reconcile_matches_pending_order():
    result = reconcile(
        customer_internal_reference="order-1",
        pending_order_ids={"order-1", "order-2"},
        get_order_response={"orderCode": "ES-100", "customerInternalReference": "order-1"},
    )
    assert result.matched is True
    assert result.order_code == "ES-100"


def test_reconcile_does_not_match_unrelated_order():
    result = reconcile(
        customer_internal_reference="order-99",
        pending_order_ids={"order-1", "order-2"},
        get_order_response={"orderCode": "ES-100", "customerInternalReference": "order-99"},
    )
    assert result.matched is False


def test_reconcile_handles_missing_reference():
    result = reconcile(
        customer_internal_reference="",
        pending_order_ids={"order-1"},
        get_order_response={"orderCode": "ES-100"},
    )
    assert result.matched is False
    assert "no customerInternalReference" in result.reason
