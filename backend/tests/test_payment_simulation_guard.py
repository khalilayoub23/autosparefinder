from starlette.requests import Request

from routes.payments import _allow_simulated_payments_for_request


def _make_request(headers: dict[str, str], scheme: str = "http") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": scheme,
        "path": "/api/v1/payments/create-checkout",
        "raw_path": b"/api/v1/payments/create-checkout",
        "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("backend", 8000),
    }
    return Request(scope)


def test_public_origin_blocks_simulation_by_default(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATED_PAYMENTS", "1")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ALLOW_SIMULATED_PAYMENTS_PUBLIC", raising=False)

    req = _make_request({"origin": "https://autosparefinder.co.il"})
    assert _allow_simulated_payments_for_request(req) is False


def test_internal_host_allows_simulation_when_enabled(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATED_PAYMENTS", "1")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ALLOW_SIMULATED_PAYMENTS_PUBLIC", raising=False)

    req = _make_request({"host": "frontend"})
    assert _allow_simulated_payments_for_request(req) is True


def test_public_simulation_can_be_explicitly_overridden(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATED_PAYMENTS", "1")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_SIMULATED_PAYMENTS_PUBLIC", "1")

    req = _make_request({"origin": "https://autosparefinder.co.il"})
    assert _allow_simulated_payments_for_request(req) is True


def test_global_simulation_off_always_blocks(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATED_PAYMENTS", "0")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_SIMULATED_PAYMENTS_PUBLIC", "1")

    req = _make_request({"host": "frontend"})
    assert _allow_simulated_payments_for_request(req) is False


def test_production_blocks_internal_simulation_without_public_override(monkeypatch):
    monkeypatch.setenv("ALLOW_SIMULATED_PAYMENTS", "1")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ALLOW_SIMULATED_PAYMENTS_PUBLIC", raising=False)

    req = _make_request({"host": "frontend"})
    assert _allow_simulated_payments_for_request(req) is False
