"""Owner WhatsApp notifications: change reporting, central routing, watchdog kill burst, queue atomicity
(FIXES_TRACKER 2026-11-13 "WhatsApp notification system closure").

Nothing here reaches WhatsApp: the bridge send / `_wa_send_update` is always a recorder.

  A. notify_owner change reporting (new / changed / unchanged / reminder / resolved) - in-memory Redis.
  B. Rendering: state tag, the `error` severity icon, the `system` category alias, unchanged legacy header.
  C. db_update_agent.validate_watchdog_actions: kill burst -> notify_owner, key `watchdog_kill_burst`.
  D. Source guard (AST): no owner alert bypasses notify_owner; every direct provider send is on a
     reviewed allowlist of conversational / transactional customer paths.
  E. Email digest and Facebook-session alert content.
  F. Quiet-hours queue against the REAL isolated sandbox Redis: concurrent enqueue, a head that moves
     while a send is in flight, exception / ok=False retention, legacy entries.

Expected values are written out here, independently of the code under test.
"""
import ast
import asyncio
import json
import os
import sys
import types
import uuid
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import BACKEND_API_ROUTES as app  # noqa: E402


# ───────────────────────────── helpers ─────────────────────────────
class FakeRedis:
    def __init__(self):
        self.keys = {}

    async def exists(self, k):
        return k in self.keys

    async def get(self, k):
        return self.keys.get(k)

    async def set(self, k, v, ex=None):
        self.keys[k] = v

    async def delete(self, *ks):
        for k in ks:
            self.keys.pop(k, None)


@pytest.fixture
def owner(monkeypatch):
    calls, result, r, clock = [], {"value": {"ok": True}}, FakeRedis(), {"t": 1_800_000_000.0}

    async def fake_update(text, critical=False, alert_key=""):
        calls.append({"text": text, "critical": critical, "alert_key": alert_key})
        return dict(result["value"])

    async def _get():
        return r
    monkeypatch.setattr(app, "_wa_send_update", fake_update)
    monkeypatch.setattr(app, "get_redis", _get)
    monkeypatch.setattr(app.time, "time", lambda: clock["t"])

    def send(*a, **kw):
        # a cooldown is not what is under test here: clear it so only the fingerprint decides
        r.keys = {k: v for k, v in r.keys.items() if not k.startswith("autospare:alert_cooldown:")}
        return asyncio.run(app.notify_owner(*a, **kw))
    return types.SimpleNamespace(calls=calls, result=result, r=r, clock=clock, send=send)


def _header(call):
    return call["text"].split("\n", 1)[0]


# ───────────────────────────── A. change reporting ─────────────────────────────
@pytest.mark.parametrize("prev,fp,state,remind,now,expected", [
    (None, "a", "", 0, 100, "new"),                                   # first report
    (None, "a", "action", 0, 100, "action"),                          # first report, caller's tag kept
    ({"fp": "a", "ts": 0}, "a", "", 0, 10**9, None),                  # unchanged, no reminder configured
    ({"fp": "a", "ts": 0}, "a", "action", 3600, 3599, None),          # unchanged, reminder not due
    ({"fp": "a", "ts": 0}, "a", "action", 3600, 3600, "reminder"),    # unchanged, reminder due
    ({"fp": "a", "ts": 0}, "b", "action", 3600, 1, "changed"),        # the condition changed
    ({"fp": "a", "ts": 0}, "a", "resolved", 0, 1, "resolved"),        # resolution always goes out
    ({}, "a", "", 0, 1, "new"),                                       # unreadable previous state
])
def test_decide_state_matrix(prev, fp, state, remind, now, expected):
    assert app._notify_decide_state(prev, fp, state, remind, now) == expected


def test_unchanged_report_is_suppressed_and_a_genuine_change_is_delivered(owner):
    kw = dict(alert_key="cond", cooldown_s=60, fingerprint="stagnant", remind_s=86400)
    first = owner.send("health", "קטלוג: עדכונים נמוכים", "רק 0 חלקים", **kw)
    again = owner.send("health", "קטלוג: עדכונים נמוכים", "רק 8 חלקים", **kw)       # same condition
    owner.clock["t"] += 3600
    hour_later = owner.send("health", "קטלוג: עדכונים נמוכים", "רק 3 חלקים", **kw)
    changed = owner.send("health", "קטלוג: עדכונים נמוכים", "x", **dict(kw, fingerprint="other"))
    assert first["ok"] is True
    assert again == {"ok": False, "suppressed": True, "error": "unchanged"}
    assert hour_later == {"ok": False, "suppressed": True, "error": "unchanged"}
    assert changed["ok"] is True
    assert [_header(c) for c in owner.calls] == [
        "ℹ️ [🏥 בריאות המערכת] 🆕 חדש · קטלוג: עדכונים נמוכים",
        "ℹ️ [🏥 בריאות המערכת] 🔄 שינוי · קטלוג: עדכונים נמוכים",
    ]


