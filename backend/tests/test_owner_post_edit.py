"""2026-11-05 regression: an owner edit of a NOA post must reach the post.

The owner edited posts from WhatsApp and from the Telegram "✏️ ערוך" button and neither changed anything:
the console had no edit command (free text went to the chat LLM) and the Telegram button promised "send the
corrected text" with nothing listening. Both now go through owner_console.edit_post_text ->
campaign_manager.update_post_content (content_version bump, back to pending_approval). No network, no database.
"""
import asyncio

from agents import owner_console as oc

POST = {"id": "ce982985-38a4-46d8-868f-71c35e6055db", "content": "old", "platforms": ["telegram"]}


def _run(msg, monkeypatch, pending=(POST,)):
    calls = []

    async def fake_list(db, limit=25):
        return list(pending)

    async def fake_edit(db, post_id, new_text):
        calls.append((post_id, new_text))
        return "EDITED"

    monkeypatch.setattr(oc, "_list_pending_posts", fake_list)
    monkeypatch.setattr(oc, "edit_post_text", fake_edit)
    reply = asyncio.run(oc._process_owner_message(msg, db=None))
    return reply, calls


def test_whatsapp_edit_command_replaces_text(monkeypatch):
    reply, calls = _run("ערוך ce982985 טקסט חדש\nשורה שנייה", monkeypatch)
    assert reply == "EDITED"
    assert calls == [(POST["id"], "טקסט חדש\nשורה שנייה")]


def test_edit_without_text_or_unknown_id_changes_nothing(monkeypatch):
    for msg in ("ערוך ce982985", "ערוך deadbeef משהו", "ערוך"):
        reply, calls = _run(msg, monkeypatch)
        assert calls == [] and "ערוך" in reply


def test_edit_post_text_uses_update_post_content(monkeypatch):
    seen = {}

    async def fake_update(db, *, post_id, new_content, scheduled_at=None):
        seen.update(post_id=post_id, new_content=new_content)
        return {"post_id": post_id, "content_version": 2, "status": "pending_approval"}

    from social import campaign_manager
    monkeypatch.setattr(campaign_manager, "update_post_content", fake_update)
    out = asyncio.run(oc.edit_post_text(None, POST["id"], "  new text  "))
    assert seen == {"post_id": POST["id"], "new_content": "new text"}
    assert "ממתין לאישור מחדש" in out and "ce982985" in out
    assert "ריק" in asyncio.run(oc.edit_post_text(None, POST["id"], "   "))
