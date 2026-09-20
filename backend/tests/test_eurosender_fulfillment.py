"""handle_eligible_suppliers() — the interception point in
trigger_supplier_fulfillment(). Core invariant under test: a supplier
determined Eurosender-eligible must NEVER be handed back to the caller's
OrdersAgent/fake-tracking path, regardless of outcome (success, manual
review, or an internal error)."""
import uuid
from types import SimpleNamespace

from services.shipping.eurosender_fulfillment import handle_eligible_suppliers


def _bucket(supplier_id, items=None):
    return {
        "supplier_id": supplier_id,
        "supplier_name": "Test Supplier",
        "items": items or [],
        "credentials": {},
    }


async def test_non_eligible_supplier_is_left_in_remaining(monkeypatch):
    monkeypatch.setenv("EUROSENDER_ENABLED", "0")  # closed gate
    sid = str(uuid.uuid4())
    by_supplier = {sid: _bucket(sid)}
    order_db = SimpleNamespace(order_number="AUTO-1", eurosender_status=None)
    remaining = await handle_eligible_suppliers(by_supplier, {sid}, order_db, {}, db=None)
    assert remaining == {sid}


async def test_eligible_supplier_always_removed_from_remaining_even_on_internal_error(monkeypatch):
    sid = str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", sid)
    by_supplier = {sid: _bucket(sid)}
    order_db = SimpleNamespace(order_number="AUTO-1", eurosender_status=None, id="order-1", shipping_address={}, user_id="u1")

    # db=None will cause an internal error inside _attempt_shipment (no real
    # session to query) — the safety invariant must still hold: sid is gone
    # from `remaining` no matter what breaks downstream.
    remaining = await handle_eligible_suppliers(by_supplier, {sid}, order_db, {}, db=None)
    assert sid not in remaining


async def test_mixed_eligible_and_non_eligible_suppliers_only_removes_eligible(monkeypatch):
    eligible_sid = str(uuid.uuid4())
    other_sid = str(uuid.uuid4())
    monkeypatch.setenv("EUROSENDER_ENABLED", "1")
    monkeypatch.setenv("EUROSENDER_SUPPLIER_ALLOWLIST", eligible_sid)
    by_supplier = {
        eligible_sid: _bucket(eligible_sid),
        other_sid: _bucket(other_sid),
    }
    order_db = SimpleNamespace(order_number="AUTO-1", eurosender_status=None, id="order-1", shipping_address={}, user_id="u1")
    remaining = await handle_eligible_suppliers(by_supplier, {eligible_sid, other_sid}, order_db, {}, db=None)
    assert remaining == {other_sid}


async def test_missing_bucket_entry_is_skipped_safely():
    remaining = await handle_eligible_suppliers({}, {"ghost-key"}, SimpleNamespace(), {}, db=None)
    assert remaining == {"ghost-key"}


async def test_no_eligible_suppliers_returns_untouched_set_without_importing_db_models(monkeypatch):
    monkeypatch.setenv("EUROSENDER_ENABLED", "0")
    sid1, sid2 = str(uuid.uuid4()), str(uuid.uuid4())
    by_supplier = {sid1: _bucket(sid1), sid2: _bucket(sid2)}
    remaining = await handle_eligible_suppliers(by_supplier, {sid1, sid2}, SimpleNamespace(), {}, db=None)
    assert remaining == {sid1, sid2}
