"""
Script: tests/test_policy_enforcement.py
Purpose: Regression gate for (1) the fabricated "shipping ₪29–₪149" range, (2) mandatory-policy
         integrity across providers/fallbacks, (3) application-level enforcement outside the model
         (grounded amounts/links, router allowlist, editor fact preservation, autonomous NOA replies).
Process: deterministic — the LLM is replaced by a scripted fake; provider HTTP is intercepted.
         No network, no DB, no Eurosender call.
Data Imported/Modified: none.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

import BACKEND_AI_AGENTS as A
import hf_client
import policy_guard as pg
import reply_language as rl

CHANNELS = ("whatsapp", "telegram", "web")
MANDATORY_SECTIONS = ("CORE BEHAVIOR", "LANGUAGE RULES:", "CLOSING RULES:", "PROHIBITED:",
                      "SHIPPING TRUTH", "VERIFY, DON'T ASSUME", "[REPLY LANGUAGE - FINAL AND OVERRIDING]")
RANGE_REPLIES = {
    "he": "המשלוח עולה ₪29–₪149 לפי הספק, ואפשר להזמין עכשיו.",
    "en": "Shipping is ₪29–₪149 depending on the supplier.",
    "ar": "تكلفة الشحن 29-149 ₪ حسب المورد.",
}


class FakeLLM:
    def __init__(self, replies, provider="hf:deepinfra"):
        self.replies = list(replies)
        self.calls = []
        self.provider = provider

    async def __call__(self, prompt, system="", **kw):
        self.calls.append({"prompt": prompt, "system": system, **kw})
        hf_client._trace(self.provider)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


@pytest.fixture
def fake(monkeypatch):
    def install(replies, provider="hf:deepinfra"):
        f = FakeLLM(replies, provider)
        monkeypatch.setattr(A, "hf_text", f)
        monkeypatch.setattr(A, "hf_text_fast", f)
        return f
    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
    return install


@pytest.fixture(autouse=True)
def _no_turn_language():
    tok = rl.REPLY_LANGUAGE.set(None)
    yield
    rl.REPLY_LANGUAGE.reset(tok)


# ══ 1. SHIPPING — the fabricated range ══════════════════════════════════════════

def test_range_removed_from_every_runtime_source():
    """The range lived in 2 agent prompts + 4 catalog templates (since 2026-03-20). No runtime copy may remain."""
    src = Path(A.__file__).read_text(encoding="utf-8")
    hits = [ln for ln in src.splitlines() if re.search(r"29\s*[-–]\s*₪?\s*149", ln)]
    assert len(hits) == 1 and '"""' in hits[0] or "source of the fabricated range" in hits[0], hits
    for name, cls in A.AGENT_MAP.items():
        assert not re.search(r"29\s*[-–]\s*₪?\s*149", cls.system_prompt), name
        assert 'מע"מ 18% + משלוח' not in cls.system_prompt, name     # flat-VAT instruction gone too
    assert not re.search(r"29\s*[-–]\s*₪?\s*149", A.TELEGRAM_BOT_POLICY)


@pytest.mark.parametrize("pricing,expected", [
    ({"shipping": 35.0}, "35₪ משלוח (להצעה זו)"),
    ({"shipping": 0.0}, "0₪ משלוח (להצעה זו)"),
    ({}, 'משלוח: כלול בסה"כ'),                      # owner 2026-10-27: included, never "at checkout"
    ({"shipping": None}, 'משלוח: כלול בסה"כ'),
    ({"shipping": "n/a"}, 'משלוח: כלול בסה"כ'),
])
def test_shipping_fact_is_the_offer_figure_or_included(pricing, expected):
    assert A._shipping_fact_he(pricing) == expected


@pytest.mark.parametrize("pricing,confirmed", [
    ({"estimated_delivery_days": 14, "delivery_days_confirmed": False}, False),   # search placeholder
    ({"estimated_delivery_days": 6, "delivery_days_confirmed": True}, True),
    ({"estimated_delivery_days": None}, False),
    ({}, False),
    ({"estimated_delivery_days": 9}, True),                                     # legacy dict w/o flag
])
def test_delivery_placeholder_never_presented_as_estimate(pricing, confirmed):
    assert A._delivery_confirmed(pricing) is confirmed
    text = A._delivery_fact_he(pricing)
    assert ("אין הערכה" in text) is (not confirmed)


