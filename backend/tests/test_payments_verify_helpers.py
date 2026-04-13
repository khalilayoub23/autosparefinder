from types import SimpleNamespace
from uuid import uuid4

from routes.payments import _build_paid_verify_response, _dedupe_orders_from_rows


def _order(num: str, status: str, amount: float):
    return SimpleNamespace(
        id=uuid4(),
        order_number=num,
        status=status,
        total_amount=amount,
    )


def test_dedupe_orders_from_rows_keeps_order_and_uniques():
    o1 = _order("AUTO-1", "paid", 100.0)
    o2 = _order("AUTO-2", "supplier_ordered", 50.0)

    rows = [
        (SimpleNamespace(id=uuid4()), o1),
        (SimpleNamespace(id=uuid4()), o1),
        (SimpleNamespace(id=uuid4()), o2),
    ]

    out = _dedupe_orders_from_rows(rows)

    assert len(out) == 2
    assert out[0].order_number == "AUTO-1"
    assert out[1].order_number == "AUTO-2"


def test_build_paid_verify_response_single_order_shape():
    o1 = _order("AUTO-1", "paid", 123.45)

    payload = _build_paid_verify_response([o1])

    assert payload["status"] == "paid"
    assert payload["order_number"] == "AUTO-1"
    assert payload["order_status"] == "paid"
    assert payload["amount"] == 123.45
    assert "is_multi" not in payload


def test_build_paid_verify_response_multi_order_shape_and_sum():
    o1 = _order("AUTO-1", "paid", 100.0)
    o2 = _order("AUTO-2", "supplier_ordered", 40.5)

    payload = _build_paid_verify_response([o1, o2])

    assert payload["status"] == "paid"
    assert payload["is_multi"] is True
    assert len(payload["orders"]) == 2
    assert payload["orders"][0]["order_number"] == "AUTO-1"
    assert payload["orders"][1]["order_number"] == "AUTO-2"
    assert payload["amount"] == 140.5
