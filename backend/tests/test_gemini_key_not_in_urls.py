"""#109 regression: no Gemini API key may appear in a request URL, and no URL credential may reach the logs.

2026-10-23: httpx logs every request at INFO with the full URL; Gemini calls carried the key as `?key=…`, so the live
key was written to `docker logs` in clear text (the error path was already scrubbed; this path was not). Gemini now
authenticates with the `x-goog-api-key` header, and a filter on the httpx/httpcore loggers redacts any `?key=` /
`?token=` style value that still appears in a logged URL. No network: every request is intercepted.
"""
import ast
import asyncio
import logging
import os

import httpx
import pytest

import hf_client

KEY = "test-gemini-key-0123456789"
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hf_client.py")


def test_no_key_query_parameter_is_built_in_hf_client():
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    hits = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "?key=" in n.value and id(n) not in docstrings]
    assert hits == [], f"a URL with ?key= is built at lines {hits}"


class _Resp:
    status_code = 200
    text = '{"ok": true}'
    headers = {}

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]},
                                "groundingMetadata": {"groundingChunks": []}}],
                "embedding": {"values": [0.1] * 384}}

    def raise_for_status(self):
        return None


@pytest.fixture
def captured(monkeypatch):
    calls = []

    async def fake_post_with_retry(url, headers, body, timeout, label, *a, **k):
        calls.append((url, dict(headers)))
        return _Resp()

    class _Http:
        async def post(self, url, headers=None, json=None, **k):
            calls.append((url, dict(headers or {})))
            return _Resp()

    monkeypatch.setattr(hf_client, "GEMINI_API_KEY", KEY)
    monkeypatch.setattr(hf_client, "WHATSAPP_GEMINI_KEY", KEY)
    monkeypatch.setattr(hf_client, "_post_with_retry", fake_post_with_retry)
    monkeypatch.setattr(hf_client, "_get_http", lambda: _Http())
    monkeypatch.setattr(hf_client, "_gemini_cb_open_until", 0, raising=False)
    return calls


@pytest.mark.parametrize("call", [
    lambda: hf_client.gemini_text("hello"),
    lambda: hf_client.whatsapp_gemini_text("hello"),
    lambda: hf_client.gemini_web_search("brake pads"),
    lambda: hf_client.hf_vision("aGVsbG8=", "what is this"),
    lambda: hf_client.hf_embed("brake pads"),
], ids=["gemini_text", "whatsapp_gemini_text", "gemini_web_search", "hf_vision", "hf_embed"])
def test_every_gemini_call_sends_the_key_in_the_header_only(captured, call):
    try:
        asyncio.run(call())
    except Exception:
        pass                                   # response parsing is not under test; the request is
    gemini = [(u, h) for u, h in captured if "generativelanguage.googleapis.com" in u]
    assert gemini, "the function made no Gemini request - the test proves nothing"
    for url, headers in gemini:
        assert "key=" not in url and KEY not in url, url
        assert headers.get("x-goog-api-key") == KEY


def test_a_url_credential_in_an_httpx_log_line_is_redacted(caplog):
    def handler(request):
        return httpx.Response(200, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await c.get("https://example.invalid/v1/models?key=SECRET-ABC123&pageSize=5")
            await c.get("https://example.invalid/v1/x?access_token=SECRET-TOK456")
    with caplog.at_level(logging.INFO, logger="httpx"):
        asyncio.run(go())
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("httpx"))
    assert "HTTP Request" in text, "httpx logged no request line - the test proves nothing"
    assert "SECRET-ABC123" not in text and "SECRET-TOK456" not in text
    assert "key=<redacted>" in text and "access_token=<redacted>" in text
    assert "pageSize=5" in text                          # the rest of the URL is kept for debugging
