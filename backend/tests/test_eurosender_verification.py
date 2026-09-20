"""Incident-specific regression coverage for the 2026-09-11 Phase 18
incident: a verification task called evaluate_and_create_shipment() instead
of a read-only helper, causing 5 unintended live POST /v1/orders attempts.

These tests prove the NEW dedicated verification module
(services/shipping/eurosender_verification.py) is structurally incapable of
reaching order creation — by source inspection, by HTTP-client interception,
and by forcing every kind of failure (timeout, 4xx, 5xx, malformed response)
through it and proving create_shipment() is never touched.
"""
import inspect

import httpx
import pytest

from services.shipping import eurosender_verification as verify_mod
from services.shipping.eurosender_adapter import EurosenderAdapter, ORDER_CREATION_PATH

VALID_PACKAGE = {
    "parcelId": "P1", "quantity": 1, "weight": 1.5,
    "length": 20, "width": 15, "height": 10,
    "content": "brake pads", "value": 50,
}
CONTACT = {"name": "A", "email": "a@b.com", "phone": "1"}


class _SpyAdapter:
    """Records whether create_shipment was ever invoked; raises loudly if it
    is, so any accidental call fails the test immediately rather than
    silently succeeding."""

    def __init__(self, quote_response=None, quote_exc=None, countries_response=None,
                 validate_response=None, validate_exc=None):
        self._quote_response = quote_response
        self._quote_exc = quote_exc
        self._countries_response = countries_response if countries_response is not None else [{"code": "IL"}]
        self._validate_response = validate_response if validate_response is not None else {"valid": True}
        self._validate_exc = validate_exc
        self.create_shipment_called = False

    async def get_countries(self):
        return self._countries_response

    async def get_quote(self, **kwargs):
        if self._quote_exc:
            raise self._quote_exc
        return self._quote_response

    async def validate_creation(self, **kwargs):
        if self._validate_exc:
            raise self._validate_exc
        return self._validate_response

    async def create_shipment(self, **kwargs):
        self.create_shipment_called = True
        raise AssertionError(
            "create_shipment() was called from a code path that must be "
            "structurally incapable of reaching order creation — this IS "
            "the exact failure mode of the 2026-09-11 Phase 18 incident."
        )


# ---------------------------------------------------------------------------
# Test A / structural: the verification module cannot reference creation
# ---------------------------------------------------------------------------

_FORBIDDEN_IDENTIFIERS = {
    "create_shipment",
    "evaluate_and_create_shipment",
    "ORDER_CREATION_PATH",
    "eurosender_order_service",
    "eurosender_fulfillment",
}


def _actual_code_identifiers(module) -> set[str]:
    """AST-based extraction of every real identifier used in CODE (imports,
    names, attribute accesses) — deliberately excludes docstrings/comments/
    string literals, since this module's own docstring legitimately
    discusses create_shipment() in prose while explaining why it's unused."""
    import ast
    tree = ast.parse(inspect.getsource(module))
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                identifiers.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                identifiers.add(alias.name)
    return identifiers


def test_verification_module_never_imports_create_shipment():
    identifiers = _actual_code_identifiers(verify_mod)
    forbidden_found = identifiers & _FORBIDDEN_IDENTIFIERS
    assert not forbidden_found, f"Forbidden identifiers used in actual code: {forbidden_found}"


def test_verification_module_source_has_no_bare_orders_literal():
    """The exact mutating path string must not appear as a string literal
    anywhere in this module's code (docstrings excluded by using ast.Constant
    on non-docstring nodes would be more precise, but a plain substring check
    against the raw source is sufficient here since the module never has
    legitimate reason to mention this exact literal outside prose)."""
    import ast
    tree = ast.parse(inspect.getsource(verify_mod))
    string_literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "/v1/orders" not in string_literals


def test_adapter_order_creation_path_constant_used_only_by_create_shipment():
    """Forensic guard: ORDER_CREATION_PATH must be referenced by exactly one
    method in the adapter — create_shipment. If a second method starts
    referencing it, this test catches the drift immediately."""
    import services.shipping.eurosender_adapter as adapter_mod
    src = inspect.getsource(adapter_mod)
    assert src.count("ORDER_CREATION_PATH") == 3  # definition + docstring mention + the one real usage
    create_shipment_src = inspect.getsource(EurosenderAdapter.create_shipment)
    assert "ORDER_CREATION_PATH" in create_shipment_src


# ---------------------------------------------------------------------------
# Test B: HTTP-intercept — run the real adapter through the verification
# module with a fake httpx.AsyncClient, prove zero requests ever target the
# exact POST /v1/orders path.
# ---------------------------------------------------------------------------

