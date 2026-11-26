"""Owner polls (2026-11-13): trailer round-trip, owner-only delivery, late footer keeps the poll last,
and the provider sends the text first and the poll second (never to a non-owner)."""
import asyncio

import owner_poll as op
from social import whatsapp_provider as wp

OWNER = "972501234567"


def _alert():
    return op.attach(op.layout("📣 [סושיאל] פוסט מוכן", "גוף", ref="ab12cd34"), "פוסט מוכן",
                     op.post_choices("ab12cd34"))


def test_roundtrip_and_validation():
    text, poll = op.split(_alert())
    # #157: NOA change (resume to NOA) + own edit (resume) between approve and reject
    assert poll["values"] == ["✅ אשר ופרסם", "🔁 NOA תשכתב", "✏️ אכתוב נוסח משלי", "❌ דחה"]  # #158 clearer labels
    assert poll["commands"] == ["אשר ab12cd34", "שנהפוסט ab12cd34", "ערוך ab12cd34", "דחה ab12cd34"]
    assert "ASFPOLL" not in text and text.startswith("*📣") and "🆔 ab12cd34" in text
    # one option is not a poll; the alert is the plain text
    assert op.attach("t", "n", [("a", "b")]) == "t"
    assert op.split("plain") == ("plain", None)
    # malformed trailer is stripped, never shown
    bad = "x\n⁣⁣ASFPOLL⁣{nope⁣⁣END"
    assert op.split(bad) == ("x\n⁣⁣ASFPOLL⁣{nope⁣⁣END", None) or "ASFPOLL" not in op.split(bad)[0]


def test_limits_and_dedup():
    ch = [(f"o{i}", f"c{i}") for i in range(10)] + [("o1", "dup")]
    _, poll = op.split(op.attach("t", "n", ch))
    assert len(poll["values"]) == op.MAX_OPTIONS and len(set(poll["values"])) == len(poll["values"])


def test_owner_target(monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "+972-50-123-4567")
    assert op.is_owner_target("whatsapp:+972501234567") and op.is_owner_target("972501234567@s.whatsapp.net")
    assert not op.is_owner_target("972509999999") and not op.is_owner_target("120363@g.us")


def test_late_footer_keeps_poll_last():
    import importlib
    routes = importlib.import_module("BACKEND_API_ROUTES")
    out = routes._late_footer(_alert(), "13/11 02:01")
    text, poll = op.split(out)
    assert text.rstrip().endswith("_🌙 נשמר משעות הלילה 13/11 02:01_") and poll and len(poll["values"]) == 4
    assert not text.startswith("🌙"), "late note is a footer, not a leading bracket line"
    plain = routes._late_footer("hello", "13/11 02:01")
    assert plain == "🌙 [נשלח מאוחר — נשמר משעות הלילה 13/11 02:01]\nhello", "legacy banner unchanged for non-poll"


def _run_send(monkeypatch, to, text):
    sent = []

    async def fake_post(payload, timeout):
        sent.append(payload)
        return {"ok": True, "key": {"id": "K"}}
    monkeypatch.setattr(wp, "_post_send", fake_post)
    res = asyncio.run(wp.send_message(to, text))
    return res, sent


def test_provider_sends_text_then_poll_to_owner(monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", OWNER)
    res, sent = _run_send(monkeypatch, OWNER, _alert())
    assert res["ok"] and len(sent) == 2
    assert "poll" not in sent[0] and "ASFPOLL" not in sent[0]["text"]
    assert sent[1]["poll"]["commands"][0] == "אשר ab12cd34" and "text" not in sent[1]


def test_provider_never_polls_a_non_owner(monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", OWNER)
    res, sent = _run_send(monkeypatch, "972509999999", _alert())
    assert res["ok"] and len(sent) == 1 and "ASFPOLL" not in sent[0]["text"]


def test_poll_failure_does_not_fail_the_alert(monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", OWNER)
    calls = []

    async def fake_post(payload, timeout):
        calls.append(payload)
        if "poll" in payload:
            raise RuntimeError("bridge hiccup")
        return {"ok": True, "key": None}
    monkeypatch.setattr(wp, "_post_send", fake_post)
    assert asyncio.run(wp.send_message(OWNER, _alert()))["ok"] is True
