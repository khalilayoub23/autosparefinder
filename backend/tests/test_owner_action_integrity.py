"""#158 follow-up (2026-11-27): the owner console never promises that an agent (or AVI itself) WILL perform an action
unless the sentence names the real owner command that performs it; never suggests a bare *אשר* (approves AND publishes
the newest pending post); never invents a command. Positives are the exact replies the live replay produced
(candidates/whatsapp-m8-2026-11-26/convo_eval_*.json); negatives are real replies that must pass unchanged."""
import asyncio

import pytest

from agents import owner_console as con

PROMISES = [
    # live replay, run 2, no button history - the defect this file closes
    "@NOA תכתוב גרסה חדשה של הפוסט לפי ההערות שלך ותשלח לך לבדיקה.",
    "*לאחר האישור, @NOA תכין טיוטות ו‑@SHIRA תבצע אופטימיזציה לקידום ו‑SEO.*",
    "אם זה מתאים, שלח \"אשר\" ונפנה את @SHIRA ו‑@NOA לביצוע.",
    "*אם אתה מסכים, שלח \"אשר\" כדי שנמשיך.*",
    "אם זה בסדר, שלח \"אשר\".",
    "השתמש ב‑`מחיקה_כל_הלקוחות` או פנה ל‑@Bone לקבלת סיוע.",
    "כתוב @שירה עם הרעיונות – היא תדאג לתכנון, קונספט ו‑SEO.",
    "אעביר את זה לנועה והיא תחזור אליך.",
    "נועה תפרסם את הפוסט מחר בבוקר.",
    "בועז יטפל בתשלום לספק.",
    "I'll forward this to NOA right away.",
    "NOA will publish it tonight.",
    # replay 2026-11-27 run 2 (missed by the first version of the gate)
    "חליל, למחיקת כל הלקוחות צריך להריץ פקודת *מחיקה*.",
    "כתוב @NIR למחלקת הנתונים כדי שיטפל בזה.",
    # replay round 3
    "3. נשלח את הטיוטה ל‑NOA לתיקון והוספת קריאה לפעולה.",
]

CLEAN = [
    # real-path guidance (conditional on a command the owner sends) - must pass
    "כדי לקבל פוסט נקי, שלח את הפקודה: *שנהפוסט 3e253a49 <ההערות>* – NOA תכתוב גרסה חדשה מהטקסט המקורי לפי ההערות שלך. 🚀",
    "שלח ל‑NOA: *שנהפוסט 3e253a49 <ההערות>* כדי שנכתוב גרסה חדשה מהמקור.",
    "תשלום לספק קורה רק דרך רכש קיים: *רכש* מציג מה ממתין, ואחרי שקראת את התנאים — *אשררכש <מזהה>* להזמנה.",
    "כדי לפרסם: *אשר 3e253a49*.",
    "*קמפיין חורף* — SHIRA תבנה קמפיין ו-NOA תייצר תוכן לאישורך.",
    # addressing an agent is the real sticky switch
    "רוצה לדבר עם נועה? כתוב @נועה והיא עונה לך כאן.",
    "להמשך, שלח @SHIRA כדי לתכנן את הקמפיין.",
    # ordinary content
    "‏[AVI] *1,926,666* חלקים עם מחיר במערכת.",
    "‏[AVI] *שאיבת הקטלוג* הושלמה 100 % – 6,695 מודלים. כשל אחרון היה במשימת *run_all_tasks*. 🚀",
    "המשימה האחרונה (run_all_tasks) נכשלה – אפשר להריץ *סטטוס*/ *שאיבה* מחדש.",
    "הריצה האחרונה של המשימה *run_all_tasks* נכשלה; יש לבדוק ולפעול על‑ידי פקודת *שאיבה*.",
    "הסגנון שמביא הכי הרבה תגובות הוא פוסט קצר עם פתיח שמדגיש בעיה נפוצה, וקוראת פעולה שמזמינה חיפוש לפי מספר רישוי.",
    "סרטונים של 15‑30 שניות שמציגים תהליך חיפוש חלק לפי מספר רישוי – “לפני‑אחרי”.",
    "חליל, למה הכוונה? איזו משימה תרצה לבצע – סטטוס, שאיבה, פוסטים, או משהו אחר? 🚀",
    "הפוסט שמוצג כרגע הוא גרסה עם ההערות שלך – הוא עדיין לא פורסם.",
    "חליל, אין אפשרות למחוק את כל הלקוחות דרך הצ'אט.",
    "הפוסט ממתין לאישור — ראה *פוסטים*, ואז *אשר <מזהה>* או *דחה <מזהה>*.",
    # replay 2026-11-27 round 2: the real path split over two sentences was wrongly replaced by the fallback
    "חליל, כדי לתקן את הפוסט 3e253a49 לפי ההערות שלך, שלח *שנהפוסט 3e253a49 <ההערות המדויקות>* או לחץ על כפתור NOA "
    "תשכתב. NOA תכתוב גרסה חדשה מהפוסט המקורי.",
]


@pytest.mark.parametrize("text", PROMISES)
def test_unbacked_promises_are_detected(text):
    assert con.unbacked_action_sentences(text), text


@pytest.mark.parametrize("text", CLEAN)
def test_real_paths_and_ordinary_replies_pass(text):
    assert con.unbacked_action_sentences(text) == [], con.unbacked_action_sentences(text)


def _run(coro):
    return asyncio.run(coro)


