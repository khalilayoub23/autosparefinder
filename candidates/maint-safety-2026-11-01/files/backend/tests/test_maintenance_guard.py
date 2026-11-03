"""#119 safety controller (maintenance_guard.py) — stop conditions, write caps, recovery, locking (2026-11-01).

Isolated database + sandbox Redis only (tests/isolated_db_guard.py). Every test builds its own rows inside a
rolled-back transaction; the owner notification is a recorder (never a real message); load / CPU probes are fakes.

Every stop test proves the SAME thing from the database: the rows after the stop boundary are unchanged
(`base_of`, `fit_rows`, `cv`), not merely that a status string says "stopped".
"""
import asyncio
import json
import os
import sys

import pytest
import sqlalchemy as sa

import db_update_agent as dua
import maintenance_guard as mg
import price_policy as pp
from test_maintenance_policy_alignment import (  # noqa: F401  (db is the shared isolated-DB fixture)
    base_of, cv, db, fit_rows, supplier)
from test_maintenance_policy_alignment import offer as _offer, part as _part, pvf as _pvf
import maint_guard_support as support


# A guard stop ROLLS BACK the interrupted batch (that is its job), so the fixture rows must be committed first
# (the shared `db` fixture joins an outer transaction in savepoint mode: commit() releases a savepoint only).
async def part(*a, **k):
    pid = await _part(*a, **k)
    await a[0].commit()
    return pid


async def offer(*a, **k):
    out = await _offer(*a, **k)
    await a[0].commit()
    return out


async def pvf(*a, **k):
    out = await _pvf(*a, **k)
    await a[0].commit()
    return out

PRICE = ("fix_base_prices", "normalize_base_price")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BASE_PRICE_TASK_BATCH", "2")
    monkeypatch.setenv("NAME_FITMENT_BATCH", "2")
    monkeypatch.setenv("FITMENT_MERGE_BATCH", "2")
    return support.neutral_probes(monkeypatch)            # the notice recorder


@pytest.fixture(autouse=True)
async def quiet_catalog(db):
    """Cursor-mode runs walk every active part: park rows other suites left committed in the sandbox (rolled back)."""
    await db.execute(sa.text("UPDATE parts_catalog SET is_active = FALSE WHERE sku NOT LIKE 'MPA-%'"))
    await db.commit()


async def setting(db, key):
    return (await db.execute(sa.text("SELECT value FROM system_settings WHERE key = :k"), {"k": key})).scalar()


async def offer_parts(db, n, base=1.0):
    ids = []
    for _ in range(n):
        pid = await part(db, base=base)
        await offer(db, pid, ils=100)
        ids.append(pid)
    return sorted(ids)


async def bases(db, ids):
    return [await base_of(db, i) for i in ids]


class Crash(BaseException):
    """Stands in for a killed process: bypasses every `except Exception`."""


# ── kill switch ───────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("scope", ["all", "price", "fix_base_prices"])
async def test_kill_switch_active_before_the_run_writes_nothing(db, scope):
    ids = await offer_parts(db, 3)
    await mg.trip(db, scope, "test")
    r = await dua.fix_base_prices(db)                                    # cursor mode (lock path)
    assert r["status"] == "stopped" and r["stop"]["scope"] == scope and r["stop"]["latched"] is True
    assert await bases(db, ids) == [1.0] * 3
    assert await setting(db, "cursor__fix_base_prices") in (None, dua._ZERO_UUID)


@pytest.mark.parametrize("task,fn", [
    ("normalize_base_price", lambda: dua.normalize_base_price),
    ("merge_catalog_fitment_from_part_vehicle_fitment", lambda: dua.merge_catalog_fitment_from_part_vehicle_fitment),
    ("backfill_bmw_fitment_from_name_he", lambda: dua.backfill_bmw_fitment_from_name_he),
    ("backfill_ford_fitment_from_name_he", lambda: dua.backfill_ford_fitment_from_name_he),
    ("backfill_jaguar_fitment_from_name", lambda: dua.backfill_jaguar_fitment_from_name)])
async def test_global_kill_switch_stops_every_guarded_task(db, task, fn):
    pid = await part(db, manufacturer="BMW", name="F30 pads", importer=100)
    await pvf(db, pid, "Toyota", "Corolla")
    await mg.trip(db, "all", "test")
    r = await fn()(db)
    assert r["status"] == "stopped" and r["stop"]["classification"] == "cycle"
    assert await base_of(db, pid) is None and await cv(db, pid) in (None, [])
    assert await fit_rows(db, pid) == [("Toyota", "Corolla")]


