"""
Script: tests/test_reply_language.py
Purpose: Regression suite for the customer reply-language fix (2026-10-27).
         Customers writing English/Arabic received Hebrew replies. Root causes (see FIXES_TRACKER):
         conflicting agent prompts, first-message language rule, brain language never reaching
         the agents, the customer editor with no English rule reading the PREVIOUS message,
         Hebrew-only fixed fallbacks, and Groq fallback truncating the language rules away.
Process: deterministic tests with the LLM replaced by a scripted fake (no network, no DB):
         language detection policy, every agent x every channel x every language through
         BaseAgent.think (direct and brain-turn modes), retry/fallback bounds, timeouts, canned
         templates, the customer editor, the Groq fallback payload, and context scoping.
Data Imported/Modified: none.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

import BACKEND_AI_AGENTS as A
import hf_client
import reply_language as rl

# ── fixtures / helpers ─────────────────────────────────────────────────────────

MSG = {
    "he": "יש לכם רפידות בלם לקורולה 2018?",
    "ar": "هل عندكم فحمات فرامل لكورولا 2018؟",
    "en": "Do you have brake pads for a 2018 Corolla?",
}
GOOD = {
    "he": "שלום, אשמח לעזור עם רפידות הבלם ל-Toyota Corolla 2018. מה מספר הרישוי של הרכב?",
    "ar": "مرحباً، يسعدني مساعدتك في فحمات الفرامل لسيارة Toyota Corolla 2018. ما رقم لوحة السيارة؟",
    "en": "Hi, happy to help with brake pads for your Toyota Corolla 2018. What is the licence plate number?",
}
CHANNELS = ("whatsapp", "telegram", "web")
LANGS = ("he", "ar", "en")
CUSTOMER_AGENTS = [n for n in A.AGENT_MAP if n != "router_agent"]   # 10 customer-replying agents
ALL_AGENTS = list(A.AGENT_MAP)                                       # 11 agents in the registry


class FakeLLM:
    """Scripted stand-in for hf_text / hf_text_fast. Records every call."""

    def __init__(self, replies=None, exc=None, provider="hf:deepinfra"):
        self.replies = list(replies or [])
        self.exc = exc
        self.calls = []
        self.provider = provider

    async def __call__(self, prompt, system="", **kw):
        self.calls.append({"prompt": prompt, "system": system, **kw})
        hf_client._trace(self.provider)
        if self.exc:
            raise self.exc
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeLLM(**kw)
        monkeypatch.setattr(A, "hf_text", f)
        monkeypatch.setattr(A, "hf_text_fast", f)
        return f
    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")      # think() gate
    return install


@pytest.fixture(autouse=True)
def _no_turn_language():
    tok = rl.REPLY_LANGUAGE.set(None)
    yield
    rl.REPLY_LANGUAGE.reset(tok)


def agent(name):
    return A.AGENT_MAP[name]()


def lang_of(text):
    return rl.detected_reply_language(text)


# ── 1. language selection policy ───────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("יש לכם רפידות בלם?", "he"),
    ("هل عندكم فلتر زيت؟", "ar"),
    ("Do you have an oil filter?", "en"),
    ("יש לכם brake pads ל-Mazda 3?", "he"),            # Latin part/brand terms inside Hebrew
    ("بدي brake pads لـ Kia Sportage", "ar"),           # Latin inside Arabic
    ("Hi, יש לכם", "he"),                               # RTL letters present -> RTL wins
    ("04465-02220", None),                              # OEM number only
    ("12-345-67", None),                                # licence plate only
    ("1", None),                                        # numbered choice
    ("VW", None),                                       # lone code
    ("👍", None),
    ("https://example.com/part/123", None),
    ("ok thanks", "en"),
])
def test_detect_language(text, expected):
    assert rl.detect_language(text) == expected


def test_resolve_current_message_wins_over_preferred():
    assert rl.resolve_reply_language(MSG["en"], preferred="he") == "en"   # switching honoured
    assert rl.resolve_reply_language(MSG["ar"], preferred="en") == "ar"


def test_resolve_ambiguous_inherits_preferred_then_default():
    assert rl.resolve_reply_language("12-345-67", preferred="ar") == "ar"
    assert rl.resolve_reply_language("04465-02220", preferred="en") == "en"
    assert rl.resolve_reply_language("1", preferred=None) == "he"


@pytest.mark.parametrize("reply,lang,ok", [
    (GOOD["he"], "he", True),
    (GOOD["ar"], "ar", True),
    (GOOD["en"], "en", True),
    (GOOD["he"], "en", False),
    (GOOD["he"], "ar", False),
    # exact replies captured in the 2026-10-27 baseline live run (Groq gpt-oss-120b)
    ("مرحباً! אני مايا من Auto Spare. جميع القطع مشمولة بضمان استبدال خلال 14 يوماً من استلامها في حال وجود عيب مصنع أو تلف أثناء الشحن, ובمناسبة ذلك نعيد 100% من الم", "ar", False),
    ("أنا آسف لأن القطعة لم تناسبك. כדי להתחיל בתהליך ההחזרה, אשמח אם תשלח לי מספר ההזמנה או את כתובת המייל שבה בוצעה ההזמנה. לאחר מכן ארדוף את הפרטים ואדאג לארגן את ", "ar", False),
    # one quoted foreign word is tolerated (catalog name); two are not
    ("نعم، لدينا فحمات فرامل (רפידות) لسيارتك، ما رقم اللوحة؟", "ar", True),
    ("Yes, we have brake pads (רפידות בלם) for your car.", "en", False),
    ("Price: ₪358 — OEM 04465-02220 — https://autosparefinder.co.il/pay/abc", "en", True),
    ("₪358", "ar", True),                                # numbers only: nothing to judge
    ("", "en", False),                                   # empty reply (seen live: gpt-oss content="")
    ("   \n", "he", False),
])
def test_reply_matches_language(reply, lang, ok):
    assert rl.reply_matches_language(reply, lang) is ok


# ── 2. prompts no longer contradict the policy ─────────────────────────────────

CONFLICTING = [
    r"Default language is Hebrew",
    r"ALWAYS respond in Hebrew",
    r"always use these exact labels in Hebrew",
    r"NEVER insert English words",
    r"customer's first message",
    r"ענה תמיד בעברית",
    r"If and only if the customer message is mainly in Arabic",
]


@pytest.mark.parametrize("name", ALL_AGENTS + ["base_agent", "channel_policy"])
def test_no_conflicting_language_rule(name):
    text = {"base_agent": A.BaseAgent.system_prompt,
            "channel_policy": A.TELEGRAM_BOT_POLICY}.get(name) or A.AGENT_MAP[name].system_prompt
    for pat in CONFLICTING:
        assert not re.search(pat, text), f"{name} still contains: {pat}"


# ── 3. matrix: every agent x channel x language, direct and brain-turn modes ───

@pytest.mark.parametrize("mode", ["direct", "turn"])
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("name", CUSTOMER_AGENTS)
async def test_matrix_agent_channel_language(fake, name, channel, lang, mode, caplog):
    f = fake(replies=[GOOD[lang]])
    if mode == "turn":
        rl.REPLY_LANGUAGE.set(lang)
    # Hebrew history in front of every case: a previous language must not leak.
    history = [{"role": "user", "content": "שלום"}, {"role": "assistant", "content": "שלום! איך אפשר לעזור?"}]
    caplog.set_level("INFO")
    out = await agent(name).think(history + [{"role": "user", "content": MSG[lang]}], source=channel)
    assert out == GOOD[lang]
    assert len(f.calls) == 1
    system = f.calls[0]["system"]
    block = system[system.rfind("[REPLY LANGUAGE"):]
    assert block.startswith("[REPLY LANGUAGE - FINAL AND OVERRIDING]"), "directive must be the LAST block"
    assert rl.NAMES[lang] in block
    assert f"target={lang}" in caplog.text and "outcome=ok" in caplog.text and "provider=hf:deepinfra" in caplog.text


@pytest.mark.parametrize("channel", CHANNELS)
async def test_router_output_not_language_enforced(fake, channel):
    f = fake(replies=['{"agent": "sales_agent", "language": "en"}'])
    out = await agent("router_agent").think([{"role": "user", "content": MSG["en"]}], source=channel)
    assert out.startswith("{")
    assert rl.DIRECTIVE_HEADER not in f.calls[0]["system"]
    assert len(f.calls) == 1


async def test_background_call_unchanged(fake):
    """No channel and no customer turn (NOA posts, owner tools): no directive, no validation."""
    f = fake(replies=[GOOD["he"]])
    out = await agent("social_media_manager_agent").think([{"role": "user", "content": "Write a post about brake pads"}])
    assert out == GOOD["he"]
    assert rl.DIRECTIVE_HEADER not in f.calls[0]["system"]


async def test_turn_language_overrides_rewritten_message(fake):
    """The brain may hand an agent a canonical (Hebrew) part name for a numbered choice; the
    customer's language for the turn still decides."""
    f = fake(replies=[GOOD["ar"]])
    rl.REPLY_LANGUAGE.set("ar")
    await agent("parts_finder_agent").think([{"role": "user", "content": "רפידות בלם"}], source="whatsapp")
    assert rl.NAMES["ar"] in f.calls[0]["system"][-800:]


