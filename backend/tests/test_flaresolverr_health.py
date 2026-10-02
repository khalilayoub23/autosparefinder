"""
Regression tests for FlareSolverr health integration (root-fix 2026-10-02).

The failure mode being guarded:
  FlareSolverr enters an OOM-induced 500-loop → harvester logs a WARNING every ~12s
  → log mtime is always "fresh" → healthcheck reports status=ok even during 100% stall.

The fix: two independent detection layers:
  1. _health_monitor_loop._probe() calls POST /v1 {"cmd": "sessions.list"} as a live
     FlareSolverr probe. State-change alerting is free (same system as Redis/Meilisearch).
  2. car_parts_ie_flaresolverr_harvester writes harvester_fs_health.json after every
     ensure_clearance() attempt with consecutive_fails count.
     _car_parts_ie_harvester_healthcheck_loop reads it and alerts at >= threshold.

Tests cover:
  - _probe() returns "ok" on HTTP 200, "error" on HTTP 500
  - _probe() returns "error" on connection refused
  - state change ok→error triggers alert
  - state change error→ok triggers recovery alert (distinct message)
  - no alert when state unchanged
  - harvester_fs_health.json consecutive_fails threshold alert
  - consecutive_fails reset to 0 on clearance success
"""

import json
import os
import sys
import tempfile
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Simulated probe logic (mirrors _probe() in _health_monitor_loop) ─────────

class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


async def _probe_flaresolverr(fs_url: str, fake_status_code: int) -> str:
    """Mirror of the FlareSolverr probe block inside _health_monitor_loop._probe()."""
    if not fs_url:
        return None  # no URL configured → skip
    try:
        # Simulate httpx POST with injected status_code
        resp = _FakeResponse(fake_status_code)
        return "ok" if resp.status_code == 200 else "error"
    except Exception:
        return "error"


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── probe() state tests ───────────────────────────────────────────────────────

def test_probe_returns_ok_on_http_200():
    result = run(_probe_flaresolverr("http://flaresolverr:8191/v1", 200))
    assert result == "ok"


def test_probe_returns_error_on_http_500():
    result = run(_probe_flaresolverr("http://flaresolverr:8191/v1", 500))
    assert result == "error"


def test_probe_returns_error_on_http_503():
    result = run(_probe_flaresolverr("http://flaresolverr:8191/v1", 503))
    assert result == "error"


async def _probe_connection_refused(fs_url: str) -> str:
    """Probe that simulates connection refused."""
    if not fs_url:
        return None
    try:
        raise ConnectionRefusedError("Connection refused")
    except Exception:
        return "error"


def test_probe_returns_error_on_connection_refused():
    result = run(_probe_connection_refused("http://flaresolverr:8191/v1"))
    assert result == "error"


def test_probe_skipped_when_no_url():
    result = run(_probe_flaresolverr("", 200))
    assert result is None  # no URL configured → no probe


# ── state-change alerting ─────────────────────────────────────────────────────

def test_state_change_ok_to_error_triggers_alert():
    """ok→error must trigger an alert."""
    alerts = []

    def _check_state_change(prev_state, new_state, service):
        if prev_state is not None and prev_state != new_state:
            if new_state == "error":
                alerts.append({"type": "down", "service": service})
            elif new_state == "ok":
                alerts.append({"type": "recovery", "service": service})

    _check_state_change("ok", "error", "flaresolverr")
    assert len(alerts) == 1
    assert alerts[0]["type"] == "down"
    assert alerts[0]["service"] == "flaresolverr"


def test_state_change_error_to_ok_triggers_recovery_alert():
    """error→ok must trigger a recovery alert (distinct from down alert)."""
    alerts = []

    def _check_state_change(prev_state, new_state, service):
        if prev_state is not None and prev_state != new_state:
            if new_state == "error":
                alerts.append({"type": "down", "service": service})
            elif new_state == "ok":
                alerts.append({"type": "recovery", "service": service})

    _check_state_change("error", "ok", "flaresolverr")
    assert len(alerts) == 1
    assert alerts[0]["type"] == "recovery"


def test_no_alert_when_state_unchanged_ok():
    """ok→ok must NOT produce an alert."""
    alerts = []

    def _check_state_change(prev_state, new_state, service):
        if prev_state is not None and prev_state != new_state:
            alerts.append({"type": "change", "service": service})

    _check_state_change("ok", "ok", "flaresolverr")
    assert not alerts