async def test_kill_switch_flipped_between_batches_stops_before_the_next_batch(db, monkeypatch):
    ids = await offer_parts(db, 6)
    calls = {"n": 0}
    real = mg.MaintenanceGuard._check

    async def flip(self, d):
        calls["n"] += 1
        if calls["n"] == 4:               # 1 = run_guarded pre-check, 2 = top of batch 1, 3 = pre-write b1, 4 = top of batch 2
            await mg.trip(d, "price", "flipped mid-run")
        return await real(self, d)
    monkeypatch.setattr(mg.MaintenanceGuard, "_check", flip)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and r["stop"]["classification"] == "price"
    assert await bases(db, ids) == [145.0, 145.0, 1.0, 1.0, 1.0, 1.0]     # batch 1 committed, nothing after
    assert await setting(db, "cursor__fix_base_prices") == ids[1]


async def test_kill_switch_flipped_just_before_the_write_vetoes_that_batch(db, monkeypatch):
    ids = await offer_parts(db, 2)
    calls = {"n": 0}
    real = mg.MaintenanceGuard._check

    async def flip(self, d):
        calls["n"] += 1
        if calls["n"] == 3:                      # the pre-write check of batch 1
            await mg.trip(d, "all", "flipped before write")
        return await real(self, d)
    monkeypatch.setattr(mg.MaintenanceGuard, "_check", flip)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped"
    assert await bases(db, ids) == [1.0, 1.0] and r["write_cap"]["committed"] == 0


async def test_a_latched_stop_survives_a_new_cycle_and_only_reset_clears_it(db):
    ids = await offer_parts(db, 2)
    await mg.trip(db, "fix_base_prices", "owner")
    for _ in range(3):                                   # three "scheduled cycles" (fresh guard each)
        assert (await dua.fix_base_prices(db))["status"] == "stopped"
    assert await bases(db, ids) == [1.0, 1.0]
    assert (await dua.normalize_base_price(db))["status"] == "ok"        # a task-scope stop is task-only
    assert await mg.reset(db, "fix_base_prices", operator="test") is True
    assert (await dua.fix_base_prices(db))["status"] == "ok" and await bases(db, ids) == [145.0, 145.0]
    kinds = [e["kind"] for e in await mg.recent_events(db, 50)]
    assert "reset" in kinds and "stop" in kinds


# ── consecutive errors ────────────────────────────────────────────────────────────────────────────────────────
async def test_two_consecutive_errors_latch_the_task_and_notify_once(db, monkeypatch, env):
    ids = await offer_parts(db, 2)
    n = {"calls": 0}

    async def boom(*a, **k):
        n["calls"] += 1
        raise RuntimeError("canceling statement due to statement timeout")
    monkeypatch.setattr(dua, "apply_base_price_rule", boom)
    r1 = await dua.fix_base_prices(db)
    assert r1["status"] == "error" and r1["consecutive_errors"] == 1 and "escalated" not in r1
    assert await setting(db, "cursor__fix_base_prices") in (None, dua._ZERO_UUID)      # failed batch: cursor stays
    r2 = await dua.fix_base_prices(db)
    assert r2["status"] == "error" and r2["consecutive_errors"] == 2 and r2["escalated"]["latched"] is True
    r3 = await dua.fix_base_prices(db)
    assert r3["status"] == "stopped" and n["calls"] == 2                # the third run never reaches the batch
    assert await bases(db, ids) == [1.0, 1.0]
    assert len(env) == 1                                                # ONE notification for the one latch


async def test_a_clean_run_resets_the_consecutive_error_counter(db, monkeypatch):
    await offer_parts(db, 1)
    real = dua.apply_base_price_rule
    state = {"fail": True}

    async def flaky(*a, **k):
        if state["fail"]:
            raise RuntimeError("boom")
        return await real(*a, **k)
    monkeypatch.setattr(dua, "apply_base_price_rule", flaky)
    assert (await dua.fix_base_prices(db))["consecutive_errors"] == 1
    state["fail"] = False
    assert (await dua.fix_base_prices(db))["status"] == "ok"
    assert await setting(db, f"{mg.PREFIX}errs__fix_base_prices") == "0"
    state["fail"] = True
    assert (await dua.fix_base_prices(db))["consecutive_errors"] == 1   # not 2: the clean run reset it


