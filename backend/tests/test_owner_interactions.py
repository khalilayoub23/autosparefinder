"""Owner interactive decisions (2026-11-25): classification, stable ids, owner-only, atomic one-shot consume, expiry,
correlation, follow-up binding, provider fallback (flag off / bridge refusal), group-mention rendering and routing guard."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import owner_interactions as oi
from owner_interactions_store import MemoryStore
from social import whatsapp_provider as wp


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


POST = [("✅ אשר ופרסם", "אשר ab12cd34"), ("✏️ ערוך", "ערוך ab12cd34"), ("❌ דחה", "דחה ab12cd34")]
EMAIL = [("📖 פרטים", "מייל m1"), ("✅ שלח טיוטה", "שלחמייל m1"), ("☑️ סגור", "סגורמייל m1")]


def _mk(store, choices=POST):
    return run(oi.create_from_choices(store, choices))


def tap(store, ix, action, owner=True, **kw):
    return run(oi.resolve_response(store, {"interaction_id": ix.id, "action_id": action, **kw}, sender_is_owner=owner))


def test_classification_and_agents():
    assert [a.id for a in oi.build(POST).actions] == ["approve", "edit", "reject"]
    e = oi.build(EMAIL)
    assert e.agent == "EMAIL" and [a.id for a in e.actions] == ["details", "approve", "cancel"]
    assert oi.build([("x", "משהו לא מוכר 1")]) is None, "unknown commands are never offered as buttons"
    assert all(a.label in oi.ACTION_LABELS.values() for a in e.actions), "labels come from the vocabulary"


def test_only_registered_actions_offered_and_public_payload_hides_commands():
    ix = oi.build([POST[0]])
    pl = oi.bridge_payload(ix, "b")
    assert [a["id"] for a in pl["actions"]] == ["approve"] and "command" not in pl["actions"][0] and "אשר ab12" not in str(pl["actions"])


def test_approve_runs_exact_registered_command_once():
    s = MemoryStore(); ix = _mk(s)
    r = tap(s, ix, "approve")
    assert (r.status, r.command) == ("execute", "אשר ab12cd34")
    r2 = tap(s, ix, "approve")
    assert r2.status == "reply" and r2.command == "", "second tap never executes"
    assert tap(s, ix, "reject").status == "reply", "a consumed interaction accepts no other action"
    assert [k for _, k, _ in s.events].count("response") == 1


def test_non_owner_and_unknown_and_altered_ids_rejected():
    s = MemoryStore(); ix = _mk(s)
    assert tap(s, ix, "approve", owner=False).status == "ignored"
    assert ix.state == "open"
    assert run(oi.resolve_response(s, {"interaction_id": "ix_nope1234", "action_id": "approve"}, sender_is_owner=True)).status == "reply"
    assert run(oi.resolve_response(s, {"interaction_id": "'; drop", "action_id": "approve"}, sender_is_owner=True)).status == "reply"
    assert tap(s, ix, "details").status == "reply", "an action that was not offered is refused"
    assert tap(s, ix, "approve").status == "execute"


def test_expired_and_correlation():
    s = MemoryStore(); ix = _mk(s)
    run(s.set_wa_message_id(ix.id, "WAREAL"))
    assert tap(s, ix, "approve", original_message_id="WAFAKE").status == "reply"
    ix.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert tap(s, ix, "approve", original_message_id="WAREAL").status == "reply"
    assert ix.state == "open" and not ix.consumed_action


def test_read_only_actions_do_not_consume():
    s = MemoryStore(); ix = _mk(s, EMAIL)
    assert tap(s, ix, "details").command == "מייל m1"
    assert tap(s, ix, "details").command == "מייל m1"
    assert tap(s, ix, "approve").command == "שלחמייל m1"


def test_concurrent_taps_execute_once():
    s = MemoryStore(); ix = _mk(s)

    async def both():
        return await asyncio.gather(*[oi.resolve_response(s, {"interaction_id": ix.id, "action_id": "approve"}, sender_is_owner=True) for _ in range(5)])
    assert [r.status for r in run(both())].count("execute") == 1


def test_followup_binds_next_text_then_reopens():
    s = MemoryStore()
    ix = run(oi.create_from_choices(s, [("a", "אשר p1")]))
    ix.actions.append(oi.Action("change", "שינוי", ""))   # a change action without a command -> structured follow-up
    r = tap(s, ix, "change")
    assert r.status == "followup" and ix.state == "awaiting_input"
    got = run(oi.take_followup(s, "תקצר את הכותרת"))
    assert got and got[0].id == ix.id and "תקצר את הכותרת" in got[1] and "אל תבצע" in got[1]
    assert ix.state == "open", "the revised proposal can be approved afterwards"
    assert run(oi.take_followup(s, "הודעה אחרת")) is None, "no second binding"


def test_idempotent_creation_returns_same_open_interaction():
    s = MemoryStore()
    a, b = _mk(s), _mk(s)
    assert a.id == b.id and len(s.rows) == 1


def test_group_text_never_carries_approvals():
    assert not oi.can_route_group_text("@972500 אשר ab12cd34")
    assert not oi.can_route_group_text("שלחמייל m1")
    assert oi.can_route_group_text("נועה מה הסטטוס של הפוסטים?")


# ── provider ────────────────────────────────────────────────────────────────────────────────────
def _poll_text():
    import owner_poll as op
    return op.attach(op.layout("t", "b", ref="ab12cd34"), "n", op.post_choices("ab12cd34"))


def test_provider_flag_off_keeps_poll_path(monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "972501234567"); monkeypatch.delenv("WA_INTERACTIVE_ENABLED", raising=False)
    sent = []

    async def fake(payload, t):
        sent.append(payload); return {"ok": True, "key": {"id": "K"}}
    monkeypatch.setattr(wp, "_post_send", fake)
    run(wp.send_message("972501234567", _poll_text()))
    assert "text" in sent[0] and "poll" in sent[1] and len(sent) == 2 and not any("interactive" in p for p in sent)


def test_provider_interactive_failure_falls_back_to_poll(monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "972501234567"); monkeypatch.setenv("WA_INTERACTIVE_ENABLED", "1")
    sent = []

    async def fake(payload, t):
        sent.append(payload)
        return {"ok": False, "error": "interactive disabled"} if "interactive" in payload else {"ok": True, "key": {"id": "K"}}

    async def boom(*a, **k):
        raise RuntimeError("no db in unit test")
    monkeypatch.setattr(wp, "_post_send", fake)
    import owner_interactions_store as st
    monkeypatch.setattr(st.PgStore, "insert", boom)
    r = run(wp.send_message("972501234567", _poll_text()))
    assert r["ok"] and "text" in sent[0] and any("poll" in p for p in sent), "text first, poll fallback, alert not failed"


def test_provider_never_sends_buttons_to_customers(monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "972501234567"); monkeypatch.setenv("WA_INTERACTIVE_ENABLED", "1")
    sent = []

    async def fake(payload, t):
        sent.append(payload); return {"ok": True}
    monkeypatch.setattr(wp, "_post_send", fake)
    run(wp.send_message("972509999999", _poll_text()))
    assert len(sent) == 1 and "interactive" not in sent[0] and "poll" not in sent[0]


# ── group mention rendering ─────────────────────────────────────────────────────────────────────
def test_mention_rendering_and_ui_safe_members():
    from routes import wa_group as g
    data = {"participants": [{"jid": "972500000001@s.whatsapp.net", "name": "דנה"}, {"jid": "98058566160397@lid", "name": ""},
                             {"jid": "972532426920:12@s.whatsapp.net", "name": "bot"}]}
    members = g.shape_members(data, ["972532426920"])
    assert len(members) == 2 and members[1]["name"] == "משתתף 2"
    assert "972500000001" not in str(members), "no phone numbers/JIDs reach the UI"
    t = members[0]["token"]
    text, jids = g.render_mentions(f"היי @{{{t}}} ו@{{{t}}} בדוק", data)
    assert text == "היי @972500000001 ו@972500000001 בדוק" and jids == ["972500000001@s.whatsapp.net"]
    with pytest.raises(ValueError):
        g.render_mentions("@{0123456789abcdef}", data)


# ── agent coverage added 2026-11-25 (NIR candidate, BOAZ escalation) ─────────────────────────────────────────────
import owner_poll as op


def test_nir_candidate_choices_bind_the_displayed_supplier_only():
    ch = op.supplier_candidate_choices("0a1b2c3d-aaaa-bbbb-cccc-111122223333")
    assert ch[0][1] == "אשרספק 0a1b2c3d" and ch[1][1] == "דחהספק 0a1b2c3d"
    ix = oi.build(ch)
    assert ix.agent == "NIR" and [a.id for a in ix.actions] == ["approve", "reject", "details"]
    assert op.supplier_candidate_choices("") == [], "no supplier id -> no buttons"


def test_nir_tap_resolves_to_the_exact_typed_command_once():
    store = MemoryStore()
    ix = _mk(store, op.supplier_candidate_choices("0a1b2c3d-aaaa"))
    r = tap(store, ix, "approve")
    assert (r.status, r.command, r.agent) == ("execute", "אשרספק 0a1b2c3d", "NIR")
    assert tap(store, ix, "approve").status == "reply", "second tap is a duplicate"
    assert tap(store, ix, "reject").status == "reply", "a consumed interaction cannot take a different action"


def test_boaz_choices_are_read_only_and_attributed_to_boaz():
    ch = op.boaz_task_choices("em123")
    ix = oi.build(ch)
    assert ix.agent == "BOAZ" and {a.id for a in ix.actions} == {"details", "recheck"}
    assert all(a.id in oi.READ_ONLY_ACTIONS for a in ix.actions), "no BOAZ button can approve a purchase or payment"
    assert not any(c[1].startswith(("אשררכש", "אשרתשלוםספק")) for c in ch)
    assert op.boaz_task_choices("") == []


def test_boaz_read_only_tap_does_not_consume():
    store = MemoryStore()
    ix = _mk(store, op.boaz_task_choices("em123"))
    assert tap(store, ix, "details").command == "מייל em123"
    assert tap(store, ix, "recheck").command == "משימות בועז"
    assert tap(store, ix, "details").status == "execute", "read-only actions stay re-tappable"


def test_boaz_alert_passes_choices_when_mail_ref_present():
    import boaz_tasks as bt
    seen = {}

    async def notify(*a, **kw):
        seen.update(kw)

    task = {"id": "t" * 36, "title": "x", "artifacts": {"email_ref": "em123", "source": "email_agent"}}
    run(bt._alert(notify, task, "boom"))
    assert seen["choices"] == op.boaz_task_choices("em123")
    seen.clear()
    run(bt._alert(notify, {"id": "t" * 36, "title": "x", "artifacts": {}}, "boom"))
    assert seen["choices"] is None


def test_selftest_commands_are_inert_and_bound():
    ch = [("אישור", "בדיקתאישור T1"), ("דחייה", "בדיקתדחייה T1"), ("שינוי", "בדיקתשינוי T1")]
    ix = oi.build(ch, task_ref="T1")
    assert ix.agent == "SELFTEST" and [a.id for a in ix.actions] == ["approve", "reject", "change"]
    store = MemoryStore()
    ix = run(oi.create_from_choices(store, ch, task_ref="T1"))
    r = tap(store, ix, "approve")
    assert r.status == "execute" and r.command == "בדיקתאישור T1"
    assert tap(store, ix, "approve").status == "reply", "no duplicate execution"


def test_selftest_console_reply_has_no_side_effects():
    from agents import owner_console as oc
    out = run(oc._process_owner_message("בדיקתדחייה T1", db=None))
    assert "לא בוצעה שום פעולה" in out and "דחייה" in out
