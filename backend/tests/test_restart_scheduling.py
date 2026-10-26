"""Restart-safe scheduling and the restart preflight.

Regression for 2026-10-10:
  1. Interval loops were anchored to the container start. Two approved backend recreations 28 minutes
     apart ran run_all_tasks at 05:20, 06:40 and 07:08 UTC (interval 3 h) and meili_sync twice.
  2. pre_restart.sh could not see work running INSIDE uvicorn: the 07:06 UTC recreation cut the daily
     Facebook group scan that had started at 07:00 UTC, and the loop has no catch-up.

Pure logic and temp files only: no network, no database, no Redis, no process is touched.
"""
import ast
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import inproc_job_marker as marker
import meili_checkpoint_state as mcs
from local_schedule import delay_after_restart

BACKEND = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("restart_preflight", BACKEND / "scripts" / "restart_preflight.py")
pre = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre)

NOW = datetime(2026, 10, 10, 7, 6, 43, tzinfo=timezone.utc)         # 10:06 IDT - the real incident


# ── 1. interval measured from the last completed run ─────────────────────────
def test_first_cycle_after_restart_waits_out_the_interval():
    three_h = 3 * 3600
    assert delay_after_restart(NOW - timedelta(minutes=17), three_h, 0, NOW) == three_h - 17 * 60
    assert delay_after_restart(NOW - timedelta(hours=5), three_h, 0, NOW) == 0          # overdue: run now
    assert delay_after_restart(NOW - timedelta(hours=5), 7200, 600, NOW) == 600          # never below the settle time
    assert delay_after_restart(NOW - timedelta(minutes=1), 7200, 600, NOW) == 7200 - 60
    assert delay_after_restart(NOW, 7200, 600, NOW) == 7200                              # never above the interval


def test_unknown_or_implausible_last_run_never_skips_a_cycle():
    assert delay_after_restart(None, 10800, 0, NOW) == 0
    assert delay_after_restart(None, 7200, 600, NOW) == 600
    assert delay_after_restart(NOW + timedelta(hours=2), 10800, 0, NOW) == 0             # clock skew / future
    assert delay_after_restart(datetime(2026, 10, 10, 7, 0), 10800, 90, NOW) == 90       # naive timestamp


def test_the_real_incident_would_not_have_run_three_cycles():
    # completions 05:29 and 06:49 UTC; starts 06:38:26 and 07:06:58 UTC; loop settle 90 s
    wait1 = delay_after_restart(datetime(2026, 10, 10, 5, 29, 41, tzinfo=timezone.utc), 10800, 0,
                                datetime(2026, 10, 10, 6, 39, 56, tzinfo=timezone.utc))
    assert 6500 < wait1 < 6700                     # next cycle ~08:29 UTC, not 06:40


def test_meili_checkpoint_completed_interrupted_and_broken(tmp_path):
    p = tmp_path / "cp.json"
    assert mcs.last_completed_sync(p) is None                                  # missing
    p.write_text(json.dumps({"index_ready": True, "offset": 15002, "total": 15002}))
    os.utime(p, (NOW.timestamp(), NOW.timestamp()))
    assert mcs.last_completed_sync(p) == NOW.replace(microsecond=0)
    p.write_text(json.dumps({"index_ready": True, "offset": 9000, "total": 15002, "last_id": "x"}))
    assert mcs.last_completed_sync(p) is None                                  # interrupted -> resume soon
    p.write_text(json.dumps({"index_ready": False, "offset": 5, "total": 5}))
    assert mcs.last_completed_sync(p) is None
    for bad in ("{", "[]", json.dumps({"offset": "a", "total": 1})):
        p.write_text(bad)
        assert mcs.last_completed_sync(p) is None