# ── write cap ─────────────────────────────────────────────────────────────────────────────────────────────────
async def test_write_cap_counts_committed_rows_across_batches_and_defers_the_rest_without_skipping(db, monkeypatch, env):
    ids = await offer_parts(db, 5)
    monkeypatch.setenv("MAINT_CAP_FIX_BASE_PRICES", "3")
    r = await dua.fix_base_prices(db)
    changed = [b for b in await bases(db, ids) if b == 145.0]
    assert len(changed) == 3 == r["write_cap"]["committed"] == r["updated"]              # exactly the cap, batch size 2
    assert r["status"] == "stopped" and r["stop"]["reason"] == "per-run write cap reached" and r["stop"]["latched"]
    assert await bases(db, ids) == [145.0, 145.0, 145.0, 1.0, 1.0]
    assert await setting(db, "cursor__fix_base_prices") == ids[2]        # resumes right after the last WRITTEN part
    run = json.loads(await setting(db, f"{mg.PREFIX}run__fix_base_prices"))
    assert run["committed"] == 3 and run["reserved"] == 0 and run["status"] == "stopped"
    assert len(env) == 1
    # operator resets, raises the cap: the two deferred parts are written, the first three are not touched again
    await mg.reset(db, "fix_base_prices")
    monkeypatch.setenv("MAINT_CAP_FIX_BASE_PRICES", "100")
    r2 = await dua.fix_base_prices(db)
    assert r2["status"] == "ok" and r2["updated"] == 2 and await bases(db, ids) == [145.0] * 5


async def test_cap_reached_exactly_and_a_write_beyond_it_is_impossible(db, monkeypatch):
    ids = await offer_parts(db, 4)
    monkeypatch.setenv("MAINT_CAP_FIX_BASE_PRICES", "4")
    r = await dua.fix_base_prices(db)
    assert await bases(db, ids) == [145.0] * 4 and r["write_cap"]["committed"] == 4
    assert r["status"] == "stopped"                                     # reached = stop, never exceeded
    # direct attempt to exceed: the guard grants nothing once the cap is used
    g = mg.MaintenanceGuard("fix_base_prices", cap=4)
    g.committed = 4
    assert await g.reserve(db, 10) == 0 and g.remaining == 0


async def test_reservation_is_capped_by_remaining_capacity_and_settles_to_the_real_count(db):
    g = mg.MaintenanceGuard("fix_base_prices", cap=10)
    assert await g.reserve(db, 7) == 7 and g.remaining == 3
    assert await g.reserve(db, 7) == 3 and g.remaining == 0              # the last batch cannot exceed what is left
    await g.settle(db, 7, 2)                                             # only 2 rows really changed
    await g.settle(db, 3, 3)
    assert g.committed == 5 and g.reserved == 0 and g.remaining == 5


async def test_name_backfill_cap_inserts_exactly_the_cap_and_resumes_without_duplicates(db, monkeypatch):
    ids = sorted([await part(db, manufacturer="BMW", name="F30 pads") for _ in range(3)])
    monkeypatch.setenv("MAINT_CAP_BACKFILL_BMW", "2")
    r = await dua.backfill_bmw_fitment_from_name_he(db)
    inserted = [i for i in ids if await fit_rows(db, i)]
    assert len(inserted) == 2 == r["updated"] == r["write_cap"]["committed"] and r["status"] == "stopped"
    assert await setting(db, "cursor__backfill_bmw_fitment_from_name_he") == ids[1]
    await mg.reset(db, "backfill_bmw_fitment_from_name_he")
    monkeypatch.setenv("MAINT_CAP_BACKFILL_BMW", "50")
    r2 = await dua.backfill_bmw_fitment_from_name_he(db)
    assert r2["status"] == "ok" and r2["updated"] == 1
    assert [len(await fit_rows(db, i)) for i in ids] == [1, 1, 1]        # one row each, no duplicates


async def test_merge_cap_writes_exactly_the_cap_and_resumes(db, monkeypatch):
    ids = sorted([await part(db) for _ in range(3)])
    for i in ids:
        await pvf(db, i, "Toyota", "Corolla")
    monkeypatch.setenv("MAINT_CAP_MERGE_FITMENT", "2")
    r = await dua.merge_catalog_fitment_from_part_vehicle_fitment(db)
    done = [i for i in ids if await cv(db, i)]
    assert len(done) == 2 == r["updated_parts"] == r["write_cap"]["committed"] and r["status"] == "stopped"
    await mg.reset(db, "merge_catalog_fitment_from_part_vehicle_fitment")
    monkeypatch.setenv("MAINT_CAP_MERGE_FITMENT", "50")
    r2 = await dua.merge_catalog_fitment_from_part_vehicle_fitment(db)
    assert r2["status"] == "ok" and r2["updated_parts"] == 1 and all([await cv(db, i) for i in ids])


