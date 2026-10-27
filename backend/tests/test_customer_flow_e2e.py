"""
Script: tests/test_customer_flow_e2e.py
Purpose: Integrated customer-flow end-to-end tests for the reply-language and policy fixes
         (2026-10-27). Drives the REAL process_user_message — real PII database (conversation,
         messages, context, agent actions), real router + _enforce_route, real agents, real
         BaseAgent.think enforcement, real customer editor, real guards, real persistence — on all
         three channels. ONLY the LLM is replaced (scripted per role); no external service is reached.
Process: requires an ISOLATED database (tests/isolated_db_guard.py): RUN_DB_MUTATION_TESTS=1,
         ENVIRONMENT=test, a non-production host, and a live check that the server is not production.
         Skips otherwise. Run with the disposable stack described in FIXES_TRACKER (2026-10-27).
Data Imported/Modified: test users / conversations / messages in the disposable PII database only.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import json
import os
import re
import uuid

import pytest

import isolated_db_guard as guard

_REFUSAL = guard.url_refusal(os.getenv("DATABASE_PII_URL")) or guard.url_refusal(os.getenv("DATABASE_URL"))
pytestmark = pytest.mark.skipif(bool(_REFUSAL), reason=f"isolated DB required: {_REFUSAL}")

import BACKEND_AI_AGENTS as A                                     # noqa: E402
import policy_guard as pg                                         # noqa: E402
import reply_language as rl                                       # noqa: E402

CHANNELS = ("whatsapp", "telegram", "web")


class RoleLLM:
    """Scripted LLM: answers the router with JSON, the editor with a scripted rewrite (or the raw
    text), and agents with the scripted reply for the turn. Records every call."""

    def __init__(self):
        self.calls = []
        self.route_to = "service_agent"
        self.agent_replies = []
        self.editor = None            # None -> echo raw

    async def __call__(self, prompt, system="", **kw):
        self.calls.append({"prompt": prompt, "system": system})
        if system.startswith(A.RouterAgent.system_prompt[:60]):
            return json.dumps({"agent": self.route_to, "confidence": 0.9, "intent": "e2e"})
        if "customer-service editor" in system:
            if self.editor is not None:
                return self.editor
            m = re.search(r"Raw response from the system:\n(.*?)\n\nThe customer's message", prompt, re.S)
            return m.group(1) if m else prompt
        return self.agent_replies.pop(0) if len(self.agent_replies) > 1 else self.agent_replies[0]


@pytest.fixture
async def pii():
    from BACKEND_DATABASE_MODELS import pii_session_factory, pii_engine
    async with pii_session_factory() as db:
        refusal = await guard.live_refusal(db)
        assert refusal is None, f"refusing to write: {refusal}"      # FAIL, never skip, if it looks like prod
        yield db
    await pii_engine.dispose()


@pytest.fixture
def llm(monkeypatch):
    f = RoleLLM()
    monkeypatch.setattr(A, "hf_text", f)
    monkeypatch.setattr(A, "hf_text_fast", f)
    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
    return f


async def _user(db):
    from BACKEND_DATABASE_MODELS import User
    u = User(email=f"e2e-{uuid.uuid4().hex[:10]}@example.com", full_name="E2E Test", role="customer")
    db.add(u)
    await db.commit()
    return str(u.id)


async def _stored(db, conversation_id):
    from sqlalchemy import select
    from BACKEND_DATABASE_MODELS import Message, Conversation
    msgs = (await db.execute(select(Message).where(Message.conversation_id == conversation_id)
                             .order_by(Message.created_at))).scalars().all()
    conv = (await db.execute(select(Conversation).where(Conversation.id == conversation_id))).scalar_one()
    return msgs, conv


GOOD = {
    "he": "אני מבינה. כדי לבדוק את זה אשמח לקבל את מספר ההזמנה שלך.",
    "ar": "أفهم ذلك. لأتحقق من الأمر، أرسل لي من فضلك رقم طلبك.",
    "en": "I understand. To check this for you, please send me your order number.",
}
ASK = {
    "he": "שלום, אני לא מצליח להתחבר לחשבון שלי",
    "ar": "مرحبا، لا أستطيع تسجيل الدخول إلى حسابي",
    "en": "Hi, I cannot sign in to my account",
}


@pytest.mark.parametrize("lang", ["he", "ar", "en"])
@pytest.mark.parametrize("channel", CHANNELS)
async def test_e2e_reply_language_persisted(pii, llm, channel, lang):
    llm.route_to = "security_agent"
    llm.agent_replies = [GOOD[lang]]
    uid = await _user(pii)
    out = await A.process_user_message(uid, ASK[lang], None, pii, source=channel)
    assert out["agent"] == "security_agent"
    assert rl.reply_matches_language(out["response"], lang), out["response"]
    msgs, conv = await _stored(pii, uuid.UUID(out["conversation_id"]))
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[-1].content == out["response"]
    assert conv.context.get("preferred_lang") == lang
    agent_calls = [c for c in llm.calls if "[REPLY LANGUAGE - FINAL AND OVERRIDING]" in c["system"]]
    assert agent_calls and rl.NAMES[lang] in agent_calls[0]["system"][-900:]
    assert rl.REPLY_LANGUAGE.get() is None                       # turn scope released


@pytest.mark.parametrize("channel", CHANNELS)
async def test_e2e_language_switch_within_conversation(pii, llm, channel):
    llm.route_to = "service_agent"
    uid = await _user(pii)
    conv_id = None
    for lang in ("he", "en", "ar"):
        llm.agent_replies = [GOOD[lang]]
        out = await A.process_user_message(uid, ASK[lang], conv_id, pii, source=channel)
        conv_id = out["conversation_id"]
        assert rl.reply_matches_language(out["response"], lang), (lang, out["response"])
    msgs, conv = await _stored(pii, uuid.UUID(conv_id))
    assert len(msgs) == 6 and conv.context.get("preferred_lang") == "ar"


@pytest.mark.parametrize("channel", CHANNELS)
async def test_e2e_wrong_language_model_is_corrected_or_localized(pii, llm, channel):
    llm.route_to = "finance_agent"
    llm.agent_replies = [GOOD["he"], GOOD["en"]]                 # first draft wrong, retry right
    uid = await _user(pii)
    out = await A.process_user_message(uid, "Can I get a VAT invoice for my order?", None, pii, source=channel)
    assert out["response"] == GOOD["en"]


@pytest.mark.parametrize("lang", ["he", "ar", "en"])
@pytest.mark.parametrize("channel", CHANNELS)
async def test_e2e_fabricated_shipping_range_never_reaches_customer(pii, llm, channel, lang):
    rng = {"he": "המשלוח עולה ₪29–₪149 לפי הספק.", "ar": "الشحن 29-149 ₪ حسب المورد.",
           "en": "Shipping costs ₪29–₪149 depending on the supplier."}[lang]
    llm.route_to = "service_agent"
    llm.agent_replies = [rng, rng]
    uid = await _user(pii)
    q = {"he": "כמה עולה המשלוח לחיפה?", "ar": "كم تكلفة الشحن إلى حيفا؟", "en": "How much is shipping to Haifa?"}[lang]
    out = await A.process_user_message(uid, q, None, pii, source=channel)
    assert not re.search(r"29\s*[-–]\s*₪?\s*149", out["response"])
    assert not pg.currency_amounts(out["response"])
    assert rl.reply_matches_language(out["response"], lang)
    msgs, _ = await _stored(pii, uuid.UUID(out["conversation_id"]))
    assert "149" not in msgs[-1].content                         # nothing fabricated was persisted


@pytest.mark.parametrize("channel", CHANNELS)
async def test_e2e_router_cannot_send_customer_to_admin_agent(pii, llm, channel):
    llm.route_to = "social_media_manager_agent"
    llm.agent_replies = [GOOD["en"]]
    uid = await _user(pii)
    out = await A.process_user_message(uid, "Please write a facebook campaign for my garage", None, pii, source=channel)
    assert out["agent"] == "service_agent"
    assert out["routing"]["agent"] == "service_agent"
    assert out["routing"].get("route_blocked_from") == "social_media_manager_agent"


@pytest.mark.parametrize("channel", CHANNELS)
async def test_e2e_fabricated_payment_link_never_reaches_customer(pii, llm, channel):
    fake = "Pay here: https://checkout.stripe.com/c/pay/cs_test_1234567890abcdef"
    llm.route_to = "orders_agent"
    llm.agent_replies = [fake, fake]
    uid = await _user(pii)
    out = await A.process_user_message(uid, "I want to pay for my order", None, pii, source=channel)
    assert "stripe.com" not in out["response"] and "cs_test" not in out["response"]


@pytest.mark.parametrize("channel", CHANNELS)
async def test_e2e_editor_cannot_change_language_or_add_facts(pii, llm, channel):
    raw = ("I understand you are waiting for your order. Please send me the order number and the e-mail "
           "address you used, and I will check where the shipment is right away.")
    llm.route_to = "orders_agent"
    llm.agent_replies = [raw]
    llm.editor = "אני מבינה שאתה מחכה להזמנה, המשלוח עולה ₪49. שלח לי מספר הזמנה."   # wrong language + new amount
    uid = await _user(pii)
    out = await A.process_user_message(uid, "Where is my order? I am waiting a long time", None, pii, source=channel)
    assert any("customer-service editor" in c["system"] for c in llm.calls), "editor pass was not exercised"
    assert out["response"] == raw