def test_both_loops_use_the_restart_safe_delay():
    api = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    meili = next(n for n in ast.walk(ast.parse(api)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_meili_sync_loop")
    body = ast.get_source_segment(api, meili)
    assert "delay_after_restart(last_completed_sync(), 7200, 600)" in body
    assert "await asyncio.sleep(600)" not in body                              # the start-anchored sleep is gone
    agent = (BACKEND / "db_update_agent.py").read_text(encoding="utf-8")
    loop = next(n for n in ast.walk(ast.parse(agent)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_agent_loop")
    body = ast.get_source_segment(agent, loop)
    assert "delay_after_restart(_last_done, interval_hours * 3600, 0.0)" in body
    assert "job_name = 'run_all_tasks' AND status = 'completed'" in body
    assert body.index("await asyncio.sleep(90)") < body.index("delay_after_restart(")   # lock-clear settle kept


# ── 2. in-process job marker ────────────────────────────────────────────────
def test_marker_lifecycle_and_dead_process(tmp_path, monkeypatch):
    monkeypatch.setattr(marker, "DIR", tmp_path / "m")
    assert marker.status("group_scan") == {"known": False, "running": False, "started_at": None,
                                           "finished_at": None, "age_s": None}
    marker.mark_started("group_scan", now=1000.0)
    assert marker.status("group_scan", now=1400.0)["running"] is True
    assert marker.status("group_scan", now=1000.0 + 7 * 3600)["running"] is False      # died: stale
    marker.mark_finished("group_scan", now=5000.0)
    st = marker.status("group_scan", now=5100.0)
    assert st["running"] is False and st["finished_at"] == 5000.0
    (tmp_path / "m" / "group_scan.json").write_text("{broken")
    assert marker.status("group_scan")["known"] is False
    marker.mark_started("../../etc/x", now=1.0)                                         # name is sanitised
    assert sorted(p.name for p in (tmp_path / "m").iterdir()) == ["etcx.json", "group_scan.json"]


def test_group_scan_loop_writes_both_markers():
    api = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(api)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_group_scan_loop")
    body = ast.get_source_segment(api, fn)
    assert body.index('_marker.mark_started("group_scan")') < body.index("discover_account_groups")
    assert body.index("record_scan_run") < body.index('_marker.mark_finished("group_scan")')


# ── 3. restart preflight decision ───────────────────────────────────────────
CLEAN = {"errors": {}, "other_browsers": [], "process_started_at": None, "subprocesses": [], "harvester": True,
         "group_scan_marker": {"known": False, "running": False}, "locks": [], "night_job": None,
         "running_jobs": [], "email_writes_in_flight": []}
QUIET = datetime(2026, 10, 10, 13, 0, tzinfo=timezone.utc)          # 16:00 IDT: no window


def test_quiet_system_is_safe_and_the_harvester_is_only_a_note():
    out = pre.evaluate(dict(CLEAN), QUIET)
    assert out["safe"] is True and out["blockers"] == []
    assert any("harvester" in n for n in out["notes"])


def test_the_real_incident_is_blocked():
    # 10:06 IDT, scan started at 10:00 inside uvicorn: no job row, no subprocess, no lock
    out = pre.evaluate(dict(CLEAN), NOW)
    assert out["safe"] is False and "group scan window" in out["blockers"][0]
    out = pre.evaluate({**CLEAN, "group_scan_marker": {"known": True, "running": True, "age_s": 403}}, NOW)
    assert out["blockers"] == ["Facebook group scan in progress (started 6 min ago)"]
    out = pre.evaluate({**CLEAN, "other_browsers": ["fb_native_test/profile"]}, QUIET)
    assert out["safe"] is False and out["blockers"][0].startswith("Facebook group scan: its browser is running")


def test_process_started_after_todays_slot_has_no_scan_to_cut():
    # the live case of 2026-10-10 10:40 IDT: uvicorn started 10:06:58, after the 10:00 slot
    slot = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc).timestamp()
    out = pre.evaluate({**CLEAN, "process_started_at": slot + 418}, datetime(2026, 10, 10, 7, 40, tzinfo=timezone.utc))
    assert out["safe"] is True and any("no scan" in n for n in out["notes"])
    # started BEFORE the slot (even by one second): the scan may be running -> still blocked
    out = pre.evaluate({**CLEAN, "process_started_at": slot - 1}, datetime(2026, 10, 10, 7, 40, tzinfo=timezone.utc))
    assert out["safe"] is False
    # a running marker or a browser job always wins over the start-time reasoning
    busy = {**CLEAN, "process_started_at": slot + 418, "group_scan_marker": {"known": True, "running": True, "age_s": 60}}
    assert pre.evaluate(busy, datetime(2026, 10, 11, 7, 1, tzinfo=timezone.utc))["safe"] is False
    assert pre.evaluate({**CLEAN, "process_started_at": slot + 418, "other_browsers": ["x/y"]},
                        datetime(2026, 10, 10, 7, 40, tzinfo=timezone.utc))["safe"] is False


def test_browser_detection_by_exclusion_and_process_start_time(tmp_path):
    base = "/root/.cache/ms-playwright/chromium-1228/chrome-linux64/"
    cmds = [base + "chrome --user-data-dir=/app/state/amayama_chrome_profile --load-extension=x",
            base + "chrome --type=renderer --crashpad-handler-pid=216",
            base + "chrome_crashpad_handler --monitor-self",
            base + "chrome --user-data-dir=/app/state/fb_native_test/profile --headless",
            base + "chrome --headless --no-sandbox",
            "/usr/local/bin/python3.11 /app/meili_sync.py", "Xvfb :99"]
    assert pre.other_browsers(cmds) == ["fb_native_test/profile", "no profile"]
    assert pre.other_browsers(cmds[:3]) == []
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "stat").write_text("1 (uvicorn worker) S " + " ".join(["0"] * 18) + " 4200 " + " ".join(["0"] * 10))
    (tmp_path / "stat").write_text("cpu 1 2 3\nbtime 1760000000\n")
    assert pre.process_started_at(1, str(tmp_path)) == 1760000000 + 4200 / os.sysconf("SC_CLK_TCK")
    assert pre.process_started_at(99, str(tmp_path)) is None


def test_group_scan_window_edges_and_finished_today():
    before = datetime(2026, 10, 10, 6, 58, tzinfo=timezone.utc)      # 09:58 IDT: restart is fine, scan runs at 10:00
    assert pre.evaluate(dict(CLEAN), before)["safe"] is True
    after = datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)       # 14:00 IDT: window closed
    assert pre.evaluate(dict(CLEAN), after)["safe"] is True
    done = {"known": True, "running": False, "finished_at": datetime(2026, 10, 10, 9, 50, tzinfo=timezone.utc).timestamp()}
    inside = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)      # 13:00 IDT, today's scan already finished
    assert pre.evaluate({**CLEAN, "group_scan_marker": done}, inside)["safe"] is True
    yesterday = {"known": True, "running": False, "finished_at": datetime(2026, 10, 9, 10, 5, tzinfo=timezone.utc).timestamp()}
    assert pre.evaluate({**CLEAN, "group_scan_marker": yesterday}, inside)["safe"] is False