# ── load / CPU ────────────────────────────────────────────────────────────────────────────────────────────────
async def test_high_load_stops_the_cycle_without_writes_and_is_re_evaluated_by_the_next_run(db, monkeypatch, env):
    ids = await offer_parts(db, 2)
    monkeypatch.setattr(mg, "default_load_probe", lambda: 25.0)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and r["stop"]["classification"] == "cycle" and r["stop"]["latched"] is False
    assert r["stop"]["measurements"]["load1"] == 25.0 and await bases(db, ids) == [1.0, 1.0]
    assert any(e["reason"] == "system load above limit" for e in await mg.recent_events(db))     # persisted
    monkeypatch.setattr(mg, "default_load_probe", lambda: 3.0)
    assert (await dua.fix_base_prices(db))["status"] == "ok"            # load fell: not a latch


async def test_load_rising_mid_run_stops_before_the_next_batch(db, monkeypatch):
    ids = await offer_parts(db, 6)
    loads = iter([1.0, 1.0, 1.0, 99.0] + [99.0] * 20)                   # pre-run, top b1, pre-write b1, top b2
    monkeypatch.setattr(mg, "default_load_probe", lambda: next(loads))
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and await bases(db, ids) == [145.0, 145.0, 1.0, 1.0, 1.0, 1.0]


async def test_cpu_over_threshold_stops(db, monkeypatch):
    ids = await offer_parts(db, 2)

    async def hot():
        return 590.0
    monkeypatch.setattr(mg, "default_cpu_probe", hot)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and r["stop"]["reason"] == "host CPU above limit" and await bases(db, ids) == [1.0, 1.0]


async def test_thresholds_are_independent_env_values(monkeypatch):
    monkeypatch.setenv("MAINT_GUARD_MAX_LOAD", "7.5")
    monkeypatch.setenv("MAINT_GUARD_MAX_CPU_PCT", "250")
    monkeypatch.setenv("MAINT_GUARD_MAX_CONSECUTIVE_ERRORS", "4")
    assert (mg.max_load(), mg.max_cpu_pct(), mg.max_consecutive_errors()) == (7.5, 250.0, 4)
    monkeypatch.delenv("MAINT_GUARD_MAX_LOAD")
    assert mg.max_load() == 18.0
    assert (mg.cap_for("fix_base_prices"), mg.cap_for("normalize_base_price"),
            mg.cap_for("merge_catalog_fitment_from_part_vehicle_fitment")) == (600_000, 150_000, 800_000)


# ── price verification ────────────────────────────────────────────────────────────────────────────────────────
async def test_price_verification_passes_for_a_correct_run(db):
    await offer_parts(db, 3)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "ok" and r["verification"].startswith("passed")


async def test_verification_failure_when_a_task_writes_a_part_it_does_not_own_trips_the_price_workflow(db, monkeypatch, env):
    ids = await offer_parts(db, 2)
    est = await part(db, base=5.0, importer=100)                         # estimate-owned: fix_base_prices must not touch
    real = dua.apply_base_price_rule

    async def rogue(db_, **kw):
        out = await real(db_, **kw)
        await db_.execute(sa.text("UPDATE parts_catalog SET base_price = 1.11 WHERE id = CAST(:p AS uuid)"), {"p": est})
        return out
    monkeypatch.setattr(dua, "apply_base_price_rule", rogue)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and r["verification"] == "failed" and r["stop"]["classification"] == "price"
    assert "does not own" in json.dumps(r["stop"]["measurements"]["violations"])
    monkeypatch.setattr(dua, "apply_base_price_rule", real)
    other = await dua.normalize_base_price(db)                           # the whole price workflow is stopped
    assert other["status"] == "stopped" and other["stop"]["scope"] == "price"
    assert (await dua.merge_catalog_fitment_from_part_vehicle_fitment(db))["status"] == "ok"    # fitment unaffected
    assert len(env) == 1


async def test_verification_detects_an_owned_value_that_is_not_the_policy_target(db, monkeypatch):
    await offer_parts(db, 2)
    real = dua.base_price_target

    def wrong(row, rate):
        t, s = real(row, rate)
        return (t + 1 if t is not None and s == "offer" else t), s       # the WRITER uses a wrong formula
    monkeypatch.setattr(dua, "base_price_target", wrong)
    real_verify = mg.PriceVerifier.verify

    async def honest_verify(self, d, rate, last):                        # the VERIFIER uses the policy
        monkeypatch.setattr(dua, "base_price_target", real)
        return await real_verify(self, d, rate, last)
    monkeypatch.setattr(mg.PriceVerifier, "verify", honest_verify)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and "policy target" in json.dumps(r["stop"]["measurements"]["violations"])


