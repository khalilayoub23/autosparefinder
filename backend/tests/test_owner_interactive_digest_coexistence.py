"""Digest and interactive decisions coexist (2026-11-25): with WA_INTERACTIVE_ENABLED=1 an alert that carries choices is an
owner DECISION and is delivered at once (owner 1:1, where buttons are honoured), never parked for the morning digest;
routine alerts still go to the digest; with the flag off nothing changes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_owner_digest import app, run, od  # noqa: E402,F401

CHOICES = [("✅ הפעל", "אשרספק S12"), ("❌ דחה", "דחהספק S12")]


def test_decision_is_immediate_and_routine_still_digested(app, monkeypatch):
    monkeypatch.setenv("WA_INTERACTIVE_ENABLED", "1")
    quiet = []

    async def fake_quiet(to, text, critical=False, alert_key="", meta=None, **kw):
        quiet.append({"to": to, "text": text})
        return {"ok": True}
    monkeypatch.setattr(app.a, "_wa_send_quiet", fake_quiet)
    n = app.a.notify_owner
    run(n("supplier", "ספק חדש ממתין לאישורך", "נמצא ספק", alert_key="sup_dec", ref="S12", choices=CHOICES))
    run(n("health", "סנכרון הושלם", "ok", alert_key="routine_x"))
    sent = [d["text"] for d in app.direct] + [q["text"] for q in quiet]
    assert any("ספק חדש ממתין" in t for t in sent), "decision delivered immediately"
    assert [q["to"] for q in quiet] == ["972500000000"], "decision goes to the owner 1:1 (buttons are honoured only there)"
    assert not any("סנכרון הושלם" in t for t in sent), "routine alert is not sent immediately"
    pending = run(od.Store(app.r).pending())
    assert [e for e in pending if "סנכרון" in str(e.get("title"))] and not [e for e in pending if "ספק חדש" in str(e.get("title"))]


def test_flag_off_decision_alert_keeps_digest_behaviour(app, monkeypatch):
    monkeypatch.delenv("WA_INTERACTIVE_ENABLED", raising=False)
    run(app.a.notify_owner("supplier", "ספק חדש ממתין לאישורך", "נמצא ספק", alert_key="sup_dec2", ref="S12", choices=CHOICES))
    assert app.direct == [] and run(od.Store(app.r).pending())


def test_long_decision_alert_keeps_its_buttons_after_the_cap(app, monkeypatch):
    """#157: the 1200-char cap ran AFTER the choice trailer was attached, so a long NOA post lost its buttons/poll."""
    import owner_poll as op
    monkeypatch.setenv("WA_INTERACTIVE_ENABLED", "1")
    quiet = []

    async def fake_quiet(to, text, critical=False, alert_key="", meta=None, **kw):
        quiet.append(text)
        return {"ok": True}
    monkeypatch.setattr(app.a, "_wa_send_quiet", fake_quiet)
    run(app.a.notify_owner("social", "NOA — פוסט מוכן לאישורך", "א" * 3000, alert_key="long_post", ref="ab12cd34",
                           choices=op.post_choices("ab12cd34")))
    clean, poll = op.split(quiet[-1])
    assert poll and poll["commands"][0] == "אשר ab12cd34" and len(clean) < 1300


def test_read_only_buttons_stay_routine_but_declared_decisions_are_immediate(app, monkeypatch):
    import owner_poll as op
    monkeypatch.setenv("WA_INTERACTIVE_ENABLED", "1")
    quiet = []

    async def fake_quiet(to, text, critical=False, alert_key="", meta=None, **kw):
        quiet.append(text)
        return {"ok": True}
    monkeypatch.setattr(app.a, "_wa_send_quiet", fake_quiet)
    run(app.a.notify_owner("social", "סיכום קבוצות", "x", alert_key="ro_list", choices=op.list_choices("דראפטים", "📋")))
    assert not quiet, "a list button alone does not pull a routine alert out of the digest"
    run(app.a.notify_owner("orders", "רכש ממתין לאישורך — A1", "x", alert_key="proc_A1",
                           choices=op.procurement_choices(), decision=True))
    assert any("רכש ממתין" in t for t in quiet), "an owner decision request is immediate"
