"""
Script: tests/test_privacy_and_delivery.py
Purpose: (1) Unregistered WhatsApp/Telegram customers share ONE user id — no order and no user-scope memory
         of that id may reach any customer (2026-10-27: OrdersAgent listed the 15 anonymous orders to an
         anonymous asker; a user-scope `preferred_language` memory was loaded for every anonymous customer).
         (2) Delivery-time claims must be grounded like amounts (a live Arabic reply invented "2‑5 أيام عمل").
Process: pure logic with fakes — no network, no DB.
Data Imported/Modified: none.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import BACKEND_AI_AGENTS as A
import policy_guard as pg
import reply_language as rl

ANON = "00000000-0000-0000-0000-000000000001"


def test_shared_anonymous_id_matches_the_webhooks_constant():
    from routes import webhooks
    assert str(webhooks.WHATSAPP_ANON_USER_ID) == A.SHARED_ANON_USER_ID == ANON
    assert A._is_shared_anonymous_user(ANON) and A._is_shared_anonymous_user(uuid.UUID(ANON))
    assert not A._is_shared_anonymous_user(str(uuid.uuid4())) and not A._is_shared_anonymous_user(None)


# ── orders ────────────────────────────────────────────────────────────────────

class _FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def __call__(self, prompt, system="", **kw):
        self.calls.append(system)
        return self.reply


@pytest.fixture
def llm(monkeypatch):
    f = _FakeLLM("I can check orders only for signed-in customers. Please sign in at https://autosparefinder.co.il")
    monkeypatch.setattr(A, "hf_text", f)
    monkeypatch.setattr(A, "hf_text_fast", f)
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    return f


async def test_anonymous_customer_never_gets_order_data(llm, monkeypatch):
    import BACKEND_DATABASE_MODELS as M

    def _boom():
        raise AssertionError("orders must not be queried for the shared anonymous id")
    monkeypatch.setattr(M, "pii_session_factory", _boom)
    out = await A.OrdersAgent().process("Where is my order?", [], None, user_id=ANON, source="whatsapp")
    assert "[CUSTOMER NOT IDENTIFIED]" in llm.calls[0] and "never list or guess orders" in llm.calls[0]
    assert "sign in" in out.lower()


async def test_registered_customer_orders_are_still_looked_up(llm, monkeypatch):
    import BACKEND_DATABASE_MODELS as M
    queried = {}

    class _Res:
        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            queried["sql"] = str(stmt)
            return _Res()
    monkeypatch.setattr(M, "pii_session_factory", lambda: _S())
    uid = str(uuid.uuid4())
    await A.OrdersAgent().process("Where is my order?", [], None, user_id=uid, source="web")
    assert "orders" in queried["sql"].lower()
    assert "[CUSTOMER NOT IDENTIFIED]" not in llm.calls[0]


# ── shared memory ─────────────────────────────────────────────────────────────

class _CaptureDB:
    def __init__(self):
        self.stmts, self.added = [], []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []), scalar_one_or_none=lambda: None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _sql(stmt):
    from sqlalchemy.dialects import postgresql
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


async def test_anonymous_memory_load_is_conversation_only():
    db = _CaptureDB()
    assert await A._load_shared_memory(db, ANON, None, None) == []           # no conversation -> nothing
    assert db.stmts == []
    conv = str(uuid.uuid4())
    await A._load_shared_memory(db, ANON, conv, None)
    sql = _sql(db.stmts[-1])
    assert "'conversation'" in sql and "'user'" not in sql


async def test_registered_memory_load_still_includes_user_scope():
    db = _CaptureDB()
    await A._load_shared_memory(db, str(uuid.uuid4()), str(uuid.uuid4()), None)
    assert "'user'" in _sql(db.stmts[-1])


async def test_anonymous_user_scope_memory_is_saved_per_conversation():
    db = _CaptureDB()
    conv = str(uuid.uuid4())
    await A._save_shared_memory_updates(db, ANON, conv,
                                        [{"scope": "user", "memory_key": "preferred_language", "memory_value": {"lang": "ar"}}])
    assert db.added and all(getattr(r, "scope", None) == "conversation" for r in db.added)
    db2 = _CaptureDB()
    await A._save_shared_memory_updates(db2, ANON, None,
                                        [{"scope": "user", "memory_key": "preferred_language", "memory_value": {"lang": "ar"}}])
    assert db2.added == []                                                   # no conversation -> not saved


# ── delivery time ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,grounding,bad", [
    ("العفو! عادةً الشحن داخل إسرائيل يستغرق 2‑5 أيام عمل، لكن الوقت والتكلفة يظهرا بالضبط.", [], True),  # live
    ("Delivery usually takes 7-14 business days.", [], True),
    ("המשלוח מגיע בדרך כלל תוך 10 ימים.", [], True),
    ("Shipping takes about 2 weeks.", [], True),
    ("The TRW pads arrive in about 6 days.", ["אספקה: 6 ימים"], False),
    ("You can return a part within 14 days of delivery.", [], False),
    ("Every part has a 12 months warranty.", [], False),
    ("Delivery time depends on the supplier and is shown with each part.", [], False),
    ("Toyota Corolla 2018 brake pads.", [], False),
])
def test_delivery_time_must_be_grounded(text, grounding, bad):
    kinds = {v.kind for v in pg.validate_customer_reply(text, grounding)}
    assert ("ungrounded_delivery_time" in kinds) is bad


@pytest.mark.parametrize("lang,bad,good", [
    ("ar", "عادةً الشحن داخل إسرائيل يستغرق 2‑5 أيام عمل.", "مدة التوصيل تعتمد على المورد وتظهر مع كل قطعة. ما القطعة التي تحتاجها؟"),
    ("en", "Delivery usually takes 7-14 business days.", "Delivery time depends on the supplier and is shown with each part. Which part do you need?"),
    ("he", "המשלוח מגיע בדרך כלל תוך 10 ימים.", "זמן האספקה תלוי בספק ומוצג ליד כל חלק. איזה חלק אתה צריך?"),
])
async def test_agent_invented_delivery_window_is_corrected(monkeypatch, lang, bad, good):
    calls = []

    async def fake(prompt, system="", **kw):
        calls.append(prompt)
        return bad if len(calls) == 1 else good
    monkeypatch.setattr(A, "hf_text", fake)
    monkeypatch.setattr(A, "hf_text_fast", fake)
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    q = {"ar": "كم يستغرق التوصيل؟", "en": "How long is delivery?", "he": "כמה זמן לוקח המשלוח?"}[lang]
    out = await A.ServiceAgent().think([{"role": "user", "content": q}], source="telegram")
    assert out == good and len(calls) == 2 and "delivery time" in calls[1]
    assert rl.reply_matches_language(out, lang)


def test_policy_text_forbids_generic_delivery_windows():
    assert "no \"usually 2-5 business days\"" in A.TELEGRAM_BOT_POLICY


# ── orders fallback text for anonymous customers (2026-10-27) ──────────────────

@pytest.mark.parametrize("lang,q", [("he", "איפה ההזמנה שלי?"), ("ar", "وين وصل طلبي؟"), ("en", "Where is my order?")])
async def test_anonymous_orders_fallback_guides_to_sign_in(monkeypatch, lang, q):
    import BACKEND_DATABASE_MODELS as M

    async def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(A, "hf_text", boom)
    monkeypatch.setattr(A, "hf_text_fast", boom)
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    monkeypatch.setattr(M, "pii_session_factory", lambda: (_ for _ in ()).throw(AssertionError("no order query")))
    rl.REPLY_LANGUAGE.set(lang)
    try:
        out = await A.OrdersAgent().process(q, [], None, user_id=ANON, source="whatsapp")
    finally:
        rl.REPLY_LANGUAGE.set(None)
    assert out == rl.pick(A.BaseAgent._OFFLINE_TEXTS["orders_agent_anonymous"], lang)
    assert "autosparefinder.co.il" in out and rl.reply_matches_language(out, lang)
    assert A.ANON_ORDER_TURN.get() is False                      # flag scoped to the turn


async def test_registered_orders_fallback_unchanged(monkeypatch):
    import BACKEND_DATABASE_MODELS as M

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    class _Res:
        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            return _Res()
    monkeypatch.setattr(A, "hf_text", boom)
    monkeypatch.setattr(A, "hf_text_fast", boom)
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    monkeypatch.setattr(M, "pii_session_factory", lambda: _S())
    rl.REPLY_LANGUAGE.set("en")
    try:
        out = await A.OrdersAgent().process("Where is my order?", [], None, user_id=str(uuid.uuid4()), source="web")
    finally:
        rl.REPLY_LANGUAGE.set(None)
    assert out == A.BaseAgent._OFFLINE_TEXTS["orders_agent"]["en"]