OFFERS = [
    {"name": "Brake Pad Set", "manufacturer": "TRW", "part_type": "aftermarket",
     "pricing": {"price_no_vat": 300.0, "vat": 0.0, "shipping": 35.0, "total": 335.0,
                 "availability": "in_stock", "estimated_delivery_days": 14, "delivery_days_confirmed": False,
                 "warranty_months": 24, "supplier_part_id": "sp-1"}},
    {"name": "Brake Pad Set", "manufacturer": "Bosch", "part_type": "oem",
     "pricing": {"price_no_vat": 420.0, "vat": 75.6, "shipping": 59.0, "total": 554.6,
                 "availability": "on_order", "estimated_delivery_days": 6, "delivery_days_confirmed": True,
                 "warranty_months": 12, "supplier_part_id": "sp-2"}},
]


async def test_parts_finder_context_carries_per_offer_shipping_not_range(fake, monkeypatch):
    f = fake(["רפידות TRW ב-335₪ כולל משלוח של 35₪. מה מספר הרישוי?"])
    agent = A.PartsFinderAgent()

    async def search(**kw):
        return OFFERS
    monkeypatch.setattr(agent, "search_parts_in_db", search)
    monkeypatch.setattr(agent, "get_db_stats", AsyncMock(side_effect=RuntimeError("no db")))
    out = await agent.process("יש לכם רפידות בלם לקורולה?", [], None, source="whatsapp")
    system = f.calls[0]["system"]
    assert "35₪ משלוח (להצעה זו)" in system and "59₪ משלוח (להצעה זו)" in system   # each offer its own figure
    assert not re.search(r"29\s*[-–]\s*149", system)
    assert "זמן אספקה: אין הערכה להצעה זו" in system and "אספקה: 6 ימים" in system   # placeholder not presented
    assert out.startswith("רפידות TRW")                                           # grounded reply passes


@pytest.mark.parametrize("lang", ["he", "en", "ar"])
@pytest.mark.parametrize("name", sorted(pg.CUSTOMER_AGENTS))
async def test_range_without_data_is_blocked_for_every_customer_agent(fake, name, lang, caplog):
    f = fake([RANGE_REPLIES[lang], RANGE_REPLIES[lang]])
    caplog.set_level("INFO")
    q = {"he": "כמה עולה המשלוח?", "en": "How much is shipping?", "ar": "كم تكلفة الشحن؟"}[lang]
    out = await A.AGENT_MAP[name]().think([{"role": "user", "content": q}], source="telegram")
    assert len(f.calls) == 2                                    # one bounded retry
    assert "[SYSTEM NOTE]" in f.calls[1]["prompt"] and "amount" in f.calls[1]["prompt"]
    assert not pg.currency_amounts(out), out                    # localized fallback, no invented price
    assert rl.reply_matches_language(out, lang)
    assert "ungrounded_amount:29" in caplog.text and "fallback_localized" in caplog.text


async def test_range_allowed_only_when_both_figures_are_in_the_turn_data(fake):
    """A quoted range is not forbidden per se — it is allowed only when the data supports both ends."""
    fake(["Shipping for these two offers is ₪29–₪149."])
    sales = A.SalesAgent()
    data = "\n[CATALOG]\n1. TRW – 29₪ shipping (this offer)\n2. Bosch – 149₪ shipping (this offer)"
    out = await sales.think([{"role": "user", "content": "How much is shipping?"}], source="web",
                            system_override=sales.system_prompt + data)
    assert out == "Shipping for these two offers is ₪29–₪149."


async def test_static_prompt_numbers_are_not_grounding(fake):
    """The policy's own negative example ("NO 'free over ₪300'") must not make ₪300 a valid claim."""
    fake(["Shipping is free over ₪300.", "Shipping is free over ₪300."])
    out = await A.ServiceAgent().think([{"role": "user", "content": "Is shipping free?"}], source="web")
    assert "300" not in out


async def test_customer_quoted_amount_is_grounded(fake):
    fake(["I see you were quoted ₪250 elsewhere. Let me find the right part for your car first."])
    out = await A.SalesAgent().think([{"role": "user", "content": "Another shop asked ₪250, can you beat it?"}],
                                     source="whatsapp")
    assert "₪250" in out


