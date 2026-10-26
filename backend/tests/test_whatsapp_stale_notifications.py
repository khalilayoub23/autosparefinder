"""Owner WhatsApp notifications must describe what is NEW or CURRENT, never re-announce old results
as news (FIXES_TRACKER #71).

Live evidence behind each case (2026-10-20): "15 מילות סיווג חדשות" was sent on 11 of 12 days with the
same tokens (consensus 2026-07-27 / 08-06, 2,524 really waiting); the pending-posts reminder re-listed
July posts daily; the 09:00 digest printed REX todo counts last changed on 2026-06-14 and never showed
the night pipeline; the "queue empty" harvest alert repeated every 6 h for 4 days.
Expected values are written out here. Redis cases use the isolated sandbox Redis only.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import notify_backlog as nb  # noqa: E402

_URL = os.getenv("REDIS_URL", "")
_SAFE_REDIS = (os.getenv("ENVIRONMENT", "").lower() in ("sandbox", "test")
               and "sandbox_redis" in _URL and "autospare_redis" not in _URL)
needs_redis = pytest.mark.skipif(not _SAFE_REDIS, reason="needs the isolated sandbox Redis (never production)")
D = datetime(2026, 8, 6)
NOW = 1_792_500_000.0
DAY = 86400


# ── the decision ─────────────────────────────────────────────────────────────────────────────
def test_first_use_treats_the_existing_backlog_as_old_not_new():
    plan = nb.backlog_plan({"vest": D, "lever": D}, None, last_reminder_ts=None, now_ts=NOW)
    assert plan["bootstrap"] is True and plan["new"] == [] and plan["send"] == "reminder"
    assert plan["total"] == 2 and plan["oldest"] == D


def test_unchanged_backlog_is_silent_until_the_reminder_is_due():
    cur = {"vest": D, "lever": D}
    assert nb.backlog_plan(cur, {"vest", "lever"}, last_reminder_ts=NOW - DAY, now_ts=NOW)["send"] is None
    assert nb.backlog_plan(cur, {"vest", "lever"}, last_reminder_ts=NOW - 7 * DAY, now_ts=NOW)["send"] == "reminder"


def test_only_really_new_items_are_called_new():
    cur = {"vest": D, "lever": D, "gasket": datetime(2026, 10, 20)}
    plan = nb.backlog_plan(cur, {"vest", "lever"}, last_reminder_ts=NOW - DAY, now_ts=NOW)
    assert plan["send"] == "new" and plan["new"] == ["gasket"] and plan["total"] == 3 and plan["oldest"] == D


def test_new_items_are_held_not_dropped_inside_the_daily_limit():
    cur = {"vest": D, "gasket": datetime(2026, 10, 20)}
    held = nb.backlog_plan(cur, {"vest"}, last_reminder_ts=None, now_ts=NOW, last_new_ts=NOW - 3600)
    assert held["send"] is None and held["new"] == ["gasket"]
    later = nb.backlog_plan(cur, {"vest"}, last_reminder_ts=None, now_ts=NOW + DAY, last_new_ts=NOW - 3600)
    assert later["send"] == "new" and later["new"] == ["gasket"]


# ── keyword alert end to end (real notify_backlog state in sandbox Redis) ──────────────────────
def _pending(*toks):
    return [{"token": t, "category": "engine", "since": D, "observations": 100, "agreement": 1.0} for t in toks]


@needs_redis
def test_keyword_alert_never_calls_old_tokens_new(monkeypatch):
    import redis.asyncio as aioredis
    import db_cleanup_agent as dca
    import BACKEND_API_ROUTES as app
    import category_learning as cl
    sent = []

    async def fake_notify(category, title, body="", **kw):
        sent.append((title, body))
        return {"ok": True}

    async def fake_evidence(db, token, proposed=None):
        return {"verdict": "ok" if token == "gasket" else "category_mismatch"}

    class _Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(app, "notify_owner", fake_notify)
    monkeypatch.setattr(cl, "evidence_profile", fake_evidence)
    monkeypatch.setattr(dca, "scraper_session_factory", lambda: _Db())
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "+972500000001")
    kind = f"keywords"
    tag = uuid.uuid4().hex[:8]
    monkeypatch.setattr(nb, "_key", lambda k, part: f"test:{tag}:{k}:{part}")

    async def go():
        r = aioredis.from_url(_URL, decode_responses=True)
        try:
            p1 = await dca._notify_owner_pending_keywords(_pending("vest", "lever"), now_ts=NOW, redis_client=r)
            p2 = await dca._notify_owner_pending_keywords(_pending("vest", "lever"), now_ts=NOW + DAY, redis_client=r)
            p3 = await dca._notify_owner_pending_keywords(_pending("vest", "lever", "gasket"),
                                                          now_ts=NOW + 2 * DAY, redis_client=r)
            return p1, p2, p3
        finally:
            for part in ("announced", "announced_at", "reminded_at"):
                await r.delete(f"test:{tag}:{kind}:{part}")
            await r.aclose()
    p1, p2, p3 = asyncio.run(go())
    assert p1["send"] == "reminder" and p2["send"] is None and p3["send"] == "new"
    assert len(sent) == 2
    assert sent[0][0] == "תזכורת: 2 מילות סיווג ממתינות לאישורך" and "חדשות" not in sent[0][0]
    assert sent[1][0] == "1 מילות סיווג חדשות ממתינות לאישורך"
    assert sent[1][1].startswith("gasket→engine ✅") and "סה\"כ ממתינות לאישורך: 3 (הוותיקה מ-06/08/2026)" in sent[1][1]
    assert "vest" not in sent[1][1].split("\n")[0]                       # an old token is never in the "new" list


# ── pending NOA posts ─────────────────────────────────────────────────────────────────────────
@needs_redis
def test_pending_posts_reminder_does_not_relist_july_posts_daily(monkeypatch):
    import redis.asyncio as aioredis
    import BACKEND_API_ROUTES as app
    sent = []

    async def fake_notify(category, title, body="", **kw):
        sent.append((title, body))
        return {"ok": True}
    monkeypatch.setattr(app, "notify_owner", fake_notify)
    tag = uuid.uuid4().hex[:8]
    monkeypatch.setattr(nb, "_key", lambda k, part: f"test:{tag}:{k}:{part}")
    july = datetime(2026, 7, 19, 5, 51)
    rows = [("be2c62ab-0000-0000-0000-000000000000", ["discord"], july, 2227.0),
            ("b5b7d9f7-0000-0000-0000-000000000000", ["discord"], july, 2215.0)]
    new_row = ("9f00aa11-0000-0000-0000-000000000000", ["facebook"], datetime(2026, 10, 20, 7, 0), 3.0)

    async def go():
        r = aioredis.from_url(_URL, decode_responses=True)
        try:
            a = await app._notify_pending_posts(rows, now_ts=NOW, redis_client=r)
            b = await app._notify_pending_posts(rows, now_ts=NOW + DAY, redis_client=r)
            c = await app._notify_pending_posts(rows + [new_row], now_ts=NOW + 2 * DAY, redis_client=r)
            return a, b, c
        finally:
            for part in ("announced", "announced_at", "reminded_at"):
                await r.delete(f"test:{tag}:noa_posts:{part}")
            await r.aclose()
    a, b, c = asyncio.run(go())
    assert (a["send"], b["send"], c["send"]) == ("reminder", None, "new")
    assert sent[0][0] == "תזכורת: 2 פוסטים ממתינים לאישור" and "הוותיק ממתין מ-19/07/2026" in sent[0][1]
    assert sent[1][0] == "1 פוסטים חדשים ממתינים לאישור"
    assert "*אשר 9f00aa11*" in sent[1][1] and "be2c62ab" not in sent[1][1]
    assert "סה\"כ ממתינים: 3 (הוותיק מ-19/07/2026)" in sent[1][1]


# ── 09:00 digest content ─────────────────────────────────────────────────────────────────────
def test_digest_shows_last_nights_real_runs():
    import BACKEND_API_ROUTES as app
    t = datetime(2026, 10, 19, 22, 10, tzinfo=timezone.utc)
    rows = [("auto_backup", "COMPLETED", t, t + timedelta(minutes=4, seconds=27), None),
            ("sync_prices", "FAILED", t, t + timedelta(minutes=80), "deadline exceeded"),
            ("weekly_maintenance", "QUEUED", None, None, None)]
    assert app._night_runs_lines("D:2026-10-20", rows) == [
        "\n*עבודות הלילה (D:2026-10-20):*",
        "  ✅ auto_backup · 4′",
        "  🔴 נכשל sync_prices · 80′ — deadline exceeded",
        "  ⏳ לא התחיל weekly_maintenance",
    ]
    assert app._night_runs_lines("D:2026-10-20", []) == ["\n*עבודות הלילה (D:2026-10-20):*",
                                                          "  (לא נרשמו הרצות לילה)"]


def test_digest_marks_numbers_that_have_not_changed():
    import BACKEND_API_ROUTES as app
    now = datetime(2026, 10, 20, 6, 9, tzinfo=timezone.utc)
    rows = [("completed", 27, datetime(2026, 6, 14, 22, 35, 44)),
            ("pending", 3, datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc))]
    assert app._rex_todo_lines(rows, now) == ["  הושלם: 27 (ללא שינוי מאז 15/06/2026)", "  ממתין: 3"]
    assert app._rex_todo_lines([], now) == ["  (אין)"]


# ── harvest "queue empty" repeats ────────────────────────────────────────────────────────────
def _decide(**kw):
    import BACKEND_API_ROUTES as app
    base = dict(first_sample=False, d_models=0, d_parts=0, in_progress=0, pending=0,
                prev_state="idle", realert_s=21600, mode="exceptions", idle_realert_s=86400)
    base.update(kw)
    return app._harvest_status_decision(**base)


def test_unchanged_idle_queue_is_not_repeated_every_six_hours():
    assert _decide(secs_since_alert=6 * 3600 + 1) == ("idle", False)
    assert _decide(secs_since_alert=24 * 3600 + 1) == ("idle", True)


def test_a_real_stall_still_repeats_on_its_own_cadence():
    assert _decide(pending=100, prev_state="stalled", secs_since_alert=6 * 3600 + 1) == ("stalled", True)


def test_state_changes_are_still_announced_at_once():
    assert _decide(prev_state="ok", secs_since_alert=None) == ("idle", True)
    assert _decide(d_models=3, d_parts=50, in_progress=1, prev_state="idle", secs_since_alert=60) == ("ok", True)


def test_idle_message_says_since_when():
    import inspect
    import BACKEND_API_ROUTES as app
    src = inspect.getsource(app._harvest_supervisor_loop)
    assert 'f"התור ריק מאז {_since_txt}' in src and '"idle_since"' in src