async def test_ambiguous_message_inherits_previous_customer_language(fake):
    f = fake(replies=[GOOD["en"]])
    msgs = [{"role": "user", "content": MSG["en"]}, {"role": "assistant", "content": GOOD["he"]},
            {"role": "user", "content": "12-345-67"}]
    await agent("parts_finder_agent").think(msgs, source="telegram")
    assert rl.NAMES["en"] in f.calls[0]["system"][-800:]


async def test_language_switch_mid_conversation(fake):
    f = fake(replies=[GOOD["ar"]])
    msgs = [{"role": "user", "content": MSG["en"]}, {"role": "assistant", "content": GOOD["en"]},
            {"role": "user", "content": MSG["ar"]}]
    out = await agent("service_agent").think(msgs, source="web")
    assert out == GOOD["ar"] and rl.NAMES["ar"] in f.calls[0]["system"][-800:]


async def test_tool_context_in_hebrew_does_not_decide_language(fake):
    """Catalog/tool context injected into the system prompt is Hebrew; the reply target is the customer's."""
    f = fake(replies=[GOOD["en"]])
    sys_with_tool = A.SalesAgent.system_prompt + "\n[CATALOG — 3 חלקים]\n1. רפידות בלם — ₪358"
    out = await agent("sales_agent").think([{"role": "user", "content": MSG["en"]}], source="web",
                                           system_override=sys_with_tool)
    assert out == GOOD["en"]
    assert f.calls[0]["system"].rfind("[REPLY LANGUAGE") > f.calls[0]["system"].rfind("[CATALOG")


