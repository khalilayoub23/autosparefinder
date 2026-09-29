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


# ---------------------------------------------------------------------------
# Signature verification — official Eurosender contract (support answer,
# 2026-09-25, SUPERSEDES the 2026-09-21 "raw body only" contract):
#   message = webhook_event + webhook_id + raw_body   (plain concatenation,
#             no delimiter, no JSON parsing/reserialization)
#   HMAC-SHA256(sandbox_secret_utf8, message), header `sha256=<hex>`.
# Vectors use a synthetic secret; nothing here is a real credential.
# ---------------------------------------------------------------------------
import hashlib
import hmac as _hmac

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import eurosender_webhook as _wh

_SECRET = "unit-test-secret-not-real"
_EVENT = "order_cancelled"
_WID = "10790"
_BODY = b'{"notifications":[{"orderCode":"ES-100","triggerId":4}]}'


def _msg(event=_EVENT, wid=_WID, body=_BODY):
    return event.encode() + wid.encode() + body


def _sig(event=_EVENT, wid=_WID, body=_BODY, secret=_SECRET, prefix="sha256="):
    return prefix + _hmac.new(secret.encode(), _msg(event, wid, body), hashlib.sha256).hexdigest()


# --- Phase 8 matrix -----------------------------------------------------

def test_A_correct_event_id_body_secret_passes():
    assert _wh.verify_signature(_EVENT, _WID, _BODY, _sig(), _SECRET) is True


def test_B_changed_event_fails():
    assert _wh.verify_signature("order_label_ready", _WID, _BODY, _sig(), _SECRET) is False


def test_C_changed_id_fails():
    assert _wh.verify_signature(_EVENT, "99999", _BODY, _sig(), _SECRET) is False


def test_D_one_byte_changed_in_body_fails():
    tampered = _BODY[:-1] + bytes([_BODY[-1] ^ 1])
    assert tampered != _BODY
    assert _wh.verify_signature(_EVENT, _WID, tampered, _sig(), _SECRET) is False


def test_E_changed_signature_fails():
    assert _wh.verify_signature(_EVENT, _WID, _BODY, "sha256=" + "0" * 64, _SECRET) is False


def test_F_empty_body_deterministic():
    # An empty body is still a well-defined message (event+id+b"") — never an
    # exception, never a silent True.
    sig = _sig(body=b"")
    assert _wh.verify_signature(_EVENT, _WID, b"", sig, _SECRET) is True
    assert _wh.verify_signature(_EVENT, _WID, b"", "sha256=" + "0" * 64, _SECRET) is False


def test_G_missing_headers_rejected():
    assert _wh.verify_signature("", _WID, _BODY, _sig(), _SECRET) is False
    assert _wh.verify_signature(_EVENT, "", _BODY, _sig(), _SECRET) is False
    assert _wh.verify_signature(_EVENT, _WID, _BODY, "", _SECRET) is False


def test_H_missing_secret_rejected_safely():
    assert _wh.verify_signature(_EVENT, _WID, _BODY, _sig(secret=""), "") is False


def test_I_reserialized_json_with_equal_semantics_not_accepted():
    """Same JSON meaning, different bytes -> the signature for one must not
    validate the other. Proves byte-exactness, not semantic equivalence."""
    pretty = b'{\n  "notifications": [ {"orderCode": "ES-100", "triggerId": 4} ]\n}'
    assert _BODY != pretty
    import json as _json
    assert _json.loads(_BODY) == _json.loads(pretty)
    assert _wh.verify_signature(_EVENT, _WID, pretty, _sig(body=pretty), _SECRET) is True
    assert _wh.verify_signature(_EVENT, _WID, pretty, _sig(body=_BODY), _SECRET) is False


def test_J_escaped_json_characters_remain_byte_sensitive():
    escaped_a = b'{"note":"a\\/b"}'
    escaped_b = b'{"note":"a/b"}'  # semantically identical after JSON parse
    import json as _json
    assert _json.loads(escaped_a) == _json.loads(escaped_b)
    assert escaped_a != escaped_b
    assert _wh.verify_signature(_EVENT, _WID, escaped_b, _sig(body=escaped_a), _SECRET) is False