def test_group_scan_disabled_does_not_block(monkeypatch):
    monkeypatch.setenv("SOCIAL_GROUP_SCAN_ENABLED", "0")
    assert pre.evaluate(dict(CLEAN), NOW)["safe"] is True


@pytest.mark.parametrize("fact,value,needle", [
    ("night_job", "sync_prices", "night pipeline is running sync_prices"),
    ("running_jobs", ["run_all_tasks:2026-10-10T07:08"], "job_registry: run_all_tasks"),
    ("locks", ["autospare:lock:db_update_agent"], "Redis job lock held"),
    ("subprocesses", ["meili_sync.py"], "subprocess running: meili_sync.py"),
    ("email_writes_in_flight", [{"stage": "gmail POST /drafts"}], "1 Gmail write(s) in flight"),
    ("errors", {"Redis": "ConnectionError"}, "could not read Redis"),
])
def test_each_kind_of_running_work_blocks(fact, value, needle):
    out = pre.evaluate({**CLEAN, fact: value}, QUIET)
    assert out["safe"] is False and any(needle in b for b in out["blockers"])


def test_night_window_without_a_running_job_is_a_note_not_a_blocker():
    night = datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc)       # 02:30 IDT
    out = pre.evaluate(dict(CLEAN), night)
    assert out["safe"] is True and any("night-pipeline window" in n for n in out["notes"])


def test_heavy_subprocess_detection_reports_each_script_once():
    cmds = ["/usr/local/bin/python3.11 -u /app/maintenance/build_part_thumbnails.py --limit 400",
            "/usr/local/bin/python3.11 /app/meili_sync.py", "/usr/local/bin/python3.11 /app/maintenance/meili_purge_inactive.py",
            "/usr/local/bin/python3.11 /app/harvesters/car_parts_ie_flaresolverr_harvester.py",
            "python3 /app/scripts/restart_preflight.py", "/usr/local/bin/uvicorn BACKEND_API_ROUTES:app"]
    assert pre.heavy_subprocesses(cmds) == ["build_part_thumbnails.py", "meili_purge_inactive.py", "meili_sync.py"]


def test_pre_restart_runs_the_preflight_before_any_side_effect():
    sh = (BACKEND / "scripts" / "pre_restart.sh").read_text(encoding="utf-8")
    gate = sh.index("restart_preflight.py")
    assert gate < sh.index("restart_workers.py capture") < sh.index("UPDATE job_registry SET status='superseded'")
    assert gate < sh.index("restart_workers.py stop")
    assert "exit 4" in sh[gate:sh.index("=== PRE-RESTART CAPTURE ===\" >&2", gate)]
    assert 'PRE_RESTART_FORCE:-0' in sh


# ── 4. thumbnail supervisor: the idle back-off survives a restart ───────────────────
import thumbnail_idle_state as tis  # noqa: E402


