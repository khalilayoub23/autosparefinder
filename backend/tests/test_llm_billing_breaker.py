"""Cerebras 402 = billing, not a rate limit (FIXES_TRACKER #69 §12).

A 402 used to be handled like a 429: every call kept trying Cerebras and then the Cerebras
fallback model on the same unpaid account, and nothing alerted the owner.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hf_client as hc  # noqa: E402


class _Resp:
    def __init__(self, status):
        self.status_code = status

    def json(self):
        return {"choices": [{"message": {"content": "cerebras ok"}}]}


@pytest.fixture
def rig(monkeypatch):
    calls = {"cerebras": 0, "cerebras_fallback": 0, "groq": 0, "marker": 0}
    monkeypatch.setattr(hc, "CEREBRAS_API_KEY", "k")
    monkeypatch.setattr(hc, "GROQ_API_KEY", "g")
    monkeypatch.setattr(hc, "GEMINI_API_KEY", "")
    monkeypatch.setattr(hc, "CEREBRAS_FALLBACK_MODEL", "zai-glm-4.7")
    monkeypatch.setattr(hc, "_cerebras_billing_down_until", 0.0)
    status = {"code": 402}

    async def _post(*a, **k):
        calls["cerebras"] += 1
        return _Resp(status["code"])

    async def _fallback(*a, **k):
        calls["cerebras_fallback"] += 1
        return "fallback model"

    async def _groq(*a, **k):
        calls["groq"] += 1
        return "groq ok"

    async def _no_cache(*a, **k):
        return None

    class _R:
        async def set(self, *a, **k):
            calls["marker"] += 1

    async def _get_redis():
        return _R()

    import BACKEND_AUTH_SECURITY
    monkeypatch.setattr(BACKEND_AUTH_SECURITY, "get_redis", _get_redis)
    monkeypatch.setattr(hc, "_post_with_retry", _post)
    monkeypatch.setattr(hc, "_cerebras_call", _fallback)
    monkeypatch.setattr(hc, "groq_text", _groq)
    monkeypatch.setattr(hc, "_cache_get", _no_cache)
    monkeypatch.setattr(hc, "_cache_set", _no_cache)
    return calls, status


def test_402_skips_the_same_account_fallback_model_and_trips_the_breaker(rig):
    calls, _ = rig
    assert asyncio.run(hc.hf_text("hi")) == "groq ok"
    assert calls == {"cerebras": 1, "cerebras_fallback": 0, "groq": 1, "marker": 1}
    assert hc._cerebras_billing_is_down()


def test_while_the_breaker_is_open_cerebras_is_not_called_at_all(rig):
    calls, _ = rig
    asyncio.run(hc.hf_text("one"))
    asyncio.run(hc.hf_text("two"))
    assert calls["cerebras"] == 1 and calls["groq"] == 2


def test_429_is_still_a_rate_limit_and_keeps_the_fallback_model(rig):
    calls, status = rig
    status["code"] = 429
    assert asyncio.run(hc.hf_text("hi")) == "fallback model"
    assert calls["cerebras_fallback"] == 1 and calls["marker"] == 0
    assert not hc._cerebras_billing_is_down()


def test_breaker_expires(rig, monkeypatch):
    calls, status = rig
    asyncio.run(hc.hf_text("hi"))
    monkeypatch.setattr(hc, "_cerebras_billing_down_until", 0.0)
    monkeypatch.setattr(hc, "raise_for_status_safe", lambda r: None)
    status["code"] = 200
    assert asyncio.run(hc.hf_text("again")) == "cerebras ok"
    assert calls["cerebras"] == 2


def test_health_monitor_turns_the_marker_into_a_daily_owner_alert():
    src = open(os.path.join(os.path.dirname(hc.__file__), "BACKEND_API_ROUTES.py"), encoding="utf-8").read()
    i = src.index("autospare:llm:cerebras_payment_required")
    block = src[i:i + 900]
    assert hc._CEREBRAS_BILLING_REDIS_KEY == "autospare:llm:cerebras_payment_required"
    assert "_alert_owner(" in block and "cooldown_s=86400" in block