def test_malformed_prefix_rejected():
    digest = _sig()[len("sha256="):]
    assert _wh.verify_signature(_EVENT, _WID, _BODY, digest, _SECRET) is False
    assert _wh.verify_signature(_EVENT, _WID, _BODY, "sha1=" + digest, _SECRET) is False
    assert _wh.verify_signature(_EVENT, _WID, _BODY, "SHA256=" + digest, _SECRET) is False


def test_wrong_secret_rejected():
    assert _wh.verify_signature(_EVENT, _WID, _BODY, _sig(), "other-secret") is False


# --- Route-level: signature gate, dedup, and event handling -------------

def _client(monkeypatch, calls):
    monkeypatch.setenv("EUROSENDER_SANDBOX", "1")
    monkeypatch.setenv("EUROSENDER_WEBHOOK_SECRET", _SECRET)

    async def _fake_handle(event, payload, db):
        calls.append((event, payload))

    async def _fake_db():
        class _D:
            async def commit(self): pass
            async def rollback(self): pass
        yield _D()

    async def _no_redis():
        return None

    monkeypatch.setattr(_wh, "handle_event", _fake_handle)
    monkeypatch.setattr(_wh, "_get_redis_safe", _no_redis)
    app = FastAPI()
    app.include_router(_wh.router)
    app.dependency_overrides[_wh.get_pii_db] = _fake_db
    return TestClient(app)


def _headers(wid=_WID, event=_EVENT, sig=None):
    h = {"Webhook-Id": wid, "Webhook-Event": event}
    if sig is not None:
        h["Webhook-Signature"] = sig
    return h


def test_route_accepts_valid_signature_and_handles(monkeypatch):
    calls = []
    r = _client(monkeypatch, calls).post(
        "/api/v1/webhooks/eurosender", content=_BODY, headers=_headers(sig=_sig()))
    assert r.status_code == 200 and len(calls) == 1


def test_route_valid_id_and_event_headers_do_not_replace_signature(monkeypatch):
    """(L) An invalid signature must never reach business event processing,
    even when Webhook-Id/Webhook-Event are perfectly well-formed."""
    calls = []
    c = _client(monkeypatch, calls)
    assert c.post("/api/v1/webhooks/eurosender", content=_BODY, headers=_headers()).status_code == 401
    bad = _headers(sig="sha256=" + "a" * 64)
    assert c.post("/api/v1/webhooks/eurosender", content=_BODY, headers=bad).status_code == 401
    assert calls == []  # nothing handled without a valid signature


def test_route_rejects_modified_body_and_does_not_handle(monkeypatch):
    calls = []
    r = _client(monkeypatch, calls).post(
        "/api/v1/webhooks/eurosender", content=_BODY.replace(b"ES-100", b"ES-999"),
        headers=_headers(sig=_sig()))
    assert r.status_code == 401 and calls == []


def test_route_rejects_changed_event_header_with_body_signature_for_other_event(monkeypatch):
    """A signature computed for order_cancelled must not validate the same
    body delivered with a different Webhook-Event header."""
    calls = []
    r = _client(monkeypatch, calls).post(
        "/api/v1/webhooks/eurosender", content=_BODY,
        headers=_headers(event="order_label_ready", sig=_sig(event=_EVENT)))
    assert r.status_code == 401 and calls == []


def test_route_duplicate_webhook_id_still_deduplicated_after_valid_signature(monkeypatch):
    """(K) Dedup must still fire for a signature-verified, repeated delivery."""
    calls = []
    c = _client(monkeypatch, calls)
    sig = _sig()
    seen = {}

    async def _redis_with_state():
        class _R:
            async def exists(self, key):
                return key in seen

            async def set(self, key, value, ex=None):
                seen[key] = value
        return _R()

    monkeypatch.setattr(_wh, "_get_redis_safe", _redis_with_state)
    r1 = c.post("/api/v1/webhooks/eurosender", content=_BODY, headers=_headers(sig=sig))
    r2 = c.post("/api/v1/webhooks/eurosender", content=_BODY, headers=_headers(sig=sig))
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(calls) == 1  # second delivery deduplicated, handler ran once