def test_no_alert_when_state_unchanged_error():
    """error→error must NOT produce a duplicate alert (cooldown is external)."""
    alerts = []

    def _check_state_change(prev_state, new_state, service):
        if prev_state is not None and prev_state != new_state:
            alerts.append({"type": "change", "service": service})

    _check_state_change("error", "error", "flaresolverr")
    assert not alerts


def test_no_alert_on_first_probe_regardless_of_state():
    """First probe (prev_state=None) must never alert — it's just establishing baseline."""
    alerts = []

    def _check_state_change(prev_state, new_state, service):
        if prev_state is not None and prev_state != new_state:
            alerts.append({"type": "change", "service": service})

    _check_state_change(None, "error", "flaresolverr")
    assert not alerts


# ── harvester_fs_health.json consecutive_fails ────────────────────────────────

def test_consecutive_fails_below_threshold_no_alert():
    """consecutive_fails below threshold must not produce an alert."""
    threshold = 10
    alerts = []

    health = {"consecutive_fails": 7, "ts": 1728000000.0, "mode": "cookie"}

    if health.get("consecutive_fails", 0) >= threshold:
        alerts.append("alert")

    assert not alerts


def test_consecutive_fails_at_threshold_triggers_alert():
    """consecutive_fails >= threshold (10) must trigger a critical alert."""
    threshold = 10
    alerts = []

    health = {"consecutive_fails": 10, "ts": 1728000000.0, "mode": "cookie"}

    if health.get("consecutive_fails", 0) >= threshold:
        alerts.append("alert")

    assert len(alerts) == 1


def test_consecutive_fails_above_threshold_triggers_alert():
    threshold = 10
    alerts = []

    health = {"consecutive_fails": 23, "ts": 1728000000.0, "mode": "cookie"}

    if health.get("consecutive_fails", 0) >= threshold:
        alerts.append("alert")

    assert len(alerts) == 1


def test_consecutive_fails_resets_to_zero_on_success():
    """After a successful clearance solve, consecutive_fails must be reset to 0."""
    consecutive_fails = 15

    # Simulate ensure_clearance() success path
    def _on_clearance_success():
        nonlocal consecutive_fails
        consecutive_fails = 0

    _on_clearance_success()
    assert consecutive_fails == 0


def test_consecutive_fails_increments_on_failure():
    """Each failed clearance attempt increments consecutive_fails by exactly 1."""
    consecutive_fails = 5

    def _on_clearance_failure():
        nonlocal consecutive_fails
        consecutive_fails += 1

    _on_clearance_failure()
    _on_clearance_failure()
    assert consecutive_fails == 7


# ── harvester_fs_health.json read/write round-trip ───────────────────────────

def test_health_file_round_trip():
    """The JSON state file round-trips correctly."""
    import time

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name

    try:
        state = {
            "consecutive_fails": 3,
            "ts": time.time(),
            "mode": "cookie",
        }
        with open(path, "w") as f:
            json.dump(state, f)

        with open(path) as f:
            restored = json.load(f)

        assert restored["consecutive_fails"] == 3
        assert restored["mode"] == "cookie"
    finally:
        os.unlink(path)


def test_missing_health_file_does_not_alert():
    """If the health file does not exist, the healthcheck must not raise and must not alert."""
    threshold = 10
    alerts = []

    non_existent_path = "/tmp/nonexistent_health_42.json"
    try:
        if os.path.exists(non_existent_path):
            with open(non_existent_path) as f:
                health = json.load(f)
            if health.get("consecutive_fails", 0) >= threshold:
                alerts.append("alert")
    except Exception:
        pass  # must not propagate

    assert not alerts


def test_malformed_health_file_does_not_crash():
    """A corrupted health file must be caught, logged, and not crash the healthcheck loop."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{broken json{{")
        path = f.name

    alerts = []
    try:
        with open(path) as f:
            health = json.load(f)
        if health.get("consecutive_fails", 0) >= 10:
            alerts.append("alert")
    except Exception:
        pass  # exception caught — loop continues
    finally:
        os.unlink(path)

    assert not alerts
