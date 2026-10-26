"""Owner WhatsApp notification contracts (FIXES_TRACKER #70, docs/WHATSAPP_NOTIFICATIONS.md).

- scheduled digests fire in the LOCAL (Asia/Jerusalem) hour on both sides of the 2026-10-25
  clock change, and a persisted marker stops a restart from re-sending them;
- notify_owner renders "{severity icon} [{category label}] {title}" + blank line + body, sends
  critical alerts at once, and sets its cooldown only after a confirmed send.
Expected strings are written out here, not produced by the code under test. Redis-backed cases
use the isolated sandbox Redis only.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import BACKEND_API_ROUTES as app  # noqa: E402

IL = ZoneInfo("Asia/Jerusalem")
_URL = os.getenv("REDIS_URL", "")
_SAFE_REDIS = (os.getenv("ENVIRONMENT", "").lower() in ("sandbox", "test")
               and "sandbox_redis" in _URL and "autospare_redis" not in _URL)


def _il(utc_iso):
    return datetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc).astimezone(IL)


# ── digest schedule ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("utc,expected", [
    ("2026-10-19T06:00:00", True),    # summer (UTC+3): 09:00 IL
    ("2026-10-19T05:00:00", False),   # 08:00 IL
    ("2026-10-26T06:00:00", False),   # winter (UTC+2): 08:00 IL — the old (utc+3) rule said 09:00
    ("2026-10-26T07:00:00", True),    # winter: 09:00 IL
])
def test_daily_digest_is_due_at_nine_israel_time_in_summer_and_winter(utc, expected):
    assert app._digest_due(_il(utc), 9) is expected


def test_weekly_digest_needs_sunday_local():
    assert _il("2026-10-25T07:00:00").strftime("%A %H") == "Sunday 09"   # first winter Sunday
    assert app._digest_due(_il("2026-10-25T07:00:00"), 9, weekday=6) is True
    assert app._digest_due(_il("2026-10-25T06:00:00"), 9, weekday=6) is False  # 08:00 IL
    assert app._digest_due(_il("2026-10-26T07:00:00"), 9, weekday=6) is False  # Monday


def test_digest_loops_use_local_time_not_a_fixed_offset():
    import inspect
    for fn in (app._status_update_loop, app._harvest_supervisor_loop):
        src = inspect.getsource(fn)
        assert "+ 3) % 24" not in src and "timedelta(hours=3)" not in src
        assert "datetime.now(APP_LOCAL_TZ)" in src and "_digest_already_sent(" in src


@pytest.mark.skipif(not _SAFE_REDIS, reason="needs the isolated sandbox Redis (never production)")
def test_digest_marker_survives_a_restart(monkeypatch):
    import redis.asyncio as aioredis
    kind = f"test_{uuid.uuid4().hex[:8]}"

    async def go():
        r = aioredis.from_url(_URL, decode_responses=True)

        async def _get():
            return r
        monkeypatch.setattr(app, "get_redis", _get)
        try:
            before = await app._digest_already_sent(kind, "2026-10-26")
            await app._mark_digest_sent(kind, "2026-10-26")
            after = await app._digest_already_sent(kind, "2026-10-26")      # what a restarted loop sees
            other_day = await app._digest_already_sent(kind, "2026-10-27")
            ttl = await r.ttl(app._digest_marker_key(kind, "2026-10-26"))
            return before, after, other_day, ttl
        finally:
            await r.delete(app._digest_marker_key(kind, "2026-10-26"))
            await r.aclose()
    before, after, other_day, ttl = asyncio.run(go())
    assert (before, after, other_day) == (False, True, False) and 0 < ttl <= 8 * 86400


# ── notify_owner contract ────────────────────────────────────────────────────────────────────
@pytest.fixture
def capture(monkeypatch):
    calls = []
    result = {"value": {"ok": True}}

    async def fake_update(text, critical=False, alert_key=""):
        calls.append({"text": text, "critical": critical, "alert_key": alert_key})
        return result["value"]

    class _R:
        def __init__(self):
            self.keys = {}

        async def exists(self, k):
            return k in self.keys

        async def set(self, k, v, ex=None):
            self.keys[k] = (v, ex)
    r = _R()

    async def _get():
        return r
    monkeypatch.setattr(app, "_wa_send_update", fake_update)
    monkeypatch.setattr(app, "get_redis", _get)
    return calls, result, r


def test_header_and_body_layout(capture):
    calls, _, _ = capture
    asyncio.run(app.notify_owner("harvest", "שאיבת הקטלוג בטלה", "שורה 1\nשורה 2", severity="warning"))
    assert calls[0]["text"] == "🟡 [🛰️ קטלוג וקצירה] שאיבת הקטלוג בטלה\n\nשורה 1\nשורה 2"
    assert calls[0]["critical"] is False


def test_title_only_has_no_trailing_blank_lines(capture):
    calls, _, _ = capture
    asyncio.run(app.notify_owner("orders", "2 הזמנות דורשות טיפול", severity="info"))
    assert calls[0]["text"] == "ℹ️ [📦 הזמנות] 2 הזמנות דורשות טיפול"


def test_critical_bypasses_quiet_hours(capture):
    calls, _, _ = capture
    asyncio.run(app.notify_owner("health", "DB down", "x", severity="critical"))
    assert calls[0]["critical"] is True and calls[0]["text"].startswith("🔴 [🏥 בריאות המערכת] DB down")


def test_cooldown_only_after_a_confirmed_send(capture):
    calls, result, r = capture
    result["value"] = {"ok": False, "error": "bridge unreachable"}
    asyncio.run(app.notify_owner("health", "t", "b", alert_key="k1", cooldown_s=600))
    assert "autospare:alert_cooldown:k1" not in r.keys                 # a failed send does not mute
    result["value"] = {"ok": True}
    asyncio.run(app.notify_owner("health", "t", "b", alert_key="k1", cooldown_s=600))
    assert r.keys["autospare:alert_cooldown:k1"] == ("1", 600)
    asyncio.run(app.notify_owner("health", "t", "b", alert_key="k1", cooldown_s=600))
    assert len(calls) == 2                                              # third call is inside the cooldown
