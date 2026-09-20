"""evaluate_and_create_shipment() orchestration: gates (dimension/weight/HS/
declared-value) must run BEFORE any adapter call, and every adapter failure
mode must map to the correct, safe verdict — especially that an ambiguous
failure never gets auto-retried."""
import httpx
import pytest

from services.shipping.eurosender_order_service import (
    ShipmentVerdict,
    evaluate_and_create_shipment,
)

VALID_ITEMS = [{"category": "brakes", "quantity": 1, "content": "Brake pads"}]
VALID_PRICES = [500.0]
ADDR = {"country": "IE", "zip": "1", "city": "Dublin", "street": "1 Main St"}
CONTACT = {"name": "A", "email": "a@b.com", "phone": "1"}


class _FakeAdapter:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.called_with = None

    async def create_shipment(self, **kwargs):
        self.called_with = kwargs
        if self._exc:
            raise self._exc
        return self._response


async def _run(adapter, items=None, current_status=None):
    return await evaluate_and_create_shipment(
        order_id="order-1",
        items=items or VALID_ITEMS,
        item_total_prices_ils=VALID_PRICES,
        pickup_address=ADDR,
        delivery_address=ADDR,
        pickup_contact=CONTACT,
        delivery_contact=CONTACT,
        order_contact={"email": "a@b.com"},
        current_eurosender_status=current_status,
        adapter=adapter,
    )


async def test_already_timeout_pending_never_calls_adapter():
    adapter = _FakeAdapter(response={"orderCode": "X"})
    result = await _run(adapter, current_status="timeout_pending_reconciliation")
    assert result.verdict == ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION
    assert adapter.called_with is None  # never called


async def test_blocked_category_never_calls_adapter():
    adapter = _FakeAdapter(response={"orderCode": "X"})
    result = await _run(adapter, items=[{"category": "engine", "quantity": 1, "content": "engine block"}])
    assert result.verdict == ShipmentVerdict.MANUAL_REVIEW
    assert adapter.called_with is None


async def test_manual_review_category_never_calls_adapter():
    adapter = _FakeAdapter(response={"orderCode": "X"})
    result = await _run(adapter, items=[{"category": "lighting", "quantity": 1, "content": "headlamp"}])
    assert result.verdict == ShipmentVerdict.MANUAL_REVIEW
    assert adapter.called_with is None


async def test_successful_creation():
    adapter = _FakeAdapter(response={"orderCode": "ES-001", "status": "Confirmed", "labelLink": "https://x/label.pdf"})
    result = await _run(adapter)
    assert result.verdict == ShipmentVerdict.CREATED
    assert result.eurosender_order_code == "ES-001"
    assert result.label_url == "https://x/label.pdf"
    assert result.hs_codes == {"brakes": "870830"}
    assert result.declared_value_eur is not None
    assert adapter.called_with["customer_internal_reference"] == "order-1"


async def test_israel_customs_status_maps_to_awaiting_customs():
    adapter = _FakeAdapter(response={"orderCode": "ES-002", "status": "Awaiting customs documentation"})
    result = await _run(adapter)
    assert result.verdict == ShipmentVerdict.CREATED
    assert result.eurosender_status == "awaiting_customs"


async def test_timeout_exception_is_ambiguous_not_failed():
    adapter = _FakeAdapter(exc=httpx.TimeoutException("timed out"))
    result = await _run(adapter)
    assert result.verdict == ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION


async def test_connect_error_is_ambiguous():
    adapter = _FakeAdapter(exc=httpx.ConnectError("connection refused"))
    result = await _run(adapter)
    assert result.verdict == ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION


async def test_5xx_is_ambiguous_not_clean_failed():
    req = httpx.Request("POST", "https://sandbox-api.eurosender.com/v1/orders")
    resp = httpx.Response(503, request=req)
    exc = httpx.HTTPStatusError("server error", request=req, response=resp)
    adapter = _FakeAdapter(exc=exc)
    result = await _run(adapter)
    assert result.verdict == ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION


async def test_4xx_is_clean_failed_not_ambiguous():
    req = httpx.Request("POST", "https://sandbox-api.eurosender.com/v1/orders")
    resp = httpx.Response(422, request=req)
    exc = httpx.HTTPStatusError("validation error", request=req, response=resp)
    adapter = _FakeAdapter(exc=exc)
    result = await _run(adapter)
    assert result.verdict == ShipmentVerdict.FAILED


async def test_2xx_with_no_order_code_is_ambiguous():
    adapter = _FakeAdapter(response={"status": "Confirmed"})  # missing orderCode
    result = await _run(adapter)
    assert result.verdict == ShipmentVerdict.TIMEOUT_PENDING_RECONCILIATION


async def test_declared_value_excludes_supplier_cost_by_construction():
    """item_total_prices_ils is the ONLY price input accepted — there is no
    parameter for supplier cost, so it structurally cannot leak in."""
    import inspect
    sig = inspect.signature(evaluate_and_create_shipment)
    assert "supplier_cost" not in sig.parameters
    assert "importer_price_ils" not in sig.parameters


# ---------------------------------------------------------------------------
# Phase 18: end-to-end wiring of the new content/dimension defense-in-depth
# guards through _build_packages_and_gates -> evaluate_and_create_shipment.
# ---------------------------------------------------------------------------

async def test_category_with_long_slug_and_no_short_content_goes_manual_review():
    """'suspension-steering' (20 chars) exceeds the verified 17-char
    Eurosender content limit even as a bare category slug — with no real
    item content given, this must route to manual_review, never truncate."""
    adapter = _FakeAdapter(response={"orderCode": "SHOULD-NOT-BE-CALLED"})
    result = await _run(
        adapter,
        items=[{"category": "suspension-steering", "quantity": 1, "content": None}],
    )
    assert result.verdict == ShipmentVerdict.MANUAL_REVIEW
    assert adapter.called_with is None
    assert any("content" in r.lower() for r in result.reasons)


async def test_short_real_item_content_is_used_over_category():
    adapter = _FakeAdapter(response={"orderCode": "ES-CONTENT-1", "status": "Confirmed"})
    result = await _run(
        adapter,
        items=[{"category": "brakes", "quantity": 1, "content": "brake pads"}],
    )
    assert result.verdict == ShipmentVerdict.CREATED
    sent_content = adapter.called_with["packages"][0]["content"]
    assert sent_content == "brake pads"
    assert len(sent_content) <= 17


async def test_long_real_item_content_falls_back_to_category_not_truncated():
    adapter = _FakeAdapter(response={"orderCode": "ES-CONTENT-2", "status": "Confirmed"})
    result = await _run(
        adapter,
        items=[{"category": "brakes", "quantity": 1, "content": "BOSCH Brake Pad Set Front Left Right"}],
    )
    assert result.verdict == ShipmentVerdict.CREATED
    sent_content = adapter.called_with["packages"][0]["content"]
    assert sent_content == "brakes"  # fell back to the short category, never truncated the long name


async def test_created_result_exposes_parsed_warnings():
    adapter = _FakeAdapter(response={
        "orderCode": "ES-WARN-1", "status": "Confirmed",
        "warnings": [{"code": "b2b-not-confirmed", "message": "x", "parameterPath": ""}],
    })
    result = await _run(adapter)
    assert result.verdict == ShipmentVerdict.CREATED
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "b2b-not-confirmed"
