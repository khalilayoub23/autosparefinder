"""#113 regression: a plain-language owner request for data the console has runs the real command.

2026-10-23/25 (owner WhatsApp): "רשימת מילים לאישור", "תבדוק מול המערכת איזה מילים מצריכות אישור" and, replying to
an orders alert, "תאמת את ההזמנות" all fell to the chat LLM, which has no access to that data and answered "I don't
have it" five times in a row. The messages below are the owner's real wording. No network, no database: the command
handlers and the LLM are replaced by markers.
"""
import asyncio

import pytest

from agents import owner_console as oc

# The owner replied to the orders alert with this text; the bridge forwards the reply text only, not the quote.
ORDERS_ALERT_REPLY = "תאמת את ההזמנות"


@pytest.mark.parametrize("msg,cmd", [
    ("רשימת מילים לאישור", "מילים"),
    ("תבדוק מול המערכת איזה מילים מצריכות אישור", "מילים"),
    ("אילו מילות סיווג ממתינות?", "מילים"),
    (ORDERS_ALERT_REPLY, "הזמנות"),
    ("תבדוק את ההזמנות שממתינות לטיפול", "הזמנות"),
    ("איזה הזמנות תקועות?", "הזמנות"),
    ("תראה לי פוסטים שממתינים לאישור", "פוסטים"),
    ("show pending posts", "פוסטים"),
])
def test_owner_requests_map_to_commands(msg, cmd):
    assert oc._natural_command(msg) == cmd


@pytest.mark.parametrize("msg", [
    "זו לא הכוונה הכוונה לרשימה שצריכה את האישור שלי",   # no topic word: stays a conversation
    "@נועה תכיני פוסט לאישור על רפידות בלם",               # explicit agent
    "נועה תכתבי פוסט חדש לאישור",                         # bare agent prefix
    "תכתבי פוסט לאישור על מצברים",                        # asks to CREATE a post, not to list them
    "תשמרי: פוסטים רק בשעות שיא, לאישור שלי",             # a directive is saved, not mapped
    "אשרמילה brake",                                      # approve command keeps its own handler
    "אשר 81a6b13e",
    "מילים",                                              # exact command: no mapping needed
    "מה שלומך היום?",
    "תחבר אותי לנועה לגבי הפוסטים שממתינים",               # connect intent wins
])
def test_conversation_and_other_commands_are_not_remapped(msg):
    assert oc._natural_command(msg) is None


def test_routing_runs_the_real_handler_not_the_llm(monkeypatch):
    calls = []

    async def kw():
        calls.append("words")
        return "WORDS-LIST"

    async def orders():
        calls.append("orders")
        return "ORDERS-LIST"

    async def no_llm(*a, **k):
        raise AssertionError("the chat LLM must not answer a request the console can run")

    async def no_agent():
        return None

    monkeypatch.setattr(oc, "_keywords_list", kw)
    monkeypatch.setattr(oc, "_orders_attention", orders)
    monkeypatch.setattr(oc, "_get_active_agent", no_agent)
    import hf_client
    monkeypatch.setattr(hf_client, "hf_text", no_llm)

    assert asyncio.run(oc._process_owner_message("רשימת מילים לאישור", db=None)) == "WORDS-LIST"
    assert asyncio.run(oc._process_owner_message(ORDERS_ALERT_REPLY, db=None)) == "ORDERS-LIST"
    assert asyncio.run(oc._process_owner_message("הזמנות", db=None)) == "ORDERS-LIST"
    assert calls == ["words", "orders", "orders"]


def test_help_lists_the_orders_command():
    assert "*הזמנות*" in oc._HELP
