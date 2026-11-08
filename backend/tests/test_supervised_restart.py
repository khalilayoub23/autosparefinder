"""#86 regression: a supervised background loop that crashes is restarted (bounded), never silently left down.

Before: _supervised_task only WhatsApped "המשימה לא תאותחל אוטומטית" and the loop stayed dead until the next backend
restart — including chat_request_recovery (#77), whose death stops take-over and the process heartbeat.
Rule now: only a task that ENDS WITH AN EXCEPTION and was registered with restart=<factory> is restarted, after
30 s / 2 min / 10 min, at most 3 times per rolling hour; a normal return or a cancellation never is. Timings are
scaled down. No network: the owner alert is replaced by a recorder.
"""
import asyncio
import os
import re

import pytest

import BACKEND_API_ROUTES as app

SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BACKEND_API_ROUTES.py"),
           encoding="utf-8").read()

RESTARTABLE = {
    "chat_request_recovery", "stuck_orders_monitor", "notify_search_miss_loop", "scrape_search_misses_loop",
    "abandoned_cart_loop", "pending_payment_reminder", "price_watch_loop", "health_monitor_loop", "oom_watchdog",
    "vip_detection_loop", "noa_marketing_loop", "noa_engagement_loop", "supplier_sourcing_loop",
    "social_feedback_loop", "noa_eod_report_loop", "enrich_catalog_loop", "status_update_loop", "rex_dispatch_loop",
    "zombie_reaper", "job_queue_report", "whatsapp_link_monitor", "car_parts_ie_stall_watchdog",
    "car_parts_ie_healthcheck", "amayama_harvest_monitor", "harvest_supervisor",
    "vat_basis_registry"}   # #129: pure DB read, safe to restart
# subprocess / browser / job-lock loops and unreviewed external modules: alert-only by design
ALERT_ONLY = {"embed_warmup", "night_pipeline", "cleanup_loop", "email_agent_loop", "group_scan_loop",
              "job_queue_loop", "car_parts_ie_harvester_loop", "car_parts_ie_full_seed", "thumbnail_import_loop",
              "meili_sync_loop", "amayama_fs_harvester", "amayama_server_browser",
              # added after this policy was reviewed (supplier-lifecycle release): it EXECUTES procurement tasks, so an
              # automatic restart is not assumed safe - alert-only until its idempotence on restart is reviewed
              "boaz_task_loop",
              "fb_keepalive"}   # #130: launches the Facebook browser - alert-only like group_scan_loop


def test_registration_matches_the_reviewed_policy():
    regs = dict(re.findall(r'_supervised_task\("([^"]+)",\s*[^\n]*?(, restart=[A-Za-z_\.]+)?\)\n', SRC))
    with_restart = {n for n, r in regs.items() if r}
    assert with_restart == RESTARTABLE
    assert set(regs) - with_restart == ALERT_ONLY
    for n, r in regs.items():                     # the factory is the same function the task was started with
        if r:
            fn = r.split("=")[1]
            assert re.search(r'_supervised_task\("' + n + r'",\s*' + re.escape(fn) + r'\(\)', SRC), n


@pytest.mark.parametrize("exc,factory,expected", [(RuntimeError("x"), True, 30.0), (None, True, None),
                                                  (RuntimeError("x"), False, None)])
def test_restart_only_after_an_exception_with_a_factory(monkeypatch, exc, factory, expected):
    monkeypatch.setattr(app, "_RESTART_HISTORY", {})
    monkeypatch.setattr(app, "_RESTART_BACKOFF_S", [30.0, 120.0, 600.0])
    assert app._restart_delay("t", exc, factory, 1000.0) == expected


