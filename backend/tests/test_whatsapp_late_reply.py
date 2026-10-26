"""WhatsApp chat: a slow agent answer is delivered late, never discarded; never twice (FIXES_TRACKER #71).

Before: asyncio.wait_for(process_user_message(...), 12) cancelled the agent at 12 s, told the customer in
Hebrew "שלח שוב את שאלתך" and threw the answer away. Timings are scaled down (wait 0.05 s, deadline 0.3 s).
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes import webhooks as wh  # noqa: E402


def _run_case(agent_delay, *, fail=False, wait_s=0.05, deadline_s=0.3, lang="he"):
    sent, done_calls, cancelled = [], [], []

    async def run():
        try:
            await asyncio.sleep(agent_delay)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        if fail:
            raise RuntimeError("agent exploded")
        return {"response": "<b>תשובה</b> מלאה"}

    async def send(text):
        sent.append(text)

    async def on_done():
        done_calls.append(True)

    async def main():
        out = await wh.run_agent_reply(run, lambda r: r["response"].replace("<b>", "").replace("</b>", ""),
                                       send, lang=lang, wait_s=wait_s, deadline_s=deadline_s, on_done=on_done)
        await asyncio.sleep(deadline_s + 0.2)        # let any late delivery finish
        return out
    out = asyncio.run(main())
    return out, sent, done_calls, cancelled


def test_fast_answer_is_returned_directly_and_nothing_is_sent_late():
    out, sent, done, _ = _run_case(0.0)
    assert out == {"mode": "direct", "text": "תשובה מלאה"} and sent == [] and done == [True]


def test_slow_answer_is_delivered_late_exactly_once():
    out, sent, done, cancelled = _run_case(0.15)
    assert out["mode"] == "deferred" and out["text"] == wh._WAIT_TEXT["he"]
    assert "שלח שוב" not in out["text"] and "אין צורך לשלוח שוב" in out["text"]
    assert sent == ["תשובה מלאה"] and done == [True] and cancelled == []


def test_past_the_deadline_the_work_is_cancelled_and_the_customer_told_once():
    out, sent, done, cancelled = _run_case(5.0)
    assert out["mode"] == "deferred"
    assert sent == [wh._LATE_FAIL_TEXT["he"]] and cancelled == [True] and done == [True]


def test_fast_failure_is_an_error_not_a_wait():
    out, sent, _, _ = _run_case(0.0, fail=True)
    assert out["mode"] == "error" and sent == []


def test_late_failure_sends_the_failure_text_once():
    out, sent, _, _ = _run_case(0.15, fail=True)
    assert out["mode"] == "deferred" and sent == [wh._LATE_FAIL_TEXT["he"]]


@pytest.mark.parametrize("text,lang", [("מסנן שמן לקורולה", "he"), ("فلتر زيت", "ar"),
                                       ("oil filter corolla", "en"), ("12345", "he")])
def test_reply_language_follows_the_customer(text, lang):
    assert wh._reply_lang(text) == lang


def test_waiting_text_is_in_the_customers_language():
    out, _, _, _ = _run_case(0.15, lang="ar")
    assert out["text"] == wh._WAIT_TEXT["ar"]


class _FakeRedis:
    def __init__(self):
        self.d = {}

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    async def delete(self, k):
        self.d.pop(k, None)


def test_a_resent_identical_question_does_not_start_a_second_answer():
    r = _FakeRedis()

    async def go():
        a = await wh._agent_inflight_claim(r, "u1", "מסנן שמן", 60)
        b = await wh._agent_inflight_claim(r, "u1", "  מסנן שמן ", 60)     # resend while in flight
        c = await wh._agent_inflight_claim(r, "u1", "רפידות בלם", 60)      # a different question
        await wh._agent_inflight_release(r, "u1", "מסנן שמן")
        d = await wh._agent_inflight_claim(r, "u1", "מסנן שמן", 60)        # after the answer: a new turn
        return a, b, c, d
    assert asyncio.run(go()) == ("new", "duplicate", "new", "new")


def test_without_redis_the_reply_is_never_blocked():
    assert asyncio.run(wh._agent_inflight_claim(None, "u1", "x", 60)) == "new"


def test_webhook_uses_the_late_delivery_path():
    import inspect
    src = inspect.getsource(wh._whatsapp_webhook_impl)
    assert "_process_request(" in src and "timeout=12.0" not in src
    assert "שלח שוב את שאלתך ואענה תוך שניות" not in src
    # the conversation updates are committed, then the request is persisted (#73), before the agent starts
    commit = src.index("await db.commit()\n    _lang = _reply_lang(body)")
    assert commit < src.index("creq.create(") < src.index("_process_request(")