async def test_orders_amount_from_order_context_is_grounded(fake):
    fake(["Your order #1042 total was ₪554.60 and it shipped on Monday."])
    orders = A.OrdersAgent()
    ctx = "\n\n[ORDERS]\n#1042 | status: shipped | total ₪554.60 | tracking IL123456789"
    out = await orders.think([{"role": "user", "content": "Where is my order?"}], source="web",
                             system_override=orders.system_prompt + ctx)
    assert out.startswith("Your order #1042")


@pytest.mark.parametrize("lang", ["he", "ar", "en"])
def test_sales_results_reply_uses_checkout_text_for_unknown_delivery(lang):
    rl.REPLY_LANGUAGE.set(lang)
    results = [{"name": o["name"], "manufacturer": o["manufacturer"], "suppliers": [o["pricing"]]} for o in OFFERS]
    out = A.SalesAgent()._deterministic_sales_results_reply("x", results, source="web")
    assert A._SALES_RESULT_LABELS[lang]["delivery_unknown"] in out      # TRW: placeholder 14 not shown as fact
    assert "14–21" not in out
    assert "6–13" in out                                                 # Bosch: real 6 days
    assert "335" in out and "555" in out                                 # totals unchanged


# ══ 2. POLICY INTEGRITY ACROSS PROVIDERS ═══════════════════════════════════════

def _customer_system(name, channel, lang="en"):
    return A._apply_channel_policy(A.AGENT_MAP[name].system_prompt, channel) + rl.language_directive(lang)


def test_fit_under_budget_is_unchanged():
    s, p, info = pg.fit_request("SYSTEM", "user: hi", 1000)
    assert (s, p) == ("SYSTEM", "user: hi") and not info.notes


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("name", sorted(pg.CUSTOMER_AGENTS))
def test_fit_keeps_every_mandatory_section_and_current_message(name, channel):
    system = _customer_system(name, channel)
    history = "\n".join(f"{'user' if i % 2 == 0 else 'assistant'}: {'היסטוריה ארוכה של שיחה ' * 25}" for i in range(80))
    current = "user: How much is shipping to Haifa for order 1042?"
    s, p, info = pg.fit_request(system, history + "\n" + current, hf_client._GROQ_INPUT_BUDGET)
    for section in MANDATORY_SECTIONS:
        assert section in s, f"{name}/{channel} lost {section}"
    assert p.endswith(current)
    assert info.history_lines_dropped > 0
    assert pg.estimate_tokens(s) + pg.estimate_tokens(p) <= hf_client._GROQ_INPUT_BUDGET


def test_fit_shortens_guidance_before_history_and_never_mandatory():
    system = _customer_system("sales_agent", "whatsapp")
    s, p, info = pg.fit_request(system, "user: hi", pg.estimate_tokens(system) - 200)
    assert info.guidance_removed and "PROFESSIONAL SKILLS" not in s and "SALES FLOW" not in s
    for section in MANDATORY_SECTIONS:
        assert section in s


def test_fit_refuses_rather_than_truncating_policy():
    with pytest.raises(pg.PolicyBudgetExceeded):
        pg.fit_request("PROHIBITED: " + "x" * 30000, "user: hi", 2000)


def test_groq_budget_matches_verified_limit():
    assert hf_client.GROQ_MAX_REQUEST_TOKENS == 8000
    assert hf_client._GROQ_INPUT_BUDGET == 8000 - hf_client.GROQ_MAX_COMPLETION_TOKENS - 300


