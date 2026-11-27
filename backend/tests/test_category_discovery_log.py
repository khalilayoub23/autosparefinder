"""category_discovery writes its own persistent log (owner, 2026-11-27).

Incident: on 2026-11-27 category_discovery ran 4.5 h (usually 2 h, its own budget) and was
cancelled by the night pipeline's 270-min deadline. Its stdout was lost with the 09:03 backend
recreate, so there was no record of where the time went. The log is on the worker_state volume and
brackets every search with start/done lines, so a hang is visible as a start without a done.
"""
import asyncio
import logging

import pytest

import catalog_scraper as cs


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    p = tmp_path / "logs" / "category_discovery.log"
    monkeypatch.setattr(cs, "CATEGORY_DISCOVERY_LOG", str(p))
    monkeypatch.setattr(cs, "_cd_file_logger", None)
    yield p
    lg = logging.getLogger("category_discovery_file")
    for h in [h for h in lg.handlers if getattr(h, "baseFilename", None)]:
        lg.removeHandler(h)
        h.close()


def test_cd_log_writes_an_israel_time_line(log_path):
    cs._cd_log("hello")
    line = log_path.read_text(encoding="utf-8").strip()
    assert line.endswith(" hello")
    assert ("IST" in line) or ("IDT" in line)


def test_cd_log_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(cs, "CATEGORY_DISCOVERY_LOG", str(blocker / "sub" / "x.log"))  # parent is a file
    monkeypatch.setattr(cs, "_cd_file_logger", None)
    cs._cd_log("still fine")


class _Res:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "SELECT DISTINCT manufacturer" in sql:
            return _Res(rows=[("Kia",)])
        if "COUNT(*)" in sql:
            return _Res(scalar=0)
        return _Res(rows=[])


class _Lock:
    async def release(self):
        pass


def _stub_job(monkeypatch, search):
    async def _redis():
        return None

    async def _lock(*a, **k):
        return _Lock()

    async def _noop(*a, **k):
        return None

    import BACKEND_AUTH_SECURITY
    import distributed_lock
    monkeypatch.setattr(BACKEND_AUTH_SECURITY, "get_redis", _redis)
    monkeypatch.setattr(distributed_lock, "acquire_lock", _lock)
    monkeypatch.setattr(cs, "CATEGORY_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(cs, "scraper_session_factory", lambda: _Session())
    monkeypatch.setattr(cs, "job_registry_start", _noop)
    monkeypatch.setattr(cs, "job_registry_finish", _noop)
    monkeypatch.setattr(cs, "DISCOVERY_CATEGORIES", [("בלמים", ["brake pad"])])
    monkeypatch.setattr(cs, "SCRAPE_REQUEST_DELAY", 0)
    monkeypatch.setattr(cs, "_search_category_query_multisource", search)


def test_a_completed_run_is_bracketed_in_the_log(log_path, monkeypatch):
    async def search(*a, **k):
        return []
    _stub_job(monkeypatch, search)
    rep = asyncio.run(cs._run_category_discovery())
    assert rep["status"] == "completed"
    text = log_path.read_text(encoding="utf-8")
    assert "start: 1 manufacturers" in text
    assert "manufacturer 1/1 Kia" in text
    assert "search start: Kia / brakes / 'brake pad Kia'" in text
    assert "search done: 'brake pad Kia' -> 0 candidates" in text
    assert "end: status=completed" in text


def test_a_hung_search_cancelled_by_the_deadline_leaves_its_trace(log_path, monkeypatch):
    async def search(*a, **k):
        await asyncio.sleep(3600)          # the hang
    _stub_job(monkeypatch, search)

    async def run():
        t = asyncio.create_task(cs._run_category_discovery())
        await asyncio.sleep(1.5)
        t.cancel()                          # what the night pipeline's deadline does
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(run())
    text = log_path.read_text(encoding="utf-8")
    assert "search start: Kia / brakes / 'brake pad Kia'" in text
    assert "search done" not in text
    assert "end: status=dead" in text and "cancelled (night-pipeline deadline or shutdown)" in text
