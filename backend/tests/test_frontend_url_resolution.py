from starlette.requests import Request

from routes.utils import _get_frontend_url


def _make_request(headers: dict[str, str], scheme: str = "http") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": scheme,
        "path": "/api/v1/payments/create-multi-checkout",
        "raw_path": b"/api/v1/payments/create-multi-checkout",
        "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("backend", 8000),
    }
    return Request(scope)


def test_prefers_origin_header(monkeypatch):
    monkeypatch.setenv("FRONTEND_INTERNAL_URL", "http://frontend")
    req = _make_request({"origin": "https://shop.example.com"})
    assert _get_frontend_url(req) == "https://shop.example.com"


def test_uses_referer_when_origin_missing(monkeypatch):
    monkeypatch.setenv("FRONTEND_INTERNAL_URL", "http://frontend")
    req = _make_request({"referer": "https://www.example.com/orders?tab=unpaid"})
    assert _get_frontend_url(req) == "https://www.example.com"


def test_uses_forwarded_public_host_without_origin(monkeypatch):
    monkeypatch.delenv("FRONTEND_PUBLIC_URL", raising=False)
    monkeypatch.setenv("FRONTEND_INTERNAL_URL", "http://frontend")
    req = _make_request(
        {
            "x-forwarded-proto": "https",
            "x-forwarded-host": "autosparefinder.co.il",
            "host": "backend:8000",
        }
    )
    assert _get_frontend_url(req) == "https://autosparefinder.co.il"


def test_uses_internal_url_for_internal_host(monkeypatch):
    monkeypatch.setenv("FRONTEND_INTERNAL_URL", "http://frontend")
    req = _make_request({"host": "frontend"})
    assert _get_frontend_url(req) == "http://frontend"