def test_reminder_after_remind_s_and_only_then(owner):
    kw = dict(alert_key="fb", cooldown_s=60, state="action", fingerprint="r1", remind_s=3 * 86400, severity="warning")
    owner.send("social", "NOA — נדרש חידוש חיבור לפייסבוק", "b", **kw)
    for day in (1, 2):                                   # the daily repeats of the old behaviour
        owner.clock["t"] += 86400
        assert owner.send("social", "NOA — נדרש חידוש חיבור לפייסבוק", "b", **kw)["error"] == "unchanged"
    owner.clock["t"] += 86400                           # day 3: reminder due
    assert owner.send("social", "NOA — נדרש חידוש חיבור לפייסבוק", "b", **kw)["ok"] is True
    owner.clock["t"] += 86400                           # day 4: the reminder restarted the clock
    assert owner.send("social", "NOA — נדרש חידוש חיבור לפייסבוק", "b", **kw)["error"] == "unchanged"
    assert [_header(c) for c in owner.calls] == [
        "🟡 [📣 סושיאל] 👉 נדרשת פעולה · NOA — נדרש חידוש חיבור לפייסבוק",
        "🟡 [📣 סושיאל] ⏰ תזכורת (ללא שינוי) · NOA — נדרש חידוש חיבור לפייסבוק",
    ]


def test_failed_send_never_counts_as_reported(owner):
    kw = dict(alert_key="cond2", cooldown_s=60, fingerprint="f")
    owner.result["value"] = {"ok": False, "error": "bridge unreachable"}
    assert owner.send("health", "t", "b", **kw)["ok"] is False
    assert app._alert_state_key("cond2") not in owner.r.keys            # nothing recorded as delivered
    owner.result["value"] = {"ok": True}
    assert owner.send("health", "t", "b", **kw)["ok"] is True            # so the retry is NOT "unchanged"
    assert _header(owner.calls[-1]).endswith("🆕 חדש · t")


def test_queued_for_the_morning_counts_as_reported(owner):
    owner.result["value"] = {"ok": True, "queued": True}
    kw = dict(alert_key="cond3", cooldown_s=60, fingerprint="f")
    owner.send("health", "t", "b", **kw)
    assert owner.send("health", "t", "b", **kw)["error"] == "unchanged"
    assert len(owner.calls) == 1


def test_resolved_is_sent_once_and_the_next_occurrence_is_new(owner):
    kw = dict(alert_key="cond4", cooldown_s=3600, fingerprint="f", remind_s=86400)
    not_open = asyncio.run(app.resolve_owner_alert("health", "חזר", "b", alert_key="cond4"))
    owner.send("health", "תקלה", "b", **kw)
    assert asyncio.run(app.owner_alert_active("cond4")) is True
    closed = asyncio.run(app.resolve_owner_alert("health", "חזר", "b", alert_key="cond4"))
    twice = asyncio.run(app.resolve_owner_alert("health", "חזר", "b", alert_key="cond4"))
    assert not_open == {"ok": False, "suppressed": True, "error": "not_active"}     # never invented
    assert closed["ok"] is True
    assert twice == {"ok": False, "suppressed": True, "error": "not_active"}
    assert asyncio.run(app.owner_alert_active("cond4")) is False
    owner.send("health", "תקלה", "b", **kw)
    assert [_header(c) for c in owner.calls] == [
        "ℹ️ [🏥 בריאות המערכת] 🆕 חדש · תקלה",
        "✅ [🏥 בריאות המערכת] ✅ נפתר · חזר",
        "ℹ️ [🏥 בריאות המערכת] 🆕 חדש · תקלה",
    ]


def test_resolve_does_not_send_when_the_state_store_is_unavailable(owner, monkeypatch):
    async def _down():
        raise ConnectionError("redis down")
    monkeypatch.setattr(app, "get_redis", _down)
    res = asyncio.run(app.resolve_owner_alert("health", "חזר", "b", alert_key="x"))
    assert res["ok"] is False and owner.calls == []


