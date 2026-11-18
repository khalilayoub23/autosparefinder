"""2026-11-18: every COLLECT_SECRET-protected relay handler FAILS CLOSED when the secret is unset (it used to accept any
request: `if _COLLECT_SECRET and header != _COLLECT_SECRET`)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import system


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(system.router)
    return TestClient(app, raise_server_exceptions=False)


ENDPOINTS = [
    ("post", "/api/v1/system/collect", {"vehicle": "bmw/x5", "parts": [], "done": False}),
    ("post", "/api/v1/system/unpriced-oems", {"brand": "bmw"}),
    ("get", "/api/v1/system/collect/import-status?vehicle=bmw/x5&run_id=r1", None),
    ("post", "/api/v1/system/asap-collect", {"brand_id": "1", "csv": "x" * 40}),
]


def _call(c, method, url, body, headers=None):
    return c.get(url, headers=headers or {}) if method == "get" else c.post(url, json=body, headers=headers or {})


@pytest.mark.parametrize("method,url,body", ENDPOINTS)
def test_unset_secret_authorizes_nobody(client, monkeypatch, method, url, body):
    monkeypatch.setattr(system, "_COLLECT_SECRET", "")
    for hdr in ({}, {"X-Collect-Secret": ""}, {"X-Collect-Secret": "anything"}):
        assert _call(client, method, url, body, hdr).status_code == 403


@pytest.mark.parametrize("method,url,body", ENDPOINTS)
def test_wrong_secret_refused_right_secret_passes_auth(client, monkeypatch, method, url, body):
    monkeypatch.setattr(system, "_COLLECT_SECRET", "s3cret")
    assert _call(client, method, url, body, {"X-Collect-Secret": "nope"}).status_code == 403
    assert _call(client, method, url, body, {}).status_code == 403
    assert _call(client, method, url, body, {"X-Collect-Secret": "s3cret"}).status_code != 403   # past auth (any non-403 answer)


def test_body_secret_text_plain_simple_request_still_works(client, monkeypatch):
    monkeypatch.setattr(system, "_COLLECT_SECRET", "s3cret")
    r = client.post("/api/v1/system/collect", content='{"vehicle":"bmw/x5","parts":[],"secret":"s3cret"}', headers={"Content-Type": "text/plain"})
    assert r.status_code != 403
    r = client.post("/api/v1/system/collect", content='{"vehicle":"bmw/x5","parts":[],"secret":"bad"}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 403