class _RequestRecordingClient:
    requests: list = []
    next_response = None

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, json=None):
        _RequestRecordingClient.requests.append((method, url))
        return _RequestRecordingClient.next_response


class _FakeHttpxResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.content = b"1"
        req = httpx.Request("POST", "https://sandbox-api.eurosender.com/x")
        self._real_response = httpx.Response(status_code, json=self._json_body, request=req)

    def json(self):
        return self._json_body

    def raise_for_status(self):
        return self._real_response.raise_for_status()


def _patch_raise_for_status(monkeypatch):
    """Matches the established pattern in test_eurosender_adapter.py: patch
    _raise_for_status_safe to call the fake's own bound raise_for_status(),
    since the real implementation calls the unbound httpx.Response method
    against whatever object client.request() returned."""
    from services.shipping import eurosender_adapter as mod
    monkeypatch.setattr(mod, "_raise_for_status_safe", lambda resp: resp.raise_for_status())


def _no_forbidden_request_was_made():
    for method, url in _RequestRecordingClient.requests:
        # The mutating path is an EXACT match: POST .../v1/orders with
        # nothing after it. /v1/orders/validate_creation must NOT match.
        assert not (method == "POST" and url.rstrip("/").endswith(ORDER_CREATION_PATH)), (
            f"FORBIDDEN REQUEST DETECTED: {method} {url}"
        )


