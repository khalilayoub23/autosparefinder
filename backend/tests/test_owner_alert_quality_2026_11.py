"""Owner WhatsApp quality fixes from the 2026-11-24 audit (FIXES_TRACKER #152). Nothing reaches WhatsApp: the send is a
recorder. Fixtures are redacted real messages from the bridge cache (10-23 Nov)."""
import ast
import asyncio
import os
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

import BACKEND_API_ROUTES as app  # noqa: E402
import notification_inventory  # noqa: E402
from owner_messages_2026_11 import ASAP_NEWSLETTER, DB_AGENT_BODY, DISK_SAMPLES, FLARE_TEXTS, NOA_DAILY_ENGLISH  # noqa: E402


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
    calls, r, clock = [], FakeRedis(), {"t": 1_800_000_000.0}

    async def fake_update(text, critical=False, alert_key="", meta=None):
        calls.append({"text": text, "critical": critical, "alert_key": alert_key, "meta": meta})
        return {"ok": True}

    async def _get():
        return r
    monkeypatch.setattr(app, "_wa_send_update", fake_update)
    monkeypatch.setattr(app, "get_redis", _get)
    monkeypatch.setattr(app.time, "time", lambda: clock["t"])

    def send(*a, **kw):
        r.keys = {k: v for k, v in r.keys.items() if not k.startswith("autospare:alert_cooldown:")}
        return asyncio.run(app.notify_owner(*a, **kw))
    return types.SimpleNamespace(calls=calls, r=r, clock=clock, send=send)


# ── B. repeated alert with a changing counter = ONE incident (the FlareSolverr x20) ─────────────────────────
def test_changing_counter_is_not_a_new_event(owner):
    for _ in range(12):                                   # hourly for 12 h, counter 10..34 not in the fingerprint
        owner.send("harvest", "שאיבת car-parts.ie תקועה", "אישור Cloudflare נכשל", severity="warning",
                   alert_key="harvester_fs_consecutive_fails", state="new", fingerprint="open", remind_s=6 * 3600)
        owner.clock["t"] += 3600
    labels = [c["text"].split("\n", 1)[0] for c in owner.calls]
    assert len(owner.calls) == 2                          # first report + ONE 6 h reminder, not 12 messages
    assert "🆕 חדש" in labels[0] and "⏰ תזכורת" in labels[1]


def test_incident_lifecycle_new_changed_resolved(owner):
    owner.send("harvest", "תקועה", "x", severity="warning", alert_key="k", state="new", fingerprint="open", remind_s=6 * 3600)
    owner.clock["t"] += 7 * 3600
    owner.send("harvest", "תקועה כבר 7 שעות", "x", severity="critical", alert_key="k", state="new", fingerprint="long", remind_s=6 * 3600)
    asyncio.run(app.resolve_owner_alert("harvest", "חזרה", "ok", alert_key="k"))
    heads = [c["text"].split("\n", 1)[0] for c in owner.calls]
    assert ["🆕 חדש" in heads[0], "🔄 שינוי" in heads[1], "✅ נפתר" in heads[2]] == [True] * 3
    assert owner.calls[1]["critical"] is True and owner.calls[0]["critical"] is False   # escalation keeps critical visibility


def test_no_owner_alert_tells_the_owner_to_run_a_root_command():
    """Audit: alerts told the owner to run fallocate/mkswap/docker exec as root. Scan every owner-facing source."""
    for rel in ("BACKEND_API_ROUTES.py", "db_update_agent.py", "db_cleanup_agent.py", "social/facebook_browser/session.py",
                "social/facebook_browser/group_scanner.py", "social/noa_ops.py", "email_agent/owner_flow.py"):
        src = (BACKEND / rel).read_text(encoding="utf-8")
        for banned in ("OOM?", "fallocate", "mkswap", "swapon", 'פעולה: docker exec', 'לחידוש: ```docker'):
            assert banned not in src, (rel, banned)
    routes = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    assert 'fingerprint="long" if _long else "open"' in routes and "autospare:fs_fail_since" in routes


# ── C. sanitised database error ─────────────────────────────────────────────────────────────────────────────
def test_notify_owner_never_sends_a_traceback(owner):
    owner.send("health", "עבודת הלילה: 3 משימות נכשלו", DB_AGENT_BODY, severity="error", alert_key="dbfail")
    text = owner.calls[0]["text"]
    for banned in ("sqlalche", "Background on this error", "RETURNING", "[SQL", "http", "fff4e30a"):
        assert banned not in text
    assert "אסמכתה" in text and "dedup_catalog_parts" in text and "flag_fake_skus" in text


def test_shell_command_in_a_body_is_dropped(owner):
    owner.send("harvest", "x", "הקטלוג לא מתקדם.\nfallocate -l 4G /swapfile && swapon /swapfile", alert_key="s")
    assert "fallocate" not in owner.calls[0]["text"] and "הקטלוג לא מתקדם." in owner.calls[0]["text"]


# ── A. one underlying event = one owner message ─────────────────────────────────────────────────────────────
def test_email_then_boaz_for_the_same_mail_is_one_message(owner):
    row = ASAP_NEWSLETTER[0]
    from email_agent import owner_flow
    keys = owner_flow.event_keys(row)
    owner.send("email", "מייל חדש — ספק", "מאת: amanda", alert_key="email_1", event_keys=keys)
    r = owner.send("supplier", "BOAZ צריך החלטה שלך", "x", alert_key="boaz_task_9", event_keys=[f"mail:{owner_flow.ref(row)}"])
    assert len(owner.calls) == 1 and r["error"] == "same_event"