# ── 4. validation, bounded retry, localized fallback ──────────────────────────

@pytest.mark.parametrize("name", CUSTOMER_AGENTS)
@pytest.mark.parametrize("lang", ("ar", "en"))
async def test_wrong_language_one_retry_then_ok(fake, name, lang, caplog):
    f = fake(replies=[GOOD["he"], GOOD[lang]])
    caplog.set_level("INFO")
    out = await agent(name).think([{"role": "user", "content": MSG[lang]}], source="whatsapp")
    assert out == GOOD[lang]
    assert len(f.calls) == 2
    assert "[SYSTEM NOTE]" in f.calls[1]["prompt"] and rl.NAMES[lang] in f.calls[1]["prompt"]
    assert "outcome=mismatch_retry" in caplog.text and "outcome=retry_ok" in caplog.text


@pytest.mark.parametrize("name", CUSTOMER_AGENTS)
@pytest.mark.parametrize("lang", LANGS)
async def test_wrong_language_twice_gives_localized_fallback(fake, name, lang, caplog):
    wrong = GOOD["en"] if lang != "en" else GOOD["he"]
    f = fake(replies=[wrong, wrong])
    caplog.set_level("INFO")
    out = await agent(name).think([{"role": "user", "content": MSG[lang]}], source="telegram")
    assert len(f.calls) == 2, "retry must be bounded to exactly one"
    assert rl.reply_matches_language(out, lang), f"{name}/{lang} fallback not localized: {out[:80]}"
    assert "[internal]" not in out
    assert "outcome=fallback_localized" in caplog.text


