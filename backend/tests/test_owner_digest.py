"""Mission 3 (2026-11-25): ONE consolidated morning WhatsApp digest. Nothing here reaches WhatsApp: every send is a recorder.
The store runs on an in-memory asyncio Redis; production uses the same code against the AOF-persistent Redis."""
import ast
import asyncio
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
for p in (BACKEND, Path(__file__).resolve().parent, Path(__file__).resolve().parent / "fixtures"):
    sys.path.insert(0, str(p))

import owner_digest as od  # noqa: E402
from fake_async_redis import FakeAsyncRedis  # noqa: E402
from owner_messages_2026_11 import ASAP_NEWSLETTER, DB_AGENT_BODY, DISK_SAMPLES  # noqa: E402

TZ = od.TZ
MORNING = datetime(2026, 11, 25, 9, 40, tzinfo=TZ)
T0 = MORNING.timestamp() - 8 * 3600


def run(c):
    return asyncio.run(c)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    r, clock = FakeAsyncRedis(), Clock()
    sent = []

    async def ok_send(text):
        sent.append(text)
        return {"ok": True}
    return types.SimpleNamespace(r=r, clock=clock, sent=sent, ok=ok_send, store=lambda: od.Store(r, clock))


def rec(env, title="t", body="b", **kw):
    kw.setdefault("category", "health")
    return run(env.store().record(title=title, body=body, **kw))


def deliver(env, send=None, now_local=MORNING, **kw):
    env.clock.t = now_local.timestamp()
    return run(env.store().deliver(send or env.ok, now_local=now_local, **kw))


# ── identity, dedup, lifecycle ────────────────────────────────────────────────────────────────────────────
def test_counter_in_title_is_the_same_identity_and_fingerprint():
    assert od.identity_for("health", "נכשל 10 פעמים") == od.identity_for("health", "נכשל 34 פעמים")
    assert od.fingerprint_for("נכשל 10 פעמים", "x 5", "info") == od.fingerprint_for("נכשל 34 פעמים", "x 99", "info")
    assert od.fingerprint_for("a", "b", "info") != od.fingerprint_for("a", "c", "info")


def test_unchanged_repeats_are_counted_not_listed(env):
    for i in range(20):
        rec(env, f"שאיבה תקועה {i}", "אישור Cloudflare נכשל", alert_key="fs", severity="warning")
    pend = run(env.store().pending())
    assert len(pend) == 1 and pend[0]["repeats"] == 20


def test_changed_version_replaces_pending_and_keeps_first_ts(env):
    rec(env, "דיסק", "הדיסק מתמלא", alert_key="disk", severity="warning")
    env.clock.t += 60
    rec(env, "דיסק", "הדיסק כמעט מלא", alert_key="disk", severity="warning")
    pend = run(env.store().pending())
    assert len(pend) == 1 and "כמעט" in pend[0]["body"] and pend[0]["first_ts"] == T0


def test_delivered_event_is_not_repeated_but_a_real_change_is(env):
    rec(env, "דיסק", "80%", alert_key="disk")
    assert deliver(env)["ok"]
    assert rec(env, "דיסק", "80%", alert_key="disk").get("error") == "digest_unchanged"      # same state: nothing queued
    assert run(env.store().pending()) == []
    rec(env, "דיסק", "95% — קריטי", alert_key="disk")                                          # materially different
    assert run(env.store().pending())[0]["state"] == "changed"


def test_due_reminder_is_listed_again(env):
    rec(env, "דיסק", "80%", alert_key="disk")
    deliver(env)
    rec(env, "דיסק", "80%", alert_key="disk", state="reminder")
    assert run(env.store().pending())[0]["state"] == "reminder"


def test_old_unchanged_condition_returns_after_the_suppression_window(env):
    rec(env, "דיסק", "80%", alert_key="disk"); deliver(env)
    env.clock.t += od.SUPPRESS_REPEAT_S + 10
    assert rec(env, "דיסק", "80%", alert_key="disk").get("ok") is True


def test_opened_and_resolved_before_delivery_is_listed_once_as_done(env):
    rec(env, "תקלה", "x", alert_key="inc", severity="warning")
    rec(env, "תקלה נפתרה", "ok", alert_key="inc", state="resolved", severity="success")
    pend = run(env.store().pending())
    assert len(pend) == 1 and pend[0]["opened_resolved"] and od.section_for(pend[0]) == "done"


