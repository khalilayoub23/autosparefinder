"""
Script: tests/test_conversation_ownership.py
Purpose: #117 — a conversation id supplied by a client must only ever resolve to a conversation OWNED by the
         authenticated user. Before: POST /api/v1/chat/message, the audio upload and the shared brain
         (process_user_message / its background variant) loaded `Conversation.id == <client id>` with no owner filter,
         so user A could append messages to user B's conversation and make the agent read B's history; POST
         /api/v1/chat/rate attached a rating to any conversation id.
Process: isolated DB only (tests/isolated_db_guard.py) — skips otherwise. Real PII database tables, scripted model.
Data Imported/Modified: two users, their conversations and messages in the disposable PII DB.
Last Updated: 2026-11-03
"""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace

import pytest

import isolated_db_guard as guard

_REFUSAL = guard.url_refusal(os.getenv("DATABASE_PII_URL")) or guard.url_refusal(os.getenv("DATABASE_URL"))
pytestmark = pytest.mark.skipif(bool(_REFUSAL), reason=f"isolated DB required: {_REFUSAL}")

import BACKEND_AI_AGENTS as A                                     # noqa: E402

SECRET = "VICTIM-SECRET-ORDER-PRIVATE-4417"


class CaptureLLM:
    """Router -> service agent; records every prompt/system/kwarg the model is shown."""

    def __init__(self):
        self.seen = []

    async def __call__(self, prompt, system="", **kw):
        self.seen.append(f"{system}\n{prompt}\n{kw}")
        if system.startswith(A.RouterAgent.system_prompt[:60]):
            return json.dumps({"agent": "service_agent", "confidence": 0.9, "intent": "general"})
        return "Hello, how can I help?"


@pytest.fixture(autouse=True)
async def _fresh_engines():
    yield
    from BACKEND_DATABASE_MODELS import engine, pii_engine
    await pii_engine.dispose()
    await engine.dispose()


@pytest.fixture
def llm(monkeypatch):
    f = CaptureLLM()
    monkeypatch.setattr(A, "hf_text", f)
    monkeypatch.setattr(A, "hf_text_fast", f)
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    return f


async def _seed():
    """Two users; the victim owns a conversation that already holds a private message."""
    from BACKEND_DATABASE_MODELS import pii_session_factory, User, Conversation, Message
    tag = uuid.uuid4().hex[:8]
    async with pii_session_factory() as db:
        assert await guard.live_refusal(db) is None
        victim = User(email=f"own-v-{tag}@example.com", full_name="Victim", role="customer", is_verified=True, is_active=True)
        attacker = User(email=f"own-a-{tag}@example.com", full_name="Attacker", role="customer", is_verified=True, is_active=True)
        db.add_all([victim, attacker])
        await db.flush()
        conv = Conversation(user_id=victim.id, title="victim chat", is_active=True)
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content=f"my private order is {SECRET}", content_type="text"))
        await db.commit()
        return SimpleNamespace(victim=victim, attacker=attacker, victim_conv=str(conv.id))


async def _count_messages(conv_id: str) -> int:
    from sqlalchemy import select, func
    from BACKEND_DATABASE_MODELS import pii_session_factory, Message
    async with pii_session_factory() as db:
        return (await db.execute(select(func.count(Message.id)).where(Message.conversation_id == uuid.UUID(conv_id)))).scalar_one()


# ── the single enforcement point ────────────────────────────────────────────────────────────────────────────────────
async def test_helper_returns_only_owned_conversation():
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    async with pii_session_factory() as db:
        own = await A.load_owned_conversation(db, s.victim_conv, str(s.victim.id))
        assert own is not None and str(own.id) == s.victim_conv
        assert await A.load_owned_conversation(db, s.victim_conv, str(s.attacker.id)) is None


@pytest.mark.parametrize("bad_conv", ["", "not-a-uuid", "123", "' OR 1=1 --", None])
async def test_helper_rejects_missing_or_malformed_conversation_id(bad_conv):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    async with pii_session_factory() as db:
        assert await A.load_owned_conversation(db, bad_conv, str(s.victim.id)) is None