@pytest.mark.parametrize("name", CUSTOMER_AGENTS)
@pytest.mark.parametrize("lang", LANGS)
async def test_empty_model_reply_never_reaches_customer(fake, name, lang):
    """Live 2026-10-27: gpt-oss returned content="" and the first fix accepted it as 'ok'."""
    f = fake(replies=["", ""])
    out = await agent(name).think([{"role": "user", "content": MSG[lang]}], source="web")
    assert out.strip() and rl.reply_matches_language(out, lang)
    assert len(f.calls) == 2


@pytest.mark.parametrize("name", CUSTOMER_AGENTS)
@pytest.mark.parametrize("lang", LANGS)
async def test_provider_error_gives_localized_fallback(fake, name, lang):
    fake(exc=RuntimeError("HF primary failed, Cerebras disabled and GROQ_API_KEY not set"))
    out = await agent(name).think([{"role": "user", "content": MSG[lang]}], source="web")
    assert rl.reply_matches_language(out, lang)


@pytest.mark.parametrize("lang", LANGS)
async def test_orders_timeout_fallback_localized(fake, lang):
    """The identical OrdersAgent Hebrew replies seen live were this path: the 28s cap expired and
    _offline_reply returned a fixed Hebrew text."""
    fake(exc=asyncio.TimeoutError())
    out = await agent("orders_agent").think([{"role": "user", "content": MSG[lang]}], source="whatsapp")
    assert rl.reply_matches_language(out, lang)
    assert out == rl.pick(A.BaseAgent._OFFLINE_TEXTS["orders_agent"], lang)


async def test_retry_shares_fast_agent_deadline(fake, monkeypatch):
    """No retry when the 28s budget is nearly spent — no extra 28s for the second attempt."""
    f = fake(replies=[GOOD["he"], GOOD["en"]])
    real_loop_time = asyncio.get_running_loop().time
    t = {"n": 0}

    def fake_time():
        t["n"] += 1
        return real_loop_time() + (26.0 if t["n"] > 2 else 0.0)
    monkeypatch.setattr(asyncio.get_running_loop(), "time", fake_time)
    out = await agent("orders_agent").think([{"role": "user", "content": MSG["en"]}], source="web")
    assert len(f.calls) == 1
    assert rl.reply_matches_language(out, "en")