def test_resolution_of_a_delivered_incident_and_next_occurrence_is_new(env):
    rec(env, "תקלה", "x", alert_key="inc"); deliver(env)
    rec(env, "התקלה נפתרה", "ok", alert_key="inc", state="resolved")
    env.clock.t += 100
    deliver(env, now_local=datetime(2026, 11, 26, 9, 40, tzinfo=TZ))
    rec(env, "תקלה", "x", alert_key="inc")
    assert run(env.store().pending())[0]["state"] == "new"


def test_resolution_with_nothing_open_is_dropped(env):
    assert rec(env, "נפתר", "ok", alert_key="never", state="resolved").get("error") == "nothing_to_resolve"


def test_cross_producer_alias_is_one_event(env):
    """Email Agent and BOAZ report the same supplier mail under different alert keys."""
    rec(env, "מייל חדש — ספק", "מאת amanda", alert_key="email_1", category="email", event_keys=["mail:ab12"])
    rec(env, "BOAZ צריך החלטה", "ספק ASAP", alert_key="boaz_9", category="supplier", event_keys=["mail:ab12"], state="action")
    pend = run(env.store().pending())
    assert len(pend) == 1


# ── rendering ──────────────────────────────────────────────────────────────────────────────────────────────
def mk(i, sev="info", cat="health", state="new", **kw):
    base = dict(id=f"k:{i}", cat=cat, sev=sev, state=state, title=f"אירוע מספר {i}", body="משהו קרה בשירות. פרט נוסף.",
                ref="", actions=[], fp=str(i), first_ts=T0 + i, last_ts=T0 + i, repeats=1, kind="alert", section="", keys=[])
    base.update(kw)
    return base


def test_hebrew_sections_in_the_specified_order_and_verdict():
    evs = [mk(1, cat="harvest"), mk(2, cat="email"), mk(3, cat="supplier"), mk(4, cat="social"), mk(5),
           mk(6, state="resolved", sev="success"), mk(7, actions=["אשרספק abc"])]
    [text] = od.render_digest("D20261125", evs, now_local=MORNING)
    order = [text.index(t) for _, t in od.SECTIONS[1:] if t in text]
    assert order == sorted(order) and len(order) == 7
    assert "D20261125" in text and "דורשים את פעולתך" in text and "▶ אשרספק abc" in text
    for banned in ("Traceback", "SELECT ", "sqlalche", "fallocate"):
        assert banned not in text


def test_one_hundred_events_compact_into_one_message():
    evs = [mk(i, cat=["health", "email", "supplier", "harvest", "social"][i % 5]) for i in range(100)]
    parts = od.render_digest("D1", evs, now_local=MORNING)
    assert len(parts) == 1 and len(parts[0]) <= od.MSG_LIMIT and "ועוד" in parts[0]


def test_split_only_when_unavoidable_with_shared_id_and_numbering():
    cats = ["health", "email", "supplier", "harvest", "social", "orders"]
    evs = [mk(i, cat=cats[i % 6], title="כותרת ארוכה " + "ש" * 50) for i in range(80)]
    parts = od.render_digest("D2", evs, now_local=MORNING, limit=330)
    assert len(parts) > 1 and all(len(p) <= 450 for p in parts)
    assert all("D2" in p and f"חלק {i}/{len(parts)}" in p for i, p in enumerate(parts, 1))


def test_empty_digest_is_never_rendered():
    assert od.render_digest("D", [], now_local=MORNING) == []


def test_critical_event_is_in_the_first_section():
    [text] = od.render_digest("D", [mk(1), mk(2, sev="critical")], now_local=MORNING)
    assert text.index("דורש טיפול מיידי") < text.index("בריאות המערכת")


# ── delivery, checkpoint, retry, restart ───────────────────────────────────────────────────────────────────
def test_quiet_hours_and_before_digest_time_send_nothing(env):
    rec(env, "a", "b")
    assert deliver(env, window_open=False)["skipped"] == "quiet_hours"
    assert deliver(env, now_local=datetime(2026, 11, 25, 9, 5, tzinfo=TZ))["skipped"] == "before_digest_time"
    assert deliver(env, now_local=datetime(2026, 11, 25, 3, 0, tzinfo=TZ))["skipped"] == "before_digest_time"
    assert env.sent == []


def test_empty_store_sends_no_digest(env):
    assert deliver(env)["skipped"] == "empty" and env.sent == []