def test_clean_reply_is_not_regenerated():
    async def regen(_c):
        raise AssertionError("must not regenerate a clean reply")
    assert _run(con._enforce_action_integrity("‏[AVI] *1,926,666* חלקים עם מחיר.", regen)) == (
        "‏[AVI] *1,926,666* חלקים עם מחיר.", "clean")


def test_promise_is_regenerated_once_with_the_offending_sentence_named():
    seen = []

    async def regen(correction):
        seen.append(correction)
        return "הנוסח הנוכחי הוא ההערות שלך והוא לא יפורסם. כדי ש-NOA תכתוב גרסה: *שנהפוסט 3e253a49 <מה לשנות>*."
    out, how = _run(con._enforce_action_integrity("הבנתי. @NOA תכתוב גרסה חדשה ותשלח לך לבדיקה.", regen))
    assert how == "regenerated" and "שנהפוסט 3e253a49" in out
    assert len(seen) == 1 and "@NOA תכתוב גרסה חדשה" in seen[0]


def test_still_bad_after_regeneration_strips_the_sentence():
    async def regen(_c):
        return "חליל, ההערות שלך נכנסו לפוסט והוא לא פורסם.\n@NOA תכתוב גרסה חדשה ותשלח לך."
    out, how = _run(con._enforce_action_integrity("@NOA תכתוב גרסה חדשה.", regen))
    assert how == "stripped" and "NOA" not in out and "לא פורסם" in out


def test_strip_keeps_the_true_part_of_the_first_draft_when_the_regeneration_is_all_promise():
    async def regen(_c):
        return "NOA תכתוב לך גרסה."
    out, how = _run(con._enforce_action_integrity("חליל, הנוסח הנוכחי הוא ההערות שלך. בועז יטפל בזה.", regen))
    assert how == "stripped" and out == "חליל, הנוסח הנוכחי הוא ההערות שלך."


def test_a_promise_two_sentences_after_the_command_is_not_backed():
    assert con.unbacked_action_sentences("שלח *שנהפוסט 3e253a49 <מה לשנות>*. זה ייקח רגע. NOA תפרסם את זה מחר.")


def test_numbered_list_is_not_split_at_the_item_number():
    reply = "חליל, הצעה:\n1. נבנה קמפיין ממוקד.\n2. בועז יטפל בתשלום לספק.\n3. נמדוד המרות."
    assert con._strip_unbacked(reply) == "חליל, הצעה:\n1. נבנה קמפיין ממוקד.\n3. נמדוד המרות."


def test_nothing_left_falls_back_and_never_returns_a_promise():
    async def regen(_c):
        raise RuntimeError("provider down")
    out, how = _run(con._enforce_action_integrity("@NOA תכתוב גרסה חדשה.", regen))
    assert how == "fallback" and out == con._ACTION_FALLBACK
    assert con.unbacked_action_sentences(out) == []


@pytest.mark.parametrize("source", ["whatsapp", "telegram"])
def test_publish_guard_covers_every_chat_console(monkeypatch, source):
    """a6daabff (production, 2026-11-21): the owner's notes pasted from the TELEGRAM console; the WhatsApp-only check
    would have published them."""
    import social.campaign_manager as cm

    async def revs(_db, _pid):
        return [{"actor": "noa", "source": "noa_draft",
                 "content": "‏הקפצת נוזל קירור בלי סיבה? 🚗💧 ב Seat Leon, רדיאטור פגום הוא כמו שמיכה בקיץ – רדיאטור "
                            "מקורי החל מ-₪412 כולל מע\"מ. חפשו לפי מספר רישוי ותקבלו את החלק המדויק לרכב שלכם."},
                {"actor": "owner", "source": source,
                 "content": "אין מלה שהיא פח אוויר תעשי בדיקה ותשני את המילה של פח למשהו יותר היגיוני"}]
    monkeypatch.setattr(cm, "get_revisions", revs)
    assert _run(con._current_version_is_owner_notes(None, "a6daabff")) is True


def test_admin_panel_edit_is_not_treated_as_notes(monkeypatch):
    import social.campaign_manager as cm

    async def revs(_db, _pid):
        return [{"actor": "noa", "source": "noa_draft", "content": "פוסט ארוך " * 20},
                {"actor": "owner", "source": "web", "content": "תשני את המילה"}]
    monkeypatch.setattr(cm, "get_revisions", revs)
    assert _run(con._current_version_is_owner_notes(None, "x")) is False


def test_notes_posts_context_names_the_real_command(monkeypatch):
    async def pending(_db, limit=25):
        return [{"id": "3e253a49-c38e-43e7-9727-4170cac69dce"}, {"id": "90bb938d-5fc3-419b-87b5-fbddc870e9a3"}]

    async def notes(_db, pid):
        return pid.startswith("3e253a49")
    monkeypatch.setattr(con, "_list_pending_posts", pending)
    monkeypatch.setattr(con, "_current_version_is_owner_notes", notes)
    ctx = _run(con._notes_posts_context(None))
    assert "*שנהפוסט 3e253a49" in ctx and "90bb938d" not in ctx and "אשר* בלי מזהה" in ctx
    assert "*ערוך <מזהה> <טקסט>*" in ctx and "לא חלק מהשיחה" in ctx


def test_notes_posts_context_is_empty_on_db_error(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(con, "_list_pending_posts", boom)
    assert _run(con._notes_posts_context(None)) == ""