# ── controller / infrastructure failures fail closed ──────────────────────────────────────────────────────────
async def test_settings_store_failure_fails_closed(db, monkeypatch):
    ids = await offer_parts(db, 2)

    async def broken(*a, **k):
        raise mg.GuardUnavailable("settings store down")
    monkeypatch.setattr(mg, "active_latches", broken)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and "controller unavailable" in r["reason"]
    assert await bases(db, ids) == [1.0, 1.0]


async def test_run_counter_persistence_failure_after_commit_stops_and_does_not_advance_the_cursor(db, monkeypatch):
    ids = await offer_parts(db, 4)
    real = mg.MaintenanceGuard._persist_run
    n = {"c": 0}

    async def flaky(self, d, status="running"):
        n["c"] += 1
        if n["c"] == 2:                           # 1 = the reservation, 2 = the settle after batch 1's commit
            raise mg.GuardUnavailable("disk full")
        return await real(self, d, status)
    monkeypatch.setattr(mg.MaintenanceGuard, "_persist_run", flaky)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and "after commit" in r["reason"]
    assert await bases(db, ids) == [145.0, 145.0, 1.0, 1.0]               # batch 1 is committed, batch 2 never ran
    assert await setting(db, "cursor__fix_base_prices") in (None, dua._ZERO_UUID)   # replayed next run, idempotent


async def test_redis_failure_means_no_lock_and_no_writes(db, monkeypatch):
    ids = await offer_parts(db, 2)

    async def down():
        raise ConnectionError("redis down")
    monkeypatch.setattr(mg, "_default_redis", down)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and "lock unavailable" in r["reason"] and await bases(db, ids) == [1.0, 1.0]


async def test_notification_failure_never_undoes_a_stop(db, monkeypatch):
    ids = await offer_parts(db, 2)

    async def mute(title, body):
        raise RuntimeError("whatsapp bridge down")
    monkeypatch.setattr(mg, "default_notify", mute)
    await mg.trip(db, "all", "x")                                        # trip() has no notifier by design
    dec = mg.StopDecision("price", "price", "test failure", latch=True)
    g = mg.MaintenanceGuard("fix_base_prices")
    await g.enact(db, dec)                                               # notifier raises inside: swallowed
    assert "price" in await mg.active_latches(db)
    await mg.reset(db, "all")
    assert (await dua.fix_base_prices(db))["status"] == "stopped"        # the price latch still holds
    assert await bases(db, ids) == [1.0, 1.0]


# ── timeouts and recovery ─────────────────────────────────────────────────────────────────────────────────────
async def test_every_guarded_batch_runs_with_lock_timeout_5s_and_statement_timeout_120s(db, monkeypatch):
    seen = {}
    real = mg.MaintenanceGuard._check

    async def spy(self, d):
        if self.task not in seen and self.reserved:                      # inside a batch transaction
            seen[self.task] = ((await d.execute(sa.text("SHOW lock_timeout"))).scalar(),
                               (await d.execute(sa.text("SHOW statement_timeout"))).scalar())
        return await real(self, d)
    monkeypatch.setattr(mg.MaintenanceGuard, "_check", spy)
    p = await part(db, manufacturer="BMW", name="F30 pads", importer=100)
    await offer(db, p, ils=100)
    await part(db, importer=100)                                          # estimate-owned: normalize writes it
    await part(db, manufacturer="Ford", name="x", name_he="# פוקוס")
    await part(db, manufacturer="Jaguar", name="XF brake pads")
    await pvf(db, p, "Toyota", "Corolla")
    for fn in (dua.fix_base_prices, dua.normalize_base_price, dua.merge_catalog_fitment_from_part_vehicle_fitment,
               dua.backfill_bmw_fitment_from_name_he, dua.backfill_ford_fitment_from_name_he,
               dua.backfill_jaguar_fitment_from_name):
        assert (await fn(db))["status"] == "ok"
    assert len(seen) == 6, seen
    assert set(seen.values()) == {("5s", "2min")}, seen


async def test_statement_timeout_before_the_write_rolls_back_and_keeps_the_cursor(db, monkeypatch):
    ids = await offer_parts(db, 2)
    real = dua.apply_base_price_rule

    async def slow(d, **kw):
        await d.execute(sa.text("SET LOCAL statement_timeout = '50ms'"))
        await d.execute(sa.text("SELECT pg_sleep(1)"))               # the DB cancels it: QueryCanceledError
        return await real(d, **kw)
    monkeypatch.setattr(dua, "apply_base_price_rule", slow)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "error" and "statement timeout" in r["error"]
    assert await bases(db, ids) == [1.0, 1.0] and await setting(db, "cursor__fix_base_prices") in (None, dua._ZERO_UUID)
    assert r["write_cap"]["committed"] == 0 and r["write_cap"]["reserved"] == 0     # reservation released


