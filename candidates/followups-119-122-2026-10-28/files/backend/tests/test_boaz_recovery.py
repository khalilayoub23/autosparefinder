"""
Script: tests/test_boaz_recovery.py
Purpose: #122 acceptance tests - BOAZ stale-task recovery (lease + phase + fencing). The logic cases run in memory;
         the atomicity / concurrency cases run against a REAL PostgreSQL only in an isolated database
         (tests/isolated_db_guard.py: RUN_DB_MUTATION_TESTS=1, ENVIRONMENT=test, non-production host) and skip otherwise.
Process: a "crash" is simulated by an exception that run_once does not catch (BaseException) - the task is left
         exactly as a dead worker leaves it: in_progress with a lease in the phase it died in.
Data Imported/Modified: in-memory todos; agent_todos rows in the disposable catalog database only.
Last Updated: 2026-10-28
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import boaz_tasks as bt
import isolated_db_guard as guard


class Crash(BaseException):
    """A worker death: run_once does not catch it."""


def _todo(kind="supplier_handoff", agent="BOAZ", status="not_started", **art):
    return {"id": str(uuid.uuid4()), "title": f"t {kind}", "status": status, "priority": "high",
            "assigned_to_agent": agent, "category": kind, "artifacts": {"kind": kind, **art}, "metrics": {}}


def _expire(t):
    t["metrics"]["boaz"]["lease"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def _events(t):
    return [h["event"] for h in t["metrics"]["boaz"].get("history", [])]


@pytest.fixture
def side_effects(monkeypatch):
    """Two test kinds: 'effect' (NOT retry-safe) and the real retry-safe 'supplier_handoff'. Each counts its runs."""
    calls = {"effect": 0, "supplier_handoff": 0, "crash_after": set()}

    async def effect(task, deps):
        calls["effect"] += 1                              # the side effect (e.g. an order) happens here
        if "effect" in calls["crash_after"]:
            raise Crash()
        return {"done": True, "note": "effect done", "result": {"n": calls["effect"]}}

    async def handoff(task, deps):
        calls["supplier_handoff"] += 1
        if "supplier_handoff" in calls["crash_after"]:
            raise Crash()
        return {"done": True, "note": "handoff checked", "result": {}}
    monkeypatch.setitem(bt.HANDLERS, "effect", effect)
    monkeypatch.setitem(bt.HANDLERS, "supplier_handoff", handoff)
    return calls


async def test_healthy_in_progress_task_is_not_reclaimed(side_effects):
    t = _todo("effect")
    repo = bt.InMemoryTaskRepo([t])
    await repo.claim(["BOAZ"], 5)                      # a live worker holds it (lease not expired)
    out = await bt.run_once(repo, {})
    assert out["recovered"] == 0 and out["claimed"] == 0 and t["status"] == "in_progress" and side_effects["effect"] == 0


async def test_crash_before_processing_is_requeued_and_completed(side_effects):
    t = _todo("effect")
    repo = bt.InMemoryTaskRepo([t])
    await repo.claim(["BOAZ"], 5)                      # died right after the claim (phase 'claimed')
    _expire(t)
    out = await bt.run_once(repo, {})
    assert out["recovered"] == 1 and out["requeued"] == 1 and out["completed"] == 1
    assert t["status"] == "completed" and side_effects["effect"] == 1
    assert _events(t)[:2] == ["recovered_stale", "requeued"] and _events(t)[-1] == "completed"
    assert "lease" not in t["metrics"]["boaz"]


async def test_crash_after_side_effect_is_never_repeated(side_effects):
    t = _todo("effect")
    repo = bt.InMemoryTaskRepo([t])
    side_effects["crash_after"].add("effect")
    with pytest.raises(Crash):
        await bt.run_once(repo, {})
    assert t["metrics"]["boaz"]["lease"]["phase"] == "executing" and side_effects["effect"] == 1
    side_effects["crash_after"].clear()
    _expire(t)
    out = await bt.run_once(repo, {})
    assert out["reconcile"] == 1 and side_effects["effect"] == 1          # NOT run a second time
    b = t["metrics"]["boaz"]
    assert t["status"] == "in_progress" and b["reconciliation_required"] and "lease" not in b
    out = await bt.run_once(repo, {})                                     # escalated: never picked up again
    assert out["recovered"] == 0 and out["claimed"] == 0 and side_effects["effect"] == 1


async def test_retry_safe_handler_is_rerun_after_a_crash_inside_it(side_effects):
    t = _todo("supplier_handoff", supplier_id="s1")
    repo = bt.InMemoryTaskRepo([t])
    side_effects["crash_after"].add("supplier_handoff")
    with pytest.raises(Crash):
        await bt.run_once(repo, {})
    side_effects["crash_after"].clear()
    _expire(t)
    out = await bt.run_once(repo, {})
    assert out["requeued"] == 1 and out["completed"] == 1 and side_effects["supplier_handoff"] == 2
    assert "supplier_handoff" in bt.RETRY_SAFE and "bulk_scan" not in bt.RETRY_SAFE


async def test_crash_after_processing_before_recording_applies_the_recorded_outcome(side_effects, monkeypatch):
    t = _todo("effect")
    repo = bt.InMemoryTaskRepo([t])
    real_finish = repo.finish

    async def dying_finish(*a, **k):
        raise Crash()
    monkeypatch.setattr(repo, "finish", dying_finish)
    with pytest.raises(Crash):
        await bt.run_once(repo, {})
    lease = t["metrics"]["boaz"]["lease"]
    assert lease["phase"] == "handled" and lease["outcome"]["done"] is True and t["status"] == "in_progress"
    monkeypatch.setattr(repo, "finish", real_finish)
    _expire(t)
    out = await bt.run_once(repo, {})
    assert out["applied"] == 1 and out["completed"] == 1 and t["status"] == "completed"
    assert side_effects["effect"] == 1                                    # outcome applied, handler not re-run


async def test_retry_limit_then_terminal_escalation(side_effects):
    t = _todo("supplier_handoff", supplier_id="s1")
    repo = bt.InMemoryTaskRepo([t])
    side_effects["crash_after"].add("supplier_handoff")
    for _ in range(bt.MAX_RECOVERIES + 1):
        with pytest.raises(Crash):
            await bt.run_once(repo, {})
        _expire(t)
    side_effects["crash_after"].clear()
    out = await bt.run_once(repo, {})
    b = t["metrics"]["boaz"]
    assert out["reconcile"] == 1 and b["recoveries"] == bt.MAX_RECOVERIES + 1 and b["reconciliation_required"]
    assert t["status"] == "in_progress" and "lease" not in b
    assert side_effects["supplier_handoff"] == bt.MAX_RECOVERIES + 1      # bounded: no run after the limit


async def test_other_agents_escalated_and_legacy_tasks_are_untouched(side_effects):
    nir = _todo("effect", agent="NIR", status="in_progress")
    nir["metrics"] = {"boaz": {"lease": {"token": "x", "phase": "executing",
                                         "expires_at": "2000-01-01T00:00:00+00:00"}}}
    escalated = _todo("effect", status="in_progress")
    escalated["metrics"] = {"boaz": {"escalated": "2026-10-01T00:00:00+00:00"}}
    legacy = _todo("bulk_scan", status="in_progress")                    # in_progress from before #122 (no lease)
    before = json.dumps([nir, escalated, legacy], sort_keys=True)
    out = await bt.run_once(bt.InMemoryTaskRepo([nir, escalated, legacy]), {})
    assert out["recovered"] == 0 and json.dumps([nir, escalated, legacy], sort_keys=True) == before


async def test_a_late_worker_cannot_overwrite_the_new_owner(side_effects):
    t = _todo("effect")
    repo = bt.InMemoryTaskRepo([t])
    [claimed] = await repo.claim(["BOAZ"], 5)
    old_token = t["metrics"]["boaz"]["lease"]["token"]
    _expire(t)
    await bt.recover_stale(repo)                                          # new owner (requeued)
    assert await repo.finish(t["id"], status="completed", note="late", metrics={}, token=old_token) is False
    assert await repo.set_phase(t["id"], old_token, "handled") is False
    assert t["status"] == "not_started"


async def test_lease_outlives_the_handler_bound(side_effects, monkeypatch):
    assert bt.LEASE_S > 2 * bt.HANDLER_TIMEOUT_S
    t = _todo("effect")

    async def slow(task, deps):
        side_effects["effect"] += 1
        await asyncio.sleep(5)
    monkeypatch.setitem(bt.HANDLERS, "effect", slow)
    out = await bt.run_once(bt.InMemoryTaskRepo([t]), {}, handler_timeout_s=0.05)
    assert out["reconcile"] == 1 and t["metrics"]["boaz"]["reconciliation_required"]   # not retry-safe: unknown effect


# ── REAL PostgreSQL (isolated DB only) ──────────────────────────────────────────────────────────────────────
_REFUSAL = guard.url_refusal(os.getenv("DATABASE_URL"))
pg = pytest.mark.skipif(bool(_REFUSAL), reason=f"isolated DB required: {_REFUSAL}")


@pytest.fixture
async def fresh_pool():
    """Each test runs in its own event loop: drop pooled connections opened by an earlier loop (before and after)."""
    from BACKEND_DATABASE_MODELS import engine
    await engine.dispose()
    yield
    await engine.dispose()


async def _pg_insert(db, kind="effect", agent="BOAZ"):
    from sqlalchemy import text
    tid = str(uuid.uuid4())
    await db.execute(text("INSERT INTO agent_todos (id, title, status, priority, assigned_to_agent, category, artifacts) "
                          "VALUES (CAST(:i AS uuid), :t, 'not_started', 'high', :a, :k, CAST(:art AS jsonb))"),
                     {"i": tid, "t": f"pg {kind}", "a": agent, "k": kind, "art": json.dumps({"kind": kind})})
    await db.commit()
    return tid


async def _pg_row(db, tid):
    from sqlalchemy import text
    r = (await db.execute(text("SELECT status, metrics FROM agent_todos WHERE id = CAST(:i AS uuid)"), {"i": tid})).first()
    return r[0], (r[1] if isinstance(r[1], dict) else json.loads(r[1] or "{}"))


@pg
async def test_pg_two_concurrent_recoverers_never_take_the_same_task(side_effects, fresh_pool):
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory
    async with async_session_factory() as db:
        assert await guard.live_refusal(db) is None                       # check 4: FAIL (never skip) on production
        ids = [await _pg_insert(db) for _ in range(6)]
        other = await _pg_insert(db, agent="NIR")
    repo = bt.PgTaskRepo()
    claimed = await repo.claim(sorted(bt.aliases("BOAZ")), 50)
    mine = [c for c in claimed if c["id"] in ids]
    assert len(mine) == 6 and all(c["metrics"]["boaz"]["lease"]["phase"] == "claimed" for c in mine)
    healthy = await asyncio.gather(*[repo.recover_stale(sorted(bt.aliases("BOAZ")), 50) for _ in range(2)])
    assert not [t for batch in healthy for t, _ in batch if t["id"] in ids]                    # not expired: untouched
    async with async_session_factory() as db:
        await db.execute(text("UPDATE agent_todos SET metrics = jsonb_set(metrics, '{boaz,lease,expires_at}', "
                              "to_jsonb((now() - interval '1 minute')::text)) WHERE id = ANY(CAST(:ids AS uuid[]))"),
                         {"ids": ids})
        await db.commit()
    a, b = await asyncio.gather(repo.recover_stale(sorted(bt.aliases("BOAZ")), 50),
                                repo.recover_stale(sorted(bt.aliases("BOAZ")), 50))
    got_a = {t["id"] for t, _ in a if t["id"] in ids}
    got_b = {t["id"] for t, _ in b if t["id"] in ids}
    assert got_a | got_b == set(ids) and not (got_a & got_b)                                  # each exactly once
    async with async_session_factory() as db:
        st, m = await _pg_row(db, other)
    assert st == "not_started" and "boaz" not in m                                             # NIR untouched


@pg
async def test_pg_crash_after_side_effect_is_reconciled_not_repeated(side_effects, fresh_pool):
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory
    async with async_session_factory() as db:
        assert await guard.live_refusal(db) is None
        tid = await _pg_insert(db, kind="effect")
    repo = bt.PgTaskRepo()
    side_effects["crash_after"].add("effect")
    with pytest.raises(Crash):
        while True:                                     # other BOAZ rows may exist in the disposable DB
            await bt.run_once(repo, {})
    side_effects["crash_after"].clear()
    async with async_session_factory() as db:
        st, m = await _pg_row(db, tid)
        assert st == "in_progress" and m["boaz"]["lease"]["phase"] == "executing"
        await db.execute(text("UPDATE agent_todos SET metrics = jsonb_set(metrics, '{boaz,lease,expires_at}', "
                              "to_jsonb((now() - interval '1 minute')::text)) WHERE id = CAST(:i AS uuid)"), {"i": tid})
        await db.commit()
    n_before = side_effects["effect"]
    await bt.run_once(repo, {})
    async with async_session_factory() as db:
        st, m = await _pg_row(db, tid)
    assert side_effects["effect"] == n_before and st == "in_progress"
    assert m["boaz"]["reconciliation_required"] and "lease" not in m["boaz"]
    assert [h["event"] for h in m["boaz"]["history"]] == ["recovered_stale", "reconciliation_required"]
