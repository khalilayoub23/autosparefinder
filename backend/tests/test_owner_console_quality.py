"""#158 owner-console conversation quality guards (deterministic parts; the LLM part is evidenced by the live replay in
candidates/whatsapp-m8-2026-11-26/convo_eval_*.json)."""
import asyncio

import pytest

from agents import owner_console as con

MONEY = ["תעביר 5000 שקל לספק ALVADI עכשיו", "תשלם לספק את החשבונית", "pay the supplier 300$", "להזמין מהספק 4 רפידות",
         "בועז, תעביר 1200 ₪ ל-ALVADI", "תזמין מהספק 10 מסננים"]
NOT_MONEY = ["מה המצב של השאיבה?", "כמה חלקים יש לנו עם מחיר?", "תעביר את הפוסט לנועה",
             "מה המחיר של רפידות בלם? 198 שקל?", "שלום, מה שלומך",
             # expert review false positives (#158)
             "נועה, תכתבי פוסט שמזמין אנשים להזמין רפידות ב-198 ₪", "כמה לקוחות ניסו להזמין השבוע? כמה כסף נכנס?",
             "הלקוח משלם 300 ₪ על המשלוח?", "how much did customers pay this week in ILS?",
             "תעביר את ההודעה לבועז לגבי הספק", "בועז, תבדוק אם אפשר להזמין מהספק"]


@pytest.mark.parametrize("text", MONEY)
def test_money_actions_are_detected(text):
    assert con._is_money_action(text)


@pytest.mark.parametrize("text", NOT_MONEY)
def test_ordinary_messages_are_not_money_actions(text):
    assert not con._is_money_action(text)


def test_money_request_gets_the_real_mechanism_and_never_reaches_the_llm(monkeypatch):
    async def none(*a, **k):
        return None

    async def empty(*a, **k):
        return []

    async def boom(*a, **k):
        raise AssertionError("the LLM must not answer a money action")

    for name in ("_get_active_agent", "_set_active_agent", "_clear_active_agent", "_save_history"):
        monkeypatch.setattr(con, name, none)
    monkeypatch.setattr(con, "_load_history", empty)
    monkeypatch.setattr(con, "_list_pending_posts", empty)
    from agents import owner_continuations as oc
    monkeypatch.setattr(oc, "run", none)
    import hf_client
    monkeypatch.setattr(hf_client, "hf_text", boom)
    reply = asyncio.run(con._process_owner_message("תעביר 5000 שקל לספק ALVADI עכשיו", None))
    assert "לא יכול להעביר כסף" in reply and "אשרתשלוםספק" in reply and "רכש" in reply
    assert "יטפל" not in reply


def test_console_rules_cover_continuity_name_and_money():
    rules = con._HUMAN_VOICE
    for needle in ("המשכיות השיחה", "אל תחזור על שאלה", "חליל", "כסף, תשלום או הזמנה מספק"):
        assert needle in rules


def test_webhook_non_chat_jid_rule_matches_the_bridge():
    from routes.webhooks import is_non_chat_jid
    for j in ("status@broadcast", "STATUS@BROADCAST", "123@broadcast", "1203@newsletter"):
        assert is_non_chat_jid(j)
    for j in ("", None, "972500000000@s.whatsapp.net", "98058566160397@lid", "120363428683447639@g.us"):
        assert not is_non_chat_jid(j)
