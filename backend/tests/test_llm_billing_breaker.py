"""Provider chain + billing/limit breaker for the production LLM path (rewritten 2026-10-27).

Owner decision 2026-10-27: Cerebras will not be enabled again. Production chain:
  HF Router pinned to DeepInfra (gpt-oss-120b)  ->  Groq gpt-oss-120b  ->  Groq GROQ_MODEL.
These tests replace the Cerebras-402 breaker tests (FIXES_TRACKER #69 §12): the same failure class
(credits exhausted / auth / rate limit on the paid primary) now applies to the HF primary, whose
breaker is hf_client._hf_primary_down_until (HF_PRIMARY_COOLDOWN_S).
"""
import asyncio
import json
import os
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hf_client as hc  # noqa: E402


def _resp(url, status, content="hf ok", message=None):
    msg = message if message is not None else {"content": content}
    return httpx.Response(status, json={"choices": [{"message": msg}], "usage": {"total_tokens": 7}},
                          request=httpx.Request("POST", url))


@pytest.fixture
def rig(monkeypatch):
    calls = {"hf": 0, "cerebras": 0, "groq": [], "hf_models": []}
    state = {"status": 200, "content": "hf ok", "message": None, "groq_fail_120b": False}
    monkeypatch.setattr(hc, "LLM_PRIMARY_PROVIDER", "hf")
    monkeypatch.setattr(hc, "HF_PRIMARY_PROVIDER", "deepinfra")
    monkeypatch.setattr(hc, "HF_TOKEN", "hf-test")
    monkeypatch.setattr(hc, "CEREBRAS_ENABLED", False)
    monkeypatch.setattr(hc, "CEREBRAS_API_KEY", "")
    monkeypatch.setattr(hc, "GROQ_API_KEY", "g")
    monkeypatch.setattr(hc, "GEMINI_API_KEY", "")
    monkeypatch.setattr(hc, "_hf_primary_down_until", 0.0)
    monkeypatch.setattr(hc, "_hf_billing_marked", False)

    async def _post(url, headers, body, timeout, label):
        if url.startswith(hc.CEREBRAS_BASE):
            calls["cerebras"] += 1
            return _resp(url, 200, "cerebras answered")
        assert url == f"{hc.ROUTER_BASE}/chat/completions"
        calls["hf"] += 1
        calls["hf_models"].append(json.loads(body)["model"])
        return _resp(url, state["status"], state["content"], state["message"])

    async def _groq(prompt="", system="", timeout=60.0, model=""):
        calls["groq"].append(model or "GROQ_MODEL")
        if model == "openai/gpt-oss-120b" and state["groq_fail_120b"]:
            raise RuntimeError("groq 120b throttled")
        return f"groq ok ({model or 'GROQ_MODEL'})"

    async def _no_cache(*a, **k):
        return None

    class _NoRedis:                      # the billing marker must never reach a real Redis in tests
        async def set(self, *a, **k):
            return None

    async def _get_redis():
        return _NoRedis()
    import BACKEND_AUTH_SECURITY
    monkeypatch.setattr(BACKEND_AUTH_SECURITY, "get_redis", _get_redis)
    monkeypatch.setattr(hc, "_post_with_retry", _post)
    monkeypatch.setattr(hc, "groq_text", _groq)
    monkeypatch.setattr(hc, "_cache_get", _no_cache)
    monkeypatch.setattr(hc, "_cache_set", _no_cache)
    return calls, state


def test_hf_primary_answers_with_the_pinned_provider_model(rig):
    calls, _ = rig
    assert asyncio.run(hc.hf_text("hi")) == "hf ok"
    assert calls["hf_models"] == ["openai/gpt-oss-120b:deepinfra"]
    assert calls["groq"] == [] and calls["cerebras"] == 0


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_billing_auth_or_limit_falls_to_groq_and_trips_the_breaker(rig, status):
    calls, state = rig
    state["status"] = status
    assert asyncio.run(hc.hf_text("hi")) == "groq ok (openai/gpt-oss-120b)"
    assert calls["hf"] == 1 and calls["cerebras"] == 0
    assert hc._hf_primary_down_until > time.time()


def test_while_the_breaker_is_open_hf_is_not_called_at_all(rig):
    calls, state = rig
    state["status"] = 402
    asyncio.run(hc.hf_text("one"))
    asyncio.run(hc.hf_text("two"))
    assert calls["hf"] == 1 and len(calls["groq"]) == 2 and calls["cerebras"] == 0


@pytest.mark.parametrize("status", [400, 500, 503])
def test_other_errors_fall_back_without_tripping_the_breaker(rig, status):
    calls, state = rig
    state["status"] = status
    assert asyncio.run(hc.hf_text("hi")).startswith("groq ok")
    assert hc._hf_primary_down_until == 0.0


def test_breaker_expires(rig, monkeypatch):
    calls, state = rig
    state["status"] = 402
    asyncio.run(hc.hf_text("hi"))
    monkeypatch.setattr(hc, "_hf_primary_down_until", 0.0)
    state["status"] = 200
    assert asyncio.run(hc.hf_text("again")) == "hf ok"
    assert calls["hf"] == 2


def test_empty_hf_content_never_returns_reasoning(rig):
    """gpt-oss can spend the budget on reasoning and return content="": that must not reach the
    customer as chain-of-thought — the answer comes from the fallback instead."""
    calls, state = rig
    state["message"] = {"content": "", "reasoning": "The user wants... let's think"}
    out = asyncio.run(hc.hf_text("hi"))
    assert out.startswith("groq ok") and "let's think" not in out


