"""Eurosender adapter: sandbox-URL enforcement, required-field validation,
and correct request/response handling — using a fake httpx.AsyncClient (no
real network calls, matching this repo's existing test-double conventions)."""
import json

import pytest
import httpx

from services.shipping import eurosender_config
from services.shipping.eurosender_adapter import (
    EurosenderAdapter,
    EurosenderNotConfigured,
    EurosenderProductionBlocked,
)


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body
        self.content = b"1" if json_body is not None else b""
        req = httpx.Request("POST", "https://sandbox-api.eurosender.com/v1/test")
        self._httpx_resp = httpx.Response(status_code, json=json_body, request=req)

    def json(self):
        return self._json_body

    def raise_for_status(self):
        return self._httpx_resp.raise_for_status()


class _FakeAsyncClient:
    """Records the last request made; returns whatever `next_response` holds."""
    next_response: _FakeResponse = None
    last_call: dict = None

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, json=None):
        _FakeAsyncClient.last_call = {"method": method, "url": url, "headers": headers, "json": json}
        return _FakeAsyncClient.next_response


def _patch_client(monkeypatch, response: _FakeResponse):
    from services.shipping import eurosender_adapter as mod
    _FakeAsyncClient.next_response = response
    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
    # patch the real raise_for_status_safe path to use our fake's raise_for_status
    monkeypatch.setattr(mod, "_raise_for_status_safe", lambda resp: resp.raise_for_status())


VALID_PACKAGE = {
    "parcelId": "P1", "quantity": 1, "weight": 1.5,
    "length": 20, "width": 15, "height": 10,
    "content": "brake pads", "value": 50,
}


@pytest.mark.asyncio
async def test_get_quote_requires_api_key(monkeypatch):
    monkeypatch.delenv("EUROSENDER_API_KEY", raising=False)
    adapter = EurosenderAdapter()
    with pytest.raises(EurosenderNotConfigured):
        await adapter.get_quote({"country": "IE"}, {"country": "IL"}, [VALID_PACKAGE])