def test_success_sends_once_and_advances_checkpoint_exactly_once(env):
    for i in range(5):
        rec(env, f"אירוע {i}", "x", alert_key=f"k{i}")
    res = deliver(env)
    assert res["ok"] and res["events"] == 5 and len(env.sent) == 1
    assert deliver(env)["skipped"] == "empty" and len(env.sent) == 1                 # second pass: nothing, no duplicate
    last = json.loads(run(env.r.get(od.K_LAST)))
    assert last["id"] == res["digest"] and len(last["events"]) == 5
    assert run(env.store().pending()) == [] and run(env.store().frozen()) is None


def test_failed_send_keeps_every_event_then_retries_the_same_digest(env):
    for i in range(3):
        rec(env, f"אירוע {i}", "x", alert_key=f"k{i}")
    bad = []

    async def down(text):
        bad.append(text)
        return {"ok": False, "error": "WhatsApp session invalid (logged out)"}
    r1 = deliver(env, send=down)
    assert not r1["ok"] and run(env.r.get(od.K_LAST)) is None                        # checkpoint NOT advanced
    assert run(env.store().frozen())["attempts"] == 1
    r2 = deliver(env, now_local=MORNING.replace(minute=41))                           # inside backoff: no hammering
    assert r2["skipped"] == "backoff" and len(bad) == 1
    r3 = deliver(env, now_local=MORNING.replace(hour=10))
    assert r3["ok"] and r3["digest"] == r1["digest"] and r3["events"] == 3 and r3["attempts"] == 1
    assert env.sent == [bad[0]]                                                       # identical frozen text, delivered once


def test_a_queued_result_is_not_delivery(env):
    rec(env, "a", "b")

    async def queued(text):
        return {"ok": True, "queued": True}
    assert not deliver(env, send=queued)["ok"]
    assert run(env.store().frozen()) is not None


def test_events_arriving_while_the_digest_is_sent_wait_for_the_next_one(env):
    rec(env, "ישן", "x", alert_key="old")

    async def slow(text):
        await env.store().record(category="health", title="חדש באמצע", body="y", alert_key="mid")   # arrives during delivery
        env.sent.append(text)
        return {"ok": True}
    res = deliver(env, send=slow)
    assert res["events"] == 1 and "חדש באמצע" not in env.sent[0]
    assert [e["title"] for e in run(env.store().pending())] == ["חדש באמצע"]
    deliver(env, now_local=datetime(2026, 11, 26, 9, 40, tzinfo=TZ))
    assert "חדש באמצע" in env.sent[1] and len(env.sent) == 2


def test_restart_between_record_and_digest_loses_nothing(env):
    rec(env, "לפני הפעלה מחדש", "x", alert_key="k")
    again = od.Store(env.r, env.clock)                                                # a new process, same Redis
    assert [e["title"] for e in run(again.pending())] == ["לפני הפעלה מחדש"]


def test_crash_after_the_last_part_was_sent_does_not_resend(env):
    rec(env, "a", "b")
    run(env.store().deliver(env.ok, now_local=MORNING)) if False else None
    fz = {"id": "D20261125", "events": run(env.store().pending()), "parts": ["x"], "sent_parts": 1, "attempts": 0,
          "created": env.clock.t, "next_try": 0}
    run(env.r.hdel(od.K_EVENTS, fz["events"][0]["id"]))
    run(env.r.set(od.K_FROZEN, json.dumps(fz)))
    res = deliver(env)
    assert res["ok"] and env.sent == [] and run(env.r.get(od.K_LAST))


def test_partial_multipart_delivery_resumes_at_the_first_unsent_part(env):
    cats = ["health", "email", "supplier", "harvest", "social", "orders"]
    for i in range(80):
        rec(env, f"כותרת ארוכה {i} " + "ש" * 50, "x", category=cats[i % 6], alert_key=f"k{i}")
    old = od.MSG_LIMIT
    od.MSG_LIMIT = 900
    try:
        calls = {"n": 0}

        async def flaky(text):
            calls["n"] += 1
            if calls["n"] == 2:
                return {"ok": False, "error": "timeout"}
            env.sent.append(text)
            return {"ok": True}
        import owner_digest
        orig = owner_digest.render_digest
        owner_digest.render_digest = lambda *a, **k: orig(*a, **dict(k, limit=330))
        r1 = deliver(env, send=flaky)
        assert not r1["ok"] and len(env.sent) == 1
        r2 = deliver(env, now_local=MORNING.replace(hour=11))
        assert r2["ok"] and len(env.sent) == r2["parts"]                              # each part exactly once
        assert len(set(env.sent)) == len(env.sent)
    finally:
        od.MSG_LIMIT = old
        owner_digest.render_digest = orig