def test_thumbnail_backoff_resumes_instead_of_resetting(tmp_path):
    p = tmp_path / "idle.json"
    assert tis.resume(120, now=1000.0, path=p) == (120.0, 300.0)                 # no state: old behaviour
    tis.save(1200, now=1000.0, path=p)                                             # drained, backing off 20 min
    assert tis.resume(120, now=1000.0 + 400, path=p) == (800.0, 2400.0)           # restart 400 s later: wait the rest
    assert tis.resume(120, now=1000.0 + 1190, path=p) == (120.0, 2400.0)          # never below the settle time
    assert tis.resume(120, now=1000.0 + 9000, path=p) == (120.0, 2400.0)          # long outage: check soon
    tis.save(3600, now=1000.0, path=p)
    assert tis.resume(120, now=1001.0, path=p) == (3599.0, 3600.0)                # capped at 1 h
    tis.save(99999, now=1000.0, path=p)
    assert tis.resume(120, now=1000.0, path=p)[0] == 3600.0                       # implausible value clamped
    assert tis.resume(120, now=500.0, path=p) == (120.0, 300.0)                   # clock went backwards
    p.write_text("{broken")
    assert tis.resume(120, now=1000.0, path=p) == (120.0, 300.0)
    tis.save(600, now=1.0, path=p)
    tis.clear(path=p)
    assert not p.exists() and tis.resume(120, path=p) == (120.0, 300.0)           # work found: back to normal


def test_thumbnail_loop_saves_on_empty_and_clears_on_work():
    api = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(api)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_thumbnail_import_loop")
    body = ast.get_source_segment(api, fn)
    assert "_tis.resume(floor_s=120)" in body and "idle_backoff = _resumed_backoff" in body
    assert 0 < body.rindex("await asyncio.sleep(idle_backoff)") - body.index("_tis.save(idle_backoff)") < 80   # save, then sleep
    assert body.index("_tis.clear()") < body.index("await asyncio.sleep(between)")
    assert "await asyncio.sleep(120)  #" not in body


# ── 5. monthly car-parts.ie seeder: monthly since the last success, not per restart ──
def test_full_seed_loop_is_anchored_to_its_last_success():
    api = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(api)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_car_parts_ie_full_seed_loop")
    body = ast.get_source_segment(api, fn)
    assert '_marker.status("cpie_full_seed")' in body and "delay_after_restart(" in body
    assert "await asyncio.sleep(180)  #" not in body                         # the per-start trigger is gone
    ok = body.index("if rc == 0:")
    assert ok < body.index('_marker.mark_finished("cpie_full_seed")')          # only a SUCCESS moves the anchor
    month = 30 * 24 * 3600
    assert delay_after_restart(NOW - timedelta(days=2), month, 180, NOW) == month - 2 * 86400
    assert delay_after_restart(NOW - timedelta(days=45), month, 180, NOW) == 180     # overdue: soon after start
    assert delay_after_restart(None, month, 180, NOW) == 180                         # never seeded: old behaviour


# ── 6. weekly supplier sourcing: weekly since the last completed cycle ─────────────
def test_supplier_sourcing_loop_is_anchored_to_its_last_cycle():
    api = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(api)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_supplier_sourcing_loop")
    body = ast.get_source_segment(api, fn)
    assert '_marker.status("supplier_sourcing")' in body and "delay_after_restart(" in body
    assert "await asyncio.sleep(120)  # let startup settle" not in body
    assert body.index("run_sourcing_cycle()") < body.index('_marker.mark_finished("supplier_sourcing")') \
        < body.index("await notify_owner(")
    week = 7 * 86400
    assert delay_after_restart(NOW - timedelta(minutes=43), week, 120, NOW) == week - 43 * 60


# ── 7. closure gate 2026-10-10: scan vs scraper browser, unknown state, checkpoints ──
def test_scan_browser_and_short_lived_scraper_browser_are_told_apart_and_both_block():
    scan = pre.evaluate({**CLEAN, "other_browsers": ["fb_native_test/profile"]}, QUIET)
    fetch = pre.evaluate({**CLEAN, "other_browsers": ["tmp/playwright_chromiumdev_profile-g6Y4r4"]}, QUIET)
    other = pre.evaluate({**CLEAN, "other_browsers": ["state/some_other_profile"]}, QUIET)
    assert scan["safe"] is fetch["safe"] is other["safe"] is False          # protection is not weakened
    assert "Facebook group scan" in scan["blockers"][0] and "do not restart" in scan["blockers"][0]
    assert "short-lived scraper browser" in fetch["blockers"][0] and "about a minute" in fetch["blockers"][0]
    assert "Facebook" not in fetch["blockers"][0] and "a browser job is running" in other["blockers"][0]
    # a real scan (marker running) is reported as a scan even while its browser is between batches
    both = pre.evaluate({**CLEAN, "group_scan_marker": {"known": True, "running": True, "age_s": 5400}}, QUIET)
    assert both["blockers"] == ["Facebook group scan in progress (started 90 min ago)"]


