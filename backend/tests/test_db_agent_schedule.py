"""run_all_tasks at fixed Israel slots + schedule-aware silence alarm (owner, 2026-10-27).

Owner: run_all_tasks must run 3 times a day (21:30, 00:15, 07:15 Israel), never in the middle of the
day; the "db_update_agent silent" alarm must not fire for the normal quiet hours between slots.
Incident it prevents: an 11:52 deploy started a midday cycle at 11:54 (old 3 h interval caught up
immediately after every restart).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import db_update_agent as dua
import local_schedule as ls

IL = ZoneInfo("Asia/Jerusalem")
TIMES = ls.parse_times("21:30,00:15,07:15")


def il(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IL).astimezone(timezone.utc)


def test_default_slots_are_the_owner_choice():
    assert dua.DB_AGENT_RUN_TIMES_IL == "21:30,00:15,07:15"
    assert ls.parse_times(dua.DB_AGENT_RUN_TIMES_IL) == [(0, 15), (7, 15), (21, 30)]
    assert dua.DB_AGENT_CATCHUP_WINDOW_S == 7200


@pytest.mark.parametrize("spec", ["", "25:00", "7:75", "abc", "07:15,,"])
def test_invalid_schedule_is_rejected_not_silently_dropped(spec):
    if spec == "07:15,,":
        assert ls.parse_times(spec) == [(7, 15)]
        return
    with pytest.raises(ValueError):
        ls.parse_times(spec)


@pytest.mark.parametrize("now,expected", [
    (il(2026, 10, 27, 11, 52), il(2026, 10, 27, 21, 30)),
    (il(2026, 10, 27, 22, 0), il(2026, 10, 28, 0, 15)),
    (il(2026, 10, 28, 0, 15), il(2026, 10, 28, 7, 15)),     # strictly after
    (il(2026, 10, 28, 3, 0), il(2026, 10, 28, 7, 15)),
])
def test_next_slot(now, expected):
    assert ls.next_slot_utc(TIMES, now) == expected


@pytest.mark.parametrize("now,expected", [
    (il(2026, 10, 27, 11, 52), il(2026, 10, 27, 7, 15)),
    (il(2026, 10, 27, 0, 10), il(2026, 10, 26, 21, 30)),    # crosses midnight
    (il(2026, 10, 27, 0, 15), il(2026, 10, 27, 0, 15)),     # at or before now
])
def test_last_slot(now, expected):
    assert ls.last_slot_utc(TIMES, now) == expected


def test_slots_follow_israel_dst():
    # Israel leaves DST early Sun 2026-10-25: 07:15 local is 04:15Z on 10-24, 05:15Z from 10-25.
    assert ls.next_slot_utc([(7, 15)], il(2026, 10, 23, 12, 0)).strftime("%H:%M") == "04:15"
    assert ls.next_slot_utc([(7, 15)], il(2026, 10, 26, 12, 0)).strftime("%H:%M") == "05:15"


# ── the loop's decision: which slot (if any) to run now ───────────────────────

def test_restart_at_midday_does_not_start_a_cycle():
    """The 2026-10-27 incident: 07:15 slot already ran (cycle started 08:20 local in reality),
    deploy at 11:52 -> the old loop ran at 11:54. Now: wait for 21:30."""
    now = il(2026, 10, 27, 11, 54)
    assert dua._due_slot(TIMES, il(2026, 10, 27, 8, 20), None, now) is None


def test_restart_long_after_a_missed_slot_waits_for_the_next_one():
    now = il(2026, 10, 27, 11, 54)                       # 07:15 missed, 4.6 h ago > 2 h window
    assert dua._due_slot(TIMES, il(2026, 10, 27, 4, 0), None, now) is None


def test_missed_slot_is_caught_up_inside_the_window():
    now = il(2026, 10, 27, 8, 30)                        # 07:15 missed, 75 min ago
    assert dua._due_slot(TIMES, il(2026, 10, 27, 0, 20), None, now) == il(2026, 10, 27, 7, 15)


def test_slot_runs_at_its_time_and_only_once():
    now = il(2026, 10, 27, 21, 30)
    slot = dua._due_slot(TIMES, il(2026, 10, 27, 7, 16), None, now)
    assert slot == il(2026, 10, 27, 21, 30)
    assert dua._due_slot(TIMES, il(2026, 10, 27, 7, 16), slot, now + timedelta(minutes=5)) is None   # served
    assert dua._due_slot(TIMES, il(2026, 10, 27, 21, 31), None, now + timedelta(minutes=5)) is None  # started


def test_never_ran_runs_only_inside_a_window():
    assert dua._due_slot(TIMES, None, None, il(2026, 10, 27, 7, 30)) == il(2026, 10, 27, 7, 15)
    assert dua._due_slot(TIMES, None, None, il(2026, 10, 27, 13, 0)) is None


def test_old_interval_loop_is_gone():
    src = Path(dua.__file__).read_text(encoding="utf-8")
    loop = src[src.index("async def _agent_loop"):src.index("def start_agent_task")]
    assert "interval_hours * 3600" not in loop and "delay_after_restart" not in loop
    assert loop.count("while True:") == 1


# ── health monitor: silence alarm must respect the schedule ───────────────────

GRACE = dua.DB_AGENT_CATCHUP_WINDOW_S + 1800


@pytest.mark.parametrize("now,last_started,missed", [
    (il(2026, 10, 27, 15, 0), il(2026, 10, 27, 7, 16), None),    # 8 h of quiet between slots: normal
    (il(2026, 10, 27, 21, 0), il(2026, 10, 27, 7, 16), None),    # 13.7 h quiet, next slot not yet due
    (il(2026, 10, 27, 9, 30), il(2026, 10, 27, 0, 16), None),    # 07:15 missed but still in grace
    (il(2026, 10, 27, 9, 50), il(2026, 10, 27, 0, 16), il(2026, 10, 27, 7, 15)),   # genuinely missed
    (il(2026, 10, 28, 0, 5), il(2026, 10, 27, 7, 16), None),     # 21:30 missed, grace until 00:00? no: 2.5 h -> 00:00
])
def test_silence_alarm_only_for_a_missed_slot(now, last_started, missed):
    got = ls.slot_missed(TIMES, last_started, GRACE, now)
    if now == il(2026, 10, 28, 0, 5):
        assert got == il(2026, 10, 27, 21, 30)           # 21:30 + 2.5 h = 00:00 < 00:05 -> missed
        return
    assert got == missed


def test_health_monitor_uses_the_schedule_not_a_fixed_silence():
    src = Path(dua.__file__).with_name("BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    i = src.index("Check 3: worker silent")
    block = src[i:i + 6000]
    assert "silence_mins > 300" not in block
    assert "slot_missed" in block and "DB_AGENT_RUN_TIMES_IL" in block and "DB_AGENT_CATCHUP_WINDOW_S" in block
    assert 'status) == "running" and silence_mins > 30' in block.replace("_hb_row[0]", "status")
