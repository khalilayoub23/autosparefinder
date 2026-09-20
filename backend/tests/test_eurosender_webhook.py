"""Eurosender webhook event handling (routes/eurosender_webhook.py).

Tests call handle_event() directly with a fake AsyncSession — this mirrors
the existing test-double pattern in tests/test_payments_supplier_helpers.py
(_FakeAsyncDB) rather than spinning up a live DB/FastAPI TestClient, since
these are pure event-mapping unit tests.
"""
from types import SimpleNamespace

import pytest

from routes.eurosender_webhook import KNOWN_EVENTS, handle_event


class _ScalarOneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ScalarsAllResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeDB:
    """Consumes pre-queued results in the exact order handle_event() will
    call db.execute(). Records everything added via db.add()."""

    def __init__(self, results: list):
        self._results = list(results)
        self.added = []
        self.flushed = False
        self.committed = False

    async def execute(self, _query):
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


def _order(**overrides):
    defaults = dict(
        id="order-uuid-1", order_number="AUTO-001", user_id="user-1",
        status="paid", eurosender_status="created", eurosender_order_code="ES-100",
        shipping_provider="eurosender", tracking_number=None, tracking_url=None,
        shipped_at=None, delivered_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_known_events_match_the_verified_contract():
    assert KNOWN_EVENTS == {
        "order_label_ready",
        "order_submitted_to_courier",
        "order_tracking_ready",
        "order_cancelled",
        "delivery_status_updated",
    }


async def test_order_label_ready_sets_status():
    order = _order(eurosender_status="created")
    db = _FakeDB([_ScalarOneResult(order)])
    await handle_event("order_label_ready", {"triggerId": "t1", "orderCode": "ES-100"}, db)
    assert order.eurosender_status == "label_ready"


async def test_order_label_ready_unknown_order_code_is_noop():
    db = _FakeDB([_ScalarOneResult(None)])
    # Must not raise even though no order matches.
    await handle_event("order_label_ready", {"triggerId": "t1", "orderCode": "UNKNOWN"}, db)
    assert db.added == []


async def test_order_submitted_to_courier_advances_status():
    order = _order(status="paid")
    db = _FakeDB([_ScalarOneResult(order)])
    await handle_event("order_submitted_to_courier", {"triggerId": "t1", "orderCode": "ES-100", "courierId": 66}, db)
    assert order.status == "supplier_ordered"


async def test_order_submitted_to_courier_never_regresses_delivered_order():
    order = _order(status="delivered")
    db = _FakeDB([_ScalarOneResult(order)])
    await handle_event("order_submitted_to_courier", {"orderCode": "ES-100", "courierId": 66}, db)
    assert order.status == "delivered"  # unchanged


async def test_order_tracking_ready_sets_real_tracking_and_notifies():
    order = _order(status="paid")
    db = _FakeDB([_ScalarOneResult(order), _ScalarsAllResult([])])
    payload = {
        "orderCode": "ES-100",
        "trackingCodes": [{"orderCode": "ES-100", "trackingNumber": "1Z999", "trackingUrl": "https://track/1Z999"}],
    }
    await handle_event("order_tracking_ready", payload, db)
    assert order.tracking_number == "1Z999"
    assert order.tracking_url == "https://track/1Z999"
    assert order.status == "supplier_ordered"
    # A real Notification must have been created — this is the ONLY point a
    # Eurosender customer gets a tracking notification (Phase 13).
    assert len(db.added) == 1
    assert "1Z999" in db.added[0].message


async def test_order_tracking_ready_uses_last_leg_for_multi_carrier():
    order = _order()
    db = _FakeDB([_ScalarOneResult(order), _ScalarsAllResult([])])
    payload = {
        "orderCode": "ES-100",
        "trackingCodes": [
            {"trackingNumber": "FIRST-LEG", "trackingUrl": "https://a"},
            {"trackingNumber": "FINAL-LEG", "trackingUrl": "https://b"},
        ],
    }
    await handle_event("order_tracking_ready", payload, db)
    assert order.tracking_number == "FINAL-LEG"


async def test_order_tracking_ready_empty_codes_does_not_notify():
    order = _order()
    db = _FakeDB([_ScalarOneResult(order)])
    await handle_event("order_tracking_ready", {"orderCode": "ES-100", "trackingCodes": []}, db)
    assert order.tracking_number is None
    assert db.added == []


async def test_order_tracking_ready_updates_supplier_payment_rows():
    order = _order()
    sp = SimpleNamespace(shipping_provider_ref="ES-100", tracking_number=None, tracking_url=None, status="paid")
    db = _FakeDB([_ScalarOneResult(order), _ScalarsAllResult([sp])])
    payload = {"orderCode": "ES-100", "trackingCodes": [{"trackingNumber": "1Z1", "trackingUrl": "https://x"}]}
    await handle_event("order_tracking_ready", payload, db)
    assert sp.tracking_number == "1Z1"
    assert sp.status == "tracking_received"


async def test_order_cancelled_sets_status_and_alerts_admins():
    order = _order()
    admin = SimpleNamespace(id="admin-1")
    db = _FakeDB([_ScalarOneResult(order), _ScalarsAllResult([admin])])
    await handle_event("order_cancelled", {"triggerId": "t1", "orderCode": "ES-100"}, db)
    assert order.eurosender_status == "cancelled"
    assert db.committed is True
    # Never auto-refunds — only alerts for manual review.
    assert any(n.data.get("needs_manual_review") for n in db.added)


async def test_delivery_status_updated_delivered_maps_correctly():
    order = _order(status="shipped")
    db = _FakeDB([_ScalarOneResult(order)])
    payload = {
        "notifications": [{
            "orderCode": "ES-100",
            "trackingDetails": {"parcels": [{"orderCode": "ES-100", "currentStatus": "Delivered"}]},
        }]
    }
    await handle_event("delivery_status_updated", payload, db)
    assert order.status == "delivered"
    assert order.delivered_at is not None


async def test_delivery_status_updated_in_transit_maps_to_shipped():
    order = _order(status="supplier_ordered")
    db = _FakeDB([_ScalarOneResult(order)])
    payload = {
        "notifications": [{
            "trackingDetails": {"parcels": [{"orderCode": "ES-100", "currentStatus": "InTransit"}]},
        }]
    }
    await handle_event("delivery_status_updated", payload, db)
    assert order.status == "shipped"


async def test_delivery_status_updated_never_regresses_delivered():
    order = _order(status="delivered")
    db = _FakeDB([_ScalarOneResult(order)])
    payload = {
        "notifications": [{
            "trackingDetails": {"parcels": [{"orderCode": "ES-100", "currentStatus": "InTransit"}]},
        }]
    }
    await handle_event("delivery_status_updated", payload, db)
    assert order.status == "delivered"


# ---------------------------------------------------------------------------
# Real-payload regression: PROVEN 2026-09-12 against a genuine live Eurosender
# Sandbox delivery (order 408540-26, event order_cancelled). The real body was
# {"notifications": [{"orderCode": "408540-26", "triggerId": 4}]} — NOT the
# flat {triggerId, orderCode} shape the public docs show. This is the exact
# defect that caused "order_cancelled for unknown orderCode=None" in
# production before the fix.
# ---------------------------------------------------------------------------

def test_normalize_notification_payload_unwraps_single_notification():
    from routes.eurosender_webhook import _normalize_notification_payload
    real_payload = {"notifications": [{"orderCode": "408540-26", "triggerId": 4}]}
    normalized = _normalize_notification_payload(real_payload)
    assert normalized == {"orderCode": "408540-26", "triggerId": 4}


def test_normalize_notification_payload_prefers_top_level_order_code():
    """If a flat shape genuinely occurs for some event, top-level orderCode
    must win — never silently switch to the wrapped shape when the
    documented flat shape is actually present."""
    from routes.eurosender_webhook import _normalize_notification_payload
    flat_payload = {"orderCode": "FLAT-CODE", "triggerId": 1}
    normalized = _normalize_notification_payload(flat_payload)
    assert normalized == flat_payload


def test_normalize_notification_payload_handles_missing_notifications_safely():
    from routes.eurosender_webhook import _normalize_notification_payload
    empty_payload = {}
    normalized = _normalize_notification_payload(empty_payload)
    assert normalized == {}


async def test_order_cancelled_real_wrapped_payload_extracts_order_code():
    """Reproduces the exact real Sandbox payload shape — proves the fix,
    not just the helper in isolation."""
    order = _order(eurosender_status="created")
    admin = SimpleNamespace(id="admin-1")
    db = _FakeDB([_ScalarOneResult(order), _ScalarsAllResult([admin])])
    real_payload = {"notifications": [{"orderCode": "ES-100", "triggerId": 4}]}
    await handle_event("order_cancelled", real_payload, db)
    assert order.eurosender_status == "cancelled"


async def test_order_submitted_to_courier_wrapped_payload_extracts_order_code():
    order = _order(status="paid")
    db = _FakeDB([_ScalarOneResult(order)])
    real_shaped_payload = {"notifications": [{"orderCode": "ES-100", "triggerId": 2, "courierId": 66}]}
    await handle_event("order_submitted_to_courier", real_shaped_payload, db)
    assert order.status == "supplier_ordered"


async def test_delivery_status_updated_unaffected_by_normalization():
    """The multi-notification delivery_status_updated shape must be
    completely untouched by the single-notification unwrap fix — this is
    the regression this fix must never reintroduce."""
    order = _order(status="supplier_ordered")
    db = _FakeDB([_ScalarOneResult(order)])
    payload = {
        "notifications": [{
            "orderCode": "ES-100", "triggerId": 5,
            "trackingDetails": {"parcels": [{"orderCode": "ES-100", "currentStatus": "InTransit"}]},
        }]
    }
    await handle_event("delivery_status_updated", payload, db)
    assert order.status == "shipped"
