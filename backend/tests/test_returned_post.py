"""Owner returns an edited NOA post without a command -> tied to the right pending post (2026-11-13)."""
from agents.returned_post import match_returned_post

P1 = {"id": "8f3a1c2d-0000-0000-0000-000000000001",
      "content": "🔧 רפידות בלם לקורולה 2018 - מ-198 ש\"ח בלבד!\nהתאמה מאומתת לרכב שלך.\nחפשו לפי מספר רכב ב-AutoSpareFinder"}
P2 = {"id": "11aa22bb-0000-0000-0000-000000000002",
      "content": "מסנן שמן לספורטאז' 2020 מ-61 ש\"ח. טיפ: מחליפים כל 10,000 ק\"מ.\nמה הרכב שלכם?"}


def test_edited_text_without_command_matches_original():
    edited = P1["content"].replace("בלבד!", "בלבד, כולל משלוח!")
    kind, post, text = match_returned_post(edited, [P1, P2])
    assert kind == "match" and post is P1 and text == edited


def test_second_round_matches_again():
    r1 = P1["content"].replace("בלבד!", "בלבד, כולל משלוח!")
    r2 = r1 + "\nשאלה: איזה רכב יש לכם?"
    assert match_returned_post(r2, [dict(P1, content=r1), P2])[1]["id"] == P1["id"]


def test_id_selects_post_and_is_stripped():
    msg = "🆔 8f3a1c2d\nרפידות בלם מקוריות לקורולה, התאמה מאומתת, ללא הפתעות במחיר."
    kind, post, text = match_returned_post(msg, [P1, P2])
    assert kind == "match" and post is P1 and "8f3a1c2d" not in text and "🆔" not in text


def test_short_chat_is_not_an_edit():
    assert match_returned_post("מה המצב עם הפוסטים?", [P1, P2])[0] == "none"


def test_unrelated_long_text_is_not_an_edit():
    msg = "תבדוק בבקשה למה הקטלוג לא התעדכן אתמול בלילה ותחזור אלי עם תשובה מסודרת על הסיבה"
    assert match_returned_post(msg, [P1, P2])[0] == "none"


def test_two_near_identical_posts_are_ambiguous_not_guessed():
    a = dict(P1, id="aaaaaaaa-0000-0000-0000-00000000000a")
    b = dict(P1, id="bbbbbbbb-0000-0000-0000-00000000000b", content=P1["content"] + "!")
    kind, posts, _ = match_returned_post(P1["content"], [a, b])
    assert kind == "ambiguous" and len(posts) == 2


def test_no_pending_posts():
    assert match_returned_post(P1["content"], [])[0] == "none"