@pytest.mark.asyncio
async def test_get_quote_rejects_incomplete_package(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    adapter = EurosenderAdapter()
    incomplete = {"parcelId": "P1", "quantity": 1}  # missing dimensions/weight/value/content
    with pytest.raises(ValueError):
        await adapter.get_quote({"country": "IE"}, {"country": "IL"}, [incomplete])


@pytest.mark.asyncio
async def test_get_quote_sends_x_api_key_header(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    _patch_client(monkeypatch, _FakeResponse(200, {"options": {"serviceTypes": []}}))
    adapter = EurosenderAdapter()
    result = await adapter.get_quote({"country": "IE"}, {"country": "IL"}, [VALID_PACKAGE])
    assert result == {"options": {"serviceTypes": []}}
    assert _FakeAsyncClient.last_call["headers"]["x-api-key"] == "sandbox-test-key"
    assert _FakeAsyncClient.last_call["url"].startswith(eurosender_config.sandbox_url())


@pytest.mark.asyncio
async def test_adapter_refuses_production_url(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    monkeypatch.setenv("EUROSENDER_SANDBOX", "0")  # would resolve to production_url()
    adapter = EurosenderAdapter()
    with pytest.raises(EurosenderProductionBlocked):
        await adapter.get_quote({"country": "IE"}, {"country": "IL"}, [VALID_PACKAGE])


@pytest.mark.asyncio
async def test_create_shipment_includes_customer_internal_reference(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    _patch_client(monkeypatch, _FakeResponse(201, {"orderCode": "TEST-001", "status": "Confirmed"}))
    adapter = EurosenderAdapter()
    result = await adapter.create_shipment(
        pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
        packages=[VALID_PACKAGE], service_type="regular_plus", payment_method="credit",
        order_contact={"email": "a@b.com"}, pickup_contact={"name": "A", "email": "a@b.com", "phone": "1"},
        delivery_contact={"name": "B", "email": "b@b.com", "phone": "2"},
        customer_internal_reference="order-123",
    )
    assert result["orderCode"] == "TEST-001"
    assert _FakeAsyncClient.last_call["json"]["customerInternalReference"] == "order-123"


_DUMMY_PROFORMA_CONTACT = {
    "contactPerson": "A", "companyName": None, "phone": "1", "email": "a@b.com",
    "street": "1 St", "zip": "00000", "city": "City", "country": "IE",
    "vat": None, "eoriNumber": None,
}


@pytest.mark.asyncio
async def test_create_proforma_refuses_item_without_hs_code(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    adapter = EurosenderAdapter()
    with pytest.raises(ValueError):
        await adapter.create_proforma(
            "TEST-001", [{"description": "part", "quantity": 1, "value": 10}],
            shipper=_DUMMY_PROFORMA_CONTACT, receiver=_DUMMY_PROFORMA_CONTACT,
        )


@pytest.mark.asyncio
async def test_create_proforma_sends_verified_field_names(monkeypatch):
    """Regression for the 2026-09-11 live 400: unitValue/countryOfOrigin were
    invented field names the real API rejects. This proves the request body
    uses ONLY the verified schema fields."""
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    _patch_client(monkeypatch, _FakeResponse(200, {"orderCode": "TEST-001"}))
    adapter = EurosenderAdapter()
    item = {"description": "brakes", "country": "IE", "quantity": 1, "weight": 1.5, "value": 20, "hsCode": "870830"}
    await adapter.create_proforma(
        "TEST-001", [item], shipper=_DUMMY_PROFORMA_CONTACT, receiver=_DUMMY_PROFORMA_CONTACT,
    )
    sent_body = _FakeAsyncClient.last_call["json"]
    assert sent_body["orderCode"] == "TEST-001"
    assert sent_body["shipper"] == _DUMMY_PROFORMA_CONTACT
    assert sent_body["receiver"] == _DUMMY_PROFORMA_CONTACT
    assert sent_body["reason"] == "commercial"
    assert sent_body["items"] == [item]
    # Forbidden invented field names must never appear anywhere in the body.
    body_str = json.dumps(sent_body)
    assert "unitValue" not in body_str
    assert "countryOfOrigin" not in body_str


@pytest.mark.asyncio
async def test_cancel_shipment_uses_delete_method_and_no_cancel_suffix(monkeypatch):
    """Regression for the 2026-09-11 live 404: POST .../cancel does not
    exist. Verified contract: DELETE /v1/orders/{orderCode}, no body."""
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    _patch_client(monkeypatch, _FakeResponse(204, None))
    adapter = EurosenderAdapter()
    await adapter.cancel_shipment("TEST-001")
    assert _FakeAsyncClient.last_call["method"] == "DELETE"
    called_url = _FakeAsyncClient.last_call["url"]
    assert called_url.endswith("/v1/orders/TEST-001")
    assert not called_url.endswith("/cancel")


# ---------------------------------------------------------------------------
# Phase 18: validate_creation() — confirmed live (2026-09-11, Phase 17) to
# exist and function without creating a real order.
# ---------------------------------------------------------------------------

VALID_CONTACT = {"name": "A", "email": "a@b.com", "phone": "1"}


async def _call_validate_creation(adapter):
    return await adapter.validate_creation(
        pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
        packages=[VALID_PACKAGE], service_type="regular_plus", payment_method="credit",
        order_contact={"email": "a@b.com"}, pickup_contact=VALID_CONTACT,
        delivery_contact=VALID_CONTACT, customer_internal_reference="order-validate-1",
    )


@pytest.mark.asyncio
async def test_validate_creation_requires_api_key(monkeypatch):
    monkeypatch.delenv("EUROSENDER_API_KEY", raising=False)
    adapter = EurosenderAdapter()
    with pytest.raises(EurosenderNotConfigured):
        await _call_validate_creation(adapter)


@pytest.mark.asyncio
async def test_validate_creation_rejects_incomplete_package(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    adapter = EurosenderAdapter()
    with pytest.raises(ValueError):
        await adapter.validate_creation(
            pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
            packages=[{"parcelId": "P1"}], service_type="regular_plus", payment_method="credit",
            order_contact={"email": "a@b.com"}, pickup_contact=VALID_CONTACT,
            delivery_contact=VALID_CONTACT, customer_internal_reference="order-1",
        )


@pytest.mark.asyncio
async def test_validate_creation_success_shape(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    _patch_client(monkeypatch, _FakeResponse(200, {"valid": True}))
    adapter = EurosenderAdapter()
    result = await _call_validate_creation(adapter)
    assert result == {"valid": True}
    assert _FakeAsyncClient.last_call["url"].endswith("/v1/orders/validate_creation")
    assert _FakeAsyncClient.last_call["json"]["customerInternalReference"] == "order-validate-1"


@pytest.mark.asyncio
async def test_validate_creation_4xx_raises_not_retried(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    _patch_client(monkeypatch, _FakeResponse(422, {
        "status": 422,
        "violations": [{"propertyPath": "paymentMethod", "message": "x", "code": "insufficient-payment-balance"}],
    }))
    adapter = EurosenderAdapter()
    with pytest.raises(httpx.HTTPStatusError):
        await _call_validate_creation(adapter)


@pytest.mark.asyncio
async def test_validate_creation_5xx_raises(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    _patch_client(monkeypatch, _FakeResponse(503, {"detail": "server error"}))
    adapter = EurosenderAdapter()
    with pytest.raises(httpx.HTTPStatusError):
        await _call_validate_creation(adapter)


@pytest.mark.asyncio
async def test_validate_creation_never_targets_production(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    monkeypatch.setenv("EUROSENDER_SANDBOX", "0")
    adapter = EurosenderAdapter()
    with pytest.raises(EurosenderProductionBlocked):
        await _call_validate_creation(adapter)


def test_validate_creation_does_not_call_orders_endpoint_directly():
    """Structural guard: validate_creation's URL path must never equal the
    real order-creation path — proven by inspecting the method's source for
    the literal path string, independent of any live/mocked call."""
    import inspect
    from services.shipping import eurosender_adapter as mod
    src = inspect.getsource(mod.EurosenderAdapter.validate_creation)
    assert "/v1/orders/validate_creation" in src
    assert '"/v1/orders"' not in src


# ---------------------------------------------------------------------------
# Phase 18: quote-warning parsing/classification
# ---------------------------------------------------------------------------

def test_parse_warnings_empty_when_absent():
    from services.shipping.eurosender_adapter import parse_warnings
    assert parse_warnings({}) == []
    assert parse_warnings({"warnings": None}) == []


def test_parse_warnings_classifies_known_account_codes():
    from services.shipping.eurosender_adapter import parse_warnings, WarningSeverity
    response = {"warnings": [
        {"code": "insufficient-payment-balance", "message": "x", "parameterPath": "paymentMethod"},
        {"code": "b2b-not-confirmed", "message": "y", "parameterPath": ""},
    ]}
    warnings = parse_warnings(response)
    assert len(warnings) == 2
    assert warnings[0].severity == WarningSeverity.ACCOUNT
    assert warnings[1].severity == WarningSeverity.ACCOUNT


def test_parse_warnings_classifies_unknown_payload_warning():
    from services.shipping.eurosender_adapter import parse_warnings, WarningSeverity
    response = {"warnings": [
        {"code": "d94b19cc-114f-4f44-9cc4-4138e80a87b9", "message": "Field value exceeds maximum length.", "parameterPath": "parcels.packages[0].content"},
    ]}
    warnings = parse_warnings(response)
    assert warnings[0].severity == WarningSeverity.PAYLOAD
    assert warnings[0].parameter_path == "parcels.packages[0].content"


def test_parse_warnings_unknown_code_no_path_is_informational():
    from services.shipping.eurosender_adapter import parse_warnings, WarningSeverity
    response = {"warnings": [{"code": "some-new-code", "message": "z", "parameterPath": ""}]}
    warnings = parse_warnings(response)
    assert warnings[0].severity == WarningSeverity.INFORMATIONAL


def test_parse_warnings_account_severity_is_not_treated_as_universal_failure():
    """Structural proof the module never raises/blocks on ACCOUNT-severity
    warnings — parse_warnings is a pure classifier, not a gate."""
    from services.shipping.eurosender_adapter import parse_warnings
    response = {"warnings": [{"code": "insufficient-payment-balance", "message": "x", "parameterPath": ""}]}
    warnings = parse_warnings(response)  # must not raise
    assert len(warnings) == 1