# ---------------------------------------------------------------------------
# Phase 26 regression — tracking carried by order_submitted_to_courier.
# Root cause (found via real Sandbox deliveries + DB inspection 2026-09-25):
# tracking was persisted ONLY on order_tracking_ready, but Eurosender fires that
# event only when tracking was NOT already available at submission. Real
# submitted_to_courier deliveries already carry trackingCodes[].trackingNumber,
# so orders.tracking_number stayed NULL and the customer was never notified.
# Payload shapes below are the REAL captured wrapper (notifications[0]).
# ---------------------------------------------------------------------------

def _real_submitted_payload(order_code="ES-100", tracking="794873899979", url=None):
    return {"notifications": [{
        "courierId": 101,
        "documentUrls": [{"documentType": "label", "apiUrl": f"/v1/orders/{order_code}/documents/label_pdf_x"}],
        "trackingCodes": [{"orderCode": order_code, "trackingNumber": tracking, "trackingUrl": url}],
        "orderCode": order_code,
        "triggerId": 2,
    }]}


async def test_submitted_to_courier_with_tracking_persists_number_and_notifies_once():
    order = _order(status="paid")
    sp = SimpleNamespace(shipping_provider_ref="ES-100", tracking_number=None, tracking_url=None, status="paid")
    db = _FakeDB([_ScalarOneResult(order), _ScalarsAllResult([sp])])
    await handle_event("order_submitted_to_courier", _real_submitted_payload(url="https://t/794873899979"), db)
    assert order.status == "supplier_ordered"
    assert order.tracking_number == "794873899979"
    assert order.tracking_url == "https://t/794873899979"
    assert sp.tracking_number == "794873899979" and sp.status == "tracking_received"
    assert len(db.added) == 1 and "794873899979" in db.added[0].message


async def test_submitted_to_courier_with_null_tracking_number_is_normal_and_notifies_nobody():
    """'selection' service: trackingNumber is null at submission (real capture)."""
    order = _order(status="paid")
    db = _FakeDB([_ScalarOneResult(order)])
    await handle_event("order_submitted_to_courier", _real_submitted_payload(tracking=None), db)
    assert order.status == "supplier_ordered"
    assert order.tracking_number is None
    assert db.added == []


async def test_same_tracking_number_arriving_twice_never_double_notifies():
    """submitted_to_courier (with tracking) followed by tracking_ready for the
    same number must not send the customer a second notification."""
    order = _order(status="paid")
    db = _FakeDB([_ScalarOneResult(order), _ScalarsAllResult([]), _ScalarOneResult(order)])
    await handle_event("order_submitted_to_courier", _real_submitted_payload(), db)
    assert len(db.added) == 1
    await handle_event("order_tracking_ready", {"orderCode": "ES-100", "trackingCodes": [
        {"orderCode": "ES-100", "trackingNumber": "794873899979", "trackingUrl": None}]}, db)
    assert len(db.added) == 1  # unchanged — idempotent
    assert order.tracking_number == "794873899979"


async def test_submitted_to_courier_tracking_never_regresses_delivered_order():
    order = _order(status="delivered")
    db = _FakeDB([_ScalarOneResult(order), _ScalarsAllResult([])])
    await handle_event("order_submitted_to_courier", _real_submitted_payload(), db)
    assert order.status == "delivered"  # tracking stored, status untouched
    assert order.tracking_number == "794873899979"


def test_route_unknown_event_with_valid_signature_is_acknowledged_but_never_handled(monkeypatch):
    """A correctly-signed delivery of an event type we don't handle must return
    2xx (so Eurosender doesn't retry it forever) and must never reach handle_event."""
    calls = []
    ev = "some_future_event"
    r = _client(monkeypatch, calls).post(
        "/api/v1/webhooks/eurosender", content=_BODY,
        headers=_headers(event=ev, sig=_sig(event=ev)))
    assert r.status_code == 200 and calls == []
