"""WhatsApp quiet-hours queue: validation, permanent vs transient failures, overflow, dead letters
(FIXES_TRACKER #70).

Runs the REAL `_wa_send_quiet`, the REAL enqueue Lua script and the REAL `_flush_wa_quiet_queue`
against a REAL, ISOLATED Redis (the sandbox's `sandbox_redis`) under per-test keys, deleted
afterwards. The bridge send is replaced by a recorder — nothing reaches WhatsApp. Refuses to run
unless ENVIRONMENT is sandbox/test and the Redis host is not production's.

Expected values are written out here, independently of the code under test.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_URL = os.getenv("REDIS_URL", "")
_SAFE = (os.getenv("ENVIRONMENT", "").lower() in ("sandbox", "test")
         and "sandbox_redis" in _URL and "autospare_redis" not in _URL)
pytestmark = pytest.mark.skipif(not _SAFE, reason="needs the isolated sandbox Redis (never production)")

BANNER = "🌙 [נשלח מאוחר — נשמר משעות הלילה "


@pytest.fixture
def env(monkeypatch):
    import BACKEND_API_ROUTES as app
    import redis.asyncio as aioredis
    tag = uuid.uuid4().hex[:10]
    monkeypatch.setattr(app, "_WA_QUIET_QUEUE_KEY", f"test:{tag}:wa_quiet_queue")
    monkeypatch.setattr(app, "_WA_QUIET_DEAD_KEY", f"test:{tag}:wa_quiet_dead")
    sent, notified, window = [], [], {"open": False}
    outcomes = []                                        # scripted results for _wa_send, in order

    async def fake_send(to, text, reply_jid=""):
        sent.append({"to": to, "text": text})
        return outcomes.pop(0) if outcomes else {"ok": True}

    async def fake_notify(category, title, body="", **kw):
        notified.append({"title": title, "body": body, **kw})

    monkeypatch.setattr(app, "_wa_send", fake_send)
    monkeypatch.setattr(app, "notify_owner", fake_notify)
    monkeypatch.setattr(app, "_notify_window_open", lambda now_local=None: (window["open"], datetime.now()))

    class E:
        pass
    e = E()
    e.app, e.sent, e.notified, e.window, e.outcomes = app, sent, notified, window, outcomes

    def run(coro_fn):
        async def _main():
            r = aioredis.from_url(_URL, decode_responses=True)

            async def _get():
                return r
            monkeypatch.setattr(app, "get_redis", _get)
            e.r = r
            try:
                return await coro_fn()
            finally:
                await r.delete(app._WA_QUIET_QUEUE_KEY, app._WA_QUIET_DEAD_KEY)
                await r.aclose()
        return asyncio.run(_main())
    e.run = run
    return e


async def _queue(e):
    return [json.loads(x) for x in await e.r.lrange(e.app._WA_QUIET_QUEUE_KEY, 0, -1)]


async def _dead(e):
    return [json.loads(x) for x in await e.r.lrange(e.app._WA_QUIET_DEAD_KEY, 0, -1)]


# ── 1. an unusable message is refused at the door, day or night ─────────────────────────────
@pytest.mark.parametrize("open_", [False, True])
@pytest.mark.parametrize("text", ["", "   ", "\n\t", None])
def test_empty_text_is_never_queued_or_sent(env, open_, text):
    env.window["open"] = open_

    async def go():
        res = await env.app._wa_send_quiet("+972500000001", text)
        return res, await _queue(env)
    res, q = env.run(go)
    assert res == {"ok": False, "error": "INVALID_PAYLOAD: empty body"}
    assert q == [] and env.sent == []


def test_missing_recipient_is_refused(env):
    async def go():
        return await env.app._wa_send_quiet("  ", "hello"), await _queue(env)
    res, q = env.run(go)
    assert res == {"ok": False, "error": "INVALID_PAYLOAD: no recipient"} and q == [] and env.sent == []


# ── 2. the normal night → morning path still works ──────────────────────────────────────────
def test_night_message_is_queued_then_delivered_with_banner(env):
    async def go():
        r1 = await env.app._wa_send_quiet("+972500000001", "שורה ראשונה\nשורה שנייה")
        q1 = await _queue(env)
        env.window["open"] = True
        n = await env.app._flush_wa_quiet_queue()
        return r1, q1, n, await _queue(env), await _dead(env)
    r1, q1, n, q2, dead = env.run(go)
    assert r1 == {"ok": True, "queued": True}
    assert len(q1) == 1 and q1[0]["text"] == "שורה ראשונה\nשורה שנייה"
    assert n == 1 and q2 == [] and dead == []
    assert env.sent[0]["to"] == "+972500000001"
    assert env.sent[0]["text"].startswith(BANNER) and env.sent[0]["text"].endswith("]\nשורה ראשונה\nשורה שנייה")


# ── 3. a legacy empty entry is dropped, recorded, and does not hold the queue ───────────────
def test_queued_empty_entry_is_dropped_never_sent_as_a_bare_banner(env):
    env.window["open"] = True

    async def go():
        k = env.app._WA_QUIET_QUEUE_KEY
        await env.r.rpush(k, json.dumps({"to": "+972500000001", "text": "  ", "queued_at": "20/10 03:00"}))
        await env.r.rpush(k, json.dumps({"to": "+972500000001", "text": "real alert", "queued_at": "20/10 03:05"}))
        n = await env.app._flush_wa_quiet_queue()
        return n, await _queue(env), await _dead(env)
    n, q, dead = env.run(go)
    assert n == 1 and q == []
    assert [s["text"].split("\n", 1)[1] for s in env.sent] == ["real alert"]       # never a bare banner
    assert len(dead) == 1 and dead[0]["reason"] == "invalid_payload" and dead[0]["text_len"] == 2
    assert "text" not in dead[0]                                                  # metadata only


# ── 4. permanent vs transient failures ──────────────────────────────────────────────────────
def test_permanent_error_is_dropped_at_once_and_the_rest_is_delivered(env):
    env.window["open"] = True
    env.outcomes.extend([{"ok": False, "error": "INVALID_PAYLOAD: no recipient"}, {"ok": True}])

    async def go():
        k = env.app._WA_QUIET_QUEUE_KEY
        for t in ("first", "second"):
            await env.r.rpush(k, json.dumps({"to": "+972500000001", "text": t, "queued_at": "x"}))
        n = await env.app._flush_wa_quiet_queue()
        return n, await _queue(env), await _dead(env)
    n, q, dead = env.run(go)
    assert n == 1 and q == [] and len(env.sent) == 2
    assert dead[0]["reason"] == "permanent_error" and dead[0]["error"] == "INVALID_PAYLOAD: no recipient"


def test_transient_error_keeps_fifo_and_retries_then_records_exhaustion(env):
    env.window["open"] = True
    err = {"ok": False, "error": "bridge unreachable: x"}

    async def go():
        k = env.app._WA_QUIET_QUEUE_KEY
        for t in ("head", "behind"):
            await env.r.rpush(k, json.dumps({"to": "+972500000001", "text": t, "queued_at": "x"}))
        attempts_seen = []
        for _ in range(env.app._WA_QUIET_MAX_ATTEMPTS - 1):
            env.outcomes.append(dict(err))
            assert await env.app._flush_wa_quiet_queue() == 0
            q = await _queue(env)
            attempts_seen.append(q[0]["attempts"])
            assert [i["text"] for i in q] == ["head", "behind"]                     # FIFO kept, nothing lost
        env.outcomes.extend([dict(err), {"ok": True}])
        n = await env.app._flush_wa_quiet_queue()
        return attempts_seen, n, await _queue(env), await _dead(env)
    attempts_seen, n, q, dead = env.run(go)
    assert attempts_seen == [1, 2, 3, 4]
    assert n == 1 and q == []
    assert dead[0]["reason"] == "retry_exhausted" and dead[0]["attempts"] == 5
    assert [s["text"].split("\n", 1)[1] for s in env.sent][-1] == "behind"


# ── 5. overflow is recorded, not silent ─────────────────────────────────────────────────────
def test_overflow_drops_the_oldest_and_records_each_drop(env):
    async def go():
        for i in range(53):
            assert (await env.app._wa_send_quiet("+972500000123", f"msg {i:02d}"))["queued"] is True
        return await _queue(env), await _dead(env)
    q, dead = env.run(go)
    assert len(q) == 50 and q[0]["text"] == "msg 03" and q[-1]["text"] == "msg 52"
    assert len(dead) == 3 and {d["reason"] for d in dead} == {"overflow"}
    assert sorted(d["text_len"] for d in dead) == [6, 6, 6] and {d["to_tail"] for d in dead} == {"0123"}
    assert all("text" not in d for d in dead)


def test_alert_key_dedup_still_keeps_one_entry_per_condition(env):
    async def go():
        for i in range(4):
            await env.app._wa_send_quiet("+972500000001", f"stall #{i}", alert_key="harvester_stall")
        return await _queue(env), await _dead(env)
    q, dead = env.run(go)
    assert [i["text"] for i in q] == ["stall #3"] and dead == []


# ── 6. the owner hears about drops once a day ───────────────────────────────────────────────
def test_dead_letter_summary_counts_only_the_last_24h():
    import BACKEND_API_ROUTES as app
    now = 1_800_000_000
    rows = [json.dumps({"reason": "overflow", "dropped_at": now - 10}),
            json.dumps({"reason": "overflow", "dropped_at": now - 3600}),
            json.dumps({"reason": "retry_exhausted", "dropped_at": now - 90_000}),   # older than 24 h
            "not json"]
    assert app._wa_dead_letter_summary(rows, now) == {"overflow": 2}
    assert app._wa_dead_letter_summary([], now) is None


def test_flush_reports_drops_to_the_owner_with_a_daily_key(env):
    env.window["open"] = True

    async def go():
        await env.r.rpush(env.app._WA_QUIET_QUEUE_KEY, json.dumps({"to": "", "text": "x", "queued_at": "y"}))
        await env.app._flush_wa_quiet_queue()
    env.run(go)
    assert len(env.notified) == 1
    n = env.notified[0]
    assert n["title"] == "1 התראות WhatsApp לא נמסרו ב-24 השעות האחרונות"
    assert "הודעה ריקה או ללא נמען: 1" in n["body"]
    assert n["alert_key"].startswith("wa_dead_letter_") and n["cooldown_s"] == 86400