def _intercept(monkeypatch, content="OK"):
    sent = []

    async def fake_post(url, headers, body, timeout, label):
        sent.append({"url": url, "body": json.loads(body)})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {}},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(hf_client, "_post_with_retry", fake_post)
    monkeypatch.setattr(hf_client, "_cache_get", lambda *a, **k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(hf_client, "_cache_set", lambda *a, **k: asyncio.sleep(0))
    return sent


@pytest.mark.parametrize("name", sorted(pg.CUSTOMER_AGENTS))
async def test_groq_fallback_receives_full_mandatory_policy(monkeypatch, name):
    sent = _intercept(monkeypatch)

    async def no_hf(*a, **k):
        return None
    monkeypatch.setattr(hf_client, "_hf_primary_text", no_hf)
    monkeypatch.setattr(hf_client, "CEREBRAS_ENABLED", False)
    monkeypatch.setattr(hf_client, "GROQ_API_KEY", "test")
    system = _customer_system(name, "whatsapp", "ar")
    assert len(system) > 5000                                     # beyond the old cut-off
    history = "\n".join(f"user: {'سؤال طويل عن القطع ' * 20}" for _ in range(70))
    await hf_client.hf_text(history + "\nuser: كم تكلفة الشحن؟", system=system, model="gpt-oss-120b")
    body = sent[-1]["body"]
    assert "api.groq.com" in sent[-1]["url"]
    sys_sent, user_sent = body["messages"][0]["content"], body["messages"][1]["content"]
    for section in MANDATORY_SECTIONS:
        assert section in sys_sent, section
    assert user_sent.endswith("كم تكلفة الشحن؟")
    assert body["max_tokens"] == hf_client.GROQ_MAX_COMPLETION_TOKENS


async def test_hf_primary_receives_untruncated_system(monkeypatch):
    sent = _intercept(monkeypatch, content="שלום")
    monkeypatch.setattr(hf_client, "HF_TOKEN", "test")
    monkeypatch.setattr(hf_client, "LLM_PRIMARY_PROVIDER", "hf")
    monkeypatch.setattr(hf_client, "_hf_primary_down_until", 0.0)
    system = _customer_system("sales_agent", "web", "he")
    await hf_client.hf_text("user: שלום", system=system, model="gpt-oss-120b")
    assert sent[0]["body"]["messages"][0]["content"] == system
    assert sent[0]["body"]["model"].startswith("openai/gpt-oss-120b")


async def test_policy_budget_exceeded_fails_safe_localized(fake, monkeypatch):
    async def boom(*a, **k):
        raise pg.PolicyBudgetExceeded("too big")
    monkeypatch.setattr(A, "hf_text", boom)
    monkeypatch.setattr(A, "hf_text_fast", boom)
    out = await A.SalesAgent().think([{"role": "user", "content": "كم السعر؟"}], source="web")
    assert rl.reply_matches_language(out, "ar") and not pg.currency_amounts(out)


async def test_tools_are_never_sent_to_a_provider(fake):
    """No model-driven tool execution exists: think() never forwards `tools`."""
    f = fake(["Hi! Which part do you need?"])
    await A.SalesAgent().think([{"role": "user", "content": "hi there"}], source="web",
                               tools=[{"type": "function", "function": {"name": "cancel_order"}}])
    assert "tools" not in f.calls[0]


# ══ 3. APPLICATION-LEVEL ENFORCEMENT ═══════════════════════════════════════════

@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("proposed", ["social_media_manager_agent", "supplier_manager_agent", "tech_agent",
                                      "router_agent", "admin_agent", "", None])
async def test_router_blocks_internal_and_unknown_agents_on_customer_channels(fake, channel, proposed, caplog):
    fake([json.dumps({"agent": proposed, "confidence": 0.99, "intent": "x"})])
    caplog.set_level("INFO")
    r = await A.RouterAgent().route("create a facebook campaign for me", {"source": channel})
    assert r["agent"] == "service_agent"
    assert "route_blocked" in caplog.text


@pytest.mark.parametrize("proposed", sorted(pg.CUSTOMER_AGENTS))
async def test_router_keeps_customer_agents(fake, proposed):
    fake([json.dumps({"agent": proposed})])
    r = await A.RouterAgent().route("x", {"source": "whatsapp"})
    assert r["agent"] == proposed


async def test_router_internal_use_unchanged(fake):
    fake([json.dumps({"agent": "social_media_manager_agent"})])
    r = await A.RouterAgent().route("x", {"source": None})
    assert r["agent"] == "social_media_manager_agent"


@pytest.mark.parametrize("reply,ok", [
    ("Pay here: https://autosparefinder.co.il/pay/FAKE123", False),           # payment links come from code
    ("Pay here: https://checkout.stripe.com/c/pay/cs_test_123", False),
    ("Visit https://partsdeals.example.com", False),
    ("Your cart: https://autosparefinder.co.il/cart", True),
    ("Choose a channel: https://autosparefinder.co.il/api/v1/go?src=chat", True),
])
async def test_links_must_come_from_data(fake, reply, ok):
    fake([reply, reply])
    out = await A.OrdersAgent().think([{"role": "user", "content": "send me the payment link"}], source="web")
    assert (out == reply) is ok


async def test_link_in_turn_data_is_allowed(fake):
    link = "https://autosparefinder.co.il/pay/UwX2QDM"
    fake([f"Here is your secure payment link: {link}"])
    o = A.OrdersAgent()
    out = await o.think([{"role": "user", "content": "payment link please"}], source="web",
                        system_override=o.system_prompt + f"\n[CHECKOUT] link: {link}")
    assert link in out


@pytest.mark.parametrize("rewrite", [
    "Your order #1042 shipped Monday; shipping was ₪49.",                         # adds an amount
    "Your order #1042 shipped Monday. Track it at https://track.example.com/1",  # adds a link
])
async def test_formatter_cannot_add_facts(monkeypatch, rewrite):
    raw = ("Your order #1042 was shipped on Monday with tracking number IL123456789. Delivery usually "
           "takes several business days depending on the courier.")
    monkeypatch.setattr(A, "hf_text", FakeLLM([rewrite]))
    out = await A._format_response_for_customer(raw, "orders_agent", "web", [], current_message="Where is my order?",
                                                target_lang="en")
    assert out == raw


@pytest.mark.parametrize("text,blocked", [
    ("Thanks! Find your part here: https://autosparefinder.co.il/api/v1/go?src=fb", False),
    ("Brake pads from ₪199 with free shipping!", True),
    ("Shipping is 29-149 ₪.", True),
    ("Check https://cheap-parts.example.com", True),
])
async def test_autonomous_noa_reply_policy_gate(monkeypatch, text, blocked):
    from social import noa_ops
    db = AsyncMock()
    sent = AsyncMock(return_value={"ok": True, "id": "x"})
    monkeypatch.setattr("social.engagement.send_reply", sent)
    monkeypatch.setattr("social.engagement.mark_replied", AsyncMock())
    monkeypatch.setattr(noa_ops, "rate_limit_ok", AsyncMock(return_value=(True, "")))
    r = await noa_ops.send_page_reply(db, {"id": "00000000-0000-0000-0000-000000000001", "platform": "facebook",
                                           "external_id": "1_2"}, text, actor="autonomous")
    assert (not sent.called) is blocked
    if blocked:
        assert r["blocked"] and r["error"].startswith("safety:")


@pytest.mark.parametrize("text", ["Brake pads from ₪199 with free shipping!", "Shipping is 29-149 ₪.",
                                  "Check https://cheap-parts.example.com"])
async def test_policy_gate_blocks_even_if_safety_check_misses(monkeypatch, text):
    """Defense in depth: the new grounded-claims gate holds on its own."""
    from social import noa_ops
    sent = AsyncMock(return_value={"ok": True, "id": "x"})
    monkeypatch.setattr(noa_ops, "safety_check", lambda t, max_chars=300: (True, ""))
    monkeypatch.setattr("social.engagement.send_reply", sent)
    monkeypatch.setattr(noa_ops, "rate_limit_ok", AsyncMock(return_value=(True, "")))
    r = await noa_ops.send_page_reply(AsyncMock(), {"id": "00000000-0000-0000-0000-000000000001",
                                                    "platform": "facebook", "external_id": "1_2"}, text, actor="autonomous")
    assert not sent.called and r["error"].startswith("safety:policy:")


async def test_owner_approved_noa_reply_not_gated(monkeypatch):
    """The owner is the authority for approved replies; the gate applies to AUTONOMOUS sends only."""
    from social import noa_ops
    sent = AsyncMock(return_value={"ok": True, "id": "x"})
    monkeypatch.setattr("social.engagement.send_reply", sent)
    monkeypatch.setattr("social.engagement.mark_replied", AsyncMock())
    await noa_ops.send_page_reply(AsyncMock(), {"id": "00000000-0000-0000-0000-000000000001", "platform": "facebook",
                                                "external_id": "1_2"}, "Pads are ₪199 today", actor="owner")
    assert sent.called


@pytest.mark.parametrize("reply", ["", "   ", "\n\n"])
async def test_empty_reply_is_a_violation(fake, reply):
    f = fake([reply, reply])
    out = await A.ServiceAgent().think([{"role": "user", "content": "hello, I need help"}], source="web")
    assert out.strip() and len(f.calls) == 2


@pytest.mark.parametrize("text,amounts", [
    ("₪29–₪149", [29.0, 149.0]),
    ("29-149 ₪", [29.0, 149.0]),
    ("1,250 ש\"ח", [1250.0]),
    ("$57.99 shipping", [57.99]),
    ("ضمان 14 يوماً", []),                    # days are not money
    ("OEM 04465-02220, year 2018", []),
    ("₪358 (incl. VAT)", [358.0]),
])
def test_currency_amount_extraction(text, amounts):
    assert sorted(set(pg.currency_amounts(text))) == sorted(set(amounts))
