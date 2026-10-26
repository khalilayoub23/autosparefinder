"""Sandbox harness for the chat-request lifecycle E2E tests (FIXES_TRACKER #73/#74) — WhatsApp and Telegram.

Drives the REAL webhook routes (FastAPI app + routes.webhooks router) against the sandbox PII DB and sandbox Redis
only (refuses anything else). Replaced: the agent (a fake that writes its conversation turn exactly like
process_user_message — user row, flush, assistant row, flush, commit — and returns message_id) and the transport
send (a recorder whose per-text behaviour each case scripts). No message can reach WhatsApp or Telegram; senders
are synthetic; every row and key created is removed. A "process death" is simulated by cancelling every task the
process owns, clearing its in-memory state, dropping its heartbeat and switching BOOT_ID (a new process).
"""
import asyncio
import json
import os
import random
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolated_db_guard  # noqa: E402

REFUSAL = isolated_db_guard.static_refusal()
WA_SENDER = "972500000779"
TG_CHAT = 990000000779
TG_SECRET = "sandbox-test-secret"
OK = {"ok": True, "key": None, "message_id": 1}
WA_DOWN = {"ok": False, "error": "WhatsApp disconnected — reconnecting", "key": None}
WA_TIMEOUT = {"ok": False, "error": "ReadTimeout", "key": None}
TG_DOWN = {"ok": False, "error": "telegram unreachable: ConnectError", "status_code": 502, "not_sent": True}
TG_TIMEOUT = {"ok": False, "error": "ReadTimeout", "status_code": 502, "not_sent": False}


def uid(tag):
    return f"e2elc-{tag}-{random.getrandbits(32):08x}"


def tg_update_id():
    return 99_000_000_000 + random.getrandbits(29)       # stays below 99_600_000_000: every id starts with "99"


def spec(delay=0.0, response=None, save="full", after_commit=None, raise_before=False, checkout=False,
         after_checkout=None):
    """What the fake agent does on one call. save: full | user_only | none; after_commit: None | raise | hang.
    checkout: call the REAL BACKEND_AI_AGENTS.create_checkout_link (the payment function behind it is a recorder);
    after_checkout: None | hang (the process dies after the side effect, before the turn is saved)."""
    return {"delay": delay, "response": response, "save": save, "after_commit": after_commit,
            "raise_before": raise_before, "checkout": checkout, "after_checkout": after_checkout}


CHECKOUT_PART = "00000000-0000-0000-0000-0000000000aa"


class Ctx:
    def __init__(self, channel, client, wh, creq, redis):
        self.channel, self.c, self.wh, self.creq, self.r = channel, client, wh, creq, redis
        self.rids, self.before = [], {}

    def rid_for(self, ref):
        return f"wa:{ref}" if self.channel == "wa" else f"tg:{ref}"

    async def post(self, body, ref=None):
        ref = ref if ref is not None else (uid("m") if self.channel == "wa" else tg_update_id())
        if self.channel == "wa":
            resp = await self.c.post("/api/v1/webhooks/whatsapp",
                                     json={"from": WA_SENDER, "body": body, "message_id": ref, "profile_name": "t"})
        else:
            resp = await self.c.post("/api/v1/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": TG_SECRET},
                                     json={"update_id": ref, "message": {"message_id": 1, "chat": {"id": TG_CHAT},
                                                                         "from": {"id": 1, "first_name": "t"},
                                                                         "text": body}})
        assert resp.status_code == 200, resp.text
        rid = self.rid_for(ref)
        if rid not in self.rids:
            self.rids.append(rid)
        self.before[rid] = await self.creq.get(self.r, rid)
        return rid

    async def rec(self, rid):
        return await self.creq.get(self.r, rid)

    async def die(self):
        """The process dies: all its tasks and in-memory state are gone; a NEW process (new BOOT_ID) starts."""
        for t in list(self.wh._AGENT_REPLY_TASKS):
            t.cancel()
        await asyncio.sleep(0.05)
        self.wh._ACTIVE_REQUESTS.clear()
        await self.r.delete(self.creq.BOOT_KEY.format(self.creq.BOOT_ID))
        self.creq.BOOT_ID = "testboot" + format(random.getrandbits(24), "06x")

    async def recover(self):
        return await self.wh.recover_agent_requests(self.r)

    async def conv_id(self):
        from sqlalchemy import text
        from BACKEND_DATABASE_MODELS import pii_session_factory
        key, val = ("whatsapp_phone", f"%{WA_SENDER[-7:]}") if self.channel == "wa" else ("telegram_chat_id", str(TG_CHAT))
        op = "LIKE" if self.channel == "wa" else "="
        async with pii_session_factory() as db:
            row = (await db.execute(text(f"SELECT id FROM conversations WHERE context->>'{key}' {op} :v "
                                         "ORDER BY started_at DESC LIMIT 1"), {"v": val})).fetchone()
        return str(row[0]) if row else ""

    async def history(self, rid=None):
        """Authoritative history rows of the test conversation (optionally only those tagged with rid)."""
        from sqlalchemy import text
        from BACKEND_DATABASE_MODELS import pii_session_factory
        cid = await self.conv_id()
        if not cid:
            return []
        async with pii_session_factory() as db:
            rows = (await db.execute(text(
                "SELECT role, agent_name, content, analysis, deleted_at IS NOT NULL, id, created_at FROM messages "
                "WHERE conversation_id = CAST(:c AS uuid) ORDER BY created_at"), {"c": cid})).fetchall()
        out = [{"role": r[0], "agent": r[1], "content": r[2], "a": r[3] or {}, "deleted": r[4], "id": str(r[5]),
                "created_at": str(r[6])} for r in rows]
        return [h for h in out if rid is None or h["a"].get("chat_request_id") == rid]