async def test_crash_after_commit_before_cursor_save_replays_idempotently(db, monkeypatch):
    ids = await offer_parts(db, 4)
    real = dua._save_task_cursor
    state = {"crash": True}

    async def crashing(d, task, value):
        if state["crash"]:
            state["crash"] = False
            raise Crash()
        return await real(d, task, value)
    monkeypatch.setattr(dua, "_save_task_cursor", crashing)
    with pytest.raises(Crash):
        await dua.fix_base_prices(db)
    assert await bases(db, ids) == [145.0, 145.0, 1.0, 1.0]               # batch 1 committed ...
    assert await setting(db, "cursor__fix_base_prices") in (None, dua._ZERO_UUID)    # ... cursor not saved
    r = await dua.fix_base_prices(db)                                     # the "restart"
    assert r["status"] == "ok" and r["updated"] == 2 and r["unchanged"] == 2         # replay wrote nothing twice
    assert await bases(db, ids) == [145.0] * 4


async def test_a_permanently_failing_name_row_is_escalated_not_skipped(db, monkeypatch, env):
    good = await part(db, manufacturer="BMW", name="F30 pads")
    bad = await part(db, manufacturer="BMW", name="F31 pads")
    first, second = sorted([good, bad])
    real = dua._bmw_fitment_from_name

    def extract(name, name_he):
        found, why = real(name, name_he)
        if "F31" in name:
            return [("BMW", "x" * 300, 2012, 2019, "too long for varchar(100)")], why
        return found, why
    monkeypatch.setattr(dua, "_bmw_fitment_from_name", extract)
    r1 = await dua.backfill_bmw_fitment_from_name_he(db)
    assert r1["status"] == "error" and r1["failed"] == 1 and r1["consecutive_errors"] == 1
    assert len(await fit_rows(db, good)) == 1 and await fit_rows(db, bad) == []
    assert await setting(db, "cursor__backfill_bmw_fitment_from_name_he") in (None, dua._ZERO_UUID)   # not advanced
    r2 = await dua.backfill_bmw_fitment_from_name_he(db)                  # bounded retry: second failure
    assert r2["status"] == "error" and r2["escalated"]["latched"] is True and len(await fit_rows(db, good)) == 1
    assert (await dua.backfill_bmw_fitment_from_name_he(db))["status"] == "stopped"
    assert len(env) == 1


# ── lock contract ─────────────────────────────────────────────────────────────────────────────────────────────
async def test_lock_has_ownership_expiry_and_compare_and_release():
    r = await support.fresh_redis()
    a = mg.TaskLock(r, "zz-own", ttl_s=0.4)
    b = mg.TaskLock(r, "zz-own", ttl_s=5)
    assert await a.acquire() is True and await b.acquire() is False
    a._beat.cancel()                                                      # worker A freezes: no more renewals
    await asyncio.sleep(0.7)                                              # ... and its lock expires
    assert await b.acquire() is True                                      # worker B takes over
    assert await a.release() is False                                     # A must NOT release B's lock
    assert await r.get(b.key) is not None and (await b.holder_token()) == b.token
    assert await b.release() is True and await r.get(b.key) is None


async def test_heartbeat_keeps_a_long_run_alive_and_reports_a_lost_lock():
    r = await support.fresh_redis()
    lost = []
    a = mg.TaskLock(r, "zz-beat", ttl_s=0.6, on_lost=lambda: lost.append(1))
    assert await a.acquire()
    await asyncio.sleep(1.5)                                              # 2.5 TTLs: only the heartbeat keeps it
    assert await r.get(a.key) is not None and not lost
    await r.set(a.key, "someone-else")                                    # lock taken over behind A's back
    await asyncio.sleep(0.5)
    assert lost == [1] and a.held is False
    await r.delete(a.key)
    g = mg.MaintenanceGuard("fix_base_prices")
    g.lock_lost = True
    assert "lock lost" in (await g.check(None)).reason


