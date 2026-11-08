"""#84 regression: web chat is on the durable chat-request lifecycle (deadline, kept task, one honest failure row,
take-over after a restart, no duplicate answer).

Before: routes/chat.py started the agent with asyncio.create_task(_guarded_task(...)) — no kept reference, no
deadline, nothing recorded. A slow provider left the page spinning forever, and a backend restart lost the answer
silently. These cases drive the REAL POST /api/v1/chat/message route and the REAL lifecycle (routes.webhooks)
against the isolated PII database and a Redis logical database of their own (as the #103 harness does); the agent
is a fake that writes its answer exactly like process_agent_response_for_message (assistant row, commit) and the
customer is a synthetic @example.com user. Every row and key created is removed. Isolated databases only.
"""
import asyncio
import os
import random
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolated_db_guard  # noqa: E402

REFUSAL = isolated_db_guard.static_refusal()
pytestmark = pytest.mark.skipif(REFUSAL is not None, reason=f"isolated DB only: {REFUSAL}")
TAG = "zz84" + uuid.uuid4().hex[:8]
OLD_FALLBACK = "מצטער, נתקלתי בבעיה טכנית"


def _run(script, steps, *, deadline_s=1.0, settle_s=0.6):
    """script(n) -> {"delay", "raise", "save", "hang_after_commit"}; steps(ctx) drives the case."""
    import httpx
    from fastapi import FastAPI
    from sqlalchemy import text
    import routes.chat as chat
    import routes.webhooks as wh
    import chat_requests as creq
    import BACKEND_AI_AGENTS as baa
    import BACKEND_AUTH_SECURITY as sec
    from BACKEND_DATABASE_MODELS import pii_session_factory, pii_engine, Message
    from chat_lifecycle_harness import isolated_redis, _dispose
    calls, out = [], {}
    boot0 = creq.BOOT_ID

    async def fake_agent(user_id, message, conversation_id, db, source="web"):
        n = len(calls) + 1
        calls.append((message, source))
        s = script(n)
        await asyncio.sleep(s.get("delay", 0))
        if s.get("raise"):
            raise RuntimeError("agent failed")
        if s.get("save", True):
            db.add(Message(conversation_id=conversation_id, role="assistant", agent_name="fake_agent",
                           content=f"ANSWER {n} to: {message}", content_type="text"))
            await db.commit()
        if s.get("hang_after_commit"):
            await asyncio.sleep(3600)

    async def main():
        mp = pytest.MonkeyPatch()
        redis = await isolated_redis()
        mp.setattr(sec, "_redis_client", redis)
        mp.setattr(baa, "process_agent_response_for_message", fake_agent)
        mp.setattr(wh, "WEB_CHAT_DEADLINE_S", deadline_s)
        async with pii_session_factory() as db:
            why = await isolated_db_guard.live_refusal(db)
            if why:
                pytest.fail(f"REFUSED before any write: {why}")
            uid = (await db.execute(text(
                "INSERT INTO users (id, email, full_name, is_verified) VALUES (gen_random_uuid(), :e, :n, true) "
                "RETURNING id"), {"e": f"{TAG}@example.com", "n": TAG})).scalar()
            await db.commit()
        user = (await _load_user(uid))
        app = FastAPI()
        app.include_router(chat.router)
        app.dependency_overrides[sec.get_current_verified_user] = lambda: user
        app.dependency_overrides[sec.get_current_user] = lambda: user

        async def _r():
            return redis
        app.dependency_overrides[sec.get_redis] = _r
        ctx = {"redis": redis, "wh": wh, "creq": creq, "uid": str(uid), "calls": calls}
        try:
            assert pii_engine.url.host == "sandbox_postgres", f"REFUSED: PII host {pii_engine.url.host!r}"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                ctx["c"] = c
                await steps(ctx)
                await asyncio.sleep(settle_s)
                out["rows"] = await _rows(ctx.get("conv"))
                out["api"] = (await c.get(f"/api/v1/chat/conversations/{ctx['conv']}/messages")).json()["messages"] \
                    if ctx.get("conv") else []
                out["rec"] = await creq.get(redis, ctx["rid"]) if ctx.get("rid") else None
                out["open"] = {x for x, _ in await creq.open_requests(redis)}
                out.update({k: v for k, v in ctx.items() if k in ("first", "rid", "conv", "kept")})
        finally:
            for t in list(wh._AGENT_REPLY_TASKS):
                t.cancel()
            await asyncio.sleep(0.05)
            mp.undo()
            wh._ACTIVE_REQUESTS.clear()
            creq.BOOT_ID = boot0
            async with pii_session_factory() as db:
                await db.execute(text("DELETE FROM messages WHERE conversation_id IN "
                                      "(SELECT id FROM conversations WHERE user_id = :u)"), {"u": uid})
                await db.execute(text("DELETE FROM conversations WHERE user_id = :u"), {"u": uid})
                await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
                await db.commit()
            async for k in redis.scan_iter("chat_req:web:*"):
                await redis.delete(k)
            for m in await redis.smembers("chat_req:open"):
                if str(m).startswith("web:"):
                    await redis.srem("chat_req:open", m)
            async for k in redis.scan_iter("chat_req:boot:*"):
                await redis.delete(k)
            await redis.aclose()
            await _dispose(pii_engine)
    asyncio.run(main())
    out["calls"] = calls
    return out


async def _load_user(uid):
    from sqlalchemy import select
    from BACKEND_DATABASE_MODELS import pii_session_factory, User
    async with pii_session_factory() as db:
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        db.expunge(u)
        return u