def test_backoff_then_give_up_within_the_window_then_recover_after_it(monkeypatch):
    monkeypatch.setattr(app, "_RESTART_HISTORY", {})
    monkeypatch.setattr(app, "_RESTART_BACKOFF_S", [30.0, 120.0, 600.0])
    monkeypatch.setattr(app, "_RESTART_WINDOW_S", 3600.0)
    e = RuntimeError("x")
    assert [app._restart_delay("t", e, True, t) for t in (0, 10, 20, 30)] == [30.0, 120.0, 600.0, None]
    assert app._restart_delay("t", e, True, 3605) == 600.0          # t=0 left the window; t=10/20 are still in it
    assert app._restart_delay("other", e, True, 40) == 30.0         # per task


def _harness(monkeypatch, behaviour, *, restart=True, run_s=0.6):
    """behaviour(n) -> 'raise' | 'return' | 'forever'. Returns (starts, alerts, final task registered)."""
    starts, alerts = [], []

    async def fake_alert(text, critical=False, **k):
        alerts.append((text, critical))
        return {"ok": True}

    def factory():
        n = len(starts) + 1
        starts.append(n)

        async def loop():
            await asyncio.sleep(0.01)
            b = behaviour(n)
            if b == "raise":
                raise RuntimeError(f"crash {n}")
            if b == "forever":
                await asyncio.sleep(3600)
        return loop()

    monkeypatch.setattr(app, "_wa_send_update", fake_alert)
    monkeypatch.setattr(app, "_RESTART_HISTORY", {})
    monkeypatch.setattr(app, "_RESTART_BACKOFF_S", [0.05, 0.05, 0.05])
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "+972500000086")

    async def main():
        app._supervised_task("zz86_task", factory(), restart=factory if restart else None)
        await asyncio.sleep(run_s)
        t = app._SUPERVISED_TASKS.get("zz86_task")
        state = "running" if t and not t.done() else "down"
        for x in list(app._RESTART_TASKS):
            x.cancel()
        if t and not t.done():
            t.cancel()
        await asyncio.sleep(0.05)
        app._SUPERVISED_TASKS.pop("zz86_task", None)
        return state
    state = asyncio.run(main())
    return starts, alerts, state


def test_a_crashed_loop_is_restarted_and_keeps_running(monkeypatch):
    starts, alerts, state = _harness(monkeypatch, lambda n: "raise" if n == 1 else "forever")
    assert starts == [1, 2] and state == "running"
    assert len(alerts) == 1 and alerts[0][1] is True                  # critical, bypasses quiet hours
    assert "תאותחל אוטומטית" in alerts[0][0] and "crash 1" in alerts[0][0]


def test_a_loop_crashing_forever_stops_after_three_restarts_and_says_so(monkeypatch):
    starts, alerts, state = _harness(monkeypatch, lambda n: "raise")
    assert starts == [1, 2, 3, 4] and state == "down"
    assert all(c for _, c in alerts) and len(alerts) == 4
    assert "לא תאותחל שוב אוטומטית" in alerts[-1][0]


def test_a_normal_return_is_never_restarted(monkeypatch):
    starts, alerts, state = _harness(monkeypatch, lambda n: "return")
    assert starts == [1] and state == "down"


def test_alert_only_task_is_not_restarted(monkeypatch):
    starts, alerts, state = _harness(monkeypatch, lambda n: "raise", restart=False)
    assert starts == [1] and state == "down" and "המשימה לא תאותחל אוטומטית" in alerts[0][0]


def test_cancellation_is_silent_and_not_restarted(monkeypatch):
    starts, alerts = [], []

    async def fake_alert(text, critical=False, **k):
        alerts.append(text)

    def factory():
        starts.append(1)
        return asyncio.sleep(3600)
    monkeypatch.setattr(app, "_wa_send_update", fake_alert)
    monkeypatch.setattr(app, "_RESTART_HISTORY", {})
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "+972500000086")

    async def main():
        t = app._supervised_task("zz86_cancel", factory(), restart=factory)
        await asyncio.sleep(0.02)
        t.cancel()
        await asyncio.sleep(0.1)
        app._SUPERVISED_TASKS.pop("zz86_cancel", None)
    asyncio.run(main())
    assert starts == [1] and alerts == []