def run_case(name, channel, steps, *, agent=None, send=None, settle_s=1.5, retry_delays=(0.1, 0.1, 0.1),
             wait_s=0.3, deadline_s=1.5, patches=None, checkout_behaviour=None, real_checkout=False,
             stripe_setup=None):
    """real_checkout: the agent's checkout goes through the REAL create_whatsapp_checkout against a sandbox fixture
    part, with Stripe replaced by checkout_fixture.FakeStripe (stripe_setup(fake) configures it); the result then
    carries the persisted orders and the Stripe sessions."""
    """steps(ctx) drives the case. agent(message, n) → spec(...); send(text, n_same_text) → transport result.
    Returns a dict: sent (customer-visible texts in order, refused attempts included), calls, recs, hist, ctx."""
    import httpx
    from fastapi import FastAPI
    import routes.webhooks as wh
    import chat_requests as creq
    import social.whatsapp_provider as wprov
    import social.telegram_publisher as tprov
    from BACKEND_DATABASE_MODELS import pii_session_factory, pii_engine, Message
    sent, calls, result = [], [], {}
    seen_history, checkouts, state = [], [], {}
    boot0 = creq.BOOT_ID
    import BACKEND_AI_AGENTS as baa
    import routes.payments as pay

    async def fake_checkout(user_id, part_id, quantity, shipping_address, source="whatsapp"):
        """Stands in for create_whatsapp_checkout (Order + Stripe session): records every call, never calls Stripe."""
        checkouts.append({"part": part_id, "qty": quantity})
        n = len(checkouts)
        if (checkout_behaviour or {}).get(n) == "hang":
            await asyncio.sleep(3600)                   # the process dies inside the side effect
        return {"ok": True, "checkout_url": f"https://checkout.example.test/session-{n}", "order_id": f"order-{n}"}

    async def fake_shorten(url):
        return url

    async def fake_agent(user_id, message, conversation_id, db, source="web"):
        calls.append(message)
        s = (agent or (lambda m, n: spec()))(message, len(calls))
        # what the REAL history loader gives the model for this call
        rows = await baa._load_history_rows(db, conversation_id)
        seen_history.append([(r.role, r.content) for r in rows])
        await asyncio.sleep(s["delay"])
        if s["raise_before"]:
            raise RuntimeError("agent failed before answering")
        if s["checkout"]:
            url = await baa.create_checkout_link(part_id=state.get("part") or CHECKOUT_PART, quantity=1,
                                                 user_id=str(user_id), shipping_address={}, source=source)
            if s["after_checkout"] == "hang":
                await asyncio.sleep(3600)
            s = dict(s, response=f"PAY: {url}")
        if s["save"] == "none":
            return {"response": s["response"] if s["response"] is not None else f"ANSWER to: {message}"}
        um = Message(conversation_id=conversation_id, role="user", content=message, content_type="text")
        db.add(um)
        await db.flush()
        if s["save"] == "user_only":                    # a mid-turn commit, then the process dies
            await db.commit()
            await asyncio.sleep(3600)
        resp = s["response"] if s["response"] is not None else f"ANSWER to: {message}"
        am = Message(conversation_id=conversation_id, role="assistant", agent_name="fake_agent",
                     content=resp if isinstance(resp, str) else "", content_type="text")
        db.add(am)
        await db.flush()
        await db.commit()
        if s["after_commit"] == "hang":                 # the turn is saved; the process dies before reporting it
            await asyncio.sleep(3600)
        if s["after_commit"] == "raise":                # the turn is saved, then an error (e.g. refresh) surfaces
            raise RuntimeError("post-commit failure")
        return {"response": resp, "message_id": str(am.id)}

    async def _record(text):
        sent.append((text, time.time()))
        n = sum(1 for t, _ in sent if t == text)
        return send(text, n) if send else dict(OK)

    async def fake_wa(to, text, reply_jid=""):
        assert str(to).endswith(WA_SENDER[-4:]), "REFUSED: not the synthetic sender"
        return await _record(text)

    async def fake_tg(chat_id, text):
        assert str(chat_id) == str(TG_CHAT), "REFUSED: not the synthetic chat"
        return await _record(text)

    async def noop(*a, **k):
        return {"ok": True}

    async def main():
        mp = pytest.MonkeyPatch()
        mp.setattr(wh, "process_user_message", fake_agent)
        mp.setattr(wprov, "send_message", fake_wa)
        mp.setattr(wprov, "send_typing", noop)
        mp.setattr(tprov, "send_telegram_message", fake_tg)
        mp.setattr(tprov, "send_telegram_chat_action", noop)
        mp.setattr(pay, "shorten_payment_url", fake_shorten)
        if real_checkout:
            from checkout_fixture import FakeStripe, patch_stripe, create_fixture
            state["fake"] = FakeStripe()
            if stripe_setup:
                stripe_setup(state["fake"])
            patch_stripe(mp, state["fake"])
            state["part"] = await create_fixture()
        else:
            mp.setattr(pay, "create_whatsapp_checkout", fake_checkout)
        mp.setattr(wh, "WA_AGENT_REPLY_WAIT_S", wait_s)
        mp.setattr(wh, "WA_AGENT_LATE_DEADLINE_S", deadline_s)
        mp.setattr(creq, "DELIVERY_RETRY_DELAYS_S", list(retry_delays))
        mp.setenv("OWNER_WHATSAPP_PHONE", "+972599999999")
        mp.setenv("TELEGRAM_WEBHOOK_SECRET", TG_SECRET)
        for target, attr, val in (patches or []):
            mp.setattr(target, attr, val)
        app = FastAPI()
        app.include_router(wh.router)
        # #103: the case runs on its OWN Redis logical database. On the environment's shared one, the environment's
        # own backend (chat_request_recovery_loop, every 20 s) saw the case's simulated-dead requests and took them
        # over with the REAL agent — X3 then found 0 orders. Every redis user in this process resolves
        # get_redis() -> BACKEND_AUTH_SECURITY._redis_client at call time, so patching it isolates them all.
        import BACKEND_AUTH_SECURITY as _sec
        redis = await isolated_redis()
        mp.setattr(_sec, "_redis_client", redis)
        ctx = None
        try:
            assert pii_engine.url.host == "sandbox_postgres", f"REFUSED: PII host {pii_engine.url.host!r}"
            assert redis is not None and "sandbox" in str(redis.connection_pool.connection_kwargs.get("host")), \
                "REFUSED: not the sandbox Redis"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                ctx = Ctx(channel, c, wh, creq, redis)
                await steps(ctx)
                await asyncio.sleep(settle_s)
                result["recs"] = {rid: await ctx.rec(rid) for rid in ctx.rids}
                result["hist"] = await ctx.history()
                result["open"] = {x for x, _ in await creq.open_requests(redis)}
                if real_checkout:
                    from checkout_fixture import order_state
                    result["orders"] = await order_state(state["part"])
                    result["stripe_sessions"] = len(state["fake"].sessions)
                result["before"] = dict(ctx.before)
        finally:
            for t in list(wh._AGENT_REPLY_TASKS):       # stop the case's tasks BEFORE restoring the shared client
                t.cancel()
            await asyncio.sleep(0.05)
            mp.undo()
            wh._ACTIVE_REQUESTS.clear()
            creq.BOOT_ID = boot0
            if state.get("part"):
                from checkout_fixture import cleanup as _chk_cleanup
                await _chk_cleanup(state["part"])
            await _cleanup(redis, pii_session_factory)
            await redis.aclose()
            await _dispose(pii_engine)
    asyncio.run(main())
    result.update(sent=[t for t, _ in sent], calls=calls, seen_history=seen_history, checkouts=checkouts)
    for rid, rec in result["recs"].items():
        keep = ("status", "reason", "attempts", "delivery", "delivery_attempts", "delivered_kind", "history",
                "status_note", "recovered", "reconcile_attempts", "created_at", "updated_at")
        rows = [{"role": h["role"], "kind": h["a"].get("chat_kind") or h["a"].get("chat_turn"),
                 "delivery": h["a"].get("delivery"), "deleted": h["deleted"]}
                for h in result["hist"] if h["a"].get("chat_request_id") == rid]
        print("EVIDENCE " + json.dumps({"case": name, "channel": channel, "rid": rid,
                                        "before": {k: ((result.get("before") or {}).get(rid) or {}).get(k)
                                                   for k in ("status", "delivery", "attempts")},
                                        "after": {k: (rec or {}).get(k) for k in keep}, "history": rows,
                                        "customer_saw": [t[:28] for t in result["sent"]],
                                        "agent_calls": len(calls)}, ensure_ascii=False))
    return result


