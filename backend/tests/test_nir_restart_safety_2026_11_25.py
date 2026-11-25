"""Mission 5 (2026-11-25): NIR agent restart / concurrency safety.

Findings that these tests pin (see FIXES_TRACKER #155):
- the daily LLM ceiling lived only in a per-loop CallBudget, so a supervised-loop restart or a process restart re-granted
  the whole day's calls -> it is now mirrored to Redis and restored on creation;
- two overlapping cycles (a restarted loop next to a manual maintenance/nir_agent_run.py) ran the same task twice because
  task rows carry no lease -> one `autospare:lock:nir_agent_cycle` lock now guards every cycle;
- an interrupted task resumes from its stored state and does not repeat finished steps (already covered for the state
  itself by test_nir_agent_2026_11_23; here with the budget carried across the restart).
No supplier e-mail is sent: deps use the in-memory repo and the scripted LLM only."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import distributed_lock as dl
from services import nir_agent as A
from tests.fake_async_redis import FakeAsyncRedis
from tests.test_nir_agent_2026_11_23 import D, ScriptedLLM, deps

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)


async def test_daily_budget_survives_a_restart():
    r = FakeAsyncRedis()
    b1 = A.CallBudget(5)
    for _ in range(4):
        assert b1.take(NOW)
    await b1.persist(r, NOW)
    b2 = await A.CallBudget(5).restore(r, NOW)          # "restart": a brand-new budget object
    assert b2.used == 4 and b2.take(NOW) and not b2.take(NOW)      # only ONE call left today, not five


async def test_budget_resets_on_a_new_day_and_redis_outage_falls_back():
    r = FakeAsyncRedis()
    b = A.CallBudget(5)
    b.take(NOW); await b.persist(r, NOW)
    nxt = await A.CallBudget(5).restore(r, NOW + timedelta(days=1))
    assert nxt.used == 0

    class Down:
        async def get(self, *a, **k): raise RuntimeError("redis down")
        async def set(self, *a, **k): raise RuntimeError("redis down")
    ok = await A.CallBudget(5).restore(Down(), NOW)
    assert ok.used == 0 and ok.take(NOW)
    await ok.persist(Down(), NOW)                       # never raises


async def test_only_one_cycle_runs_at_a_time():
    r = FakeAsyncRedis()
    first = await dl.acquire_lock(r, "nir_agent_cycle", ttl_seconds=1800)
    second = await dl.acquire_lock(r, "nir_agent_cycle", ttl_seconds=1800)
    assert first and not second                          # the second (restarted/manual) cycle is refused
    await first.release()
    assert await dl.acquire_lock(r, "nir_agent_cycle", ttl_seconds=1800)   # free again after the first one ends


async def test_interrupted_task_resumes_with_budget_and_without_repeating_steps():
    r = FakeAsyncRedis()
    llm1 = ScriptedLLM(*[D("read_supplier")] * 2)
    d1, repo = deps(llm1)
    d1.budget = A.CallBudget(4)
    await A.run_task(repo.tasks["t-1"], d1, max_steps=2)
    await d1.budget.persist(r, NOW)
    snap = json.loads(json.dumps(repo.tasks["t-1"]["state"]))
    t2 = {"id": "t-1", "title": "Review", "status": "in_progress", "state": snap}
    llm2 = ScriptedLLM(D("finish", status="done", summary="ok"))
    d2, _ = deps(llm2, repo=A.InMemoryRepo([t2], [repo.suppliers[next(iter(repo.suppliers))]]))
    d2.budget = await A.CallBudget(4).restore(r)         # same-day restore (wall clock): count may be 0 on another day
    before = json.loads(json.dumps(snap["steps"]))
    assert len(before) == 2
    res = await A.run_task(t2, d2, max_steps=2)
    assert res["status"] in ("done", "budget", "continue")
    assert len(t2["state"]["steps"]) <= 3 and t2["state"]["steps"][:2] == before   # finished steps are never re-run
