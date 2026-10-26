"""Side effects of an agent turn under crash/recovery, and the context a recovery re-run shows the model
(FIXES_TRACKER #75). Sandbox only; harness: tests/chat_lifecycle_harness.py.

The consequential side effect of a turn is the checkout: BACKEND_AI_AGENTS.create_checkout_link ->
routes.payments.create_whatsapp_checkout, which commits an Order + Stripe session in its own session, outside the
agent's turn. Here the REAL create_checkout_link and the REAL create_whatsapp_checkout run against a sandbox fixture
part (tests/checkout_fixture.py); only Stripe is replaced (FakeStripe, idempotency-key semantics). Assertions are on the
PERSISTED orders/payments and the Stripe sessions created (#77).
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chat_lifecycle_harness import (REFUSAL, WA_DOWN, OK, answer_rows, customer, run_case, spec, uid)  # noqa: E402

pytestmark = pytest.mark.skipif(REFUSAL is not None, reason=f"isolated DB only: {REFUSAL}")


def _one(res):
    (rid, rec), = res["recs"].items()
    return rid, rec


def _pay_texts(res):
    return [t for t in res["sent"] if t.startswith("PAY:")]


# ── the checkout side effect, through the REAL checkout (sandbox fixture part, FakeStripe) ─────────────
def _one_order(res):
    assert len(res["orders"]) == 1, res["orders"]
    assert res["stripe_sessions"] == 1, res["stripe_sessions"]
    return res["orders"][0]


def test_X1_crash_before_the_side_effect_one_order_from_the_rerun():
    async def steps(x):
        await x.post("כן תשלח לינק")
        await asyncio.sleep(0.4)                                    # still before the checkout
        await x.die()
        await x.recover()
    res = run_case("se-crash-before", "wa", steps, real_checkout=True,
                   agent=lambda m, n: spec(delay=1.5, checkout=True) if n == 1 else spec(checkout=True))
    rid, rec = _one(res)
    order = _one_order(res)
    assert res["calls"] == ["כן תשלח לינק"] * 2 and len(_pay_texts(res)) == 1
    assert rec["side_effect"] == "checkout_created" and rec["checkout_order_id"] == order["order_id"]


def test_X2_crash_after_the_side_effect_before_the_turn_is_saved_same_order_same_link():
    async def steps(x):
        await x.post("כן תשלח לינק")
        await asyncio.sleep(0.5)                                    # order committed, the agent dies before saving
        x.mid = await x.rec(x.rids[0])
        await x.die()
        await x.recover()
    res = run_case("se-crash-after", "wa", steps, real_checkout=True,
                   agent=lambda m, n: spec(checkout=True, after_checkout="hang") if n == 1 else spec(checkout=True))
    rid, rec = _one(res)
    order = _one_order(res)                                         # ONE order, ONE Stripe session
    assert len(_pay_texts(res)) == 1 and rec["side_effect"] == "checkout_reused"
    assert rec["checkout_order_id"] == order["order_id"] and rec["attempts"] == "2"
    assert [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]


def test_X3_crash_while_stripe_is_creating_the_session_reconciled_to_one_order():
    def slow(fake):
        fake.slow_s = 1.0

    async def steps(x):
        await x.post("כן תשלח לינק")
        await asyncio.sleep(0.6)                                    # inside the Stripe call (order flushed)
        await x.die()                                               # transaction rolled back; Stripe finishes anyway
        await asyncio.sleep(1.0)
        await x.recover()
    res = run_case("se-crash-inside", "wa", steps, real_checkout=True, stripe_setup=slow, settle_s=3.0,
                   agent=lambda m, n: spec(checkout=True))
    rid, rec = _one(res)
    _one_order(res)                                                 # the replayed key returned the same session
    assert len(_pay_texts(res)) == 1 and rec["status"] == "answered"


def test_X3b_the_environments_own_recovery_loop_cannot_take_over_a_cases_requests():
    """#103 regression. X3 failed intermittently (0 orders) because the case shared the environment's Redis with the
    environment's running backend: its chat recovery pass (every 20 s) saw the case's simulated-dead request and took
    it over with the REAL agent. Reproduced 5/5 by running that pass in a second process; here it runs every 0.2 s
    for the whole case. The case's requests must be invisible to it (own Redis logical database)."""
    import subprocess
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    rival = subprocess.Popen([sys.executable, os.path.join(tests_dir, "competing_chat_recovery.py"), "12"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                             cwd=os.path.dirname(tests_dir))
    try:
        for _ in range(200):                                        # the app import prints warnings first
            line = rival.stdout.readline()
            if not line or line.strip() == "READY":
                break
        assert line.strip() == "READY", "the rival recovery process did not start"

        def slow(fake):
            fake.slow_s = 1.0

        async def steps(x):
            await x.post("כן תשלח לינק")
            await asyncio.sleep(0.6)
            await x.die()
            await asyncio.sleep(1.0)
            await x.recover()
        res = run_case("se-crash-inside-rival", "wa", steps, real_checkout=True, stripe_setup=slow, settle_s=3.0,
                       agent=lambda m, n: spec(checkout=True))
        out = rival.communicate(timeout=60)[0]
    finally:
        if rival.poll() is None:
            rival.kill()
    import re
    m = re.search(r"^TAKEN (\d+) PASSES (\d+)$", out, re.M)
    assert m, out[-500:]
    taken, passes = int(m.group(1)), int(m.group(2))
    assert passes > 20, out                                         # the rival really ran during the case
    assert taken == 0, f"the environment's recovery pass took over the case's request ({out.strip()})"
    rid, rec = _one(res)
    _one_order(res)
    assert len(_pay_texts(res)) == 1 and rec["status"] == "answered"


def test_X4_after_persistence_before_delivery_no_second_order():
    state = {"down": True}

    def send(text, n):
        return WA_DOWN if text.startswith("PAY:") and state["down"] else dict(OK)

    async def steps(x):
        await x.post("כן תשלח לינק")
        await asyncio.sleep(0.3)
        await x.die()
        state["down"] = False
        await x.recover()
    import routes.webhooks as wh
    res = run_case("se-undelivered", "wa", steps, real_checkout=True, agent=lambda m, n: spec(checkout=True),
                   send=send, retry_delays=(5.0,), patches=[(wh, "WA_FAST_DELIVERY_WAIT_S", 0.1)])
    rid, rec = _one(res)
    _one_order(res)
    assert res["calls"] == ["כן תשלח לינק"] and rec["delivery"] == "sent" and rec["delivery_attempts"] == "2"


def test_X5_concurrent_recovery_one_order():
    import chat_requests as creq
    real = creq.take_over

    async def slow_take_over(r, rid, expected_owner):
        ok = await real(r, rid, expected_owner)
        if ok:
            await asyncio.sleep(0.2)
        return ok

    async def steps(x):
        await x.post("שאלה ראשונה")
        cid = await x.conv_id()
        rid = x.rid_for(uid("x5"))
        await x.creq.create(x.r, rid, channel="wa", target="972500000779", user_key=str(x.wh.WHATSAPP_ANON_USER_ID),
                            conv_id=cid, body="כן תשלח לינק", lang="he")
        await x.creq.update(x.r, rid, owner="deadboot")
        x.rids.append(rid)
        a = asyncio.ensure_future(x.recover())
        await asyncio.sleep(0.1)
        b = asyncio.ensure_future(x.recover())
        await asyncio.gather(a, b)
        await asyncio.sleep(1.0)
    res = run_case("se-concurrent", "wa", steps, real_checkout=True,
                   agent=lambda m, n: spec(delay=0.5, checkout=True) if m == "כן תשלח לינק" else spec(),
                   patches=[(creq, "take_over", slow_take_over)])
    _one_order(res)
    assert res["calls"].count("כן תשלח לינק") == 1


def test_X6_turn_saved_then_error_reuses_the_turn_one_order():
    async def steps(x):
        await x.post("כן תשלח לינק")
    res = run_case("se-saved-then-error", "wa", steps, real_checkout=True,
                   agent=lambda m, n: spec(checkout=True, after_commit="raise"))
    rid, rec = _one(res)
    _one_order(res)
    assert len(_pay_texts(res)) == 1 and rec["status"] == "answered"


def test_X7_a_new_request_is_a_new_order():
    async def steps(x):
        await x.post("כן תשלח לינק")
        await x.post("עוד לינק בבקשה")
    res = run_case("se-per-request", "wa", steps, real_checkout=True, agent=lambda m, n: spec(checkout=True))
    assert len(res["orders"]) == 2 and res["stripe_sessions"] == 2       # deliberate repeat orders are not blocked


def test_X8_telegram_crash_after_checkout_same_order():
    async def steps(x):
        await x.post("כן תשלח לינק")
        await asyncio.sleep(0.5)
        await x.die()
        await x.recover()
    res = run_case("se-tg-crash-after", "tg", steps, real_checkout=True,
                   agent=lambda m, n: spec(checkout=True, after_checkout="hang") if n == 1 else spec(checkout=True))
    rid, rec = _one(res)
    _one_order(res)
    assert len(_pay_texts(res)) == 1 and rec["side_effect"] == "checkout_reused"


# ── TODO 3: what a recovery re-run shows the model ────────────────────────────────────────────────
def test_C1_partial_turn_rerun_model_sees_the_question_once():
    q = "משאבת דלק לגולף"

    async def steps(x):
        await x.post(q)
        await asyncio.sleep(0.5)
        await x.die()
        await x.recover()
    res = run_case("ctx-partial", "wa", steps, agent=lambda m, n: spec(save="user_only") if n == 1 else spec())
    rid, rec = _one(res)
    first, rerun = res["seen_history"]
    assert ("user", q) not in first                                 # the question is the current message
    assert ("user", q) not in rerun                                 # NOT also in history (the saved original row)
    visible = answer_rows(res["hist"], rid)
    assert [(r["role"], r["content"]) for r in visible] == [("user", q), ("assistant", f"ANSWER to: {q}")]


def test_C2_two_interrupted_attempts_then_success_one_visible_turn_question_once_each_time():
    import chat_requests as creq
    q = "רדיאטור לקורולה"

    async def steps(x):
        await x.post(q)
        await asyncio.sleep(0.5)
        await x.die()
        await x.recover()                                            # attempt 2: interrupted again
        await asyncio.sleep(0.5)
        await x.die()
        await x.recover()                                            # attempt 3: completes
    res = run_case("ctx-partial-twice", "wa", steps, agent=lambda m, n: spec(save="user_only") if n <= 2 else spec(),
                   patches=[(creq, "MAX_ATTEMPTS", 3)])
    rid, rec = _one(res)
    assert len(res["seen_history"]) == 3 and all(("user", q) not in h for h in res["seen_history"])
    every = [h for h in res["hist"] if h["a"].get("chat_request_id") == rid]
    assert [r["role"] for r in answer_rows(res["hist"], rid)] == ["user", "assistant"]
    assert sum(1 for h in every if h["deleted"]) == 2                # the two re-run copies, never the original
    orig = [h for h in every if h["role"] == "user" and not h["deleted"]]
    assert len(orig) == 1 and all(h["a"].get("chat_duplicate_of") == orig[0]["id"] for h in every if h["deleted"])
    assert rec["attempts"] == "3" and rec["status"] == "answered"


def test_C3_normal_turn_history_unchanged_by_the_filter():
    async def steps(x):
        await x.post("מסנן שמן")
        await x.post("ומסנן אוויר?")
    res = run_case("ctx-normal", "wa", steps)
    assert res["seen_history"][1][-2:] == [("user", "מסנן שמן"), ("assistant", "ANSWER to: מסנן שמן")]


# ── #77: history ordering — the LATEST 20, oldest → newest ────────────────────────────────────────
def _db_rows(res):
    return [h for h in res["hist"] if not h["deleted"]]


def test_H1_long_conversation_model_gets_the_latest_20_in_chronological_order():
    async def steps(x):
        for i in range(14):                                         # 14 turns = 28 rows + the welcome
            await x.post(f"שאלה מספר {i:02d}")
    res = run_case("hist-latest", "wa", steps)
    last = res["seen_history"][-1]                                  # what the model saw on the 14th turn
    visible = [(h["role"], h["content"]) for h in _db_rows(res)]
    before_last = visible[:-2]                                      # history as it stood before turn 14 was saved
    assert len(last) == 20 and last == before_last[-20:]            # the latest 20, in the same (chronological) order
    assert ("user", "שאלה מספר 00") not in last                     # the oldest are the ones left out
    assert last[-1] == ("assistant", "ANSWER to: שאלה מספר 12") and last[-2] == ("user", "שאלה מספר 12")
    assert [r for r, _ in last] == ["user", "assistant"] * 10       # mixed roles, alternating as written


def test_H2_soft_deleted_rows_never_reach_the_model():
    async def steps(x):
        for i in range(3):
            await x.post(f"שאלה {i}")
        from sqlalchemy import text
        from BACKEND_DATABASE_MODELS import pii_session_factory
        cid = await x.conv_id()
        async with pii_session_factory() as db:                     # soft-delete the middle question (sandbox)
            await db.execute(text("UPDATE messages SET deleted_at = now() WHERE conversation_id = CAST(:c AS uuid) "
                                  "AND content = 'שאלה 1'"), {"c": cid})
            await db.commit()
        await x.post("שאלה 3")
    res = run_case("hist-soft-deleted", "wa", steps)
    last = res["seen_history"][-1]
    assert ("user", "שאלה 1") not in last and ("user", "שאלה 0") in last and ("user", "שאלה 2") in last


def test_H3_recovered_partial_turn_in_a_long_conversation_latest_history_question_once():
    q = "השאלה שנקטעה"

    async def steps(x):
        for i in range(12):
            await x.post(f"שאלה מספר {i:02d}")
        await x.post(q)
        await asyncio.sleep(0.5)
        await x.die()
        await x.recover()
    res = run_case("hist-recovered", "wa", steps,
                   agent=lambda m, n: spec(save="user_only") if m == q and n == 13 else spec())
    rerun = res["seen_history"][-1]
    assert ("user", q) not in rerun                                 # not twice
    assert rerun[-1] == ("assistant", "ANSWER to: שאלה מספר 11")    # the latest turns ARE there
    assert len(rerun) == 20 and ("user", "שאלה מספר 00") not in rerun