@pytest.mark.parametrize("bad_user", ["", "not-a-uuid", None, str(uuid.uuid4())])
async def test_helper_rejects_missing_malformed_or_unknown_user(bad_user):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    async with pii_session_factory() as db:
        assert await A.load_owned_conversation(db, s.victim_conv, bad_user) is None


# ── the shared brain (WhatsApp / Telegram / web / audio all use it) ─────────────────────────────────────────────────
async def test_brain_does_not_continue_another_users_conversation(llm):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    before = await _count_messages(s.victim_conv)
    async with pii_session_factory() as db:
        out = await A.process_user_message(str(s.attacker.id), "what did I order?", s.victim_conv, db, source="web")
        await db.commit()
    assert out["conversation_id"] != s.victim_conv, "forged conversation id was accepted"
    assert await _count_messages(s.victim_conv) == before, "attacker wrote into the victim's conversation"
    assert not any(SECRET in x for x in llm.seen), "victim's history reached the model on the attacker's turn"
    assert SECRET not in out["response"]


async def test_brain_still_continues_own_conversation(llm):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    before = await _count_messages(s.victim_conv)
    async with pii_session_factory() as db:
        out = await A.process_user_message(str(s.victim.id), "hello again", s.victim_conv, db, source="web")
        await db.commit()
    assert out["conversation_id"] == s.victim_conv
    assert await _count_messages(s.victim_conv) > before
    assert any(SECRET in x for x in llm.seen), "own history must still be given to the agent"


async def test_background_variant_does_not_write_into_another_users_conversation(llm):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    before = await _count_messages(s.victim_conv)
    async with pii_session_factory() as db:
        await A._process_agent_response_for_message_impl(str(s.attacker.id), "hi", s.victim_conv, db, source="web")
        await db.commit()
    assert await _count_messages(s.victim_conv) == before
    assert not any(SECRET in x for x in llm.seen)


# ── HTTP handlers (called directly with an authenticated principal) ─────────────────────────────────────────────────
async def _call_send_message(monkeypatch, user, conv_id, message="hello"):
    import routes.chat as C
    from BACKEND_DATABASE_MODELS import pii_session_factory

    async def _noop(*a, **k):          # the background agent is not under test here
        return None

    monkeypatch.setattr(C, "process_agent_response_for_message", _noop, raising=False)
    data = C.ChatMessageRequest(conversation_id=conv_id, message=message)
    async with pii_session_factory() as db:
        out = await C.send_message(data, SimpleNamespace(client=None), user, db, None)
        await db.commit()
    return out


async def test_send_message_forged_conversation_id_is_not_accepted(monkeypatch):
    s = await _seed()
    before = await _count_messages(s.victim_conv)
    out = await _call_send_message(monkeypatch, s.attacker, s.victim_conv)
    assert out["conversation_id"] != s.victim_conv
    assert await _count_messages(s.victim_conv) == before


async def test_send_message_own_conversation_still_works(monkeypatch):
    s = await _seed()
    before = await _count_messages(s.victim_conv)
    out = await _call_send_message(monkeypatch, s.victim, s.victim_conv)
    assert out["conversation_id"] == s.victim_conv
    assert await _count_messages(s.victim_conv) == before + 1


async def test_send_message_without_conversation_id_creates_one_for_the_caller(monkeypatch):
    s = await _seed()
    out = await _call_send_message(monkeypatch, s.attacker, None)
    assert out["conversation_id"] not in ("", None, s.victim_conv)


@pytest.mark.parametrize("bad_conv", ["not-a-uuid", "123", "' OR 1=1 --"])
async def test_send_message_malformed_conversation_id_is_rejected(monkeypatch, bad_conv):
    from fastapi import HTTPException
    s = await _seed()
    with pytest.raises(HTTPException) as e:
        await _call_send_message(monkeypatch, s.attacker, bad_conv)
    assert e.value.status_code == 422


