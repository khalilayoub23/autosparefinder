"""maintenance/fitment_impact_check.py must never hold one transaction across models
(FIXES_TRACKER #69 §10: a 70-minute transaction queued an ALTER and, behind it, all fitment I/O)."""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BACKEND, "maintenance"))
sys.path.insert(0, BACKEND)
import fitment_impact_check as fic  # noqa: E402


class _Tracker:
    def __init__(self):
        self.open = 0
        self.max_open = 0
        self.opened = 0
        self.closed = 0

    def factory(self):
        tracker = self

        @asynccontextmanager
        async def _session():
            tracker.open += 1
            tracker.opened += 1
            tracker.max_open = max(tracker.max_open, tracker.open)
            try:
                yield object()
            finally:
                tracker.open -= 1
                tracker.closed += 1
        return _session()


def test_one_short_session_per_model_and_every_session_closed_even_on_error(monkeypatch):
    t = _Tracker()
    seen = []

    async def _eval(db, make, model):
        seen.append(model)
        if model == "boom":
            raise RuntimeError("statement timeout")
        return {"make": make, "model": model}

    monkeypatch.setattr(fic, "evaluate_model", _eval)
    stats = asyncio.run(fic.run([("toyota", "corolla"), ("toyota", "boom"), ("kia", "rio")],
                                t.factory, None, sleep_s=0, log=lambda *_: None))
    assert seen == ["corolla", "boom", "rio"]                  # an error does not stop the run
    assert t.opened == t.closed == 3 and t.max_open == 1       # never two at once, all closed
    assert stats["errors"] == 1 and stats["sessions_opened"] == stats["sessions_closed"] == 3


def test_interruption_closes_the_open_session(monkeypatch):
    t = _Tracker()

    async def _eval(db, make, model):
        raise asyncio.CancelledError()

    monkeypatch.setattr(fic, "evaluate_model", _eval)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fic.run([("toyota", "corolla")], t.factory, None, sleep_s=0, log=lambda *_: None))
    assert t.opened == t.closed == 1 and t.open == 0


def test_each_model_transaction_is_read_only_and_time_bounded():
    import inspect
    src = inspect.getsource(fic.evaluate_model)
    assert src.index('SET TRANSACTION READ ONLY') < src.index("part_vehicle_fitment")
    assert "SET LOCAL statement_timeout" in src
    run_src = inspect.getsource(fic.run)
    assert "async with session_factory() as db:" in run_src and "for i, (make, model)" in run_src
    assert run_src.index("for i, (make, model)") < run_src.index("async with session_factory()")
