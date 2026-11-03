"""disk_alert thresholds, escalation, dedup, recovery (2026-11-03)."""
import disk_alert as da


def test_levels():
    assert [da.level_for(x) for x in (79.9, 80, 84.9, 85, 90, 94.9, 95, 100)] == \
        ["ok", "warning", "warning", "high", "critical", "critical", "emergency", "emergency"]


def test_first_alert_and_dedup():
    assert da.evaluate(81, "ok", None, 0) == ("warning", "escalate")
    assert da.evaluate(82, "warning", 0, 3600) == ("warning", None)
    assert da.evaluate(82, "warning", 0, 25 * 3600) == ("warning", "alert")


def test_escalation_not_suppressed_by_lower_cooldown():
    assert da.evaluate(86, "warning", 100, 110) == ("high", "escalate")
    assert da.evaluate(96, "critical", 100, 110) == ("emergency", "escalate")


def test_downgrade_and_recovery_once():
    assert da.evaluate(86, "critical", 0, 1) == ("high", "downgrade")
    assert da.evaluate(70, "high", 0, 1) == ("ok", "recovered")
    assert da.evaluate(70, "ok", None, 1) == ("ok", None)


def test_emergency_repeats_hourly():
    assert da.evaluate(97, "emergency", 0, 3599)[1] is None
    assert da.evaluate(97, "emergency", 0, 3600)[1] == "alert"


def test_sample_and_message():
    s = da.sample("/")
    assert {"total", "used", "free", "pct"} <= set(s)
    t, b = da.format_message({"path": "/", "used": 8e10, "total": 1e11, "free": 2e10, "pct": 80.0}, "warning", "escalate",
                             [("/x", 5_000_000_000)])
    assert "80.0%" in t and "/x" in b


def test_error_is_not_ok():
    import pytest
    with pytest.raises(OSError):
        da.sample("/definitely/not/here")


# ---- integration: run the REAL health-monitor block (extracted from BACKEND_API_ROUTES.py) against fakes ----
import asyncio
import pathlib
import textwrap
import types

import pytest

_SRC = (pathlib.Path(__file__).resolve().parent.parent / "BACKEND_API_ROUTES.py").read_text()


def _block():
    a = _SRC.index("# 5a2: Disk usage")
    b = _SRC.index("# 5b: Zombie jobs")
    body = textwrap.dedent(" " * 20 + _SRC[a:b].lstrip())
    return "async def _run():\n" + textwrap.indent(body, "    ")


class FakeRedis:
    def __init__(self): self.d = {}
    async def get(self, k): return self.d.get(k)
    async def set(self, k, v, **kw): self.d[k] = v


class Env:
    """Executes the production block once per call() with controllable disk usage / clock / notifier."""

    def __init__(self, monkeypatch, devs=None):
        self.r, self.sent, self.logs = FakeRedis(), [], []
        self.pct = {"/": 50.0, "/app/state": 50.0}
        self.devs = devs or {"/": 1, "/app/state": 1}
        self.fail = {}
        self.now = 1_000_000.0
        self.scans = 0
        self.notify_ok = True
        self.fake_da = types.SimpleNamespace(
            sample=self._sample, evaluate=da.evaluate, format_message=da.format_message, SEVERITY=da.SEVERITY,
            top_consumers=self._scan)
        monkeypatch.setitem(__import__("sys").modules, "disk_alert", self.fake_da)

    def _sample(self, p):
        if self.fail.get(p):
            raise OSError("statvfs boom")
        pct = self.pct[p]
        return {"path": p, "total": 100e9, "used": pct * 1e9, "free": (100 - pct) * 1e9, "pct": pct}

    def _scan(self):
        self.scans += 1
        return [("/var/lib/docker", 5_000_000_000)]

    def call(self):
        async def get_redis(): return self.r
        async def notify_owner(cat, title, body, **kw):
            self.sent.append((cat, title, body, kw))
            return {"ok": True} if self.notify_ok else {"ok": False, "error": "bridge down"}
        ns = {"asyncio": asyncio, "time": types.SimpleNamespace(time=lambda: self.now), "get_redis": get_redis,
              "notify_owner": notify_owner, "print": lambda *a: self.logs.append(" ".join(map(str, a))),
              "os": types.SimpleNamespace(stat=lambda p: types.SimpleNamespace(st_dev=self.devs[p]))}
        exec(_block(), ns)
        n = len(self.sent)
        asyncio.run(ns["_run"]())
        return self.sent[n:]


