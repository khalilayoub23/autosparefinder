"""AliExpress IOP OAuth lifecycle regression suite (no network, no real DB, no real authorization).

Covers: authorize URL, callback (GET/POST), code exchange, encryption, refresh (+failure cooldown),
legacy-endpoint isolation, IOP API-client compatibility, and secret hygiene in logs/responses.
DB-backed persistence (real Postgres row, cross-process reload) lives in
devtests/aliexpress_token_persistence_test.py and is run alongside this suite.
"""
import hashlib
import hmac
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI

import routes.auth as ra
import routes.suppliers as rs
import services.suppliers.aliexpress_supplier as m
from services.suppliers.aliexpress_supplier import AliExpressSupplier

APP_KEY, APP_SECRET = "546482", "SECRET_" + "s" * 25
ACC, REF = "TESTACCESS_" + "a" * 30, "TESTREFRESH_" + "r" * 30
ACC2, REF2 = "TESTACCESS2_" + "b" * 30, "TESTREFRESH2_" + "q" * 30
CODE = "TESTCODE_" + "c" * 20
ALL_SECRETS = [APP_SECRET, ACC, REF, ACC2, REF2, CODE]
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ALIEXPRESS_APP_KEY", APP_KEY)
    monkeypatch.setenv("ALIEXPRESS_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("ALIEXPRESS_ACCESS_TOKEN", "")
    monkeypatch.setenv("ALIEXPRESS_REFRESH_TOKEN", "")
    monkeypatch.setenv("ALIEXPRESS_TOKEN_EXPIRE", "0")
    monkeypatch.setenv("ENCRYPTION_KEY", "k" * 64)
    m.AliExpressSupplier._refresh_fail_digest, m.AliExpressSupplier._refresh_fail_until = "", 0.0


class FakeStore:
    """In-memory stand-in for the encrypted supplier-row store (real one: devtests integration)."""
    def __init__(self):
        self.saved = None

    def install(self, monkeypatch):
        async def persist(inst, a, r, e):
            self.saved = (a, r, int(e or 0))

        async def load(inst):
            if not self.saved:
                return False
            inst._access_token, inst._refresh_token, inst._token_expire = self.saved
            return True
        monkeypatch.setattr(AliExpressSupplier, "persist_tokens", persist)
        monkeypatch.setattr(AliExpressSupplier, "_load_persisted", load)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ra.router)
    app.include_router(rs.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _exchange_ok(monkeypatch, exp=None):
    async def ex(self, code):
        assert code == CODE
        return {"access_token": ACC, "refresh_token": REF, "expire_time": exp or int(time.time() * 1000) + 86400000}
    monkeypatch.setattr(AliExpressSupplier, "exchange_code_for_token", ex)


# ---------------------------------------------------------------- A. authorization URL
def test_authorize_url_is_iop_only():
    url = AliExpressSupplier().get_oauth_url()
    u = urlparse(url)
    q = parse_qs(u.query)
    assert (u.scheme, u.netloc, u.path) == ("https", "api-sg.aliexpress.com", "/oauth/authorize")
    assert q == {"response_type": ["code"], "client_id": [APP_KEY],
                 "redirect_uri": ["https://autosparefinder.co.il/api/aliexpress/callback"]}
    assert "redirect_uri=https%3A%2F%2Fautosparefinder.co.il%2Fapi%2Faliexpress%2Fcallback" in url
    for bad in ("sp=", "view=", "force_auth", "oauth.aliexpress.com"):
        assert bad not in url


# ---------------------------------------------------------------- B. callback
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("path", ["/api/aliexpress/callback", "/api/suppliers/aliexpress/callback"])
async def test_callback_missing_code_400(client, method, path):
    r = await client.request(method, path)
    assert r.status_code == 400 and "missing code" in r.text


async def test_callback_error_param_escaped(client):
    r = await client.get("/api/aliexpress/callback", params={"error": "<script>x</script>"})
    assert r.status_code == 400 and "<script>" not in r.text and "&lt;script&gt;" in r.text


@pytest.mark.parametrize("how", ["get", "post_form", "post_json", "post_query"])
async def test_callback_success_all_methods_masked(client, monkeypatch, how):
    store = FakeStore(); store.install(monkeypatch); _exchange_ok(monkeypatch)
    p = "/api/aliexpress/callback"
    r = {"get": lambda: client.get(p, params={"code": CODE}),
         "post_form": lambda: client.post(p, data={"code": CODE}),
         "post_json": lambda: client.post(p, json={"code": CODE}),
         "post_query": lambda: client.post(p + f"?code={CODE}")}[how]
    resp = await r()
    assert resp.status_code == 200 and "stored (encrypted)" in resp.text
    assert not any(s in resp.text for s in ALL_SECRETS)
    assert store.saved[:2] == (ACC, REF) and store.saved[2] > 0      # all three persisted


async def test_callback_exchange_failures_are_sanitized(client, monkeypatch):
    async def bad(self, code):
        raise ValueError("Token exchange failed: code=InvalidCode message=Invalid authorization code")
    monkeypatch.setattr(AliExpressSupplier, "exchange_code_for_token", bad)
    r = await client.get("/api/aliexpress/callback", params={"code": CODE})
    assert r.status_code == 502 and "InvalidCode" in r.text and CODE not in r.text

    async def boom(self, code):
        raise httpx.ConnectError(f"conn failed for {CODE}")
    monkeypatch.setattr(AliExpressSupplier, "exchange_code_for_token", boom)
    r = await client.post("/api/aliexpress/callback", data={"code": CODE})
    assert r.status_code == 502 and "ConnectError" in r.text and CODE not in r.text


async def test_callback_persistence_failure_500_no_tokens(client, monkeypatch):
    _exchange_ok(monkeypatch)
    async def fail(self, a, r, e):
        raise RuntimeError(f"db down {ACC} {REF}")
    monkeypatch.setattr(AliExpressSupplier, "persist_tokens", fail)
    r = await client.get("/api/aliexpress/callback", params={"code": CODE})
    assert r.status_code == 500 and "RuntimeError" in r.text and not any(s in r.text for s in ALL_SECRETS)


async def test_no_second_callback_implementation():
    src = (ROOT / "routes" / "auth.py").read_text()
    assert "exchange_code_for_token" not in src and "aliexpress.system.oauth" not in src
    assert "aliexpress_callback_request" in src                    # thin alias only


# ---------------------------------------------------------------- C. token exchange
class _Resp:
    def __init__(self, d, status=200): self._d, self.status_code = d, status
    def json(self): return self._d
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


def _capture_post(monkeypatch, reply):
    seen = {}
    async def post(self, url, data=None, **kw):
        seen["url"], seen["data"] = url, dict(data or {})
        return reply
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return seen


async def test_exchange_uses_iop_create_and_valid_signature(monkeypatch):
    seen = _capture_post(monkeypatch, _Resp({"access_token": ACC, "refresh_token": REF, "expire_time": 1, "code": "0"}))
    out = await AliExpressSupplier().exchange_code_for_token(CODE)
    assert out["access_token"] == ACC
    assert seen["url"] == "https://api-sg.aliexpress.com/rest/auth/token/create"
    d = dict(seen["data"]); sign = d.pop("sign")
    exp = hmac.new(APP_SECRET.encode(), ("/auth/token/create" + "".join(f"{k}{v}" for k, v in sorted(d.items()))).encode(),
                   hashlib.sha256).hexdigest().upper()
    assert sign == exp and d["app_key"] == APP_KEY and d["code"] == CODE


async def test_exchange_failure_raises_sanitized(monkeypatch):
    _capture_post(monkeypatch, _Resp({"code": "InvalidCode", "type": "ISP", "message": "Invalid authorization code",
                                      "request_id": "x"}))
    with pytest.raises(ValueError) as e:
        await AliExpressSupplier().exchange_code_for_token(CODE)
    assert "InvalidCode" in str(e.value) and CODE not in str(e.value) and APP_SECRET not in str(e.value)


# ---------------------------------------------------------------- D. encryption primitive
def test_fernet_ciphertext_hides_tokens_and_requires_key(monkeypatch):
    blob = m._fernet().encrypt(json.dumps({"access_token": ACC, "refresh_token": REF}).encode()).decode()
    assert ACC not in blob and REF not in blob
    assert json.loads(m._fernet().decrypt(blob.encode()))["refresh_token"] == REF
    monkeypatch.delenv("ENCRYPTION_KEY")
    with pytest.raises(RuntimeError):
        m._fernet()


# ---------------------------------------------------------------- E. refresh
async def test_refresh_uses_iop_persisted_credentials_and_repersists(monkeypatch):
    store = FakeStore(); store.saved = (ACC, REF, int(time.time() * 1000) - 1000)
    store.install(monkeypatch)
    seen = _capture_post(monkeypatch, _Resp({"access_token": ACC2, "refresh_token": REF2,
                                             "expire_time": int(time.time() * 1000) + 86400000}))
    s = AliExpressSupplier()
    assert await s._ensure_token()                                   # loads persisted, expired -> refresh
    assert seen["url"] == "https://api-sg.aliexpress.com/rest/auth/token/refresh"
    assert seen["data"]["refresh_token"] == REF                      # from persisted store, not env
    assert store.saved[:2] == (ACC2, REF2)                           # re-persisted via same store
    d = dict(seen["data"]); sign = d.pop("sign")
    assert sign == hmac.new(APP_SECRET.encode(), ("/auth/token/refresh" + "".join(f"{k}{v}" for k, v in sorted(d.items()))).encode(),
                            hashlib.sha256).hexdigest().upper()


async def test_refresh_failure_backs_off_then_retries_for_new_refresh_token(monkeypatch):
    store = FakeStore(); store.saved = (ACC, REF, int(time.time() * 1000) - 1000); store.install(monkeypatch)
    calls = {"n": 0}
    async def post(self, url, data=None, **kw):
        calls["n"] += 1
        return _Resp({"code": "IllegalRefreshToken", "message": "invalid or expired"})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    s = AliExpressSupplier()
    await s._load_persisted()                                            # refresh token comes from the store
    assert await s._auto_refresh_token() is False and calls["n"] == 1
    assert await s._auto_refresh_token() is False and calls["n"] == 1   # cooldown: no hammering
    s._refresh_token = REF2                                              # re-authorized -> new token tried at once
    await s._auto_refresh_token()
    assert calls["n"] == 2
    assert store.saved[:2] == (ACC, REF)                                 # failed refresh never overwrites store


async def test_no_refresh_token_returns_false(monkeypatch):
    assert await AliExpressSupplier()._auto_refresh_token() is False


# ---------------------------------------------------------------- F. legacy isolation
def _code_lines(path):
    return [l for l in path.read_text().splitlines() if not l.strip().startswith("#")]

def test_no_active_legacy_oauth_path():
    files = [ROOT / "services/suppliers/aliexpress_supplier.py", ROOT / "routes/suppliers.py", ROOT / "routes/auth.py",
             ROOT / "services/aliexpress_price_sync.py", ROOT / "services/supplier_aggregator.py"]
    for f in files:
        for line in _code_lines(f):
            assert "oauth.aliexpress.com" not in line, (f.name, line)
            assert "aliexpress.system.oauth" not in line, (f.name, line)
    assert m.ALIEXPRESS_AUTH_URL.startswith("https://api-sg.aliexpress.com/")
    for u in (m.ALIEXPRESS_TOKEN_URL, m.ALIEXPRESS_REFRESH_URL, m.ALIEXPRESS_API_URL):
        assert u.startswith("https://api-sg.aliexpress.com/")


# ---------------------------------------------------------------- G. IOP API client compatibility
def test_api_request_signing_unchanged():
    s = AliExpressSupplier(); s._access_token = ACC
    p = s._build_request("aliexpress.ds.text.search", {"keyWord": "x"})
    sign = p.pop("sign")
    assert p["method"] == "aliexpress.ds.text.search" and p["access_token"] == ACC and p["sign_method"] == "sha256"
    assert sign == hmac.new(APP_SECRET.encode(), "".join(f"{k}{v}" for k, v in sorted(p.items())).encode(),
                            hashlib.sha256).hexdigest().upper()
    assert m.ALIEXPRESS_API_URL == "https://api-sg.aliexpress.com/sync"


# ---------------------------------------------------------------- H. security: logs + responses
async def test_no_secrets_in_logs_or_responses(client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    store = FakeStore(); store.saved = (ACC, REF, int(time.time() * 1000) - 1000); store.install(monkeypatch)
    async def post(self, url, data=None, **kw):
        return _Resp({"code": "IllegalRefreshToken", "message": "bad", "access_token_echo": ACC2})
    with monkeypatch.context() as mp:                                    # scope the patch: the ASGI test client also uses post
        mp.setattr(httpx.AsyncClient, "post", post)
        await AliExpressSupplier()._ensure_token()
    _exchange_ok(monkeypatch)
    bodies = [(await client.get("/api/aliexpress/callback", params={"code": CODE})).text,
              (await client.post("/api/aliexpress/callback", data={"code": CODE})).text]
    # httpx logs the TEST client's own request line (http://t/...); that is a harness artifact, not app output
    app_logs = " ".join(r.getMessage() for r in caplog.records if "http://t/" not in r.getMessage())
    blob = app_logs + " ".join(bodies)
    assert not any(s in blob for s in ALL_SECRETS)


def test_uvicorn_access_log_redacts_oauth_query():
    """The real exposure path: uvicorn access-log request line for the callback."""
    lg = logging.getLogger("uvicorn.access")
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:1", "GET", f"/api/aliexpress/callback?code={CODE}&state=abc", "1.1", 200), None)
    for f in lg.filters:
        f.filter(rec)
    out = rec.getMessage()
    assert CODE not in out and "code=<redacted>" in out and "state=<redacted>" in out
    other = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s "%s %s"',
                              ("x", "GET", "/api/v1/parts/search?code=keep"), None)
    for f in lg.filters:
        f.filter(other)
    assert "code=keep" in other.getMessage()                 # unrelated paths untouched