@pytest.mark.parametrize("task", sorted(mg.GUARDED_TASKS))
async def test_every_entry_point_hits_the_same_lock(db, monkeypatch, task):
    """run_task (admin API, owner console, maintenance scripts, run_all_tasks) -> the task function -> run_guarded."""
    monkeypatch.setattr(dua, "OOM_BLOCKED_TASKS", frozenset())            # as if A3 released the gate
    r = await support.fresh_redis()
    holder = mg.TaskLock(r, mg.lock_name_for(task), ttl_s=30)
    assert await holder.acquire()
    ids = await offer_parts(db, 2)
    out = await dua.run_task(task, db)
    assert out["status"] == "skipped" and out["lock_conflict"] is True and "lock_conflict" in out["reason"]
    assert await bases(db, ids) == [1.0, 1.0]
    await holder.release()
    assert (await dua.run_task(task, db))["status"] == "ok"


async def test_the_two_price_tasks_exclude_each_other(db):
    ids = await offer_parts(db, 2)
    r = await support.fresh_redis()
    holder = mg.TaskLock(r, mg.lock_name_for("fix_base_prices"), ttl_s=30)   # fix_base_prices is "running"
    assert await holder.acquire()
    out = await dua.normalize_base_price(db)
    assert out["status"] == "skipped" and out["lock_conflict"] is True
    await holder.release()


async def test_two_processes_race_for_one_lock_exactly_one_wins():
    code = ("import asyncio,sys,os\n"
            "sys.path.insert(0,'/app')\n"
            "import redis.asyncio as a, maintenance_guard as mg\n"
            "async def main():\n"
            "    r=a.from_url(os.environ['REDIS_URL']); l=mg.TaskLock(r,'zz-race',ttl_s=3)\n"
            "    ok=await l.acquire(); print('ACQ' if ok else 'BUSY', flush=True)\n"
            "    await asyncio.sleep(1.5); await l.release()\n"
            "asyncio.run(main())\n")
    r = await support.fresh_redis()
    await r.delete("autospare:lock:maint:zz-race")
    procs = [await asyncio.create_subprocess_exec(sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE,
                                                  env={**os.environ, "PYTHONPATH": "/app"}) for _ in range(3)]
    outs = [(await p.communicate())[0].decode().strip() for p in procs]
    assert sorted(outs) == ["ACQ", "BUSY", "BUSY"], outs
    assert await r.get("autospare:lock:maint:zz-race") is None            # released by its owner


async def test_a_concurrent_second_worker_cannot_double_write(db):
    """Two logical workers share one cursor: the second is refused while the first holds the lock, so no part is
    processed twice and the counters add up."""
    ids = await offer_parts(db, 4)
    r = await support.fresh_redis()
    results = await asyncio.gather(dua.fix_base_prices(db), asyncio.sleep(0, result=None))
    first = results[0]
    assert first["status"] == "ok" and first["updated"] == 4
    again = await dua.fix_base_prices(db)
    assert again["updated"] == 0 and again["unchanged"] == 4              # nothing written twice
    assert await bases(db, ids) == [145.0] * 4
    assert await r.get("autospare:lock:maint:price") is None


# ── structure ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_all_six_task_functions_go_through_run_guarded_and_the_registry_is_complete():
    import inspect
    assert mg.GUARDED_TASKS <= set(dua.TASK_REGISTRY)
    funnel = {"fix_base_prices": dua._run_base_price_task, "normalize_base_price": dua._run_base_price_task,
              "merge_catalog_fitment_from_part_vehicle_fitment": dua.merge_catalog_fitment_from_part_vehicle_fitment,
              "backfill_bmw_fitment_from_name_he": dua._name_fitment_backfill,
              "backfill_ford_fitment_from_name_he": dua._name_fitment_backfill,
              "backfill_jaguar_fitment_from_name": dua._name_fitment_backfill}
    for task, fn in funnel.items():
        assert "run_guarded" in inspect.getsource(fn), task
    for task in ("fix_base_prices", "normalize_base_price"):
        assert "_run_base_price_task" in inspect.getsource(dua.TASK_REGISTRY[task]), task


def test_guarded_tasks_no_longer_run_ddl_or_a_catalogue_scan_per_run():
    import inspect
    for fn in (dua._name_fitment_runner, dua._merge_fitment_runner):
        src = inspect.getsource(fn)
        assert "ensure_part_vehicle_fitment_table" not in src and "require_part_vehicle_fitment_table" in src
    sql = [c for c in dua.require_part_vehicle_fitment_table.__code__.co_consts if isinstance(c, str) and "to_regclass" in c]
    assert len(sql) == 1 and sql[0].lstrip().upper().startswith("SELECT") and "ALTER" not in sql[0].upper()