def test_unknown_or_unreadable_scan_state_is_never_proof_of_no_scan():
    slot = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
    during = slot + timedelta(minutes=30)
    started_before = slot.timestamp() - 3600                       # the process that DID start today's scan
    unknown = {"known": False, "running": False, "started_at": None, "finished_at": None, "age_s": None}
    # no marker file at all (first scan after deployment), process alive since before the slot
    assert pre.evaluate({**CLEAN, "group_scan_marker": unknown, "process_started_at": started_before}, during)["safe"] is False
    # marker source unreadable -> fail closed, even outside the window
    assert pre.evaluate({**CLEAN, "errors": {"group scan marker": "PermissionError"}}, QUIET)["safe"] is False
    # process start time unknown -> the start-time exemption cannot apply
    assert pre.evaluate({**CLEAN, "group_scan_marker": unknown, "process_started_at": None}, during)["safe"] is False
    # stale marker (started, never finished, process died): not "running", but the window still protects
    stale = {"known": True, "running": False, "started_at": slot.timestamp() - 86400, "finished_at": None, "age_s": 88200}
    assert pre.evaluate({**CLEAN, "group_scan_marker": stale, "process_started_at": started_before}, during)["safe"] is False
    assert pre.evaluate({**CLEAN, "group_scan_marker": stale, "process_started_at": started_before},
                        slot + timedelta(hours=5))["safe"] is True     # after the window: nothing to protect


def test_marker_end_to_end_through_the_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(marker, "DIR", tmp_path / "inproc")
    slot = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
    facts = lambda now: {**CLEAN, "process_started_at": slot.timestamp() - 3600,
                         "group_scan_marker": marker.status("group_scan", now=now.timestamp())}
    marker.mark_started("group_scan", now=slot.timestamp() + 5)                    # scan starts 10:00:05
    mid = slot + timedelta(minutes=95)
    assert pre.evaluate(facts(mid), mid)["blockers"] == ["Facebook group scan in progress (started 94 min ago)"]
    marker.mark_finished("group_scan", now=(slot + timedelta(hours=3, minutes=6)).timestamp())
    after = slot + timedelta(hours=3, minutes=10)                                   # 13:10, still inside the window
    assert pre.evaluate(facts(after), after)["safe"] is True                        # finished today -> restart allowed


@pytest.mark.parametrize("name,interval,floor", [("cpie_full_seed", 30 * 86400, 180), ("supplier_sourcing", 7 * 86400, 120)])
def test_seed_and_sourcing_first_delay_for_every_checkpoint_state(tmp_path, monkeypatch, name, interval, floor):
    monkeypatch.setattr(marker, "DIR", tmp_path / "inproc")

    def first_delay(now):
        fin = marker.status(name, now=now.timestamp()).get("finished_at")
        return delay_after_restart(datetime.fromtimestamp(float(fin), timezone.utc) if fin else None, interval, floor, now)

    assert first_delay(NOW) == floor                                   # never ran: runs soon after start
    marker.mark_started(name, now=NOW.timestamp() - 300)               # started but never finished (failed / killed)
    assert first_delay(NOW) == floor                                   # only a SUCCESS anchors the interval
    marker.mark_finished(name, now=NOW.timestamp() - 60)
    assert first_delay(NOW) == interval - 60                           # valid success: a restart does not repeat it
    assert first_delay(NOW + timedelta(seconds=interval + 10)) == floor          # due: not suppressed
    assert first_delay(NOW + timedelta(days=400)) == floor                        # long overdue: still runs
    (tmp_path / "inproc" / f"{name}.json").write_text("{corrupt")
    assert first_delay(NOW) == floor                                   # corrupt checkpoint: runs, never blocks forever
    (tmp_path / "inproc" / f"{name}.json").write_text(json.dumps({"started_at": 1.0, "finished_at": NOW.timestamp() + 9e6}))
    assert first_delay(NOW) == floor                                   # timestamp in the future: ignored
    (tmp_path / "inproc" / f"{name}.json").write_text(json.dumps({"started_at": "x", "finished_at": NOW.timestamp()}))
    assert first_delay(NOW) == floor                                   # malformed record: ignored