def test_redis_down_still_delivers_a_fingerprinted_alert(owner, monkeypatch):
    async def _down():
        raise ConnectionError("redis down")
    monkeypatch.setattr(app, "get_redis", _down)
    res = asyncio.run(app.notify_owner("health", "t", "b", alert_key="k", fingerprint="f"))
    assert res["ok"] is True and len(owner.calls) == 1                  # an alert is never lost to Redis


# ───────────────────────────── B. rendering ─────────────────────────────
def test_legacy_call_is_byte_identical(owner):
    owner.send("harvest", "שאיבת הקטלוג בטלה", "שורה 1", severity="warning", alert_key="k")
    assert owner.calls[0]["text"] == "🟡 [🛰️ קטלוג וקצירה] שאיבת הקטלוג בטלה\n\nשורה 1"
    assert app._alert_state_key("k") not in owner.r.keys               # no state kept for legacy callers


def test_error_severity_has_its_own_icon_and_is_not_critical(owner):
    owner.send("health", "db_update_agent — 1 משימות נכשלו", "b", severity="error", state="new")
    assert _header(owner.calls[0]) == "🟠 [🏥 בריאות המערכת] 🆕 חדש · db_update_agent — 1 משימות נכשלו"
    assert owner.calls[0]["critical"] is False                           # only `critical` bypasses quiet hours


def test_critical_stays_immediate_with_change_reporting(owner):
    owner.send("health", "DB down", "b", severity="critical", alert_key="db", fingerprint="down")
    assert owner.calls[0]["critical"] is True


def test_system_category_is_labelled(owner):
    owner.send("system", "Email Agent - חיבור Gmail דורש טיפול", "b", severity="warning")
    assert _header(owner.calls[0]) == "🟡 [🏥 בריאות המערכת] Email Agent - חיבור Gmail דורש טיפול"


# ───────────────────────────── C. watchdog kill burst ─────────────────────────────
def _run_watchdog(monkeypatch, events):
    import db_update_agent as dua
    notified = []

    async def fake_notify(category, title, body="", **kw):
        notified.append({"category": category, "title": title, "body": body, **kw})
        return {"ok": True}
    fake_app = types.ModuleType("BACKEND_API_ROUTES")
    fake_app.notify_owner = fake_notify
    wds = types.ModuleType("watchdog_state")
    wds.drain_unvalidated = lambda: events
    wds.stats = lambda: {}
    monkeypatch.setitem(sys.modules, "BACKEND_API_ROUTES", fake_app)
    monkeypatch.setitem(sys.modules, "watchdog_state", wds)
    return asyncio.run(dua.validate_watchdog_actions(None)), notified


def _evt(action, dur=3000, details="backend_start predates container start"):
    return types.SimpleNamespace(action=action, pid=1, dur_s=dur, details=details, validated=False)


def test_kill_burst_uses_notify_owner_with_the_stable_key(monkeypatch):
    events = [_evt("kill_orphan")] * 3 + [_evt("kill_zombie")] * 2 + [_evt("kill_stuck_importer", 700)]
    res, notified = _run_watchdog(monkeypatch, [types.SimpleNamespace(**vars(e)) for e in events])
    assert len(notified) == 1
    n = notified[0]
    assert n["alert_key"] == "watchdog_kill_burst" and n["severity"] == "warning" and n["category"] == "health"
    assert "cooldown_s" not in n and "critical" not in n          # notify_owner's standard routing and cooldown
    assert n["body"] == "• Unusual kill burst: 6 kills in one cycle (orphans=3 zombies=2 importers=1)"
    assert res["status"] == "warn" and res["anomalies"] == 1 and res["validated"] == 6


def test_five_kills_are_not_a_burst(monkeypatch):
    res, notified = _run_watchdog(monkeypatch, [_evt("kill_orphan") for _ in range(5)])
    assert notified == [] and res["status"] == "ok" and res["anomalies"] == 0


def test_burst_and_a_bad_kill_are_two_alerts_with_their_own_keys(monkeypatch):
    events = [_evt("kill_orphan") for _ in range(6)] + [_evt("kill_zombie", dur=10)]
    res, notified = _run_watchdog(monkeypatch, events)
    assert [n["alert_key"] for n in notified] == ["watchdog_kill_burst", "watchdog_anomaly"]
    assert notified[1]["cooldown_s"] == 1800 and "killed too early" in notified[1]["body"]
    assert "kill burst" not in notified[1]["body"]
    assert res["anomalies"] == 2


