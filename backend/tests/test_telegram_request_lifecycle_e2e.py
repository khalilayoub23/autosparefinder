"""Telegram chat requests: the same durable lifecycle as WhatsApp, end to end through the REAL Telegram webhook route
on the sandbox only (FIXES_TRACKER #74). Before #74 the Telegram path cancelled the agent at 12 s and asked the
customer to resend, sent "a representative will call you back" on any error (no such mechanism), fired the reply
as an untracked task while returning delivered=True, and dropped every update while Redis was unavailable.
Harness: tests/chat_lifecycle_harness.py (synthetic chat id; transport replaced by a recorder).
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chat_lifecycle_harness import (REFUSAL, TG_DOWN, TG_TIMEOUT, OK, answer_rows, customer, run_case, spec,  # noqa: E402
                                    tg_update_id)

pytestmark = pytest.mark.skipif(REFUSAL is not None, reason=f"isolated DB only: {REFUSAL}")


def _wh():
    import routes.webhooks as wh
    return wh


def _one(res):
    (rid, rec), = res["recs"].items()
    return rid, rec


def test_TG1_fast_answer_under_the_wait():
    async def steps(x):
        await x.post("מסנן שמן לקורולה")
    res = run_case("tg-fast", "tg", steps)
    rid, rec = _one(res)
    assert customer(res, "tg") == ["ANSWER to: מסנן שמן לקורולה"] and res["calls"] == ["מסנן שמן לקורולה"]
    assert rec["status"] == "answered" and rec["delivery"] == "sent" and rec["history"] == "synced"
    assert [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]


def test_TG2_slow_answer_is_not_cancelled_status_note_then_the_answer():
    wh = _wh()

    async def steps(x):
        await x.post("רפידות בלם לספורטאז'")
    res = run_case("tg-slow", "tg", steps, agent=lambda m, n: spec(delay=0.8))
    rid, rec = _one(res)
    assert customer(res, "tg") == [wh._WAIT_TEXT["he"], "ANSWER to: רפידות בלם לספורטאז'"]
    assert "שלח שוב את שאלתך" not in "".join(res["sent"])               # the old "please resend"
    assert res["before"][rid]["status"] == "processing" and rec["status_note"] == "sent"
    assert rec["answer"] == "ANSWER to: רפידות בלם לספורטאז'" and res["calls"] == ["רפידות בלם לספורטאז'"]


def test_TG3a_exception_honest_failure_no_false_promise():
    wh = _wh()

    async def steps(x):
        await x.post("מראה צד")
    res = run_case("tg-exception", "tg", steps, agent=lambda m, n: spec(raise_before=True))
    rid, rec = _one(res)
    assert customer(res, "tg") == [wh._LATE_FAIL_TEXT["he"]]
    assert "נציג שירות יחזור אליך" not in "".join(res["sent"])           # no invented call-back
    assert rec["status"] == "failed" and rec["reason"] == "exception:RuntimeError"
    rows = [h for h in answer_rows(res["hist"], rid) if h["a"].get("chat_kind") == "failure"]
    assert len(rows) == 1 and rows[0]["a"]["delivery"] == "sent"


def test_TG3b_empty_answer_is_a_failure():
    wh = _wh()

    async def steps(x):
        await x.post("חיישן חמצן")
    res = run_case("tg-empty", "tg", steps, agent=lambda m, n: spec(response=""))
    rid, rec = _one(res)
    assert customer(res, "tg") == [wh._LATE_FAIL_TEXT["he"]] and rec["reason"] == "empty_answer"
    assert not any(t.strip() == "" for t in res["sent"])


def test_TG4_process_interruption_after_persistence_is_recovered_once():
    wh = _wh()

    async def steps(x):
        await x.post("משאבת מים")
        await asyncio.sleep(0.4)                                         # still inside the agent, nothing saved
        await x.die()
        await x.recover()
    res = run_case("tg-interrupted", "tg", steps, agent=lambda m, n: spec(delay=1.5) if n == 1 else spec())
    rid, rec = _one(res)
    assert customer(res, "tg") == [wh._WAIT_TEXT["he"], "ANSWER to: משאבת מים"]
    assert rec["status"] == "answered" and rec["attempts"] == "2" and rec["recovered"] == "1"
    assert [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]   # one logical turn


def test_TG4b_interruption_after_the_turn_was_saved_reuses_it():
    async def steps(x):
        await x.post("משאבת מים")
        await asyncio.sleep(0.6)
        await x.die()
        await x.recover()
    res = run_case("tg-saved-then-died", "tg", steps, agent=lambda m, n: spec(delay=0.4, after_commit="hang"))
    rid, rec = _one(res)
    assert res["calls"] == ["משאבת מים"] and rec["status"] == "answered" and rec["attempts"] == "1"
    assert [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]


def test_TG5_answer_generated_before_delivery_then_restart_delivers_without_the_agent():
    wh = _wh()
    state = {"down": True}

    def send(text, n):
        return TG_DOWN if text.startswith("ANSWER") and state["down"] else dict(OK)

    async def steps(x):
        await x.post("מסנן דלק")
        await asyncio.sleep(0.3)
        await x.die()
        state["down"] = False
        await x.recover()
    res = run_case("tg-answer-undelivered", "tg", steps, send=send, retry_delays=(5.0,),
                   patches=[(wh, "WA_FAST_DELIVERY_WAIT_S", 0.1)])
    rid, rec = _one(res)
    assert res["calls"] == ["מסנן דלק"] and rec["delivery"] == "sent" and rec["delivery_attempts"] == "2"


def test_TG6_transport_outage_and_recovery_bounded_retries():
    def send(text, n):
        return TG_DOWN if text.startswith("ANSWER") and n <= 2 else dict(OK)

    async def steps(x):
        await x.post("מסנן דלק")
    res = run_case("tg-outage", "tg", steps, send=send)
    rid, rec = _one(res)
    assert rec["delivery"] == "sent" and rec["delivery_attempts"] == "3"
    assert answer_rows(res["hist"], rid)[-1]["a"]["delivery"] == "sent"


def test_TG6b_outage_throughout_is_failed_not_delivered():
    def send(text, n):
        return TG_DOWN if text.startswith("ANSWER") else dict(OK)

    async def steps(x):
        await x.post("מסנן דלק")
    res = run_case("tg-outage-throughout", "tg", steps, send=send)
    rid, rec = _one(res)
    assert rec["status"] == "answered" and rec["delivery"] == "failed" and rec["delivery_attempts"] == "4"


def test_TG7_ambiguous_delivery_timeout_is_unknown_never_retried():
    def send(text, n):
        return TG_TIMEOUT if text.startswith("ANSWER") else dict(OK)

    async def steps(x):
        await x.post("מסנן דלק")
    res = run_case("tg-timeout", "tg", steps, send=send)
    rid, rec = _one(res)
    assert rec["delivery"] == "unknown" and rec["delivery_attempts"] == "1"
    assert res["sent"].count("ANSWER to: מסנן דלק") == 1
    assert answer_rows(res["hist"], rid)[-1]["a"]["delivery"] == "unknown"


def test_TG8_duplicate_update_and_retry_after_restart():
    async def steps(x):
        u = tg_update_id()
        await x.post("מצבר", u)
        await x.post("מצבר", u)                                         # Telegram retried the webhook
        await x.die()
        await x.post("מצבר", u)                                         # …and again after a restart
        u2 = tg_update_id()
        await x.r.set(f"tg:update:{u2}", "deadboot", ex=3600)           # taken by a process that died early
        await x.post("רדיאטור", u2)
    res = run_case("tg-duplicate", "tg", steps)
    assert res["calls"] == ["מצבר", "רדיאטור"]
    assert customer(res, "tg") == ["ANSWER to: מצבר", "ANSWER to: רדיאטור"]


def test_TG9_redis_unavailable_does_not_drop_the_message():
    import BACKEND_AUTH_SECURITY as sec
    real = sec.get_redis
    state = {"calls": 0}

    async def flaky():
        state["calls"] += 1
        if state["calls"] == 2:                                          # the webhook's dedup lookup
            raise ConnectionError("redis down")
        return await real()

    async def steps(x):
        await x.post("מסנן שמן")
    res = run_case("tg-redis-down", "tg", steps, patches=[(sec, "get_redis", flaky)])
    assert res["calls"] == ["מסנן שמן"] and customer(res, "tg") == ["ANSWER to: מסנן שמן"]


def test_TG10_overlapping_slow_requests_each_answer_goes_with_its_update():
    async def steps(x):
        await x.post("רפידות בלם")
        await x.post("מצבר")
    res = run_case("tg-overlap", "tg", steps, agent=lambda m, n: spec(delay=0.9 if n == 1 else 0.5))
    a, b = list(res["recs"])
    assert res["recs"][a]["answer"] == "ANSWER to: רפידות בלם" and res["recs"][b]["answer"] == "ANSWER to: מצבר"
    for rid, q in ((a, "רפידות בלם"), (b, "מצבר")):
        assert [r["content"] for r in answer_rows(res["hist"], rid)] == [q, f"ANSWER to: {q}"]


def test_TG11_marked_processing_with_no_live_worker_is_recovered():
    async def steps(x):
        await x.post("מסנן שמן")                                            # opens the conversation
        cid = await x.conv_id()
        rid = x.rid_for(tg_update_id())
        await x.creq.create(x.r, rid, channel="tg", target="990000000779", user_key="u", conv_id=cid,
                            body="מסנן אוויר", lang="he")
        await x.creq.update(x.r, rid, owner="deadboot", status="processing", attempts="1")
        x.rids.append(rid)
        x.before[rid] = await x.rec(rid)
        await x.recover()
    res = run_case("tg-no-worker", "tg", steps)
    rid = list(res["recs"])[1]
    rec = res["recs"][rid]
    assert res["before"][rid]["status"] == "processing"
    assert rec["status"] == "answered" and rec["recovered"] == "1" and rec["attempts"] == "2"
    assert "ANSWER to: מסנן אוויר" in res["sent"]


def test_TG12_real_timing_13s_agent_past_the_real_12s_wait():
    """Unscaled: the production 12 s wait and 120 s deadline, an agent that takes 13 s."""
    wh = _wh()

    async def steps(x):
        await x.post("משאבת מים לקורולה")
    res = run_case("tg-real-13s", "tg", steps, agent=lambda m, n: spec(delay=13.0), wait_s=12.0, deadline_s=120.0,
                   settle_s=2.0)
    rid, rec = _one(res)
    assert customer(res, "tg") == [wh._WAIT_TEXT["he"], "ANSWER to: משאבת מים לקורולה"]
    assert rec["status"] == "answered" and rec["delivery"] == "sent" and rec["status_note"] == "sent"
    t_note, t_ans = float(rec["created_at"]), float(rec["answered_at"])
    assert 12.5 < t_ans - t_note < 15.0                                          # answered after the agent's 13 s
