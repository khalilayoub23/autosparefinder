"""Purge run record (2026-11-03): summary line, captured output, exit codes, fail-closed unchanged."""
import asyncio
import json
import sys

import pytest

from tests.test_meili_purge_inactive import FakeMeili, _ids, purge_mod  # noqa: F401
import purge_run_log as prl


def test_stats_success(purge_mod):
    m = FakeMeili(_ids(30))
    st = purge_mod.purge_with_stats(_ids(100), req=m, sleep=lambda s: None)
    assert (st["checked"], st["present"], st["submitted"], st["skipped"], st["errors"]) == (100, 30, 30, 0, 0)
    assert [c["kind"] for c in st["chunks"]] == ["probe", "delete"]


def test_failed_probe_skips_chunk_never_blind_deletes(purge_mod):
    m = FakeMeili(_ids(30), fail_probe=99)
    st = purge_mod.purge_with_stats(_ids(100), req=m, sleep=lambda s: None)
    assert m.deletes == []
    assert (st["probe_failed"], st["skipped"], st["submitted"]) == (100, 100, 0)
    assert st["chunks"][0]["ok"] is False and "OSError" in st["chunks"][0]["error"]
    assert purge_mod._status(st["errors"], 0, 0, 100, st["probe_failed"]) == "failed"


def test_partial_when_delete_fails(purge_mod, monkeypatch):
    monkeypatch.setattr(purge_mod, "BATCH", 10)
    m = FakeMeili(_ids(30), fail_delete=1)
    st = purge_mod.purge_with_stats(_ids(30), req=m, sleep=lambda s: None)
    assert (st["submitted"], st["delete_errors"], st["skipped"]) == (20, 1, 10)
    assert purge_mod._status(st["errors"], 20, 30, 30, 0) == "partial"


def test_summary_line_bounded_and_parseable(purge_mod, capsys):
    purge_mod.emit_summary({"status": "completed", "chunks": [{"n": i} for i in range(80)]})
    line = capsys.readouterr().out.strip()
    parsed = prl.parse_summary(line, "meili_purge")
    assert len(parsed["chunks"]) == 50 and parsed["chunks_truncated"] == 30


def test_run_captured_keeps_rc_and_summary(tmp_path):
    code = ("import sys,json;print('noise');"
            "print('[meili_purge_summary] '+json.dumps({'status':'completed','checked':5,'submitted':0}));"
            "sys.exit(0)")
    rc, rec = asyncio.run(prl.run_captured([sys.executable, "-c", code], "meili_purge", tmp_path))
    assert rc == 0 and rec["status"] == "completed" and rec["summary"]["checked"] == 5
    lines = (tmp_path / "logs" / "meili_purge_runs.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["exit_code"] == 0
    assert "noise" in (tmp_path / "logs" / "meili_purge.log").read_text()


def test_nonzero_exit_never_completed(tmp_path):
    code = ("import sys,json;print('[meili_purge_summary] '+json.dumps({'status':'completed'}));sys.exit(1)")
    rc, rec = asyncio.run(prl.run_captured([sys.executable, "-c", code], "meili_purge", tmp_path))
    assert rc == 1 and rec["status"] == "failed"


def test_crash_without_summary(tmp_path):
    rc, rec = asyncio.run(prl.run_captured([sys.executable, "-c", "raise SystemExit(3)"], "meili_purge", tmp_path))
    assert rc == 3 and rec["status"] == "failed" and rec["summary"] is None


def test_jsonl_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(prl, "MAX_JSONL_BYTES", 200)
    for _ in range(10):
        prl.record_run("meili_purge", 0, 1.0, 2.0, "x", tmp_path)
    logs = {p.name for p in (tmp_path / "logs").iterdir()}
    assert "meili_purge_runs.jsonl.1" in logs


class _FakeConn:
    def __init__(self, inactive, active=7): self._i, self._a = inactive, active
    async def execute(self, *a): return None
    async def fetchval(self, *a): return self._a
    async def fetch(self, *a): return [(i,) for i in self._i]
    async def close(self): return None


def _drive(purge_mod, monkeypatch, capsys, meili, inactive):
    async def connect(*a, **k): return _FakeConn(inactive)
    monkeypatch.setattr(purge_mod.asyncpg, "connect", connect, raising=False)
    monkeypatch.setattr(purge_mod, "_req", meili)
    monkeypatch.setattr(purge_mod, "_doc_count", lambda: len(meili.docs))
    monkeypatch.setattr(purge_mod.time, "sleep", lambda s: None)
    # purge_with_stats captured `req=_req` as a default at def time; route it through the fake
    orig = purge_mod.purge_with_stats
    monkeypatch.setattr(purge_mod, "purge_with_stats", lambda ids: orig(ids, req=meili, sleep=lambda s: None))
    rc = asyncio.run(purge_mod.main())
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("[meili_purge_summary] ")][-1]
    return rc, json.loads(line.split(" ", 1)[1])


def test_main_summary_completed_and_nothing_to_delete(purge_mod, monkeypatch, capsys):
    rc, s = _drive(purge_mod, monkeypatch, capsys, FakeMeili(_ids(3, "a")), _ids(50))
    assert rc == 0 and s["status"] == "completed" and s["checked"] == 50 and s["present"] == 0 \
        and s["submitted"] == 0 and s["exit_code"] == 0 and s["duration_s"] >= 0


def test_main_summary_failed_probe_exit1(purge_mod, monkeypatch, capsys):
    m = FakeMeili(_ids(5), fail_probe=99)
    rc, s = _drive(purge_mod, monkeypatch, capsys, m, _ids(50))
    assert rc == 1 and s["status"] == "failed" and m.deletes == [] and s["skipped"] == 50


def test_main_summary_written_on_crash(purge_mod, monkeypatch, capsys):
    async def boom(*a, **k): raise OSError("db down")
    monkeypatch.setattr(purge_mod.asyncpg, "connect", boom, raising=False)
    monkeypatch.setattr(purge_mod, "_doc_count", lambda: 1)
    rc = asyncio.run(purge_mod.main())
    out = capsys.readouterr().out
    assert rc == 1 and '"status":"failed"' in out and "db down" in out
