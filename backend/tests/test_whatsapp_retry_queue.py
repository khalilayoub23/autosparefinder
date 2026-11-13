"""Failed IMMEDIATE WhatsApp sends (window open, or severity=critical) must not be lost.

Defect (Phase 2, 2026-11-13): `_wa_send_quiet` returned the provider's `ok=False` straight to the
caller when it sent immediately. A critical alert (DB down, crashed supervisor task) or any
in-window alert hit by a bridge outage longer than the provider's 15 s retry budget vanished -
only the persistent-condition callers happened to re-detect it. Fix: a send that provably did
NOT leave (bridge unreachable / transient 503) goes to a retry queue, delivered by the health
monitor pass; critical items go out even in quiet hours, others wait for the window.

Real `_wa_send_quiet`, real enqueue Lua, real flush against the isolated sandbox Redis; the bridge
call is a scripted recorder. Nothing reaches WhatsApp.
"""
import json

import pytest

from tests.test_whatsapp_quiet_queue import env, _queue, _dead  # noqa: F401  (fixture reuse)

UNREACHABLE = {"ok": False, "error": "bridge unreachable: connect failed"}
TRANSIENT = {"ok": False, "error": "WhatsApp disconnected — reconnecting"}


@pytest.fixture
def renv(env, monkeypatch):
    monkeypatch.setattr(env.app, "_WA_RETRY_QUEUE_KEY", env.app._WA_QUIET_QUEUE_KEY + ":retry")
    return env


async def _retry(e):
    return [json.loads(x) for x in await e.r.lrange(e.app._WA_RETRY_QUEUE_KEY, 0, -1)]


def _cleanup(e):
    async def go():
        await e.r.delete(e.app._WA_RETRY_QUEUE_KEY)
    return go


def test_critical_send_failure_is_queued_not_lost(renv):
    e = renv
    e.window["open"] = False
    e.outcomes.append(UNREACHABLE)

    async def go():
        res = await e.app._wa_send_quiet("+972500000001", "DB down", critical=True, alert_key="db")
        q = await _retry(e)
        n_night = await _queue(e)
        await e.r.delete(e.app._WA_RETRY_QUEUE_KEY)
        return res, q, n_night
    res, q, night = e.run(go)
    assert res["ok"] is True and res["queued"] is True
    assert len(q) == 1 and q[0]["critical"] is True and q[0]["alert_key"] == "db" and q[0]["text"] == "DB down"
    assert night == []                                   # not mixed into the night queue


def test_retry_delivers_critical_in_quiet_hours_without_night_banner(renv):
    e = renv
    e.window["open"] = False

    async def go():
        e.outcomes.append(TRANSIENT)
        await e.app._wa_send_quiet("+972500000001", "DB down", critical=True, alert_key="db")
        n = await e.app._flush_wa_retry_queue()
        left = await _retry(e)
        await e.r.delete(e.app._WA_RETRY_QUEUE_KEY)
        return n, left
    n, left = e.run(go)
    assert n == 1 and left == []
    assert e.sent[-1]["text"] == "DB down"               # raw text: it is not a "sent late from the night" message
    assert len(e.sent) == 2                              # first attempt + retry


def test_noncritical_retry_waits_for_the_window(renv):
    e = renv
    e.window["open"] = True

    async def go():
        e.outcomes.append(UNREACHABLE)
        await e.app._wa_send_quiet("+972500000001", "info alert", alert_key="k")
        e.window["open"] = False                          # the window closed before the retry pass
        n_closed = await e.app._flush_wa_retry_queue()
        kept = await _retry(e)
        e.window["open"] = True
        n_open = await e.app._flush_wa_retry_queue()
        left = await _retry(e)
        await e.r.delete(e.app._WA_RETRY_QUEUE_KEY)
        return n_closed, kept, n_open, left
    n_closed, kept, n_open, left = e.run(go)
    assert n_closed == 0 and len(kept) == 1
    assert n_open == 1 and left == []


def test_ambiguous_failures_are_not_retried_to_avoid_duplicates(renv):
    e = renv
    e.window["open"] = True

    async def go():
        out = []
        for err in ("timed out", "HTTP 500", "WhatsApp error: rate-overlimit"):
            e.outcomes.append({"ok": False, "error": err})
            out.append(await e.app._wa_send_quiet("+972500000001", "x", alert_key="a"))
        q = await _retry(e)
        return out, q
    out, q = e.run(go)
    assert all(r["ok"] is False and not r.get("queued") for r in out) and q == []   # message may already be out


def test_retry_exhaustion_dead_letters_and_same_key_replaces(renv):
    e = renv
    e.window["open"] = True

    async def go():
        e.outcomes.append(UNREACHABLE)
        await e.app._wa_send_quiet("+972500000001", "v1", alert_key="same")
        e.outcomes.append(UNREACHABLE)
        await e.app._wa_send_quiet("+972500000001", "v2", alert_key="same")
        q1 = await _retry(e)
        for _ in range(e.app._WA_QUIET_MAX_ATTEMPTS):
            e.outcomes.append(UNREACHABLE)
            await e.app._flush_wa_retry_queue()
        left = await _retry(e)
        dead = await _dead(e)
        await e.r.delete(e.app._WA_RETRY_QUEUE_KEY)
        return q1, left, dead
    q1, left, dead = e.run(go)
    assert len(q1) == 1 and q1[0]["text"] == "v2"
    assert left == [] and [d["reason"] for d in dead] == ["retry_exhausted"]
    assert "text" not in dead[0]


def test_expired_retry_is_dead_lettered_not_sent(renv, monkeypatch):
    e = renv
    e.window["open"] = True

    async def go():
        stale = {"to": "+972500000001", "text": "old", "critical": True, "attempts": 0,
                 "queued_ts": 1, "alert_key": "old"}
        await e.r.rpush(e.app._WA_RETRY_QUEUE_KEY, json.dumps(stale))
        n = await e.app._flush_wa_retry_queue()
        left, dead = await _retry(e), await _dead(e)
        await e.r.delete(e.app._WA_RETRY_QUEUE_KEY)
        return n, left, dead
    n, left, dead = e.run(go)
    assert n == 0 and left == [] and [d["reason"] for d in dead] == ["expired"] and e.sent == []


def test_success_path_unchanged(renv):
    e = renv
    e.window["open"] = True

    async def go():
        res = await e.app._wa_send_quiet("+972500000001", "ok now", alert_key="k")
        q = await _retry(e)
        await e.r.delete(e.app._WA_RETRY_QUEUE_KEY)
        return res, q
    res, q = e.run(go)
    assert res == {"ok": True} and q == [] and e.sent[0]["text"] == "ok now"
