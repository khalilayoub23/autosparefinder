"""End to end through the REAL WhatsApp webhook route (FastAPI app + routes.webhooks router, sandbox PII DB):
a slow agent answer is delivered late to the same sender, for the same question, exactly once (FIXES_TRACKER #71/#72).

Only two things are replaced: the agent (a fake that echoes the question after a set delay) and the bridge
send (a recorder) — no message can reach WhatsApp. Synthetic sender number; isolated DB only
(tests/isolated_db_guard.py); the test conversation rows are removed afterwards.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolated_db_guard  # noqa: E402

pytestmark = pytest.mark.skipif(isolated_db_guard.static_refusal() is not None,
                                reason=f"isolated DB only: {isolated_db_guard.static_refusal()}")
SENDER = "972500000777"


def _scenario(messages, agent_delays, *, wait_s=0.3, deadline_s=1.5, settle_s=2.0, fail=False):
    """messages: list of (body, message_id, pause_before_s). Returns (sends, agent_calls)."""
    import httpx
    from fastapi import FastAPI
    import routes.webhooks as wh
    import social.whatsapp_provider as prov
    from BACKEND_DATABASE_MODELS import get_pii_db, pii_session_factory, pii_engine
    sends, calls = [], []
    delays = list(agent_delays)

    async def fake_agent(user_id, message, conversation_id, db, source="web"):
        calls.append(message)
        await asyncio.sleep(delays.pop(0) if delays else 0)
        if fail:
            raise RuntimeError("agent failed")
        return {"response": f"ANSWER to: {message}"}

    async def fake_send(to, text, reply_jid=""):
        sends.append((to, text))
        return {"ok": True, "key": None}

    async def no_typing(*a, **k):
        return None

    async def main():
        mp = pytest.MonkeyPatch()
        mp.setattr(wh, "process_user_message", fake_agent)
        mp.setattr(prov, "send_message", fake_send)
        mp.setattr(prov, "send_typing", no_typing)
        mp.setattr(wh, "WA_AGENT_REPLY_WAIT_S", wait_s)
        mp.setattr(wh, "WA_AGENT_LATE_DEADLINE_S", deadline_s)
        mp.setenv("OWNER_WHATSAPP_PHONE", "+972599999999")
        app = FastAPI()
        app.include_router(wh.router)
        try:
            # isolated_db_guard.live_refusal() cannot run on the PII DB (it parses a parts_catalog subquery);
            # require the sandbox PII host instead — never production's postgres_pii.
            assert pii_engine.url.host == "sandbox_postgres", f"REFUSED: PII host {pii_engine.url.host!r}"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                for body, mid, pause in messages:
                    await asyncio.sleep(pause)
                    r = await c.post("/api/v1/webhooks/whatsapp",
                                     json={"from": SENDER, "body": body, "message_id": mid, "profile_name": "t"})
                    assert r.status_code == 200
                await asyncio.sleep(settle_s)          # let late deliveries finish
        finally:
            mp.undo()
            from sqlalchemy import text
            async with pii_session_factory() as db:
                await db.execute(text("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations "
                                      "WHERE context->>'whatsapp_phone' LIKE '%0000777')"))
                await db.execute(text("DELETE FROM conversations WHERE context->>'whatsapp_phone' LIKE '%0000777'"))
                await db.commit()
            from BACKEND_AUTH_SECURITY import get_redis
            r = await get_redis()
            if r:
                async for k in r.scan_iter("wa:agent_inflight:*"):
                    await r.delete(k)
                async for k in r.scan_iter("wa:msg:e2e-*"):
                    await r.delete(k)
                async for k in r.scan_iter("chat_req:wa:e2e-*"):        # #73/#74 request records
                    await r.delete(k)
                for m in await r.smembers("chat_req:open"):
                    if (m.decode() if isinstance(m, bytes) else m).startswith("wa:e2e-"):
                        await r.srem("chat_req:open", m)
            # every pooled connection / cached client belongs to THIS event loop
            import BACKEND_DATABASE_MODELS as _m
            import BACKEND_AUTH_SECURITY as _sec
            await pii_engine.dispose()
            await _m.engine.dispose()
            try:
                import catalog_scraper
                await catalog_scraper._engine.dispose()
            except Exception:
                pass
            if _sec._redis_client is not None:
                await _sec._redis_client.aclose()
                _sec._redis_client = None
    asyncio.run(main())
    mine = [t for to, t in sends if to.endswith(SENDER[-4:])]
    # a NEW conversation first gets the real welcome message, exactly once, before anything else
    # (the welcome is localized: "היי! 👋 אני …" / "أهلاً وسهلاً! 👋 أنا …")
    assert mine and "👋" in mine[0].split("\n")[0], mine[:1]
    assert sum(1 for t in mine if "👋" in t.split("\n")[0]) == 1
    return mine[1:], calls


def _uid(n):
    import uuid
    return f"e2e-{n}-{uuid.uuid4().hex[:8]}"


def test_fast_answer_is_the_only_reply():
    sends, calls = _scenario([("מסנן שמן לקורולה", _uid(1), 0)], [0.0])
    assert sends == ["ANSWER to: מסנן שמן לקורולה"] and calls == ["מסנן שמן לקורולה"]


def test_slow_answer_follows_the_wait_note_for_the_same_question_once():
    import routes.webhooks as wh
    sends, calls = _scenario([("מסנן שמן לקורולה", _uid(2), 0)], [0.8])
    assert sends == [wh._WAIT_TEXT["he"], "ANSWER to: מסנן שמן לקורולה"]
    assert "שלח שוב" not in sends[0] and calls == ["מסנן שמן לקורולה"]


def test_identical_resend_while_in_flight_gets_one_note_and_no_second_answer():
    import routes.webhooks as wh
    q = "רפידות בלם לספורטאז'"
    sends, calls = _scenario([(q, _uid(3), 0), (q, _uid(4), 0.4)], [1.0])
    assert calls == [q]                                              # the agent ran once
    assert sends.count(f"ANSWER to: {q}") == 1
    assert wh._STILL_WORKING_TEXT["he"] in sends and wh._WAIT_TEXT["he"] in sends


def test_redelivered_message_id_after_a_reconnect_is_ignored():
    mid = _uid(5)
    sends, calls = _scenario([("מצבר לגולף", mid, 0), ("מצבר לגולף", mid, 0.1)], [0.0])
    assert calls == ["מצבר לגולף"] and sends == ["ANSWER to: מצבר לגולף"]


def test_past_the_deadline_one_honest_failure_message():
    import routes.webhooks as wh
    sends, calls = _scenario([("מנוע שלם", _uid(6), 0)], [5.0], settle_s=2.5)
    assert sends == [wh._WAIT_TEXT["he"], wh._LATE_FAIL_TEXT["he"]]


def test_arabic_customer_gets_arabic_status_and_the_answer():
    import routes.webhooks as wh
    sends, _ = _scenario([("فلتر زيت لكورولا", _uid(7), 0)], [0.8])
    assert sends == [wh._WAIT_TEXT["ar"], "ANSWER to: فلتر زيت لكورولا"]


def test_after_the_answer_the_same_question_is_a_new_turn():
    q = "מסנן אוויר"
    sends, calls = _scenario([(q, _uid(8), 0), (q, _uid(9), 0.5)], [0.0, 0.0])
    assert calls == [q, q] and sends == [f"ANSWER to: {q}", f"ANSWER to: {q}"]
