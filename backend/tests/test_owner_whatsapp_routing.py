"""Owner WhatsApp delivery + alert recipients (FIXES_TRACKER #69, 2026-10-18).

1. The owner's number is sent to his LID: phone-number-session sends drew a decrypt retry
   on every message ("בהמתנה להודעה זו"); a LID send did not, and displayed correctly.
2. Admin WhatsApp copies of system alerts go only to explicitly listed numbers, never the
   owner (duplicate) and never a seeded test admin (+972501112233 received 103 alerts).
3. Repeating owner alerts are notify-by-exception and survive restarts.
"""
import ast
import os
import re
import sys
from types import SimpleNamespace

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
from social import whatsapp_provider as wp  # noqa: E402

ROUTES = os.path.join(BACKEND, "BACKEND_API_ROUTES.py")
LID = "98058566160397@lid"


@pytest.fixture
def owner_env(monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "+972586050155")
    monkeypatch.setenv("OWNER_WHATSAPP_LID", LID)
    monkeypatch.delenv("OWNER_WHATSAPP_ROUTE_VIA_LID", raising=False)


@pytest.mark.parametrize("to", ["+972586050155", "972586050155", "0586050155",
                                "whatsapp:+972586050155", "972586050155@s.whatsapp.net"])
def test_every_form_of_the_owner_number_is_sent_to_his_lid(owner_env, to):
    assert wp._normalize_bridge_phone(to) == LID


@pytest.mark.parametrize("to,expected", [("+972541234567", "0541234567"),
                                         ("120363123@g.us", "120363123@g.us"),
                                         ("12345@lid", "12345@lid")])
def test_customers_groups_and_other_lids_are_untouched(owner_env, to, expected):
    assert wp._normalize_bridge_phone(to) == expected


def test_owner_phone_reply_jid_is_routed_but_customer_reply_jid_is_not(owner_env):
    assert wp._route_reply_jid("972586050155@s.whatsapp.net") == LID
    assert wp._route_reply_jid("972541234567@s.whatsapp.net") == "972541234567@s.whatsapp.net"
    assert wp._route_reply_jid("555@lid") == "555@lid"


def test_rollback_switch_and_missing_lid_restore_phone_addressing(owner_env, monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_ROUTE_VIA_LID", "0")
    assert wp._normalize_bridge_phone("+972586050155") == "0586050155"
    monkeypatch.delenv("OWNER_WHATSAPP_ROUTE_VIA_LID")
    monkeypatch.setenv("OWNER_WHATSAPP_LID", "")
    assert wp._normalize_bridge_phone("+972586050155") == "0586050155"


def test_bare_lid_digits_are_completed_and_never_turned_into_a_phone_jid(owner_env, monkeypatch):
    monkeypatch.setenv("OWNER_WHATSAPP_LID", "98058566160397")
    assert wp._normalize_bridge_phone("+972586050155") == LID
    assert "98058566160397@s.whatsapp.net" not in open(wp.__file__, encoding="utf-8").read()


def _load_admin_predicate():
    tree = ast.parse(open(ROUTES, encoding="utf-8").read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_admin_gets_wa")
    ns = {"os": os, "WHATSAPP_ANON_USER_ID": "00000000-0000-0000-0000-000000000001"}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), ROUTES, "exec"), ns)
    return ns["_admin_gets_wa"]


def _admin(phone, uid="u1"):
    return SimpleNamespace(id=uid, phone=phone)


def test_admin_copies_are_opt_in_and_never_the_owner_or_a_test_row(monkeypatch):
    gets = _load_admin_predicate()
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "+972586050155")
    monkeypatch.delenv("ADMIN_ALERT_PHONES", raising=False)
    assert not gets(_admin("+972501112233"))          # seeded "Admin Test User"
    assert not gets(_admin("0586050155"))             # the owner, stored in local format
    monkeypatch.setenv("ADMIN_ALERT_PHONES", "0541234567, +972586050155")
    assert gets(_admin("+972541234567"))
    assert not gets(_admin("0586050155"))             # owner stays excluded even if listed
    assert not gets(_admin("+972501112233"))
    assert not gets(_admin(None))
    assert not gets(_admin("0541234567", uid="00000000-0000-0000-0000-000000000001"))


def test_no_admin_whatsapp_send_bypasses_the_predicate():
    src = open(ROUTES, encoding="utf-8").read()
    assert "admin.phone and str(admin.id) != str(WHATSAPP_ANON_USER_ID)" not in src
    sites = [m.start() for m in re.finditer(r"_wa_send_quiet\(to=admin\.phone", src)]
    assert len(sites) == 4
    for i in sites:
        assert "_admin_gets_wa(admin)" in src[i - 900:i], src[i - 300:i]
    assert "_OWNER_PHONE not in admin_phones" not in src


def test_job_queue_announcements_are_persisted_not_process_memory():
    src = open(ROUTES, encoding="utf-8").read()
    assert 'autospare:job_queue:announced:{ident}' in src and "nx=True" in src
    assert 'f"done:{_run_end}"' in src
    assert "failed:{s['step_key']}:{s.get('finished_at')}" in src