ISOLATED_REDIS_DB = 15


async def isolated_redis():
    """A client on a Redis logical database the environment's backend never uses (#103): its recovery loop scans
    `chat_req:open` in ITS database only, so it cannot see or take over this process's requests."""
    import os
    from urllib.parse import urlparse, urlunparse
    import redis.asyncio as aioredis
    p = urlparse(os.environ.get("REDIS_URL", ""))
    assert "sandbox" in (p.hostname or ""), f"REFUSED: not the sandbox Redis ({p.hostname!r})"
    backend_db = int((p.path or "/0").lstrip("/") or 0)
    db = ISOLATED_REDIS_DB if backend_db != ISOLATED_REDIS_DB else ISOLATED_REDIS_DB - 1
    client = aioredis.from_url(urlunparse(p._replace(path=f"/{db}", query="")), decode_responses=True)
    await client.ping()
    assert client.connection_pool.connection_kwargs.get("db") == db != backend_db
    return client


async def _cleanup(redis, pii_session_factory):
    from sqlalchemy import text
    async with pii_session_factory() as db:
        for where in ("context->>'whatsapp_phone' LIKE :w", "context->>'telegram_chat_id' = :t"):
            await db.execute(text(f"DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE {where})"),
                             {"w": f"%{WA_SENDER[-7:]}", "t": str(TG_CHAT)})
            await db.execute(text(f"DELETE FROM conversations WHERE {where}"), {"w": f"%{WA_SENDER[-7:]}", "t": str(TG_CHAT)})
        await db.commit()
    if redis is None:
        return
    for pat in ("chat_req:wa:e2elc-*", "chat_req:tg:99*", "chat_req:checkout:wa:e2elc-*", "chat_req:checkout:tg:99*", "wa:msg:e2elc-*", "tg:update:99*", "wa:agent_inflight:*",
                "chat_req:boot:testboot*", "chat_req:boot:otherboot*", "chat_req:boot:deadboot*"):
        async for k in redis.scan_iter(pat):
            await redis.delete(k)
    for m in await redis.smembers("chat_req:open"):
        m = m.decode() if isinstance(m, bytes) else m
        if m.startswith("wa:e2elc-") or m.startswith("tg:99"):
            await redis.srem("chat_req:open", m)


async def _dispose(pii_engine):
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


async def index_present():
    """Migration 0040's unique index must be live in the sandbox for the DB-enforced cases."""
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import pii_session_factory
    async with pii_session_factory() as db:
        return bool((await db.execute(text(
            "SELECT 1 FROM pg_indexes WHERE tablename='messages' AND indexname='uq_messages_chat_request_role'"))).scalar())


def customer(res, channel="wa"):
    """Texts the customer was sent, minus the one-time WhatsApp welcome of a new conversation (asserted once)."""
    texts = res["sent"]
    if channel == "wa":
        assert texts and "👋" in texts[0].split("\n")[0], texts[:1]
        assert sum(1 for t in texts if "👋" in t.split("\n")[0]) == 1
        return texts[1:]
    return texts


def answer_rows(hist, rid):
    return [h for h in hist if h["a"].get("chat_request_id") == rid and not h["deleted"]]
