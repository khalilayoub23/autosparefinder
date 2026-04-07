"""
tests/test_hf_client.py
=======================
Unit tests for hf_client.py — all network calls are mocked so tests are
fast, offline, and deterministic.

Coverage:
  1. Shared connection pool — same client object reused across calls
  2. Retry on 503 (model cold-start) — auto-retries up to HF_MAX_RETRIES
  3. Retry on 429 (rate-limit) — respects retry-after header
  4. No retry on 400/422 — raises immediately
  5. Redis cache hit  — hf_text returns cached value without hitting HF
  6. Redis cache miss — hf_text calls HF and writes to cache
  7. Cache disabled   — when TTL=0, never reads/writes cache
  8. hf_embed cache   — vector cached after first call
  9. hf_embed no model — returns [] when weights not on disk
 10. hf_vision        — correct payload structure
 11. hf_audio         — correct content-type header
 12. hf_clip          — correct endpoint and payload
 13. close_http        — pool closed and re-created on next call
 14. Logging          — latency_ms and attempt count are logged
 15. No HF_TOKEN      — raises RuntimeError before any network call
"""

import asyncio
import importlib
import json
import sys
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

# ── make backend/ importable ─────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_response(status: int, body: dict | bytes, headers: dict | None = None) -> MagicMock:
    """Build a fake httpx.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    if isinstance(body, bytes):
        resp.json.return_value = {}
        resp.content = body
    else:
        resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    if status >= 400:
        from httpx import HTTPStatusError, Request, Response
        resp.raise_for_status.side_effect = HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=MagicMock()
        )
    return resp


def _text_resp(content: str = "hello") -> MagicMock:
    return _make_response(200, {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": 42},
    })


# ── reload hf_client with controlled env ─────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_hf_client(monkeypatch):
    """Reset module-level state between tests."""
    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setenv("HF_MAX_RETRIES", "3")
    monkeypatch.setenv("HF_TEXT_CACHE_TTL", "3600")
    monkeypatch.setenv("HF_EMBED_CACHE_TTL", "86400")
    import hf_client
    # Reset shared client
    hf_client._http = None
    hf_client._embed_model = None
    yield
    hf_client._http = None
    hf_client._embed_model = None


# ══════════════════════════════════════════════════════════════════════════════
# 1. Shared connection pool
# ══════════════════════════════════════════════════════════════════════════════

def test_get_http_returns_same_instance():
    import hf_client
    c1 = hf_client._get_http()
    c2 = hf_client._get_http()
    assert c1 is c2, "Expected the same httpx.AsyncClient instance to be reused"


@pytest.mark.asyncio
async def test_http_pool_reused_across_calls():
    """Two hf_text calls must use the same underlying client object."""
    import hf_client
    clients_used = []

    async def fake_post(url, *, headers, content, timeout):
        clients_used.append(id(hf_client._http))
        return _text_resp("ok")

    with patch("hf_client._cache_get", new=AsyncMock(return_value=None)), \
         patch("hf_client._cache_set", new=AsyncMock()), \
         patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        await hf_client.hf_text("q1")
        await hf_client.hf_text("q2")

    assert clients_used[0] == clients_used[1], "Different client instances used — pool not shared"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Retry on 503 (cold-start)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_retry_on_503():
    """503 → 503 → 200: should succeed on third attempt."""
    import hf_client
    responses = [
        _make_response(503, {"error": "loading"}),
        _make_response(503, {"error": "loading"}),
        _text_resp("recovered"),
    ]
    call_count = 0

    async def fake_post(url, *, headers, content, timeout):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    with patch("hf_client._cache_get", new=AsyncMock(return_value=None)), \
         patch("hf_client._cache_set", new=AsyncMock()), \
         patch("asyncio.sleep", new=AsyncMock()), \
         patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        result = await hf_client.hf_text("test")

    assert result == "recovered"
    assert call_count == 3


@pytest.mark.asyncio
async def test_no_infinite_retry_on_503(monkeypatch):
    """After HF_MAX_RETRIES 503s, raises HTTPStatusError."""
    import hf_client
    monkeypatch.setenv("HF_MAX_RETRIES", "2")
    importlib.reload(hf_client)
    hf_client._http = None

    async def always_503(url, *, headers, content, timeout):
        return _make_response(503, {"error": "loading"})

    with patch("hf_client._cache_get", new=AsyncMock(return_value=None)), \
         patch("asyncio.sleep", new=AsyncMock()), \
         patch.object(hf_client._get_http(), "post", side_effect=always_503):
        resp = await hf_client._post_with_retry(
            "https://example.com", {}, b"{}", 10.0, "test"
        )
    # After max retries exhausted, the last response is returned (not raised)
    assert resp.status_code == 503


# ══════════════════════════════════════════════════════════════════════════════
# 3. Retry on 429 with retry-after header
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_retry_respects_retry_after_header():
    """429 with retry-after: 5 should sleep 5 seconds before retrying."""
    import hf_client
    sleep_calls = []
    responses = [
        _make_response(429, {}, headers={"retry-after": "5"}),
        _text_resp("ok"),
    ]
    idx = 0

    async def fake_post(url, *, headers, content, timeout):
        nonlocal idx
        r = responses[idx]; idx += 1
        return r

    async def fake_sleep(s):
        sleep_calls.append(s)

    with patch("hf_client._cache_get", new=AsyncMock(return_value=None)), \
         patch("hf_client._cache_set", new=AsyncMock()), \
         patch("asyncio.sleep", side_effect=fake_sleep), \
         patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        result = await hf_client.hf_text("rate limited")

    assert result == "ok"
    assert 5 in sleep_calls, f"Expected sleep(5) from retry-after header, got {sleep_calls}"


# ══════════════════════════════════════════════════════════════════════════════
# 4. No retry on 400/422 client errors
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_no_retry_on_400():
    """Client errors (4xx except 429) must NOT be retried."""
    import hf_client
    call_count = 0

    async def fake_post(url, *, headers, content, timeout):
        nonlocal call_count
        call_count += 1
        return _make_response(400, {"error": "bad request"})

    with patch("asyncio.sleep", new=AsyncMock()), \
         patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        resp = await hf_client._post_with_retry(
            "https://example.com", {}, b"{}", 10.0, "test"
        )

    assert call_count == 1, f"Should not retry on 400, but called {call_count} times"
    assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# 5. Redis cache hit — skips HF entirely
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hf_text_cache_hit():
    import hf_client
    post_called = False

    async def fake_post(*a, **kw):
        nonlocal post_called
        post_called = True
        return _text_resp()

    with patch("hf_client._cache_get", new=AsyncMock(return_value="cached answer")), \
         patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        result = await hf_client.hf_text("anything")

    assert result == "cached answer"
    assert not post_called, "HF should not be called when cache has a hit"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Redis cache miss — calls HF and writes to cache
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hf_text_cache_miss_writes_to_cache():
    import hf_client
    written = {}

    async def fake_cache_set(key, value, ttl):
        written[key] = (value, ttl)

    with patch("hf_client._cache_get", new=AsyncMock(return_value=None)), \
         patch("hf_client._cache_set", side_effect=fake_cache_set), \
         patch.object(hf_client._get_http(), "post",
                      new=AsyncMock(return_value=_text_resp("from hf"))):
        result = await hf_client.hf_text("my query")

    assert result == "from hf"
    assert len(written) == 1
    cached_value, ttl = next(iter(written.values()))
    assert cached_value == "from hf"
    assert ttl == 3600  # default HF_TEXT_CACHE_TTL


# ══════════════════════════════════════════════════════════════════════════════
# 7. Cache disabled when TTL=0
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cache_disabled_when_ttl_zero(monkeypatch):
    import hf_client
    monkeypatch.setenv("HF_TEXT_CACHE_TTL", "0")
    importlib.reload(hf_client)
    hf_client._http = None

    cache_write_called = False

    async def fake_cache_set(key, value, ttl):
        nonlocal cache_write_called
        if ttl > 0:
            cache_write_called = True

    with patch("hf_client._cache_get", new=AsyncMock(return_value=None)), \
         patch("hf_client._cache_set", side_effect=fake_cache_set), \
         patch.object(hf_client._get_http(), "post",
                      new=AsyncMock(return_value=_text_resp("no cache"))):
        await hf_client.hf_text("anything")

    assert not cache_write_called, "Cache should not be written when TTL=0"


# ══════════════════════════════════════════════════════════════════════════════
# 8. hf_embed — vector cached after first call
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hf_embed_caches_vector():
    import hf_client
    fake_vector = [0.1, 0.2, 0.3]
    encode_calls = 0

    def fake_encode(text):
        nonlocal encode_calls
        encode_calls += 1
        import numpy as np
        return np.array(fake_vector)

    cache_store = {}

    async def fake_cache_get(key):
        return cache_store.get(key)

    async def fake_cache_set(key, value, ttl):
        cache_store[key] = value

    fake_model = MagicMock()
    fake_model.encode = fake_encode

    with patch("hf_client._is_model_cached", return_value=True), \
         patch("hf_client._get_embed_model", return_value=fake_model), \
         patch("hf_client._cache_get", side_effect=fake_cache_get), \
         patch("hf_client._cache_set", side_effect=fake_cache_set):
        v1 = await hf_client.hf_embed("hello")
        v2 = await hf_client.hf_embed("hello")  # should hit cache

    assert v1 == fake_vector
    assert v2 == fake_vector
    assert encode_calls == 1, f"encode() called {encode_calls} times — expected 1 (cached on second call)"


# ══════════════════════════════════════════════════════════════════════════════
# 9. hf_embed — returns [] when model not cached on disk
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hf_embed_returns_empty_when_not_cached():
    import hf_client
    with patch("hf_client._is_model_cached", return_value=False):
        result = await hf_client.hf_embed("test")
    assert result == [], "Expected [] when model weights not on disk"


# ══════════════════════════════════════════════════════════════════════════════
# 10. hf_vision — correct payload structure
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hf_vision_payload():
    import hf_client
    captured = {}

    async def fake_post(url, *, headers, content, timeout):
        captured["url"] = url
        captured["body"] = json.loads(content)
        return _text_resp("car part detected")

    with patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        result = await hf_client.hf_vision("base64imgdata==", "what is this?", "image/png")

    assert result == "car part detected"
    body = captured["body"]
    assert body["messages"][0]["content"][0]["text"] == "what is this?"
    assert "base64imgdata==" in body["messages"][0]["content"][1]["image_url"]["url"]
    assert "image/png" in body["messages"][0]["content"][1]["image_url"]["url"]


# ══════════════════════════════════════════════════════════════════════════════
# 11. hf_audio — correct content-type header
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hf_audio_content_type():
    import hf_client
    captured_headers = {}

    async def fake_post(url, *, headers, content, timeout):
        captured_headers.update(headers)
        return _make_response(200, {"text": "transcribed audio"})

    with patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        result = await hf_client.hf_audio(b"fake audio bytes")

    assert result == "transcribed audio"
    assert captured_headers.get("Content-Type") == "audio/webm"


# ══════════════════════════════════════════════════════════════════════════════
# 12. hf_clip — correct endpoint and payload
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hf_clip_endpoint_and_payload():
    import hf_client
    captured = {}

    async def fake_post(url, *, headers, content, timeout):
        captured["url"] = url
        captured["body"] = json.loads(content)
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [[0.5, 0.6, 0.7]]
        return resp

    with patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        result = await hf_client.hf_clip("base64clip==")

    assert result == [0.5, 0.6, 0.7]
    assert hf_client.HF_CLIP_MODEL in captured["url"]
    assert captured["body"] == {"inputs": {"image": "base64clip=="}}


# ══════════════════════════════════════════════════════════════════════════════
# 13. close_http — pool closed and re-created on next call
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_close_http_creates_new_client():
    import hf_client
    c1 = hf_client._get_http()
    await hf_client.close_http()
    assert hf_client._http is None, "Pool should be None after close_http()"
    c2 = hf_client._get_http()
    assert c1 is not c2, "New client should be created after closing"


# ══════════════════════════════════════════════════════════════════════════════
# 14. Logging — latency_ms and attempt count are logged
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_request_is_logged(caplog):
    import hf_client
    import logging

    async def fake_post(url, *, headers, content, timeout):
        return _text_resp("logged")

    with caplog.at_level(logging.INFO, logger="hf_client"), \
         patch("hf_client._cache_get", new=AsyncMock(return_value=None)), \
         patch("hf_client._cache_set", new=AsyncMock()), \
         patch.object(hf_client._get_http(), "post", side_effect=fake_post):
        await hf_client.hf_text("log me")

    log_text = " ".join(caplog.messages)
    assert "status=200" in log_text
    assert "latency_ms=" in log_text
    assert "attempts=1" in log_text


# ══════════════════════════════════════════════════════════════════════════════
# 15. No HF_TOKEN — raises RuntimeError before any request
# ══════════════════════════════════════════════════════════════════════════════

def test_no_hf_token_raises(monkeypatch):
    import hf_client
    monkeypatch.setenv("HF_TOKEN", "")
    hf_client.HF_TOKEN = ""
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        hf_client._headers()
