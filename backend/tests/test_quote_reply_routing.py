"""2026-11-13: a quote-reply is a command on the QUOTED post; ambiguity never edits a post."""
import asyncio
import pathlib

from agents import owner_console as oc
from agents.quote_context import quoted_ids, rewrite

A = "ce982985-38a4-46d8-868f-71c35e6055db"
B = "1f2e3d4c-aaaa-bbbb-cccc-000000000002"
NOTIF = "📣 פוסט חדש לאישור (ce982985)\nלאישור: אשר ce982985"
LONG = "הנה הגרסה המתוקנת של הפוסט עם שורה חדשה ומחיר מעודכן"


def test_single_id_commands_and_edit():
    assert rewrite("אשר", NOTIF) == "אשר ce982985"
    assert rewrite("דחה", NOTIF) == "דחה ce982985"
    assert rewrite("היסטוריה", NOTIF) == "היסטוריה ce982985"
    assert rewrite(LONG, NOTIF) == f"ערוך ce982985 {LONG}"


def test_never_guesses():
    two = NOTIF + "\nand 1f2e3d4c"
    assert len(quoted_ids(two)) == 2
    assert rewrite(LONG, two).startswith(LONG) and "בתגובה להודעה" in rewrite(LONG, two)
    assert rewrite("אשר", two) == "אשר quote-ambiguous"
    assert rewrite("מה המצב?", NOTIF).startswith("מה המצב?")           # short question: context only
    assert rewrite("ערוך 1f2e3d4c " + LONG, NOTIF).startswith("ערוך 1f2e3d4c")  # owner's own id wins
    assert rewrite(LONG, "") == LONG


def test_console_routes_quote_to_the_quoted_post(monkeypatch):
    calls = []

    async def fake_list(db, limit=25):
        return [{"id": A, "content": "old", "platforms": ["telegram"]}, {"id": B, "content": "other", "platforms": ["x"]}]

    async def fake_edit(db, post_id, new_text, source="whatsapp"):
        calls.append((post_id, new_text))
        return "EDITED"

    monkeypatch.setattr(oc, "_list_pending_posts", fake_list)
    monkeypatch.setattr(oc, "edit_post_text", fake_edit)
    for txt in (LONG, LONG + " v2", LONG + " v3"):                       # several successive revisions
        assert asyncio.run(oc._process_owner_message(txt, None, "whatsapp", NOTIF)) == "EDITED"
    assert [c[0] for c in calls] == [A, A, A]
    calls.clear()                                                         # unknown quoted id: nothing changes
    out = asyncio.run(oc._process_owner_message(LONG, None, "whatsapp", "x deadbeef y"))
    assert calls == [] and out != "EDITED"


def test_bridge_and_webhook_forward_quote_context():
    root = pathlib.Path(__file__).resolve().parent.parent
    js_path = root.parent / "whatsapp-bridge" / "index.js"
    if js_path.exists():                      # host checkout only (the backend image does not carry the bridge)
        js = js_path.read_text()
        assert "quoted_text: quotedText" in js and "ctxInfo?.stanzaId" in js
    hook = (root / "routes" / "webhooks.py").read_text()
    assert 'quoted_text=_quoted' in hook and "reply_to_message" in hook
