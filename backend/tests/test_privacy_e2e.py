"""
Script: tests/test_privacy_e2e.py
Purpose: End-to-end privacy check through the REAL process_user_message on a disposable database:
         an unregistered (shared anonymous id) customer asking about orders must never receive any order
         of that shared id; a registered customer still gets their own order (2026-10-27).
         The scripted model ECHOES every order number it was given, so any leak to the model is visible.
Process: isolated DB only (tests/isolated_db_guard.py) — skips otherwise.
Data Imported/Modified: two anonymous orders, one registered user + order, conversations in the disposable PII DB.
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

ANON = "00000000-0000-0000-0000-000000000001"
ORDER_RE = re.compile(r"PRIV-[A-Z0-9-]+")


class EchoLLM:
    """Router -> orders_agent; agents echo every order number visible in their prompt."""

    def __init__(self):
        self.systems = []

    async def __call__(self, prompt, system="", **kw):
        if system.startswith(A.RouterAgent.system_prompt[:60]):
            return json.dumps({"agent": "orders_agent", "confidence": 0.9, "intent": "order_status"})
        self.systems.append(system)
        found = sorted(set(ORDER_RE.findall(system + prompt)))
        if found:
            return "Your orders: " + ", ".join(found)
        return "For privacy I can check orders only for signed-in customers. Please sign in at https://autosparefinder.co.il"


@pytest.fixture(autouse=True)
async def _fresh_engines():
    """Same teardown as test_customer_flow_e2e: engines are module-level; each test runs in its own loop."""
    yield
    from BACKEND_DATABASE_MODELS import engine, pii_engine
    await pii_engine.dispose()
    await engine.dispose()


@pytest.fixture
def llm(monkeypatch):
    f = EchoLLM()
    monkeypatch.setattr(A, "hf_text", f)
    monkeypatch.setattr(A, "hf_text_fast", f)
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    return f


async def _seed():
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import pii_session_factory, User
    tag = uuid.uuid4().hex[:6].upper()
    anon_orders = [f"PRIV-ANON-{tag}-1", f"PRIV-ANON-{tag}-2"]
    async with pii_session_factory() as db:
        assert await guard.live_refusal(db) is None
        u = User(email=f"e2e-priv-{tag.lower()}@example.com", full_name="E2E", role="customer", is_verified=True, is_active=True)
        db.add(u)
        await db.flush()
        own = f"PRIV-OWN-{tag}"
        for num, uid in ((anon_orders[0], ANON), (anon_orders[1], ANON), (own, str(u.id))):
            await db.execute(text("INSERT INTO orders (id, user_id, order_number, status, subtotal, vat_amount, shipping_cost, "
                                  "total_amount, currency, shipping_address, created_at, updated_at) VALUES (gen_random_uuid(), "
                                  "CAST(:u AS uuid), :n, 'pending_payment', 100, 18, 30, 148, 'ILS', '{\"city\": \"Haifa\"}', NOW(), NOW())"),
                             {"u": uid, "n": num})
        await db.commit()
        return anon_orders, own, str(u.id)


@pytest.mark.parametrize("channel", ["whatsapp", "telegram", "web"])
async def test_anonymous_customer_never_sees_shared_orders(llm, channel):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    anon_orders, own, _uid = await _seed()
    async with pii_session_factory() as db:
        out = await A.process_user_message(ANON, "Where is my order? I ordered last week", None, db, source=channel)
    assert out["agent"] == "orders_agent"
    leaked = [n for n in anon_orders + [own] if n in out["response"] or any(n in s for s in llm.systems)]
    assert leaked == [], f"leaked to model/reply: {leaked}"
    assert "sign in" in out["response"].lower()


async def test_registered_customer_still_sees_own_order_only(llm):
    from BACKEND_DATABASE_MODELS import pii_session_factory
    anon_orders, own, uid = await _seed()
    async with pii_session_factory() as db:
        out = await A.process_user_message(uid, "Where is my order? I ordered last week", None, db, source="web")
    assert own in out["response"]
    assert not any(n in out["response"] for n in anon_orders)
