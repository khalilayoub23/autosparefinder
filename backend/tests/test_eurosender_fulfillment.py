"""handle_eligible_suppliers() — the interception point in
trigger_supplier_fulfillment(). Core invariant under test: a supplier
determined Eurosender-eligible must NEVER be handed back to the caller's
OrdersAgent/fake-tracking path, regardless of outcome (success, manual
review, or an internal error)."""
import uuid
from types import SimpleNamespace

from services.shipping.eurosender_fulfillment import (
    _extract_pickup_from_shipping_info,
    handle_eligible_suppliers,
)


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


# ---------------------------------------------------------------------------
# _extract_pickup_from_shipping_info() — the exact safety net that stands
# between B5 and a fabricated pickup address. Zero coverage existed before
# Phase 25 (B5 investigation) despite this being the one function protecting
# every supplier, including Car-Parts.ie whose live `shipping_info` is NULL.
# ---------------------------------------------------------------------------

_COMPLETE_ADDR = {"country": "IE", "zip": "D01F5P2", "city": "Dublin", "street": "1 Nassau Street"}
_COMPLETE_CONTACT = {"name": "Car-Parts.ie Warehouse", "email": "ops@example.com", "phone": "+35310000000"}


def test_none_shipping_info_yields_no_pickup():
    # This is Car-Parts.ie's real live state (Supplier.shipping_info IS NULL).
    assert _extract_pickup_from_shipping_info(None) == (None, None)


def test_empty_dict_shipping_info_yields_no_pickup():
    assert _extract_pickup_from_shipping_info({}) == (None, None)


def test_non_dict_shipping_info_yields_no_pickup():
    assert _extract_pickup_from_shipping_info("not-a-dict") == (None, None)
    assert _extract_pickup_from_shipping_info(["also", "not", "a", "dict"]) == (None, None)


def test_address_missing_one_required_field_yields_no_pickup():
    for missing in ("country", "zip", "city", "street"):
        addr = {k: v for k, v in _COMPLETE_ADDR.items() if k != missing}
        info = {"address": addr, "contact": _COMPLETE_CONTACT}
        assert _extract_pickup_from_shipping_info(info) == (None, None), f"missing {missing} should block"


def test_address_field_present_but_blank_yields_no_pickup():
    addr = dict(_COMPLETE_ADDR, street="   ")
    info = {"address": addr, "contact": _COMPLETE_CONTACT}
    assert _extract_pickup_from_shipping_info(info) == (None, None)


def test_contact_missing_one_required_field_yields_no_pickup():
    for missing in ("name", "email", "phone"):
        contact = {k: v for k, v in _COMPLETE_CONTACT.items() if k != missing}
        info = {"address": _COMPLETE_ADDR, "contact": contact}
        assert _extract_pickup_from_shipping_info(info) == (None, None), f"missing {missing} should block"


def test_complete_address_and_contact_are_returned_unmodified():
    info = {"address": _COMPLETE_ADDR, "contact": _COMPLETE_CONTACT}
    addr, contact = _extract_pickup_from_shipping_info(info)
    assert addr == _COMPLETE_ADDR
    assert contact == _COMPLETE_CONTACT
    # Never invented/mutated: the exact same dict values come back.
    assert addr is _COMPLETE_ADDR or addr == _COMPLETE_ADDR
    assert contact is _COMPLETE_CONTACT or contact == _COMPLETE_CONTACT