def test_stale_unsent_digest_is_dissolved_and_rebuilt_complete(env):
    rec(env, "ליל קודם", "x", alert_key="a")

    async def down(t):
        return {"ok": False, "error": "bridge unreachable"}
    deliver(env, send=down, now_local=datetime(2026, 11, 24, 9, 40, tzinfo=TZ))
    rec(env, "יום אחר כך", "y", alert_key="b")                                         # arrives while the old digest is stuck
    res = deliver(env, now_local=datetime(2026, 11, 25, 11, 0, tzinfo=TZ))
    assert res["ok"] and res["events"] == 2 and len(env.sent) == 1


def test_old_events_go_to_a_visible_dead_list_and_can_be_replayed(env):
    rec(env, "ישן מאוד", "x", alert_key="a")
    env.clock.t += od.EVENT_MAX_AGE_S + 100
    deliver(env, now_local=datetime.fromtimestamp(env.clock.t, TZ).replace(hour=10, minute=0))
    st = run(env.store().status())
    assert st["dead"] == 1 and st["pending"] == 0 and env.sent == []
    assert run(env.store().replay_dead()) == 1 and len(run(env.store().pending())) == 1


def test_digest_id_is_unique_per_delivery_on_one_day(env):
    rec(env, "a", "b", alert_key="a"); r1 = deliver(env)
    rec(env, "c", "d", alert_key="c"); r2 = deliver(env)
    assert r1["digest"] == "D20261125" and r2["digest"] == "D20261125-2"


# ── the app wiring (notify_owner / bridge outage / critical exception) ─────────────────────────────────────
@pytest.fixture
def app(monkeypatch):
    import BACKEND_API_ROUTES as a
    r, direct, bridge = FakeAsyncRedis(), [], []

    async def _get():
        return r

    async def fake_update(text, critical=False, alert_key="", meta=None):
        direct.append({"text": text, "critical": critical})
        return {"ok": True}

    async def fake_bridge(to, text):
        bridge.append({"to": to, "text": text})
        return {"ok": True}
    monkeypatch.setattr(a, "get_redis", _get)
    monkeypatch.setattr(a, "_wa_send_update", fake_update)
    monkeypatch.setattr(a, "_wa_send", fake_bridge)
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "972500000000")
    monkeypatch.setenv("OWNER_DIGEST_ENABLED", "1")
    monkeypatch.setattr(a, "_notify_window_open", lambda now_local=None: (True, datetime.now(TZ)))
    return types.SimpleNamespace(a=a, r=r, direct=direct, bridge=bridge)


def test_routine_alerts_never_send_immediately_and_critical_still_does(app):
    run(app.a.notify_owner("health", "אזהרה", "x", severity="warning", alert_key="w"))
    run(app.a.notify_owner("supplier", "מידע", "y", severity="info", alert_key="i"))
    assert app.direct == []
    run(app.a.notify_owner("health", "שירות נפל", "z", severity="critical", alert_key="c"))
    assert len(app.direct) == 1 and app.direct[0]["critical"] is True
    assert len(run(od.Store(app.r).pending())) == 2                                  # the critical one is not in the digest