async def test_no_cerebras_key_offline_localized(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    out = await agent("finance_agent").think([{"role": "user", "content": MSG["ar"]}], source="web")
    assert rl.reply_matches_language(out, "ar")


# ── 5. fixed templates / canned replies ───────────────────────────────────────

@pytest.mark.parametrize("name", ["finance_agent", "service_agent"])
@pytest.mark.parametrize("text,lang", [
    ("I received the wrong part and I'm frustrated", "en"),
    ("وصلتني wrong part", "ar"),
    ("קיבלתי חלק לא נכון", "he"),
    ("I'm really angry about this", "en"),
])
async def test_canned_replies_follow_language(fake, name, text, lang):
    f = fake(replies=["should not be called"])
    out = await agent(name).process(text, [], None, source="whatsapp")
    assert not f.calls
    assert rl.reply_matches_language(out, lang), out


@pytest.mark.parametrize("lang", LANGS)
def test_marketing_campaign_proposal_localized(lang):
    rl.REPLY_LANGUAGE.set(lang)
    out = agent("marketing_agent")._campaign_intent_proposal(MSG[lang])
    assert rl.reply_matches_language(out, lang) and "POST /api/v1/campaigns" in out


@pytest.mark.parametrize("lang", LANGS)
async def test_supplier_manager_customer_reply_localized(lang):
    rl.REPLY_LANGUAGE.set(lang)
    out = await agent("supplier_manager_agent").process(MSG[lang], [], None)
    assert rl.reply_matches_language(out, lang)


RESULTS = [
    {"name": "Brake Pad Set", "manufacturer": "TRW", "suppliers": [
        {"total": 358.0, "warranty_months": 24, "availability": "in_stock", "estimated_delivery_days": 7},
        {"total": 401.0}]},
    {"name": "Brake Pad Set", "manufacturer": "Bosch", "suppliers": [
        {"total": 541.0, "warranty_months": 12, "availability": "on_order", "estimated_delivery_days": 10}]},
]


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("channel", CHANNELS)
def test_sales_results_reply_localized_and_facts_unchanged(lang, channel):
    rl.REPLY_LANGUAGE.set(lang)
    out = agent("sales_agent")._deterministic_sales_results_reply(MSG[lang], RESULTS, source=channel)
    assert rl.reply_matches_language(out, lang), out
    # business facts identical in every language
    nums = sorted(re.findall(r"\d+", out))
    rl.REPLY_LANGUAGE.set("he")
    he_nums = sorted(re.findall(r"\d+", agent("sales_agent")._deterministic_sales_results_reply(MSG["he"], RESULTS, source=channel)))
    assert [n for n in nums if n != "2018"] == [n for n in he_nums if n != "2018"]
    for price in ("358", "401", "541"):
        assert price in out


@pytest.mark.parametrize("lang", LANGS)
def test_sales_fallback_localized(lang):
    rl.REPLY_LANGUAGE.set(lang)
    out = agent("sales_agent")._deterministic_sales_fallback(MSG[lang], source="whatsapp")
    assert rl.reply_matches_language(out, lang), out


@pytest.mark.parametrize("lang", LANGS)
def test_brain_fixed_texts_localized(lang):
    rl.REPLY_LANGUAGE.set(lang)
    assert rl.reply_matches_language(A._localized_agent_error(), lang)
    leaked = "price_no_vat = cost × 1.45 margin 45%"
    out = A._sanitize_internal_pricing_disclosure(leaked)
    assert rl.reply_matches_language(out, lang), out
    assert rl.reply_matches_language(A._strip_leaked_reasoning("The user wants brake pads. Analyze the user intent."), lang)


# ── 6. customer editor (_format_response_for_customer) ─────────────────────────

RAW_EN = ("Your order #1042 was shipped on Monday with tracking number IL123456789. Delivery usually "
          "takes several business days depending on the courier, and the courier will text you first.")


@pytest.mark.parametrize("channel", CHANNELS)
async def test_formatter_uses_current_message_not_previous(monkeypatch, channel):
    f = FakeLLM(replies=["Your order #1042 shipped on Monday (tracking IL123456789). The courier will text you before delivery."])
    monkeypatch.setattr(A, "hf_text", f)
    history = [{"role": "user", "content": "שלום"}]           # PREVIOUS message, Hebrew
    out = await A._format_response_for_customer(RAW_EN, "orders_agent", channel, history,
                                                current_message="Where is my order?", target_lang="en")
    assert "English" in f.calls[0]["system"] and "Where is my order?" in f.calls[0]["prompt"]
    assert out.startswith("Your order #1042")


@pytest.mark.parametrize("channel", CHANNELS)
async def test_formatter_wrong_language_rewrite_rejected(monkeypatch, channel, caplog):
    f = FakeLLM(replies=["ההזמנה שלך מספר 1042 נשלחה ביום שני עם מספר מעקב IL123456789."])
    monkeypatch.setattr(A, "hf_text", f)
    caplog.set_level("INFO")
    out = await A._format_response_for_customer(RAW_EN, "orders_agent", channel, [],
                                                current_message="Where is my order?", target_lang="en")
    assert out == RAW_EN
    assert "rewrite_rejected_kept_raw" in caplog.text


@pytest.mark.parametrize("lang", LANGS)
async def test_formatter_prompt_names_target_for_every_language(monkeypatch, lang):
    f = FakeLLM(replies=[GOOD[lang] * 3])
    monkeypatch.setattr(A, "hf_text", f)
    await A._format_response_for_customer(GOOD[lang] * 3, "security_agent", "telegram", [],
                                          current_message=MSG[lang], target_lang=lang)
    assert rl.NAMES[lang] in f.calls[0]["system"]


# ── 7. provider fallback keeps the language requirement (Groq payload) ─────────

async def test_groq_fallback_payload_keeps_language_block_and_current_message(monkeypatch):
    import httpx
    captured = {}

    async def fake_post(url, headers, body, timeout, label):
        captured["url"] = url
        captured["body"] = json.loads(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": GOOD["en"]}}], "usage": {}},
                              request=httpx.Request("POST", url))

    async def no_hf(*a, **k):
        return None

    monkeypatch.setattr(hf_client, "_hf_primary_text", no_hf)
    monkeypatch.setattr(hf_client, "_post_with_retry", fake_post)
    monkeypatch.setattr(hf_client, "CEREBRAS_ENABLED", False)
    monkeypatch.setattr(hf_client, "GROQ_API_KEY", "test")
    monkeypatch.setattr(hf_client, "_cache_get", lambda *a, **k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(hf_client, "_cache_set", lambda *a, **k: asyncio.sleep(0))

    system = A._apply_channel_policy(A.SalesAgent.system_prompt, "whatsapp") + rl.language_directive("en")
    assert len(system) > 5000
    long_history = "\n".join(f"user: {'היסטוריה ארוכה ' * 20}" for _ in range(40))
    prompt = long_history + "\nuser: " + MSG["en"]
    trace = {}
    hf_client.LLM_CALL_TRACE.set(trace)
    out = await hf_client.hf_text(prompt, system=system, model="gpt-oss-120b")
    sent = captured["body"]["messages"]
    assert "api.groq.com" in captured["url"]
    assert "[REPLY LANGUAGE - FINAL AND OVERRIDING]" in sent[0]["content"] and "English" in sent[0]["content"]
    for section in ("PROHIBITED:", "SHIPPING TRUTH", "VERIFY, DON'T ASSUME", "LANGUAGE RULES:"):
        assert section in sent[0]["content"], section
    assert sent[1]["content"].endswith(MSG["en"]), "current customer message must survive truncation"
    assert out == GOOD["en"] and trace["provider"] == "groq:openai/gpt-oss-120b"


def test_language_block_survives_groq_fit():
    """Groq fitting (policy_guard.fit_request) may shorten guidance/history, never the language block."""
    import policy_guard as pg
    system = A._apply_channel_policy(A.SalesAgent.system_prompt, "whatsapp") + rl.language_directive("ar")
    history = "\n".join(f"user: {'רפידות בלם לקורולה ' * 30}" for _ in range(60))
    s2, p2, info = pg.fit_request(system, history + "\nuser: " + MSG["ar"], 3500)
    assert rl.DIRECTIVE_HEADER in s2 and rl.NAMES["ar"] in s2
    assert p2.endswith(MSG["ar"]) and info.history_lines_dropped > 0


# ── 8. turn scoping ───────────────────────────────────────────────────────────

async def test_turn_language_scoped_to_one_call(monkeypatch):
    async def impl(*a, **k):
        rl.REPLY_LANGUAGE.set("ar")
        assert rl.REPLY_LANGUAGE.get() == "ar"
        return {"ok": True}
    monkeypatch.setattr(A, "_process_user_message_impl", impl)
    monkeypatch.setattr(A, "_process_agent_response_for_message_impl", impl)
    assert await A.process_user_message("u", MSG["ar"], None, None, source="whatsapp") == {"ok": True}
    assert rl.REPLY_LANGUAGE.get() is None
    await A.process_agent_response_for_message("u", MSG["ar"], "c", None, source="web")
    assert rl.REPLY_LANGUAGE.get() is None


def test_registry_has_eleven_agents():
    assert len(A.AGENT_MAP) == 11
