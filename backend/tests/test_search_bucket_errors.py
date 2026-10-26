"""Search bucket safety (FIXES_TRACKER #69 §14, §16).

- one short session per bucket, statement limit set first, closed as soon as THAT bucket is done
- a failing / timed-out bucket surfaces its own error after its siblings finish; nothing leaks
- concurrent searches never leave a session open
- the strict vehicle clause resolves fitting part ids first (no correlated EXISTS walk)
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes import parts  # noqa: E402


class _Sessions:
    def __init__(self):
        self.open = 0
        self.max_open = 0
        self.events = []

    def factory(self):
        rec = self

        class _S:
            def __init__(self):
                self.sql = []

            async def execute(self, stmt, params=None):
                self.sql.append(str(stmt))

        @asynccontextmanager
        async def _ctx():
            s = _S()
            rec.open += 1
            rec.max_open = max(rec.max_open, rec.open)
            rec.events.append(("open", id(s)))
            try:
                yield s
            finally:
                rec.open -= 1
                rec.events.append(("close", id(s), list(s.sql)))
        return _ctx()


SPECS = [(["original"], False), (["oem"], False), (["aftermarket"], True)]


def test_one_session_per_bucket_limit_first_and_all_closed():
    rec = _Sessions()

    async def fetch(db, pt, ig):
        return pt[0]

    out = asyncio.run(parts._run_buckets(rec.factory, fetch, SPECS, timeout_ms=1234))
    assert out == ["original", "oem", "aftermarket"]
    closes = [e for e in rec.events if e[0] == "close"]
    assert len(closes) == 3 and rec.open == 0
    assert all(c[2] and c[2][0] == "SET LOCAL statement_timeout = '1234ms'" for c in closes)


def test_a_finished_bucket_closes_its_session_before_the_slow_one_ends():
    rec = _Sessions()
    order = []

    async def fetch(db, pt, ig):
        await asyncio.sleep(0.2 if pt[0] == "aftermarket" else 0.01)
        order.append(("done", pt[0], rec.open))
        return pt[0]

    asyncio.run(parts._run_buckets(rec.factory, fetch, SPECS, timeout_ms=1000))
    # when the slow bucket finishes, the two fast sessions are already closed
    assert ("done", "aftermarket", 1) in order


@pytest.mark.parametrize("exc", [RuntimeError("canceling statement due to statement timeout"),
                                 asyncio.TimeoutError()])
def test_error_or_timeout_surfaces_the_real_error_and_closes_everything(exc):
    rec = _Sessions()
    finished = []

    async def fetch(db, pt, ig):
        if pt[0] == "oem":
            raise exc
        await asyncio.sleep(0.05)
        finished.append(pt[0])
        return pt[0]

    with pytest.raises(type(exc)):
        asyncio.run(parts._run_buckets(rec.factory, fetch, SPECS, timeout_ms=1000))
    assert sorted(finished) == ["aftermarket", "original"]      # siblings were not cut mid-query
    assert rec.open == 0 and len([e for e in rec.events if e[0] == "close"]) == 3


def test_concurrent_searches_never_leave_a_session_open():
    rec = _Sessions()

    async def fetch(db, pt, ig):
        await asyncio.sleep(0.01)
        if pt[0] == "oem" and id(db) % 3 == 0:
            raise RuntimeError("boom")
        return pt[0]

    async def many():
        return await asyncio.gather(*(parts._run_buckets(rec.factory, fetch, SPECS, timeout_ms=1000)
                                      for _ in range(25)), return_exceptions=True)

    asyncio.run(many())
    assert rec.open == 0
    assert len([e for e in rec.events if e[0] == "open"]) == 75 == len([e for e in rec.events if e[0] == "close"])


# ── strict clause: fitting ids first ──────────────────────────────────────────
class _IdDb:
    def __init__(self, ids=None, fail=False):
        self.ids, self.fail, self.nested = ids or [], fail, 0

    @asynccontextmanager
    async def _nested(self):
        self.nested += 1
        yield

    def begin_nested(self):
        return self._nested()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "statement_timeout" in sql:                       # vehicle_model_match.bounded
            if "set_config" in sql:
                self.limits = getattr(self, "limits", []) + [(params or {}).get("v")]

            class _S:
                def scalar(_s):
                    return "900s"
            return _S()
        if "car_brands" in sql:
            raise RuntimeError("no brand lookup in this test")
        if "SELECT DISTINCT pvf.part_id" in sql:
            if self.fail:
                raise RuntimeError("statement timeout")
            ids = self.ids

            class R:
                def scalars(_s):
                    class X:
                        def all(_x):
                            return ids
                    return X()
            return R()
        raise AssertionError(sql)


@pytest.fixture
def _allowed(monkeypatch):
    import vehicle_model_match as vmm

    async def _a(_db, _m, models):
        return ["land cruiser prado"]
    monkeypatch.setattr(vmm, "allowed_fitment_models", _a)


def test_strict_clause_resolves_fitting_ids_first(_allowed):
    db, params = _IdDb(ids=["11111111-1111-1111-1111-111111111111"]), {}
    clause = asyncio.run(parts._build_strict_vehicle_match_clause(
        db=db, params=params, vehicle_manufacturer="Toyota", vehicle_model="Land Cruiser Prado",
        vehicle_year=2015, prefix="t", include_json=False))
    assert clause == "pc.id = ANY(CAST(:t_fit_ids AS uuid[]))"
    assert params["t_fit_ids"] == ["11111111-1111-1111-1111-111111111111"] and db.nested == 1
    assert db.limits == [f"{parts.SEARCH_STATEMENT_TIMEOUT_MS}ms", "900s"]   # set, then restored


def test_no_fitting_ids_means_no_results_not_an_unbounded_scan(_allowed):
    params = {}
    clause = asyncio.run(parts._build_strict_vehicle_match_clause(
        db=_IdDb(ids=[]), params=params, vehicle_manufacturer="Toyota", vehicle_model="Land Cruiser Prado",
        vehicle_year=2015, prefix="t", include_json=False))
    assert clause == "FALSE"


def test_id_lookup_error_falls_back_to_exists_inside_a_savepoint(_allowed):
    db = _IdDb(fail=True)
    clause = asyncio.run(parts._build_strict_vehicle_match_clause(
        db=db, params={}, vehicle_manufacturer="Toyota", vehicle_model="Land Cruiser Prado",
        vehicle_year=2015, prefix="t", include_json=False))
    assert clause.startswith("EXISTS (") and db.nested == 1      # the failure stayed in the savepoint


def test_too_many_fitting_ids_keeps_the_exists_form(_allowed, monkeypatch):
    monkeypatch.setattr(parts, "FIT_ID_RESOLVE_CAP", 2)
    clause = asyncio.run(parts._build_strict_vehicle_match_clause(
        db=_IdDb(ids=["a", "b", "c"]), params={}, vehicle_manufacturer="Toyota",
        vehicle_model="Land Cruiser Prado", vehicle_year=2015, prefix="t", include_json=False))
    assert clause.startswith("EXISTS (")


def test_search_uses_the_bucket_runner():
    import inspect
    src = inspect.getsource(parts.search_parts)
    assert "await _run_buckets(" in src and "async_session_factory() as db_original" not in src


def test_bounded_sets_then_restores_and_never_leaks_on_error():
    import vehicle_model_match as vmm
    db = _IdDb()

    async def ok():
        async with vmm.bounded(db, 1234):
            pass

    async def boom():
        async with vmm.bounded(db, 1234):
            raise RuntimeError("canceling statement due to statement timeout")
    asyncio.run(ok())
    assert db.limits == ["1234ms", "900s"] and db.nested == 1
    with pytest.raises(RuntimeError):
        asyncio.run(boom())
    # on error the restore is NOT executed: the savepoint rollback reverts set_config(..., true)
    assert db.limits == ["1234ms", "900s", "1234ms"] and db.nested == 2


def test_fitment_lookups_are_bounded():
    import inspect
    import vehicle_model_match as vmm
    assert "async with bounded(db, STATEMENT_LIMIT_MS)" in inspect.getsource(vmm.allowed_fitment_models)
    assert "_vmm.bounded(db, SEARCH_STATEMENT_TIMEOUT_MS)" in inspect.getsource(parts._build_strict_vehicle_match_clause)