async def test_rate_agent_requires_owned_conversation():
    import routes.chat as C
    from fastapi import HTTPException
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    async with pii_session_factory() as db:
        with pytest.raises(HTTPException) as e:
            await C.rate_agent(s.victim_conv, "service_agent", 5, None, s.attacker, db)
        assert e.value.status_code == 404
        ok = await C.rate_agent(s.victim_conv, "service_agent", 5, None, s.victim, db)
        assert ok == {"message": "Rating submitted"}


# ── server-resolved conversations (WhatsApp / Telegram) ─────────────────────────────────────────────────────────────
# WhatsApp picks the conversation BY PHONE NUMBER, not by user: a customer who chatted anonymously (conversation owned
# by the shared anonymous user) and then registered is served as the new user inside the OLD conversation. That id is
# resolved by the server from the channel identity, never taken from a client, so only server code may say
# `trusted_conversation=True`; every client-supplied id keeps the strict owner rule.
async def test_trusted_server_resolved_conversation_is_continued_across_owners(llm):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    async with pii_session_factory() as db:
        out = await A.process_user_message(str(s.attacker.id), "hi", s.victim_conv, db, source="whatsapp",
                                           trusted_conversation=True)
        await db.commit()
    assert out["conversation_id"] == s.victim_conv


async def test_untrusted_default_still_refuses_another_owners_conversation(llm):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    s = await _seed()
    async with pii_session_factory() as db:
        out = await A.process_user_message(str(s.attacker.id), "hi", s.victim_conv, db, source="whatsapp")
        await db.commit()
    assert out["conversation_id"] != s.victim_conv


@pytest.mark.parametrize("channel", ["wa", "tg"])
async def test_whatsapp_telegram_durable_path_keeps_the_conversation(llm, channel):
    """The real server path (_run_agent_for) for a customer who registered after an anonymous chat."""
    import routes.webhooks as W
    s = await _seed()
    before = await _count_messages(s.victim_conv)
    rec = {"channel": channel, "user_key": str(s.attacker.id), "conv_id": s.victim_conv, "body": "hello again", "target": "x"}
    out = await W._run_agent_for(rec, f"{channel}:own-{uuid.uuid4().hex[:8]}")
    assert out["conversation_id"] == s.victim_conv
    assert await _count_messages(s.victim_conv) > before


def test_trusted_flag_is_only_ever_set_by_server_code():
    """No HTTP handler may pass it (a client could then pick any conversation)."""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for dirpath, _d, files in os.walk(root):
        if any(x in dirpath for x in ("/tests", "/devtests", "/legacy", "/node_modules", "/state", "/backups")):
            continue
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(dirpath, f), encoding="utf-8", errors="ignore").read()
                if re.search(r"trusted_conversation\s*=\s*True", src):
                    offenders.append(os.path.relpath(os.path.join(dirpath, f), root))
    assert sorted(offenders) == ["routes/webhooks.py"], offenders


def test_test_doubles_mirror_the_real_process_user_message_signature():
    """Two lifecycle suites replace process_user_message with a stub. A stub that lacks a parameter the real function
    gained (trusted_conversation, #117) breaks every WhatsApp/Telegram lifecycle test with a TypeError - keep them in step."""
    import inspect
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    real = list(inspect.signature(A.process_user_message).parameters)
    for f in ("chat_lifecycle_harness.py", "test_whatsapp_late_reply_e2e.py"):
        src = open(os.path.join(here, f), encoding="utf-8").read()
        m = re.search(r"async def fake_agent\(([^)]*)\)", src)
        assert m, f
        params = [p.split("=")[0].strip() for p in m.group(1).split(",")]
        assert params == real, f"{f}: stub {params} != real {real}"


def test_customer_chat_routes_resolve_conversations_only_through_the_owner_loader():
    """#117 live check (2026-11-04): GET/DELETE /chat/conversations/{id}, handoff request/feedback and checkout-smoke
    compared the client-supplied id straight against Conversation.id, so a malformed id reached Postgres and answered
    500. Every customer-facing conversation lookup in routes/chat.py goes through load_owned_conversation (404 for
    malformed / unknown / foreign alike)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "routes", "chat.py"), encoding="utf-8").read()
    assert "Conversation.id ==" not in src