def test_watchdog_never_sends_whatsapp_itself():
    src = (BACKEND / "db_update_agent.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "validate_watchdog_actions")
    body = ast.unparse(fn)
    for banned in ("whatsapp_provider", "_wa_send", "send_message"):
        assert banned not in body
    assert "WATCHDOG_KILL_BURST_THRESHOLD" in body
    import db_update_agent as dua
    assert dua.WATCHDOG_KILL_BURST_THRESHOLD == 5


# ───────────────────────────── D. no bypass of the central path ─────────────────────────────
_SKIP_DIRS = {"tests", "devtests", "legacy", "state", "__pycache__", "node_modules", "alembic", "alembic_pii",
              "data", "uploads", "backups", "test_images"}
# Direct provider sends that are NOT owner alerts, each reviewed: (file, function).
_PROVIDER_SEND_ALLOWED = {
    ("BACKEND_API_ROUTES.py", "_wa_send_quiet"),                  # the gate itself
    ("BACKEND_API_ROUTES.py", "_flush_wa_quiet_queue"),           # morning delivery of the queue
    ("BACKEND_API_ROUTES.py", "_wa_send_now"),                    # the gate's immediate send + retry-queue on a proven not-sent
    ("BACKEND_API_ROUTES.py", "_flush_wa_retry_queue"),           # retry pass for those (critical at any hour, others in-window)
    ("BACKEND_API_ROUTES.py", "_abandoned_cart_loop"),            # customer reminder: own window + lifetime cap
    ("BACKEND_API_ROUTES.py", "_pending_payment_reminder_loop"),  # customer reminder: own window + cap
    ("BACKEND_AUTH_SECURITY.py", "send_whatsapp_2fa"),            # login code requested by the user
    ("routes/utils.py", "_send_post_payment_notification"),       # transactional, right after payment
    ("routes/admin.py", "_send_channel_message"),                 # an admin typing a reply in the console
    ("routes/webhooks.py", "_send"),                              # late chat answer to the same conversation
    ("routes/webhooks.py", "_owner_reply_bg"),                    # reply to the owner's own console message
    ("routes/webhooks.py", "_whatsapp_webhook_impl"),             # chat replies
}
# Calls to the quiet-hours gate / update router that are not notify_owner itself, each reviewed.
_GATE_CALL_ALLOWED = {
    ("BACKEND_API_ROUTES.py", "_wa_send_update"),          # the router
    ("BACKEND_API_ROUTES.py", "notify_owner"),             # the owner-alert API
    ("BACKEND_API_ROUTES.py", "_on_done"),                 # sync done-callback of a crashed task (critical)
    ("BACKEND_API_ROUTES.py", "_status_update_loop"),      # daily digest, once-per-day marker
    ("BACKEND_API_ROUTES.py", "_job_queue_report_loop"),   # night-pipeline report, own markers
    ("BACKEND_API_ROUTES.py", "_harvest_supervisor_loop"), # weekly digest, own marker
    ("BACKEND_API_ROUTES.py", "_health_monitor_loop"),     # opt-in admin copies (ADMIN_ALERT_PHONES)
    ("social/tools.py", "whatsapp_send_message"),          # campaign message to a recipient, quiet-hours gated
}


def _calls():
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if set(rel.parts[:-1]) & _SKIP_DIRS or rel.parts[0] in _SKIP_DIRS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        parents = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parents[ch] = node
        aliases = {}                                         # local name -> what it really is
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            q = node
            while q in parents and not isinstance(q, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = parents[q]
            yield str(rel), getattr(q, "name", "<module>"), name, aliases.get(name, ""), node


def test_every_direct_provider_send_is_on_the_reviewed_allowlist():
    found = set()
    for rel, fn, name, real, node in _calls():
        if rel == "social/whatsapp_provider.py":
            continue
        is_provider = real == "social.whatsapp_provider.send_message" or (
            name == "send_message" and isinstance(node.func, ast.Attribute)
            and "provider" in ast.unparse(node.func.value).lower())
        if is_provider:
            found.add((rel, fn))
    assert found - _PROVIDER_SEND_ALLOWED == set(), f"unreviewed direct WhatsApp sends: {found - _PROVIDER_SEND_ALLOWED}"
    assert ("BACKEND_API_ROUTES.py", "_flush_wa_quiet_queue") in found          # the scan really sees sends


def test_no_owner_alert_bypasses_notify_owner():
    found = set()
    for rel, fn, name, real, node in _calls():
        if name in ("_wa_send_quiet", "_wa_send_update"):
            found.add((rel, fn))
    assert found - _GATE_CALL_ALLOWED == set(), f"owner alert outside notify_owner: {found - _GATE_CALL_ALLOWED}"
    # the three bypasses removed in this change stay removed
    for gone in (("social/facebook_browser/session.py", "_notify_owner_reauth_required"),
                 ("social/facebook_browser/group_scanner.py", "_wa_notify")):
        assert gone not in found


def test_notify_owner_severities_and_categories_are_known():
    bad = []
    for rel, fn, name, real, node in _calls():
        if name not in ("notify_owner", "_notify", "notify", "_alert_owner", "_notify_res"):
            continue
        for kw in node.keywords:
            if kw.arg == "severity" and isinstance(kw.value, ast.Constant) and kw.value.value not in app._NOTIFY_SEVERITY_ICON:
                bad.append((rel, node.lineno, kw.value.value))
            if kw.arg == "state" and isinstance(kw.value, ast.Constant) and kw.value.value not in app._NOTIFY_STATE_LABEL:
                bad.append((rel, node.lineno, kw.value.value))
        if name in ("notify_owner", "notify") and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str) and node.args[0].value not in app._NOTIFY_CATEGORIES:
            bad.append((rel, node.lineno, node.args[0].value))
    assert bad == []


def test_group_scan_does_not_repeat_the_session_alert():
    src = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    i = src.index('logger.error("[group_scan] Facebook session not authenticated')
    block = src[i:i + 1600]
    assert 'owner_alert_active("fb_session_expired")' in block
    assert 'alert_key="group_scan_auth_failed"' in block and 'fingerprint="auth"' in block


# ───────────────────────────── E. content ─────────────────────────────
def test_email_digest_reports_only_what_changed():
    from email_agent import owner_flow as of
    old = [{"id": f"old-{i}", "classification": "order_shipping", "sender_email": "a@hotmail.com",
            "requires_human": True} for i in range(29)]
    new_open = {"id": "n1", "classification": "supplier", "sender_email": "x@asapnetwork.org", "requires_human": True}
    new_quiet = {"id": "n2", "classification": "newsletter_marketing", "sender_email": "n@news.example", "subject": "Sale"}
    assert of.format_digest([], old) is None                          # nothing received -> no digest at all
    assert of.format_digest([], []) is None
    title, body = of.format_digest([new_open, new_quiet], [new_open] + old)
    assert title == "סיכום מיילים יומי"
    assert "התקבלו מאז הסיכום הקודם: 2" in body
    assert "🆕 חדשים שממתינים לך: 1" in body and "asapnetwork.org" in body
    assert "ממתינים מקודם (ללא שינוי): 29" in body and 'סה"כ ממתינים: 30' in body
    assert "old-" not in body and body.count("hotmail.com") == 0      # the unchanged backlog is not re-listed


def test_fb_session_alert_goes_through_notify_owner_with_a_stable_key(monkeypatch):
    pytest.importorskip("playwright")
    from social.facebook_browser import session as fbs
    seen = []

    async def fake_notify(category, title, body="", **kw):
        seen.append({"category": category, "title": title, **kw})
        return {"ok": True}

    async def fake_resolve(category, title, body="", **kw):
        seen.append({"resolved": True, "category": category, **kw})
        return {"ok": True}
    monkeypatch.setattr(app, "notify_owner", fake_notify)
    monkeypatch.setattr(app, "resolve_owner_alert", fake_resolve)
    obj = types.SimpleNamespace()
    asyncio.run(fbs.FacebookSession._notify_owner_reauth_required(obj, "account_picker: rejected"))
    asyncio.run(fbs.FacebookSession._notify_owner_session_restored(obj))
    assert seen[0]["alert_key"] == "fb_session_expired" and seen[0]["state"] == "action"
    assert seen[0]["fingerprint"] == "account_picker: rejected" and seen[0]["remind_s"] == 3 * 86400
    assert seen[0]["category"] == "social" and seen[0]["severity"] == "warning"
    assert seen[1] == {"resolved": True, "category": "social", "alert_key": "fb_session_expired"}
    src = (BACKEND / "social/facebook_browser/session.py").read_text(encoding="utf-8")
    assert "reauth_alert_sent_at" not in src and "_wa_send_quiet" not in src


# ───────────────────────────── F. queue, real isolated Redis ─────────────────────────────
_URL = os.getenv("REDIS_URL", "")
_SAFE = (os.getenv("ENVIRONMENT", "").lower() in ("sandbox", "test")
         and "sandbox_redis" in _URL and "autospare_redis" not in _URL)
needs_redis = pytest.mark.skipif(not _SAFE, reason="needs the isolated sandbox Redis (never production)")
BANNER = "🌙 [נשלח מאוחר — נשמר משעות הלילה "


@pytest.fixture
def q(monkeypatch):
    import redis.asyncio as aioredis
    tag = uuid.uuid4().hex[:10]
    monkeypatch.setattr(app, "_WA_QUIET_QUEUE_KEY", f"test:{tag}:wa_quiet_queue")
    monkeypatch.setattr(app, "_WA_QUIET_DEAD_KEY", f"test:{tag}:wa_quiet_dead")
    e = types.SimpleNamespace(sent=[], window={"open": False}, outcomes=[], hook=None, app=app)

    async def fake_send(to, text, reply_jid=""):
        e.sent.append({"to": to, "text": text})
        if e.hook:
            await e.hook(text)
        out = e.outcomes.pop(0) if e.outcomes else {"ok": True}
        if isinstance(out, Exception):
            raise out
        return out

    async def fake_notify(*a, **kw):
        return {"ok": True}
    monkeypatch.setattr(app, "_wa_send", fake_send)
    monkeypatch.setattr(app, "notify_owner", fake_notify)
    monkeypatch.setattr(app, "_notify_window_open", lambda now_local=None: (e.window["open"], datetime.now()))

    def run(coro_fn):
        async def _main():
            r = aioredis.from_url(_URL, decode_responses=True)

            async def _get():
                return r
            monkeypatch.setattr(app, "get_redis", _get)
            e.r = r
            try:
                return await coro_fn()
            finally:
                await r.delete(app._WA_QUIET_QUEUE_KEY, app._WA_QUIET_DEAD_KEY)
                await r.aclose()
        return asyncio.run(_main())
    e.run = run
    return e


async def _items(e):
    return [json.loads(x) for x in await e.r.lrange(e.app._WA_QUIET_QUEUE_KEY, 0, -1)]


def _texts(e):
    return [s["text"].split("\n", 1)[1] if s["text"].startswith(BANNER) else s["text"] for s in e.sent]


@needs_redis
def test_concurrent_enqueue_same_key_leaves_exactly_one(q):
    async def go():
        await asyncio.gather(*[
            q.app._wa_send_quiet("+972500000001", f"stall report {i}", alert_key="harvest_stall") for i in range(60)])
        return await _items(q)
    items = q.run(go)
    assert len(items) == 1 and items[0]["alert_key"] == "harvest_stall"
    assert q.sent == []                                                  # night: nothing was sent


@needs_redis
def test_concurrent_enqueue_distinct_and_keyless_loses_nothing(q):
    async def go():
        await q.r.rpush(q.app._WA_QUIET_QUEUE_KEY, json.dumps({"to": "+972500000001", "text": "legacy"}))
        await asyncio.gather(
            *[q.app._wa_send_quiet("+972500000001", f"k{i}", alert_key=f"key{i % 10}") for i in range(30)],
            *[q.app._wa_send_quiet("+972500000001", f"plain{i}") for i in range(15)])
        return await _items(q)
    items = q.run(go)
    keyed = [i for i in items if i.get("alert_key")]
    plain = [i for i in items if not i.get("alert_key")]
    assert sorted({i["alert_key"] for i in keyed}) == sorted(f"key{n}" for n in range(10)) and len(keyed) == 10
    assert sorted(i["text"] for i in plain) == sorted(["legacy"] + [f"plain{i}" for i in range(15)])


@needs_redis
@pytest.mark.parametrize("failure", [ConnectionError("bridge unreachable"), {"ok": False, "error": "bridge 503"},
                                     {"ok": False}])
def test_failed_delivery_is_kept_and_delivered_once_on_retry(q, failure):
    q.window["open"] = True

    async def go():
        k = q.app._WA_QUIET_QUEUE_KEY
        for t in ("first", "second"):
            await q.r.rpush(k, json.dumps({"to": "+972500000001", "text": t, "queued_at": "x", "alert_key": t}))
        q.outcomes.append(failure)
        n1 = await q.app._flush_wa_quiet_queue()
        mid = await _items(q)
        n2 = await q.app._flush_wa_quiet_queue()
        n3 = await q.app._flush_wa_quiet_queue()
        return n1, mid, n2, n3, await _items(q)
    n1, mid, n2, n3, end = q.run(go)
    assert n1 == 0 and [i["text"] for i in mid] == ["first", "second"] and mid[0]["attempts"] == 1
    assert mid[0]["alert_key"] == "first"                                # alert-key semantics survive the retry
    assert n2 == 2 and n3 == 0 and end == []
    assert _texts(q) == ["first", "first", "second"]                    # one failed attempt, then each exactly once


@needs_redis
def test_head_removed_by_an_enqueue_while_sending_never_costs_another_message(q):
    """A night enqueue lands while the flush is sending the head: same alert_key (LREM of the head)
    or overflow (LTRIM). The old flush then LPOPped whatever was first - an undelivered message."""
    q.window["open"] = True

    async def go():
        k = q.app._WA_QUIET_QUEUE_KEY
        await q.r.rpush(k, json.dumps({"to": "+972500000001", "text": "stall v1", "queued_at": "x", "alert_key": "stall"}))
        await q.r.rpush(k, json.dumps({"to": "+972500000001", "text": "other", "queued_at": "x", "alert_key": "other"}))

        async def hook(text):
            if "stall v1" in text:                       # mid-send: the window closes and a newer report arrives
                q.window["open"] = False
                await q.app._wa_send_quiet("+972500000001", "stall v2", alert_key="stall")
                q.window["open"] = True
        q.hook = hook
        n = await q.app._flush_wa_quiet_queue()
        return n, await _items(q)
    n, end = q.run(go)
    assert end == [] and n == 3
    assert _texts(q) == ["stall v1", "other", "stall v2"]               # "other" was not dropped


@needs_redis
def test_failed_head_that_was_superseded_is_not_overwritten(q):
    q.window["open"] = True

    async def go():
        k = q.app._WA_QUIET_QUEUE_KEY
        await q.r.rpush(k, json.dumps({"to": "+972500000001", "text": "stall v1", "queued_at": "x", "alert_key": "stall"}))
        await q.r.rpush(k, json.dumps({"to": "+972500000001", "text": "other", "queued_at": "x", "alert_key": "other"}))

        async def hook(text):
            if "stall v1" in text:
                q.window["open"] = False
                await q.app._wa_send_quiet("+972500000001", "stall v2", alert_key="stall")
                q.window["open"] = True
        q.hook = hook
        q.outcomes.append({"ok": False, "error": "bridge 503"})          # the head's send fails
        await q.app._flush_wa_quiet_queue()
        return await _items(q)
    end = q.run(go)
    assert [i["text"] for i in end] == ["other", "stall v2"]             # LSET 0 used to overwrite "other"
    assert "attempts" not in end[0]


@needs_redis
def test_legacy_entries_without_key_or_attempts_are_delivered_in_order(q):
    q.window["open"] = True

    async def go():
        k = q.app._WA_QUIET_QUEUE_KEY
        await q.r.rpush(k, json.dumps({"to": "+972500000001", "text": "legacy 1"}))
        await q.r.rpush(k, json.dumps({"to": "+972500000001", "text": "legacy 1"}))      # identical payload twice
        await q.r.rpush(k, json.dumps({"to": "+972500000001", "text": "legacy 2", "queued_at": "01/11 03:00"}))
        return await q.app._flush_wa_quiet_queue(), await _items(q)
    n, end = q.run(go)
    assert n == 3 and end == [] and _texts(q) == ["legacy 1", "legacy 1", "legacy 2"]


@needs_redis
def test_successful_delivery_is_not_repeated(q):
    q.window["open"] = True

    async def go():
        await q.r.rpush(q.app._WA_QUIET_QUEUE_KEY, json.dumps({"to": "+972500000001", "text": "once", "alert_key": "a"}))
        return [await q.app._flush_wa_quiet_queue() for _ in range(3)]
    assert q.run(go) == [1, 0, 0] and _texts(q) == ["once"]