def test_ten_identical_supplier_newsletters_are_one_alert(owner):
    from email_agent import owner_flow
    for row in ASAP_NEWSLETTER:
        owner.send("email", "מייל חדש — ספק", "מאת: amanda", alert_key=f"email_{row['id']}", event_keys=owner_flow.event_keys(row))
    assert len(owner.calls) == 1


def test_a_different_subject_from_the_same_supplier_is_new_information(owner):
    from email_agent import owner_flow
    a, b = dict(ASAP_NEWSLETTER[0]), dict(ASAP_NEWSLETTER[1], subject="Your account application was approved")
    owner.send("email", "t", "b", alert_key="e1", event_keys=owner_flow.event_keys(a))
    owner.send("email", "t", "b", alert_key="e2", event_keys=owner_flow.event_keys(b))
    assert len(owner.calls) == 2


def test_boaz_reasons_are_hebrew_and_actionable():
    import boaz_tasks
    h = boaz_tasks._he_boaz_reason("no automated BOAZ handler for task type 'shipping' - owner / manual handling")
    assert "shipping" in h and "נדרש טיפול ידני" in h and "no automated" not in h
    h2 = boaz_tasks._he_boaz_reason("linked to supplier 8237c52f. Any reply, offer or commitment is the owner's decision: 'מייל 59fc5220'.")
    assert "מייל 59fc5220" in h2 and "Any reply" not in h2


def test_boaz_alert_for_a_non_email_task_is_not_merged(monkeypatch):
    import boaz_tasks
    seen = {}

    async def notify(*a, **kw):
        seen.update(kw)
    asyncio.run(boaz_tasks._alert(notify, {"id": "abcdef12", "title": "t", "artifacts": {"kind": "shipping"}}, "x"))
    assert seen["event_keys"] is None
    asyncio.run(boaz_tasks._alert(notify, {"id": "abcdef12", "title": "t", "artifacts": {"source": "email_agent", "email_ref": "59fc5220"}}, "x"))
    assert seen["event_keys"] == ["mail:59fc5220"]


# ── disk: one alert per storage, wired in the health loop ───────────────────────────────────────────────────
def test_disk_two_mounts_one_stream_in_the_health_loop_source():
    src = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    assert "disk_same_storage(_probe, _k)" in src and "_seen_dev" not in src


# ── D. Hebrew + NOA report ──────────────────────────────────────────────────────────────────────────────────
def test_noa_daily_report_is_hebrew_with_a_decision_request():
    import owner_message_format as f
    out = f.format_noa_daily(groups_identified=20, groups_approved=0, groups_published=0, groups_pending_24h=20,
                             groups_pending_total=582, page_identified=0, page_published=0, page_posts=0,
                             published_all=0, pending_posts=1, approved_unpublished=0,
                             notable=["סריקת הקבוצות לא רצה ב-24 השעות האחרונות"])
    assert "Relevant posts" not in out and "GROUPS" not in out
    assert "582 טיוטות" in out and "פוסט אחד ממתינים" in out and "*תגובות*" in out
    assert NOA_DAILY_ENGLISH.split("\n")[0] not in out


def test_no_hebrew_task_count_defect_remains_in_owner_alert_sources():
    for rel in ("db_update_agent.py", "email_agent/owner_flow.py"):
        src = (BACKEND / rel).read_text(encoding="utf-8")
        assert "{err_count} משימות נכשלו" not in src
    src = (BACKEND / "email_agent/owner_flow.py").read_text(encoding="utf-8")
    assert "he_count" in src


# ── H. inventory of notification sources ────────────────────────────────────────────────────────────────────
REGISTERED = {   # file -> {call name: count}; every owner-notification call site in production code
    "BACKEND_AI_AGENTS.py": {"_notify": 2},
    "BACKEND_API_ROUTES.py": {"_alert_owner": 11, "_wa_send_update": 5, "notify_owner": 29, "resolve_owner_alert": 5},  # +1 #157: single engagement draft alert
    "boaz_tasks.py": {"notify": 1},
    "db_cleanup_agent.py": {"notify_owner": 2},
    "db_update_agent.py": {"_notify": 4, "_notify_res": 1},
    "ebay_api_watch.py": {"notify_owner": 1},
    "email_agent/loop.py": {"notify": 1},
    "email_agent/owner_flow.py": {"notify": 3, "resolve_owner_alert": 1},
    "procurement.py": {"notify": 1},
    "social/facebook_browser/group_scanner.py": {"notify_owner": 1},
    "social/facebook_browser/session.py": {"notify_owner": 1, "resolve_owner_alert": 1},
    "social/noa_ops.py": {"_notify": 3, "notify_owner": 1},
}


def test_notification_source_inventory_is_complete():
    """A new owner-notification call site (or a new file) fails here until it is added to REGISTERED AND to
    docs/WHATSAPP_NOTIFICATIONS.md §9 - an unregistered path cannot appear silently."""
    assert notification_inventory.scan() == REGISTERED
    assert sum(sum(v.values()) for v in REGISTERED.values()) == 74   # +1 #157 (single engagement-draft decision alert)


def test_inventory_is_documented():
    docp = BACKEND.parent / "docs" / "WHATSAPP_NOTIFICATIONS.md"
    if not docp.exists():
        pytest.skip("docs/ is not mounted in this environment (run with the repo root mounted)")
    doc = docp.read_text(encoding="utf-8")
    for rel in REGISTERED:
        assert rel in doc, f"{rel} missing from docs/WHATSAPP_NOTIFICATIONS.md"
