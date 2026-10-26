"""WhatsApp chat requests: durable lifecycle, recovery idempotency and failure history, end to end through the REAL
webhook route on the sandbox only (FIXES_TRACKER #73/#74). Harness: tests/chat_lifecycle_harness.py.
Timings scaled: the 12 s wait is 0.3 s, the 120 s deadline 1.5 s, the delivery back-off 0.1 s. Run with -s for
the per-request EVIDENCE lines (request id, record state, history rows, customer-visible sends).
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chat_lifecycle_harness import (REFUSAL, WA_DOWN, WA_TIMEOUT, OK, answer_rows, customer, run_case, spec, uid)

pytestmark = pytest.mark.skipif(REFUSAL is not None, reason=f"isolated DB only: {REFUSAL}")


def _wh():
    import routes.webhooks as wh
    return wh


def _one(res):
    (rid, rec), = res["recs"].items()
    return rid, rec


def _pick(res, tag):
    rid = [r for r in res["recs"] if tag in r][0]
    return rid, res["recs"][rid]


async def _inject(x, tag, body="q", **fields):
    """A request persisted by a process that then died (owner without heartbeat)."""
    cid = await x.conv_id()
    rid = x.rid_for(uid(tag))
    await x.creq.create(x.r, rid, channel="wa", target="972500000779", user_key=str(x.wh.WHATSAPP_ANON_USER_ID),
                        conv_id=cid, body=body, lang="he", claim_body=body)
    await x.creq.update(x.r, rid, owner="deadboot", **fields)
    x.rids.append(rid)
    x.before[rid] = await x.rec(rid)
    return rid


# ── baseline lifecycle ───────────────────────────────────────────────────────────────────────────────
def test_L1_fast_answer_only_reply_recorded_and_delivery_in_history():
    async def steps(x):
        await x.post("מסנן שמן")
    res = run_case("fast", "wa", steps)
    rid, rec = _one(res)
    assert customer(res) == ["ANSWER to: מסנן שמן"] and res["calls"] == ["מסנן שמן"]
    assert rec["status"] == "answered" and rec["delivery"] == "sent" and rec["history"] == "synced"
    assert not rec.get("status_note") and rid not in res["open"]
    rows = answer_rows(res["hist"], rid)
    assert [r["role"] for r in rows] == ["user", "assistant"] and rows[1]["a"]["delivery"] == "sent"


def test_L2_slow_status_note_then_the_answer_note_is_not_the_answer():
    wh = _wh()

    async def steps(x):
        await x.post("מסנן שמן לקורולה")
    res = run_case("slow", "wa", steps, agent=lambda m, n: spec(delay=0.8))
    rid, rec = _one(res)
    assert customer(res) == [wh._WAIT_TEXT["he"], "ANSWER to: מסנן שמן לקורולה"]
    assert res["before"][rid]["status"] == "processing" and res["before"][rid]["delivery"] == "none"
    assert rec["status_note"] == "sent" and rec["delivered_kind"] == "answer" and rec["delivery"] == "sent"


def test_L3_two_slow_questions_each_answer_correlated_to_its_request():
    async def steps(x):
        await x.post("רפידות בלם")
        await x.post("מצבר לגולף")
    res = run_case("correlation", "wa", steps, agent=lambda m, n: spec(delay=0.8))
    a, b = list(res["recs"])
    assert res["recs"][a]["answer"] == "ANSWER to: רפידות בלם" and res["recs"][b]["answer"] == "ANSWER to: מצבר לגולף"
    for rid, q in ((a, "רפידות בלם"), (b, "מצבר לגולף")):
        assert [r["content"] for r in answer_rows(res["hist"], rid)] == [q, f"ANSWER to: {q}"]


def test_L4_arabic_customer_gets_arabic_status():
    wh = _wh()

    async def steps(x):
        await x.post("فلتر زيت لكورولا")
    res = run_case("arabic", "wa", steps, agent=lambda m, n: spec(delay=0.8))
    assert customer(res) == [wh._WAIT_TEXT["ar"], "ANSWER to: فلتر زيت لكورولا"]


# ── TODO 2: recovery idempotency — no duplicate logical turn ────────────────────────────────────────
def test_R1_crash_after_persistence_before_agent_execution_runs_once():
    async def steps(x):
        await x.post("שאלה ראשונה")                                  # opens the conversation
        await _inject(x, "r1", body="משאבת מים")
        await x.recover()
    res = run_case("crash-before-agent", "wa", steps)
    rid, rec = _pick(res, "r1")
    assert res["calls"].count("משאבת מים") == 1
    assert res["before"][rid]["status"] == "received"
    assert rec["status"] == "answered" and rec["delivery"] == "sent" and rec["recovered"] == "1"
    assert [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]


def test_R2_crash_after_agent_saved_turn_before_lifecycle_update_reuses_the_saved_answer():
    wh = _wh()

    async def steps(x):
        await x.post("משאבת מים")
        await asyncio.sleep(0.6)                                       # the agent has committed its turn
        await x.die()
        await x.recover()
    res = run_case("crash-after-commit", "wa", steps, agent=lambda m, n: spec(delay=0.4, after_commit="hang"))
    rid, rec = _one(res)
    assert res["calls"] == ["משאבת מים"]                               # the agent ran ONCE
    assert customer(res) == [wh._WAIT_TEXT["he"], "ANSWER to: משאבת מים"]
    assert rec["status"] == "answered" and rec["attempts"] == "1" and rec["recovered"] == "1"
    rows = answer_rows(res["hist"], rid)
    assert [r["role"] for r in rows] == ["user", "assistant"] and rows[1]["a"]["delivery"] == "sent"


def test_R3_answer_persisted_but_not_delivered_is_delivered_after_restart_without_the_agent():
    state = {"down": True}

    def send(text, n):
        return WA_DOWN if text.startswith("ANSWER") and state["down"] else dict(OK)

    async def steps(x):
        await x.post("מסנן דלק")
        await asyncio.sleep(0.3)                                       # first attempt refused, waiting to retry
        x.mid = await x.rec(x.rids[0])
        await x.die()
        state["down"] = False
        await x.recover()
    wh = _wh()
    # the webhook has returned (it waits at most WA_FAST_DELIVERY_WAIT_S); the retry is pending in the background
    res = run_case("answer-undelivered", "wa", steps, send=send, retry_delays=(5.0,),
                   patches=[(wh, "WA_FAST_DELIVERY_WAIT_S", 0.1)])
    rid, rec = _one(res)
    assert res["calls"] == ["מסנן דלק"]
    assert rec["status"] == "answered" and rec["delivery"] == "sent" and rec["delivery_attempts"] == "2"
    assert customer(res).count("ANSWER to: מסנן דלק") == 2                 # 1 refused by the bridge + 1 delivered
    assert answer_rows(res["hist"], rid)[-1]["a"]["delivery"] == "sent"


def test_R3b_crash_during_the_send_is_unknown_and_never_resent():
    wh = _wh()
    orig = wh._transport
    hold = {"on": True}

    def hanging(channel):
        s, c = orig(channel)

        async def _s(rec, text):
            if text.startswith("ANSWER") and hold["on"]:
                await asyncio.sleep(3600)                              # the process dies inside the send
            return await s(rec, text)
        return _s, c

    async def steps(x):
        await x.post("מסנן דלק")
        await asyncio.sleep(0.3)
        await x.die()
        hold["on"] = False
        await x.recover()
    res = run_case("crash-mid-send", "wa", steps, patches=[(wh, "_transport", hanging)])
    rid, rec = _one(res)
    assert rec["delivery"] == "unknown" and rec["delivery_attempts"] == "1"
    assert "ANSWER to: מסנן דלק" not in customer(res)                    # never re-sent (it may be on the phone)
    assert answer_rows(res["hist"], rid)[-1]["a"]["delivery"] == "unknown"


def test_R4_duplicate_inbound_and_redelivery_after_a_crash_before_persistence():
    async def steps(x):
        mid = uid("r4")
        await x.post("מצבר", mid)
        await x.post("מצבר", mid)                                      # same message id again: ignored
        mid2 = uid("r4b")                                              # a process took this message, then died
        await x.r.set(f"wa:msg:{mid2}", "deadboot", ex=3600)          # before persisting any request
        await x.post("רדיאטור", mid2)
    res = run_case("redelivery", "wa", steps)
    assert res["calls"] == ["מצבר", "רדיאטור"]
    assert customer(res) == ["ANSWER to: מצבר", "ANSWER to: רדיאטור"]


def test_R4b_redelivery_after_restart_of_an_already_handled_message_is_ignored():
    async def steps(x):
        mid = uid("r4c")
        await x.post("מצבר", mid)
        await x.die()                                                   # restart: the key says "done"
        await x.post("מצבר", mid)
    res = run_case("redelivery-done", "wa", steps)
    assert res["calls"] == ["מצבר"] and customer(res) == ["ANSWER to: מצבר"]


def test_R5_concurrent_recovery_takes_a_dead_request_once_and_skips_a_live_owner():
    async def steps(x):
        await x.post("שאלה ראשונה")
        await _inject(x, "r5d", body="מסנן מזגן", status="processing", attempts="1")
        live = await _inject(x, "r5l", body="חיישן", status="processing", attempts="1")
        await x.creq.update(x.r, live, owner="otherboot1")
        await x.r.set(x.creq.BOOT_KEY.format("otherboot1"), "1", ex=60)  # another LIVE process owns this one
        x.outs = await asyncio.gather(x.recover(), x.recover())
        await asyncio.sleep(1.0)
        await x.r.srem(x.creq.OPEN, live)
    res = run_case("concurrent-recovery", "wa", steps)
    _, dead = _pick(res, "r5d")
    _, live = _pick(res, "r5l")
    assert res["calls"].count("מסנן מזגן") == 1 and "חיישן" not in res["calls"]
    assert dead["status"] == "answered" and live["owner"] == "otherboot1" and live["status"] == "processing"
    assert customer(res).count("ANSWER to: מסנן מזגן") == 1


def test_R5b_stale_inflight_claim_of_a_dead_process_does_not_block_a_resend():
    import hashlib

    async def steps(x):
        d = hashlib.sha256("מסנן מזגן".encode()).hexdigest()[:16]
        dead = x.rid_for(uid("dead"))
        await x.creq.create(x.r, dead, channel="wa", target="972500000779", user_key="u", conv_id="",
                            body="מסנן מזגן", lang="he")
        await x.creq.update(x.r, dead, owner="deadboot", status="processing")
        await x.r.srem(x.creq.OPEN, dead)
        await x.r.set(f"wa:agent_inflight:{x.wh.WHATSAPP_ANON_USER_ID}:{d}", dead, ex=150)
        await x.post("מסנן מזגן")
    res = run_case("stale-claim", "wa", steps)
    assert res["calls"] == ["מסנן מזגן"] and customer(res) == ["ANSWER to: מסנן מזגן"]


def test_R5c_identical_resend_while_genuinely_in_flight_one_note_one_answer():
    wh = _wh()

    async def steps(x):
        await x.post("רפידות בלם")
        await x.post("רפידות בלם")
    res = run_case("resend", "wa", steps, agent=lambda m, n: spec(delay=1.0))
    assert res["calls"] == ["רפידות בלם"] and customer(res).count("ANSWER to: רפידות בלם") == 1
    assert wh._STILL_WORKING_TEXT["he"] in customer(res)
    assert list(res["recs"].values())[1] is None                        # the resend created no request


def test_R6_ambiguous_completion_turn_saved_then_error_is_answered_not_failed():
    async def steps(x):
        await x.post("מסנן אוויר")
    res = run_case("saved-then-error", "wa", steps, agent=lambda m, n: spec(after_commit="raise"))
    rid, rec = _one(res)
    assert res["calls"] == ["מסנן אוויר"] and customer(res) == ["ANSWER to: מסנן אוויר"]
    assert rec["status"] == "answered" and [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]


def test_R6b_unreconcilable_outcome_stays_ambiguous_then_resolves_without_a_second_turn():
    wh = _wh()
    flag = {"db": False}
    real = wh._reconcile_turn

    async def flaky(rec, rid):
        if not flag["db"]:
            raise ConnectionError("db unreachable")
        return await real(rec, rid)

    async def steps(x):
        await x.post("מסנן אוויר")
        await asyncio.sleep(0.3)
        x.mid = await x.rec(x.rids[0])
        assert x.mid["status"] == "ambiguous" and x.rids[0] in {r for r, _ in await x.creq.open_requests(x.r)}
        flag["db"] = True
        await x.recover()
    res = run_case("unreconciled-then-resolved", "wa", steps, agent=lambda m, n: spec(after_commit="raise"),
                   patches=[(wh, "_reconcile_turn", flaky)])
    rid, rec = _one(res)
    assert res["calls"] == ["מסנן אוויר"]
    assert rec["status"] == "answered" and customer(res) == ["ANSWER to: מסנן אוויר"]


def test_R6c_unreconcilable_after_the_bound_gets_one_honest_failure():
    wh = _wh()
    import chat_requests as creq

    async def never(rec, rid):
        raise ConnectionError("db unreachable")

    async def steps(x):
        await x.post("מסנן אוויר")
        await asyncio.sleep(0.3)
        for _ in range(3):
            await x.recover()
    res = run_case("unreconciled-exhausted", "wa", steps, agent=lambda m, n: spec(raise_before=True),
                   patches=[(wh, "_reconcile_turn", never), (creq, "MAX_RECONCILE", 2)])
    rid, rec = _one(res)
    assert rec["status"] == "failed" and rec["reason"] == "unreconciled"
    assert customer(res) == [wh._LATE_FAIL_TEXT["he"]] and res["calls"] == ["מסנן אוויר"]


def test_R7_partial_turn_keeps_the_original_question_and_reruns_once():
    async def steps(x):
        await x.post("משאבת דלק")
        await asyncio.sleep(0.5)
        x.orig = [h for h in await x.history(x.rids[0])]                 # what the interrupted run left
        await x.die()
        await x.recover()
    res = run_case("partial-turn", "wa", steps, agent=lambda m, n: spec(save="user_only") if n == 1 else spec())
    rid, rec = _one(res)
    every = [h for h in res["hist"] if h["a"].get("chat_request_id") == rid]
    visible = answer_rows(res["hist"], rid)
    orig = [h for h in every if h["role"] == "user" and not h["a"].get("chat_duplicate_of")]
    dup = [h for h in every if h["a"].get("chat_duplicate_of")]
    assert [r["role"] for r in visible] == ["user", "assistant"]            # ONE logical turn visible
    assert len(orig) == 1 and not orig[0]["deleted"]                        # the customer's question, untouched
    assert len(dup) == 1 and dup[0]["deleted"] and dup[0]["a"]["chat_duplicate_of"] == orig[0]["id"]
    assert rec["status"] == "answered" and rec["attempts"] == "2" and rec["rerun_reuses_user_row"] == orig[0]["id"]


def test_R9_two_concurrent_agent_runs_for_one_request_the_database_keeps_one_turn():
    from chat_lifecycle_harness import index_present

    async def steps(x):
        assert await index_present(), "migration 0040 is not applied to the sandbox PII DB"
        await x.post("שאלה ראשונה")
        rid = await _inject(x, "r9", body="מסנן שמן")
        rec = await x.rec(rid)
        x.results = await asyncio.gather(x.wh._run_agent_for(rec, rid), x.wh._run_agent_for(rec, rid),
                                         return_exceptions=True)
        from sqlalchemy.exc import IntegrityError
        errs = [e for e in x.results if isinstance(e, BaseException)]
        assert len(errs) == 1 and isinstance(errs[0], IntegrityError), x.results   # the DB refused the second
        x.state = await x.wh._reconcile_turn(rec, rid)
        assert x.state["state"] == "complete"
        await x.r.srem(x.creq.OPEN, rid)
    res = run_case("concurrent-agent-runs", "wa", steps)
    rid, _ = _pick(res, "r9")
    assert res["calls"].count("מסנן שמן") == 2                                # both runs really executed
    rows = answer_rows(res["hist"], rid)
    assert [r["role"] for r in rows] == ["user", "assistant"]                # one logical turn, enforced by the DB


def test_R10_concurrent_failure_history_writes_create_one_row():
    wh = _wh()
    from chat_lifecycle_harness import index_present

    async def steps(x):
        assert await index_present(), "migration 0040 is not applied to the sandbox PII DB"
        await x.post("שאלה ראשונה")
        rid = await _inject(x, "r10")
        rec = await x.rec(rid)
        ids = await asyncio.gather(*[x.wh._history_failure_row(rid, rec, wh._LATE_FAIL_TEXT["he"], "exception:X")
                                     for _ in range(4)])
        assert len(set(ids)) == 1, ids
        await x.r.srem(x.creq.OPEN, rid)
    res = run_case("concurrent-failure-rows", "wa", steps)
    rid, _ = _pick(res, "r10")
    assert len([h for h in answer_rows(res["hist"], rid) if h["a"].get("chat_kind") == "failure"]) == 1


def test_R8_interrupted_with_attempts_exhausted_one_honest_failure_in_history():
    wh = _wh()

    async def steps(x):
        await x.post("שאלה ראשונה")
        await _inject(x, "r8", status="processing", attempts="2")
        await x.recover()
    res = run_case("interrupted-exhausted", "wa", steps)
    rid, rec = _pick(res, "r8")
    assert rec["status"] == "failed" and rec["reason"] == "interrupted" and "q" not in res["calls"]
    rows = answer_rows(res["hist"], rid)
    assert len(rows) == 1 and rows[0]["a"]["chat_kind"] == "failure" and rows[0]["a"]["delivery"] == "sent"
    assert rows[0]["content"] == wh._LATE_FAIL_TEXT["he"]


# ── TODO 3: failure history ───────────────────────────────────────────────────────────────────────
def _failure_row(res, rid):
    rows = [h for h in answer_rows(res["hist"], rid) if h["a"].get("chat_kind") == "failure"]
    assert len(rows) == 1, rows
    return rows[0]


def test_F1_agent_exception_before_answer_failure_in_history_delivered():
    wh = _wh()

    async def steps(x):
        await x.post("מראה צד")
    res = run_case("exception", "wa", steps, agent=lambda m, n: spec(raise_before=True))
    rid, rec = _one(res)
    assert customer(res) == [wh._LATE_FAIL_TEXT["he"]]
    assert rec["status"] == "failed" and rec["reason"] == "exception:RuntimeError" and rec["history"] == "synced"
    row = _failure_row(res, rid)
    assert row["a"]["reason"] == "exception:RuntimeError" and row["a"]["delivery"] == "sent"
    assert "RuntimeError" not in row["content"] and "agent failed" not in row["content"]   # no internals shown


@pytest.mark.parametrize("response,reason", [("", "empty_answer"), ("   ", "empty_answer"), ("<b></b>", "empty_answer"),
                                              (123, "malformed_answer")])
def test_F2_empty_or_malformed_answer_is_a_failure_never_success(response, reason):
    wh = _wh()

    async def steps(x):
        await x.post("חיישן חמצן")
    res = run_case("empty-or-malformed", "wa", steps, agent=lambda m, n: spec(response=response))
    rid, rec = _one(res)
    assert customer(res) == [wh._LATE_FAIL_TEXT["he"]] and rec["status"] == "failed" and rec["reason"] == reason
    assert _failure_row(res, rid)["a"]["delivery"] == "sent"


def test_F2b_late_empty_answer_after_the_status_note():
    wh = _wh()

    async def steps(x):
        await x.post("חיישן חמצן")
    res = run_case("late-empty", "wa", steps, agent=lambda m, n: spec(delay=0.8, response=""))
    assert customer(res) == [wh._WAIT_TEXT["he"], wh._LATE_FAIL_TEXT["he"]]


def test_F3_deadline_exceeded_failure_recorded_and_delivered():
    wh = _wh()

    async def steps(x):
        await x.post("מנוע שלם")
    res = run_case("deadline", "wa", steps, agent=lambda m, n: spec(delay=5.0), settle_s=2.0)
    rid, rec = _one(res)
    assert customer(res) == [wh._WAIT_TEXT["he"], wh._LATE_FAIL_TEXT["he"]]
    assert rec["reason"] == "deadline" and _failure_row(res, rid)["a"]["delivery"] == "sent"


def test_F5_failure_message_delivery_fails_history_says_failed():
    wh = _wh()

    def send(text, n):
        return WA_DOWN if text == wh._LATE_FAIL_TEXT["he"] else dict(OK)

    async def steps(x):
        await x.post("מראה צד")
    res = run_case("failure-undeliverable", "wa", steps, agent=lambda m, n: spec(raise_before=True), send=send)
    rid, rec = _one(res)
    assert rec["status"] == "failed" and rec["delivery"] == "failed" and rec["delivery_attempts"] == "4"
    assert _failure_row(res, rid)["a"]["delivery"] == "failed" and rid not in res["open"]


def test_F6_ambiguous_transport_timeout_is_unknown_not_retried():
    wh = _wh()

    def send(text, n):
        return WA_TIMEOUT if text == wh._LATE_FAIL_TEXT["he"] else dict(OK)

    async def steps(x):
        await x.post("מראה צד")
    res = run_case("failure-timeout", "wa", steps, agent=lambda m, n: spec(raise_before=True), send=send)
    rid, rec = _one(res)
    assert rec["delivery"] == "unknown" and rec["delivery_attempts"] == "1"
    assert customer(res).count(wh._LATE_FAIL_TEXT["he"]) == 1 and _failure_row(res, rid)["a"]["delivery"] == "unknown"


def test_F7_duplicate_webhook_while_a_failure_record_exists_adds_nothing():
    wh = _wh()

    async def steps(x):
        mid = uid("f7")
        await x.post("מראה צד", mid)
        await x.post("מראה צד", mid)                                   # same process
        await x.die()
        await x.post("מראה צד", mid)                                   # after a restart
        await x.recover()
    res = run_case("failure-duplicate", "wa", steps, agent=lambda m, n: spec(raise_before=True))
    rid = list(res["recs"])[0]
    assert res["calls"] == ["מראה צד"] and customer(res) == [wh._LATE_FAIL_TEXT["he"]]
    _failure_row(res, rid)


def test_F8_recovery_after_failure_persisted_but_before_history_sync():
    wh = _wh()

    async def steps(x):
        await x.post("שאלה ראשונה")
        await _inject(x, "f8", status="failed", reason="exception:RuntimeError",
                      failure_text=wh._LATE_FAIL_TEXT["he"], attempts="1")
        await x.recover()
        await x.recover()                                               # a second pass changes nothing
    res = run_case("failure-before-history", "wa", steps)
    rid, rec = _pick(res, "f8")
    assert rec["history"] == "synced" and rec["delivery"] == "sent" and rid not in res["open"]
    assert _failure_row(res, rid)["a"]["delivery"] == "sent"
    assert customer(res).count(wh._LATE_FAIL_TEXT["he"]) == 1


def test_F9_history_write_failure_is_reconciled_later_exactly_once():
    wh = _wh()
    real = wh._history_failure_row
    calls = {"n": 0}

    async def flaky(rid, rec, text, reason):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ConnectionError("db unreachable")
        return await real(rid, rec, text, reason)

    async def steps(x):
        await x.post("מראה צד")
        await asyncio.sleep(0.2)
        x.mid = await x.rec(x.rids[0])
        assert x.mid["history"] == "pending" and x.mid["delivery"] == "sent"
        await x.recover()
        await x.recover()
    res = run_case("history-write-failed", "wa", steps, agent=lambda m, n: spec(raise_before=True),
                   patches=[(wh, "_history_failure_row", flaky)])
    rid, rec = _one(res)
    assert rec["history"] == "synced" and rec["delivery"] == "sent" and rid not in res["open"]
    assert _failure_row(res, rid)["a"]["delivery"] == "sent"
    assert customer(res).count(wh._LATE_FAIL_TEXT["he"]) == 1


# ── transport ─────────────────────────────────────────────────────────────────────────────────────
def test_T1_bridge_down_then_back_answer_delivered_with_bounded_retries():
    def send(text, n):
        return WA_DOWN if text.startswith("ANSWER") and n <= 2 else dict(OK)

    async def steps(x):
        await x.post("מסנן דלק")
    res = run_case("bridge-recovers", "wa", steps, agent=lambda m, n: spec(delay=0.8), send=send)
    rid, rec = _one(res)
    assert rec["delivery"] == "sent" and rec["delivery_attempts"] == "3"
    assert answer_rows(res["hist"], rid)[-1]["a"]["delivery"] == "sent"


def test_T2_bridge_down_throughout_answered_but_not_delivered():
    def send(text, n):
        return WA_DOWN if text.startswith("ANSWER") else dict(OK)

    async def steps(x):
        await x.post("מסנן דלק")
    res = run_case("bridge-down", "wa", steps, send=send)
    rid, rec = _one(res)
    assert rec["status"] == "answered" and rec["delivery"] == "failed" and rec["delivery_attempts"] == "4"
    assert answer_rows(res["hist"], rid)[-1]["a"]["delivery"] == "failed"


def test_F10_history_committed_but_the_record_lost_its_row_id_no_duplicate_failure():
    wh = _wh()

    async def steps(x):
        await x.post("שאלה ראשונה")
        rid = await _inject(x, "f10", status="failed", reason="exception:RuntimeError",
                            failure_text=wh._LATE_FAIL_TEXT["he"], attempts="1")
        rec = await x.rec(rid)
        # the history row was committed, then the process died before the record got history_msg_id
        await x.wh._history_failure_row(rid, rec, wh._LATE_FAIL_TEXT["he"], "exception:RuntimeError")
        assert not (await x.rec(rid)).get("history_msg_id")
        await x.recover()
    res = run_case("history-ok-record-not", "wa", steps)
    rid, rec = _pick(res, "f10")
    assert rec["history"] == "synced" and rec["delivery"] == "sent"
    rows = [h for h in answer_rows(res["hist"], rid) if h["a"].get("chat_kind") == "failure"]
    assert len(rows) == 1 and rows[0]["a"]["delivery"] == "sent" and rows[0]["id"] == rec["history_msg_id"]


def test_R11_overlapping_recovery_passes_stress_each_request_runs_once():
    """Load check: 15 requests of a dead process, 3 overlapping recovery passes, every agent runs exactly once.
    This does NOT reach the take-over race window (it passes on the pre-fix loop too); R12 is the deterministic
    test for that race."""
    async def steps(x):
        await x.post("שאלה ראשונה")
        x.bodies = [f"חלק מספר {i}" for i in range(15)]
        for i, b in enumerate(x.bodies):
            await _inject(x, f"s{i}", body=b, status="processing", attempts="1")
        await asyncio.gather(x.recover(), x.recover(), x.recover())
        await asyncio.sleep(1.0)
    res = run_case("recovery-stress", "wa", steps)
    counts = {b: res["calls"].count(b) for b in [f"חלק מספר {i}" for i in range(15)]}
    assert all(c == 1 for c in counts.values()), counts
    for rid, rec in res["recs"].items():
        if "-s" in rid:
            assert rec["status"] == "answered" and rec["delivery"] == "sent", (rid, rec)
            assert [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]


def test_R12_second_pass_inside_the_take_over_window_does_not_rerun():
    """Deterministic form of the R5 race: pass A takes the request over and pauses (as any await can); pass B
    snapshots the record during that pause and sees owner == this process. B must not run the agent again."""
    import chat_requests as creq
    real = creq.take_over

    async def slow_take_over(r, rid, expected_owner):
        ok = await real(r, rid, expected_owner)
        if ok:
            await asyncio.sleep(0.2)                         # widen the window between take-over and acting
        return ok

    async def steps(x):
        await x.post("שאלה ראשונה")
        await _inject(x, "r12", body="משאבת שמן", status="received", attempts="0")
        a = asyncio.ensure_future(x.recover())
        await asyncio.sleep(0.1)                              # A has taken it over and is paused
        b = asyncio.ensure_future(x.recover())
        await asyncio.gather(a, b)
        await asyncio.sleep(1.0)
    res = run_case("take-over-window", "wa", steps, agent=lambda m, n: spec(delay=0.5),
                   patches=[(creq, "take_over", slow_take_over)])
    rid, rec = _pick(res, "r12")
    assert res["calls"].count("משאבת שמן") == 1, res["calls"]                # the agent ran ONCE
    assert customer(res).count("ANSWER to: משאבת שמן") == 1 and rec["attempts"] == "1"
    assert rec["status"] == "answered" and [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]


def test_R13_redis_failure_on_the_request_record_still_answers_never_drops_or_promises_a_human():
    """The request record cannot be written (Redis error): the customer is still answered (without durability) —
    the message is never dropped and no human call-back is promised."""
    import chat_requests as creq

    async def broken_create(*a, **k):
        raise ConnectionError("redis down")

    async def steps(x):
        await x.post("מסנן שמן")
    res = run_case("redis-create-fails", "wa", steps, patches=[(creq, "create", broken_create)])
    assert res["calls"] == ["מסנן שמן"] and customer(res) == ["ANSWER to: מסנן שמן"]
    assert not any("נציג" in t for t in customer(res))