async def _rows(conv):
    if not conv:
        return []
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import pii_session_factory
    async with pii_session_factory() as db:
        rows = (await db.execute(text(
            "SELECT role, agent_name, content, analysis, deleted_at IS NOT NULL FROM messages "
            "WHERE conversation_id = CAST(:c AS uuid) ORDER BY created_at"), {"c": conv})).fetchall()
    return [{"role": r[0], "agent": r[1], "content": r[2], "a": r[3] or {}, "deleted": r[4]} for r in rows]


async def _send(ctx, text="רפידות בלם לקורולה 2018"):
    t0 = time.time()
    resp = await ctx["c"].post("/api/v1/chat/message", json={"message": text, "preferred_lang": "he"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ctx.update(first=dict(body, elapsed=time.time() - t0), conv=body["conversation_id"])
    rows = await _rows(ctx["conv"])
    ctx["rid"] = [r["a"]["chat_request_id"] for r in rows if r["role"] == "user"][-1]
    ctx["kept"] = sum(1 for t in ctx["wh"]._AGENT_REPLY_TASKS if not t.done())
    return body


async def _die(ctx):
    """The process dies: its tasks and in-memory state are gone; a NEW process (new BOOT_ID) starts."""
    wh, creq, r = ctx["wh"], ctx["creq"], ctx["redis"]
    for t in list(wh._AGENT_REPLY_TASKS):
        t.cancel()
    await asyncio.sleep(0.05)
    wh._ACTIVE_REQUESTS.clear()
    await r.delete(creq.BOOT_KEY.format(creq.BOOT_ID))
    creq.BOOT_ID = "testboot" + format(random.getrandbits(24), "06x")


def _visible(rows):
    return [r for r in rows if not r["deleted"]]


def test_W1_answer_is_saved_once_and_the_request_closes():
    async def steps(ctx):
        await _send(ctx)
    res = _run(lambda n: {"delay": 0.1}, steps)
    assert res["first"]["status"] == "processing" and res["first"]["elapsed"] < 1.0   # the route never waits
    assert res["kept"] >= 1                                                               # the task is referenced
    rows = _visible(res["rows"])
    assert [(r["role"], r["a"].get("chat_turn")) for r in rows] == [("user", "user"), ("assistant", "assistant")]
    assert all(r["a"]["chat_request_id"] == res["rid"] for r in rows) and res["rid"].startswith("web:")
    rec = res["rec"]
    assert (rec["status"], rec["delivery"], rec["history"]) == ("answered", "sent", "synced")
    assert rec.get("closed_at") and res["rid"] not in res["open"]
    assert res["calls"] == [("רפידות בלם לקורולה 2018", "web")]


def test_W2_deadline_gives_one_honest_failure_row_never_silence():
    async def steps(ctx):
        await _send(ctx)
    res = _run(lambda n: {"delay": 30}, steps, deadline_s=0.5, settle_s=1.0)
    rows = _visible(res["rows"])
    fail = [r for r in rows if r["a"].get("chat_kind") == "failure"]
    assert len(fail) == 1 and fail[0]["role"] == "assistant"
    assert fail[0]["content"].startswith("מצטערים — לא הצלחתי להשלים את החיפוש")                       # Hebrew text
    assert not [r for r in rows if r["role"] == "assistant" and r["a"].get("chat_kind") is None]       # no late answer
    assert (res["rec"]["status"], res["rec"]["reason"]) == ("failed", "deadline")
    assert any(m["content"] == fail[0]["content"] for m in res["api"])                                 # the page sees it
    assert not any(OLD_FALLBACK in (r["content"] or "") for r in rows)


def test_W3_agent_exception_gives_one_failure_row():
    async def steps(ctx):
        await _send(ctx)
    res = _run(lambda n: {"raise": True}, steps)
    fail = [r for r in _visible(res["rows"]) if r["a"].get("chat_kind") == "failure"]
    assert len(fail) == 1 and res["rec"]["status"] == "failed"
    assert res["rec"]["reason"] == "exception:RuntimeError"


def test_W4_restart_mid_answer_is_taken_over_and_answered_once():
    async def steps(ctx):
        await _send(ctx)
        await asyncio.sleep(0.2)                     # the agent is mid-run (first call hangs)
        await _die(ctx)
        out = await ctx["wh"].recover_agent_requests(ctx["redis"])
        ctx["recovery"] = out
        assert out["taken"] == 1 and out["rerun"] == 1, out
    res = _run(lambda n: {"delay": 3600} if n == 1 else {"delay": 0.05}, steps, settle_s=0.8)
    rows = _visible(res["rows"])
    assert [r["role"] for r in rows] == ["user", "assistant"]                   # one question, one answer
    assert rows[1]["content"].startswith("ANSWER 2") and len(res["calls"]) == 2
    assert res["rec"]["status"] == "answered" and res["rec"].get("recovered") == "1"


def test_W5_answer_saved_before_the_restart_is_reused_not_produced_again():
    async def steps(ctx):
        await _send(ctx)
        await asyncio.sleep(0.3)                     # answer committed, then the process hangs and dies
        await _die(ctx)
        out = await ctx["wh"].recover_agent_requests(ctx["redis"])
        assert out["taken"] == 1 and out["reused"] == 1 and out["rerun"] == 0, out
    res = _run(lambda n: {"delay": 0.05, "hang_after_commit": True}, steps)
    rows = _visible(res["rows"])
    assert [r["role"] for r in rows] == ["user", "assistant"] and len(res["calls"]) == 1
    assert res["rec"]["status"] == "answered" and res["rid"] not in res["open"]


def test_W6_route_no_longer_fires_an_unreferenced_task():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "routes", "chat.py"),
               encoding="utf-8").read()
    assert "asyncio.create_task(_guarded_task(_run_agent_bg()))" not in src
    assert "start_web_chat_request(" in src