def test_repeating_alerts_are_notify_by_exception():
    src = open(ROUTES, encoding="utf-8").read()
    assert "אין פעולה נדרשת — המחזור הבא ינסה נושא אחר" not in src
    assert 'alert_key="noa_coherence_gate_streak"' in src and "_streak >= 3" in src
    ws = src[src.index('alert_key="worker_silence"') - 200:src.index('alert_key="worker_silence"') + 120]
    assert "cooldown_s=21600" in ws
    assert 'alert_key="worker_silence_recovered"' in src
    assert "_np_mod.running_job()" in src


# ── send survives a bridge reconnect, without ever risking a duplicate ─────────
import asyncio  # noqa: E402

import httpx  # noqa: E402


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._d = status, data

    def json(self):
        return self._d


def _fake_bridge(monkeypatch, script):
    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            calls.append(json)
            step = script[min(len(calls) - 1, len(script) - 1)]
            if isinstance(step, Exception):
                raise step
            return _Resp(*step)

    monkeypatch.setattr(wp.httpx, "AsyncClient", _Client)

    async def _no_sleep(_s):
        return None
    monkeypatch.setattr(wp.asyncio, "sleep", _no_sleep)
    return calls


def _send(text="hi"):
    return asyncio.run(wp.send_message("+972541234567", text))


@pytest.mark.parametrize("err", list(wp._TRANSIENT_BRIDGE_ERRORS))
def test_reconnecting_bridge_is_retried_until_it_sends(monkeypatch, err):
    calls = _fake_bridge(monkeypatch, [(503, {"ok": False, "error": err}),
                                       (503, {"ok": False, "error": err}),
                                       (200, {"ok": True, "key": {"id": "X"}})])
    r = _send()
    assert r["ok"] and r["key"] == {"id": "X"} and len(calls) == 3


def test_unreachable_bridge_is_retried(monkeypatch):
    calls = _fake_bridge(monkeypatch, [httpx.ConnectError("refused"), (200, {"ok": True})])
    assert _send()["ok"] and len(calls) == 2


@pytest.mark.parametrize("step", [
    (500, {"ok": False, "error": "Connection Closed"}),       # may already be out
    (503, {"ok": False, "error": "WhatsApp session invalid (logged out by WhatsApp) — re-pairing required"}),
    (503, {"ok": False, "error": "WhatsApp not authenticated — awaiting QR scan"}),
    httpx.ReadTimeout("slow"),                                  # may already be out
])
def test_possible_send_or_permanent_state_is_never_retried(monkeypatch, step):
    calls = _fake_bridge(monkeypatch, [step, (200, {"ok": True})])
    assert not _send()["ok"] and len(calls) == 1


def test_retry_gives_up_inside_its_budget(monkeypatch):
    monkeypatch.setattr(wp, "SEND_RETRY_BUDGET_S", 3.5)
    t = {"now": 0.0}

    class _Loop:
        def time(self):
            return t["now"]
    monkeypatch.setattr(wp.asyncio, "get_running_loop", lambda: _Loop())
    calls = _fake_bridge(monkeypatch, [(503, {"ok": False, "error": wp._TRANSIENT_BRIDGE_ERRORS[1]})])

    async def _sleep(s):
        t["now"] += s
    monkeypatch.setattr(wp.asyncio, "sleep", _sleep)
    r = asyncio.run(wp.send_message("+972541234567", "hi"))
    assert not r["ok"] and len(calls) == 3                      # t=0, 1, 3 → next would pass 3.5


def test_empty_body_never_reaches_the_bridge(monkeypatch):
    calls = _fake_bridge(monkeypatch, [(200, {"ok": True})])
    assert asyncio.run(wp.send_message("+972541234567", "  "))["error"].startswith("INVALID_PAYLOAD")
    assert calls == []


# ── NOA notification content (FIXES_TRACKER #69 §13) ────────────────────────────
def test_weekly_campaign_is_never_sent_or_stored_empty():
    src = open(ROUTES, encoding="utf-8").read()
    i = src.index("def _plan_usable(")
    block = src[i:i + 4000]
    assert 'core_message' in block and 'content_angle' in block
    assert "for _plan_attempt in range(2):" in block                 # one retry
    assert "if not _plan_usable(plan):" in block and "לא נבנתה תוכנית קמפיין" in block
    guard = "if weekday == 0 and now.hour <= _post_hours[0] and _plan_usable(plan):"
    assert guard in src
    g = src.index(guard)
    assert src.index('await mem.set("current_week_plan"', g) > g          # stored only when usable
    assert src.index('_brief_title = f"NOA — קמפיין שבוע', g) > g         # sent only when usable
    assert src.count('await mem.set("current_week_plan"') == 1


def test_daily_facebook_report_says_whether_the_group_scan_ran():
    src = open(ROUTES, encoding="utf-8").read()
    i = src.index('_gs_marker.status("group_scan")')
    block = src[i:i + 1800]
    for phrase in ("still running", "INTERRUPTED", "did NOT run in the last 24 h", "no run recorded"):
        assert phrase in block, phrase