def test_run_all_tasks_halts_instead_of_continuing_after_a_cycle_or_price_stop():
    import inspect
    assert mg.halted_reason("fix_base_prices", {"all": "load"}) == "load"
    assert mg.halted_reason("merge_catalog_fitment_from_part_vehicle_fitment", {"price": "bad"}) is None
    assert mg.halted_reason("normalize_base_price", {"price": "bad"}) == "bad"
    assert mg.halted_reason("backfill_bmw_fitment_from_name_he", {}) is None
    src = inspect.getsource(dua.run_all_tasks)
    assert "_mg.halted_reason" in src and "tasks_stopped" in src and "STOPPED by the maintenance guard" in src


# ── run_all_tasks: a critical stop halts the guarded group instead of "errored, continuing" ─────────────────────
async def _run_all(db, monkeypatch, tasks):
    import BACKEND_AUTH_SECURITY as auth

    async def fresh():
        return await support.fresh_redis()
    monkeypatch.setattr(auth, "get_redis", fresh)
    monkeypatch.setattr(dua, "OOM_BLOCKED_TASKS", frozenset())            # as if A3 released the six
    return await dua.run_all_tasks(db, ordered_override=tasks)


async def test_run_all_tasks_cycle_stop_halts_every_remaining_guarded_task(db, monkeypatch):
    ids = await offer_parts(db, 2)
    monkeypatch.setattr(mg, "default_load_probe", lambda: 99.0)
    out = await _run_all(db, monkeypatch, ["fix_base_prices", "normalize_base_price", "backfill_bmw_fitment_from_name_he"])
    assert [r["status"] for r in out["results"]] == ["stopped"] * 3
    assert out["tasks_stopped"] == 3 and out["tasks_error"] == 0 and out["tasks_ok"] == 0
    assert "cycle halted earlier" in out["results"][1]["reason"] and "cycle halted earlier" in out["results"][2]["reason"]
    assert await bases(db, ids) == [1.0, 1.0]


async def test_run_all_tasks_price_stop_halts_only_the_price_workflow(db, monkeypatch):
    ids = await offer_parts(db, 2)
    bmw = await part(db, manufacturer="BMW", name="F30 pads")
    est = await part(db, base=5.0, importer=100)
    real = dua.apply_base_price_rule

    async def rogue(db_, **kw):
        out = await real(db_, **kw)
        await db_.execute(sa.text("UPDATE parts_catalog SET base_price = 1.11 WHERE id = CAST(:p AS uuid)"), {"p": est})
        return out
    monkeypatch.setattr(dua, "apply_base_price_rule", rogue)
    out = await _run_all(db, monkeypatch, ["fix_base_prices", "normalize_base_price", "backfill_bmw_fitment_from_name_he"])
    by = {r["task"]: r for r in out["results"]}
    assert by["fix_base_prices"]["status"] == "stopped" and by["fix_base_prices"]["stop"]["classification"] == "price"
    assert by["normalize_base_price"]["status"] == "stopped" and "cycle halted earlier" in by["normalize_base_price"]["reason"]
    assert by["backfill_bmw_fitment_from_name_he"]["status"] == "ok"        # independent safe task preserved
    assert len(await fit_rows(db, bmw)) == 1


async def test_run_all_tasks_next_cycle_cannot_bypass_a_latched_stop(db, monkeypatch):
    ids = await offer_parts(db, 2)
    await mg.trip(db, "fix_base_prices", "owner")
    for _ in range(2):                                                        # two scheduled cycles
        out = await _run_all(db, monkeypatch, ["fix_base_prices"])
        assert out["results"][0]["status"] == "stopped" and out["tasks_stopped"] == 1
    assert await bases(db, ids) == [1.0, 1.0]


# ── metric unavailable: the guard cannot see the system -> it does not write ────────────────────────────────────
async def test_unavailable_load_metric_fails_closed(db, monkeypatch):
    ids = await offer_parts(db, 2)

    def broken():
        raise OSError("getloadavg unsupported")
    monkeypatch.setattr(mg, "default_load_probe", broken)
    r = await dua.fix_base_prices(db)
    assert r["status"] == "stopped" and "controller unavailable" in r["reason"] and await bases(db, ids) == [1.0, 1.0]


async def test_unavailable_cpu_metric_fails_closed(db, monkeypatch):
    ids = await offer_parts(db, 2)

    async def broken():
        raise OSError("/proc/stat unreadable")
    monkeypatch.setattr(mg, "default_cpu_probe", broken)
    r = await dua.normalize_base_price(db)
    assert r["status"] == "stopped" and "controller unavailable" in r["reason"]
    assert await bases(db, ids) == [1.0, 1.0]