def test_groq_120b_failure_uses_groq_model(rig):
    calls, state = rig
    state["status"] = 402
    state["groq_fail_120b"] = True
    assert asyncio.run(hc.hf_text("hi")) == "groq ok (GROQ_MODEL)"
    assert calls["groq"] == ["openai/gpt-oss-120b", "GROQ_MODEL"]


def test_provider_is_traced(rig):
    _, state = rig
    trace = {}
    hc.LLM_CALL_TRACE.set(trace)
    asyncio.run(hc.hf_text("a"))
    assert trace["provider"] == "hf:deepinfra"
    state["status"] = 402
    trace.clear()
    asyncio.run(hc.hf_text("b"))
    assert trace["provider"] == "groq:openai/gpt-oss-120b"


def test_non_gpt_oss_model_skips_hf_and_never_reaches_cerebras(rig):
    calls, _ = rig
    assert asyncio.run(hc.hf_text("hi", model="zai-glm-4.7")).startswith("groq ok")
    assert calls["hf"] == 0 and calls["cerebras"] == 0


def test_no_provider_configured_raises(monkeypatch):
    monkeypatch.setattr(hc, "CEREBRAS_API_KEY", "")
    monkeypatch.setattr(hc, "HF_TOKEN", "")
    monkeypatch.setattr(hc, "GROQ_API_KEY", "")
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        asyncio.run(hc.hf_text("hi"))


def test_health_monitor_turns_the_marker_into_a_daily_owner_alert():
    """Static check of the (now dormant) Cerebras billing alert code — kept while that code exists."""
    src = open(os.path.join(os.path.dirname(hc.__file__), "BACKEND_API_ROUTES.py"), encoding="utf-8").read()
    i = src.index("autospare:llm:cerebras_payment_required")
    block = src[i:i + 900]
    assert hc._CEREBRAS_BILLING_REDIS_KEY == "autospare:llm:cerebras_payment_required"
    assert "_alert_owner(" in block and "cooldown_s=86400" in block


# ── owner alert when the paid primary is blocked (2026-10-27, owner: "both" — alert + credit) ──

@pytest.fixture
def marker(monkeypatch):
    store = {}

    class _R:
        async def set(self, k, v, ex=None):
            store[k] = (v, ex)

    async def _get_redis():
        return _R()
    import BACKEND_AUTH_SECURITY
    monkeypatch.setattr(BACKEND_AUTH_SECURITY, "get_redis", _get_redis)
    return store


@pytest.mark.parametrize("status,marked", [(402, True), (401, True), (403, True), (429, False), (500, False), (200, False)])
def test_hf_billing_or_token_failure_leaves_the_owner_alert_marker(rig, marker, status, marked):
    _, state = rig
    state["status"] = status
    asyncio.run(hc.hf_text("hi"))
    if marked:
        assert marker[hc.HF_BILLING_REDIS_KEY] == (str(status), 3600)
    else:
        assert hc.HF_BILLING_REDIS_KEY not in marker


def test_health_monitor_alert_block_for_hf():
    src = open(os.path.join(os.path.dirname(hc.__file__), "BACKEND_API_ROUTES.py"), encoding="utf-8").read()
    i = src.index('"autospare:llm:hf_primary_blocked"')
    block = src[i:i + 1400]
    assert hc.HF_BILLING_REDIS_KEY == "autospare:llm:hf_primary_blocked"
    assert "_alert_owner(" in block and "llm_hf_primary_blocked" in block
    assert "huggingface.co/settings/billing" in block


# ── one alert per occurrence (owner 2026-10-27: "only when reached", not daily) ──

def test_alert_fires_once_and_rearms_only_when_hf_answers_again(rig, monkeypatch):
    calls, state = rig
    store = {}

    class _R:
        async def set(self, k, v, ex=None):
            store[k] = v

        async def delete(self, *keys):
            for k in keys:
                store.pop(k, None)

    async def _get_redis():
        return _R()
    import BACKEND_AUTH_SECURITY
    monkeypatch.setattr(BACKEND_AUTH_SECURITY, "get_redis", _get_redis)
    monkeypatch.setattr(hc, "_hf_billing_marked", False)
    state["status"] = 402
    asyncio.run(hc.hf_text("a"))
    assert store[hc.HF_BILLING_REDIS_KEY] == "402"
    store[hc.HF_BILLING_ANNOUNCED_KEY] = "1"            # the health monitor told the owner
    monkeypatch.setattr(hc, "_hf_primary_down_until", 0.0)
    asyncio.run(hc.hf_text("b"))                          # still blocked: no re-arm
    assert hc.HF_BILLING_ANNOUNCED_KEY in store
    monkeypatch.setattr(hc, "_hf_primary_down_until", 0.0)
    state["status"] = 200
    asyncio.run(hc.hf_text("c"))                          # recovered: marker + announced cleared
    assert hc.HF_BILLING_REDIS_KEY not in store and hc.HF_BILLING_ANNOUNCED_KEY not in store


def test_health_monitor_alerts_once_per_occurrence_not_daily():
    src = open(os.path.join(os.path.dirname(hc.__file__), "BACKEND_API_ROUTES.py"), encoding="utf-8").read()
    i = src.index('"autospare:llm:hf_primary_blocked"')
    block = src[i - 400:i + 1600]
    assert 'not await _rb.exists("autospare:llm:hf_primary_blocked:announced")' in block
    assert 'await _rb.set("autospare:llm:hf_primary_blocked:announced", "1")' in block
    assert "cooldown_s=86400" not in block
    assert hc.HF_BILLING_ANNOUNCED_KEY == "autospare:llm:hf_primary_blocked:announced"