@pytest.fixture()
def env(monkeypatch):
    return Env(monkeypatch)


def test_block_below_threshold_silent_and_no_scan(env):
    env.pct["/"] = 79.9
    assert env.call() == [] and env.scans == 0 and env.r.d == {}


def test_block_each_threshold_and_escalation_not_suppressed(env):
    got = []
    for pct, lvl in ((80, "warning"), (85, "high"), (90, "critical"), (95, "emergency")):
        env.pct["/"] = pct; env.now += 60          # seconds apart: far inside every cooldown
        out = env.call()
        assert len(out) == 1, (pct, out)
        assert out[0][3]["severity"] == da.SEVERITY[lvl]
        assert f"disk_{lvl}:/:escalate" == out[0][3]["alert_key"]
        got.append(lvl)
    assert got == ["warning", "high", "critical", "emergency"]
    assert env.scans == 4  # du only when an alert fired


def test_block_dedup_then_cooldown_expiry(env):
    env.pct["/"] = 82
    assert len(env.call()) == 1
    env.now += 3600; assert env.call() == []          # repeated check, same level
    env.now += 24 * 3600; out = env.call()            # cooldown over
    assert len(out) == 1 and out[0][3]["alert_key"].endswith(":alert")


def test_block_recovery_once_without_scan(env):
    env.pct["/"] = 91; env.call(); scans = env.scans
    env.pct["/"] = 60; env.now += 60
    out = env.call()
    assert len(out) == 1 and out[0][3]["alert_key"].endswith(":recovered") and env.scans == scans
    env.now += 60; assert env.call() == []


def test_block_same_device_paths_single_stream(env):
    env.pct.update({"/": 88, "/app/state": 88})
    out = env.call()
    assert len(out) == 1                              # /app/state shares the device with /
    assert set(env.r.d) == {"autospare:disk_alert:/:level", "autospare:disk_alert:/:sent_at"}


def test_block_distinct_device_gets_own_stream(monkeypatch):
    e = Env(monkeypatch, devs={"/": 1, "/app/state": 2})
    e.pct.update({"/": 81, "/app/state": 96})
    assert len(e.call()) == 2


def test_block_monitoring_failure_is_alerted_not_ok(env):
    env.fail["/"] = True
    out = env.call()
    assert len(out) == 1 and "ניטור הדיסק נכשל" in out[0][1] and out[0][3]["alert_key"].startswith("disk_monitor_error")
    assert env.r.d == {}                              # no "ok" state recorded


def test_block_failed_delivery_keeps_state_for_retry(env):
    env.pct["/"] = 86; env.notify_ok = False
    env.call()
    assert env.r.d == {}                              # not marked as sent
    env.notify_ok = True; env.now += 300
    assert len(env.call()) == 1


def test_block_exception_is_logged_and_does_not_raise(monkeypatch, env):
    class BoomRedis(FakeRedis):
        async def get(self, k): raise RuntimeError("redis down")
    env.r = BoomRedis(); env.pct["/"] = 90
    assert env.call() == []
    assert any("disk check error" in l for l in env.logs)


def test_sample_rejects_invalid_usage(monkeypatch):
    monkeypatch.setattr(da.shutil, "disk_usage", lambda p: types.SimpleNamespace(total=0, used=0, free=0))
    with pytest.raises(OSError):
        da.sample("/")