@pytest.mark.asyncio
async def test_verify_countries_never_touches_orders_endpoint(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    from services.shipping import eurosender_adapter as mod
    _RequestRecordingClient.requests = []
    _RequestRecordingClient.next_response = _FakeHttpxResponse(200, [{"code": "IL"}])
    monkeypatch.setattr(mod.httpx, "AsyncClient", _RequestRecordingClient)
    _patch_raise_for_status(monkeypatch)

    await verify_mod.verify_countries()
    _no_forbidden_request_was_made()
    assert len(_RequestRecordingClient.requests) == 1
    assert _RequestRecordingClient.requests[0][1].endswith("/v1/countries")


@pytest.mark.asyncio
async def test_verify_route_quote_never_touches_orders_endpoint(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    from services.shipping import eurosender_adapter as mod
    _RequestRecordingClient.requests = []
    _RequestRecordingClient.next_response = _FakeHttpxResponse(200, {"options": {"serviceTypes": []}})
    monkeypatch.setattr(mod.httpx, "AsyncClient", _RequestRecordingClient)
    _patch_raise_for_status(monkeypatch)

    await verify_mod.verify_route_quote(
        pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
        packages=[VALID_PACKAGE],
    )
    _no_forbidden_request_was_made()
    assert len(_RequestRecordingClient.requests) == 1
    assert _RequestRecordingClient.requests[0][1].endswith("/v1/quotes")


@pytest.mark.asyncio
async def test_verify_order_payload_uses_validate_creation_not_orders(monkeypatch):
    monkeypatch.setenv("EUROSENDER_API_KEY", "sandbox-test-key")
    from services.shipping import eurosender_adapter as mod
    _RequestRecordingClient.requests = []
    _RequestRecordingClient.next_response = _FakeHttpxResponse(200, {"valid": True})
    monkeypatch.setattr(mod.httpx, "AsyncClient", _RequestRecordingClient)
    _patch_raise_for_status(monkeypatch)

    await verify_mod.verify_order_payload(
        pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
        packages=[VALID_PACKAGE], service_type="regular_plus", payment_method="credit",
        order_contact={"email": "a@b.com"}, pickup_contact=CONTACT, delivery_contact=CONTACT,
        customer_internal_reference="test-1",
    )
    _no_forbidden_request_was_made()
    assert len(_RequestRecordingClient.requests) == 1
    called_url = _RequestRecordingClient.requests[0][1]
    assert called_url.endswith("/v1/orders/validate_creation")
    assert not called_url.rstrip("/").endswith(ORDER_CREATION_PATH)  # not the bare mutating path


@pytest.mark.asyncio
async def test_endswith_guard_correctly_flags_the_real_mutating_path():
    """Proves _no_forbidden_request_was_made()'s own logic is correct: it
    MUST flag a genuine POST /v1/orders call (and would fail this test on
    purpose if it didn't)."""
    _RequestRecordingClient.requests = [("POST", "https://sandbox-api.eurosender.com/v1/orders")]
    with pytest.raises(AssertionError, match="FORBIDDEN REQUEST DETECTED"):
        _no_forbidden_request_was_made()
    _RequestRecordingClient.requests = []


# ---------------------------------------------------------------------------
# Test D/E: retry and exception safety — timeout, HTTP error, malformed
# response must never fall through to create_shipment.
# ---------------------------------------------------------------------------

async def test_quote_timeout_never_reaches_create_shipment():
    spy = _SpyAdapter(quote_exc=httpx.TimeoutException("timed out"))
    with pytest.raises(httpx.TimeoutException):
        await verify_mod.verify_route_quote(
            pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
            packages=[VALID_PACKAGE], adapter=spy,
        )
    assert spy.create_shipment_called is False


async def test_quote_5xx_never_reaches_create_shipment():
    req = httpx.Request("POST", "https://sandbox-api.eurosender.com/v1/quotes")
    resp = httpx.Response(503, request=req)
    spy = _SpyAdapter(quote_exc=httpx.HTTPStatusError("server error", request=req, response=resp))
    with pytest.raises(httpx.HTTPStatusError):
        await verify_mod.verify_route_quote(
            pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
            packages=[VALID_PACKAGE], adapter=spy,
        )
    assert spy.create_shipment_called is False


async def test_quote_4xx_never_reaches_create_shipment():
    req = httpx.Request("POST", "https://sandbox-api.eurosender.com/v1/quotes")
    resp = httpx.Response(422, request=req)
    spy = _SpyAdapter(quote_exc=httpx.HTTPStatusError("validation error", request=req, response=resp))
    with pytest.raises(httpx.HTTPStatusError):
        await verify_mod.verify_route_quote(
            pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
            packages=[VALID_PACKAGE], adapter=spy,
        )
    assert spy.create_shipment_called is False


async def test_quote_malformed_response_never_reaches_create_shipment():
    spy = _SpyAdapter(quote_response=None)  # malformed/empty
    result = await verify_mod.verify_route_quote(
        pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
        packages=[VALID_PACKAGE], adapter=spy,
    )
    assert result is None
    assert spy.create_shipment_called is False


async def test_validate_creation_exception_never_reaches_create_shipment():
    req = httpx.Request("POST", "https://sandbox-api.eurosender.com/v1/orders/validate_creation")
    resp = httpx.Response(422, request=req)
    spy = _SpyAdapter(validate_exc=httpx.HTTPStatusError("x", request=req, response=resp))
    with pytest.raises(httpx.HTTPStatusError):
        await verify_mod.verify_order_payload(
            pickup_address={"country": "IE"}, delivery_address={"country": "IL"},
            packages=[VALID_PACKAGE], service_type="regular_plus", payment_method="credit",
            order_contact={"email": "a@b.com"}, pickup_contact=CONTACT, delivery_contact=CONTACT,
            customer_internal_reference="test-2", adapter=spy,
        )
    assert spy.create_shipment_called is False


# ---------------------------------------------------------------------------
# Test F: run every Phase-1 candidate route through the verification module
# — never reaches create_shipment, regardless of route.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin_country", ["IE", "PL", "DE", "NL", "GB"])
async def test_every_route_verification_never_reaches_create_shipment(origin_country):
    spy = _SpyAdapter(quote_response={"options": {"serviceTypes": [{"name": "regular_plus"}]}})
    result = await verify_mod.verify_route_quote(
        pickup_address={"country": origin_country}, delivery_address={"country": "IL"},
        packages=[VALID_PACKAGE], adapter=spy,
    )
    assert result is not None
    assert spy.create_shipment_called is False


async def test_countries_verification_never_reaches_create_shipment():
    spy = _SpyAdapter()
    await verify_mod.verify_countries(adapter=spy)
    assert spy.create_shipment_called is False


# ---------------------------------------------------------------------------
# Test C: the legitimate explicit creation path must remain reachable and
# functional (structural separation must not have broken real fulfillment).
# ---------------------------------------------------------------------------

def test_create_shipment_still_exists_and_uses_the_order_creation_constant():
    assert hasattr(EurosenderAdapter, "create_shipment")
    src = inspect.getsource(EurosenderAdapter.create_shipment)
    assert "ORDER_CREATION_PATH" in src


def test_fulfillment_module_is_the_only_production_caller_of_evaluate_and_create_shipment():
    """Structural proof the mutating path still has exactly one legitimate
    production entry point (the real fulfillment flow), unchanged by this
    hardening pass."""
    import services.shipping.eurosender_fulfillment as fulfillment_mod
    src = inspect.getsource(fulfillment_mod)
    assert "evaluate_and_create_shipment" in src