def test_realistic_night_becomes_one_hebrew_digest(app, monkeypatch):
    from email_agent import owner_flow
    n = app.a.notify_owner
    for i in range(20):                                                              # FlareSolverr x20
        run(n("harvest", f"שאיבה תקועה ({i})", "אישור Cloudflare נכשל", severity="warning", alert_key="fs",
              fingerprint="open", state="new"))
    for row in ASAP_NEWSLETTER:                                                      # 10 emails + BOAZ for one sender
        run(n("email", "מייל חדש — ספק", "מאת: amanda", alert_key=f"email_{row['id']}", event_keys=owner_flow.event_keys(row)))
        run(n("supplier", "BOAZ צריך החלטה", "x", alert_key=f"boaz_{row['id']}", event_keys=[f"mail:{owner_flow.ref(row)}"]))
    for d in DISK_SAMPLES:
        run(n("health", f"שימוש בדיסק {d['pct']}%", "x", severity="warning", alert_key="disk_warning"))
    run(n("health", "עבודת הלילה: 3 משימות נכשלו", DB_AGENT_BODY, severity="error", alert_key="dbfail"))
    run(n("social", "NOA בפייסבוק אתמול", "20 טיוטות", alert_key="noa_eod"))
    run(n("health", "סנכרון מחירים הושלם", "ok", severity="success", alert_key="job_ok"))
    run(n("health", "תקלה", "x", severity="warning", alert_key="inc", fingerprint="open", state="new"))
    run(app.a.resolve_owner_alert("health", "התקלה נפתרה", "ok", alert_key="inc"))
    assert app.direct == [] and app.bridge == []
    res = run(app.a._owner_digest_pass(force=True))
    assert res["ok"] and len(app.bridge) == 1
    text = app.bridge[0]["text"]
    assert len(text) <= od.MSG_LIMIT and "עדכון בוקר" in text and "אסמכתה" in text
    for banned in ("sqlalche", "Background on this error", "RETURNING", "http", "Traceback", "OOM"):
        assert banned not in text
    assert text.count("מייל חדש") <= 1 and text.count("BOAZ צריך") <= 1               # one sender = one line
    assert "נפתח ונפתר" in text and text.index("הושלם ונפתר") < text.index("סנכרון מחירים")


def test_bridge_outage_keeps_events_reports_nothing_through_the_dead_channel_and_recovers(app):
    run(app.a.notify_owner("health", "אירוע", "x", alert_key="e1"))
    results = {"mode": "down"}

    async def bridge(to, text):
        if results["mode"] == "down":
            return {"ok": False, "error": "WhatsApp session invalid (logged out)"}
        app.bridge.append(text)
        return {"ok": True}
    app.a._wa_send = bridge
    r1 = run(app.a._owner_digest_pass(force=True))
    assert not r1["ok"] and app.direct == [] and app.bridge == []                   # no outage message through the same channel
    assert run(app.r.get(app.a._wa_channel_key()))                                    # channel state recorded
    assert len(run(od.Store(app.r).pending())) == 0 and run(od.Store(app.r).frozen())  # frozen, not lost
    results["mode"] = "up"
    r2 = run(app.a._owner_digest_pass(force=True))
    assert r2["ok"] and len(app.bridge) == 1 and "מנותק" in app.bridge[0]
    assert not run(app.r.get(app.a._wa_channel_key()))


def test_pass_respects_quiet_hours(app, monkeypatch):
    run(app.a.notify_owner("health", "אירוע", "x", alert_key="e1"))
    monkeypatch.setattr(app.a, "_notify_window_open", lambda now_local=None: (False, datetime.now(TZ)))
    assert run(app.a._owner_digest_pass(force=True))["skipped"] == "quiet_hours" and app.bridge == []


def test_redis_down_falls_back_to_a_direct_send_never_loses_the_alert(app, monkeypatch):
    class Boom:
        def __getattr__(self, n):
            async def f(*a, **k):
                raise ConnectionError("redis down")
            return f

    async def _bad():
        return Boom()
    monkeypatch.setattr(app.a, "get_redis", _bad)
    run(app.a.notify_owner("health", "אירוע", "x", alert_key="e1"))
    assert len(app.direct) == 1


def test_kill_switch_restores_the_direct_path(app, monkeypatch):
    monkeypatch.setenv("OWNER_DIGEST_ENABLED", "0")
    run(app.a.notify_owner("health", "אירוע", "x", alert_key="e1"))
    assert len(app.direct) == 1


# ── guard: no routine owner path outside the digest ────────────────────────────────────────────────────────
def test_no_routine_direct_update_send_outside_the_digest():
    """Every `_wa_send_update` call in production code is either critical=True (the approved immediate exception)
    or lives inside notify_owner (its direct fallback / critical path) or `_owner_digest_report` (fallback)."""
    allowed_funcs = {"notify_owner", "_owner_digest_report"}
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", "devtests/", "legacy/", "candidates/")):
            continue
        src = path.read_text(encoding="utf-8", errors="ignore")
        if "_wa_send_update" not in src:
            continue
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "_wa_send_update":
                    crit = any(k.arg == "critical" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
                               for k in node.keywords)
                    if not crit and fn.name not in allowed_funcs:
                        offenders.append((rel, fn.name, node.lineno))
    assert not offenders, offenders
