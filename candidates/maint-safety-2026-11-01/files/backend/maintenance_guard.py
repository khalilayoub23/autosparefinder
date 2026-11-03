"""
Script: maintenance_guard.py
Purpose: ONE safety controller + ONE lock contract for the six #119 maintenance tasks (fix_base_prices,
         normalize_base_price, merge_catalog_fitment_from_part_vehicle_fitment, backfill_bmw/ford/jaguar_*).
Process:
  * run_guarded(db, task, runner)   - the single entry every caller of a guarded task goes through (the task
    functions themselves call it, so run_all_tasks, the admin API, the owner console and the maintenance scripts
    share the same contract). Order: stop-check -> Redis lock -> runner(db, guard) -> outcome bookkeeping.
  * MaintenanceGuard.check(db)      - read-only stop check, run before each batch and again right before a batch
    writes. Any failure of the controller itself returns a stop (fail closed).
  * reserve()/settle()              - the per-run WRITE CAP: capacity is reserved (and persisted) BEFORE a batch
    writes and settled with the rows really committed afterwards; a retry or a second batch draws on the same
    run counter.
  * enact(db, decision)             - persists the stop event and, for a latching stop, the kill-switch row; one
    owner notification per newly created latch (the latch row is the de-duplication).
  * TaskLock                        - Redis lock with an ownership token, expiry and heartbeat renewal; release and
    renewal are compare-and-act, so a worker can never release or extend another worker's lock.
  * PriceVerifier                   - bounded post-run check of the base_price workflow (see class).
Stop classifications (docs/MAINTENANCE_SAFETY.md):
  task   - only this task stops (consecutive errors, write cap, lock lost, controller unavailable).
  price  - both base_price tasks stop (failed price verification). Latching.
  cycle  - every guarded task stops (global kill switch = latching; high host load / CPU = not latching, the
           condition is simply evaluated again by the next run).
Latching stops live in system_settings (`maint_guard__latch__<scope>`) and survive restarts and scheduled cycles;
ONLY reset() (maintenance/maint_guard_cli.py reset) clears them.
Data Imported/Modified: system_settings keys `maint_guard__*` only. Never touches parts_catalog itself.
Data Sources: os.getloadavg(), /proc/stat, Redis, system_settings.
Missing Data Delegation: none.
Last Updated: 2026-11-01
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

PREFIX = "maint_guard__"
LOCK_NAMESPACE = "autospare:lock:maint:"

# task -> (group, cap env var, default per-run cap of COMMITTED row changes)
# fix 600k / normalize 150k / merge 800k are the documented #119 limits (each ~ the measured backlog of THAT task);
# the three name backfills have a measured backlog of 71 / 298 / 61 rows, so their ceiling is a deliberately generous
# 5,000 each - above it the extractor is doing something it did not do in the dry run.
SPECS: Dict[str, tuple] = {
    "fix_base_prices": ("price", "MAINT_CAP_FIX_BASE_PRICES", 600_000),
    "normalize_base_price": ("price", "MAINT_CAP_NORMALIZE_BASE_PRICE", 150_000),
    "merge_catalog_fitment_from_part_vehicle_fitment": ("fitment", "MAINT_CAP_MERGE_FITMENT", 800_000),
    "backfill_bmw_fitment_from_name_he": ("fitment", "MAINT_CAP_BACKFILL_BMW", 5_000),
    "backfill_ford_fitment_from_name_he": ("fitment", "MAINT_CAP_BACKFILL_FORD", 5_000),
    "backfill_jaguar_fitment_from_name": ("fitment", "MAINT_CAP_BACKFILL_JAGUAR", 5_000),
}
GUARDED_TASKS = frozenset(SPECS)
PRICE_TASKS = frozenset(t for t, s in SPECS.items() if s[0] == "price")


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        logger.warning("%s is not a number - using the default %s", name, default)
        return float(default)


def max_consecutive_errors() -> int:
    return max(int(_env_num("MAINT_GUARD_MAX_CONSECUTIVE_ERRORS", 2)), 1)


def max_load() -> float:
    return _env_num("MAINT_GUARD_MAX_LOAD", 18.0)


def max_cpu_pct() -> float:
    """Host-wide busy CPU in percent of ONE core (600 = all 6 vCPUs). The documented limit is 'postgres_catalog CPU >
    300%', which cannot be observed from inside the backend container (no docker socket); the host-wide figure is the
    closest signal available there, so its default is 80% of the box and it is a separate, owner-tunable value."""
    return _env_num("MAINT_GUARD_MAX_CPU_PCT", 480.0)


def lock_ttl_s() -> float:
    return max(_env_num("MAINT_LOCK_TTL_S", 120), 1.0)


def cap_for(task: str) -> int:
    _, env, default = SPECS[task]
    return max(int(_env_num(env, default)), 0)


def lock_name_for(task: str) -> str:
    """The two base_price tasks share ONE lock (they must never overlap); every other task has its own."""
    return "price" if task in PRICE_TASKS else task


def halted_reason(task: str, halted: Dict[str, str]) -> Optional[str]:
    """run_all_tasks: why `task` must not start, given the stop scopes raised earlier in the same cycle."""
    if "all" in halted:
        return halted["all"]
    if task in PRICE_TASKS and "price" in halted:
        return halted["price"]
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GuardUnavailable(Exception):
    """The controller itself could not read or persist its state: callers must stop (fail closed)."""


@dataclass
class StopDecision:
    scope: str                      # 'all' | 'price' | <task name>
    classification: str             # 'task' | 'price' | 'cycle'
    reason: str
    latch: bool = False
    measurements: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"scope": self.scope, "classification": self.classification, "reason": self.reason,
                "latched": self.latch, "measurements": self.measurements}


# ── probes ──────────────────────────────────────────────────────────────────────────────────────────────────────
def default_load_probe() -> float:
    return os.getloadavg()[0]


def _read_cpu_times():
    with open("/proc/stat") as fh:
        vals = [int(x) for x in fh.readline().split()[1:]]
    return sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)


async def default_cpu_probe() -> float:
    """Host-wide busy CPU in percent of one core, sampled over ~0.3 s."""
    total_a, idle_a = _read_cpu_times()
    await asyncio.sleep(0.3)
    total_b, idle_b = _read_cpu_times()
    dt = total_b - total_a
    if dt <= 0:
        return 0.0
    return (dt - (idle_b - idle_a)) / dt * (os.cpu_count() or 1) * 100.0


# ── settings store (system_settings) ───────────────────────────────────────────────────────────────────────────
async def _get_many(db, keys: List[str]) -> Dict[str, str]:
    rows = (await db.execute(text("SELECT key, value FROM system_settings WHERE key = ANY(CAST(:k AS text[]))"),
                             {"k": keys})).fetchall()
    return {r[0]: r[1] for r in rows}


async def _put(db, key: str, value: str) -> None:
    await db.execute(text("""
        INSERT INTO system_settings (id, key, value, value_type, is_public, updated_at)
        VALUES (gen_random_uuid(), :k, :v, 'string', false, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"""), {"k": key, "v": value})
    await db.commit()


async def _insert_once(db, key: str, value: str) -> bool:
    """Create the row only if it does not exist yet; True = this call created it (the de-duplication primitive)."""
    row = (await db.execute(text("""
        INSERT INTO system_settings (id, key, value, value_type, is_public, updated_at)
        VALUES (gen_random_uuid(), :k, :v, 'string', false, NOW())
        ON CONFLICT (key) DO NOTHING RETURNING key"""), {"k": key, "v": value})).first()
    await db.commit()
    return row is not None


def latch_key(scope: str) -> str:
    return f"{PREFIX}latch__{scope}"


def _scopes_for(task: str) -> List[str]:
    return ["all"] + (["price"] if task in PRICE_TASKS else []) + [task]


async def active_latches(db, task: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Latches that apply to `task` (all of them when task is None), keyed by scope.
    Raises GuardUnavailable on a store error."""
    try:
        if task is None:
            rows = (await db.execute(text("SELECT key, value FROM system_settings WHERE key LIKE :p"),
                                     {"p": f"{PREFIX}latch\\_\\_%"})).fetchall()
            found = {r[0]: r[1] for r in rows}
        else:
            found = await _get_many(db, [latch_key(s) for s in _scopes_for(task)])
    except Exception as exc:
        raise GuardUnavailable(f"latch read failed: {type(exc).__name__}: {exc}") from exc
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in found.items():
        scope = k[len(latch_key("")):]
        try:
            out[scope] = json.loads(v)
        except (ValueError, TypeError):
            out[scope] = {"reason": "unreadable latch row (treated as active)"}
    return out


async def recent_events(db, limit: int = 20) -> List[Dict[str, Any]]:
    rows = (await db.execute(text("SELECT value FROM system_settings WHERE key LIKE :p ORDER BY key DESC LIMIT :n"),
                             {"p": f"{PREFIX}event\\_\\_%", "n": limit})).fetchall()
    out = []
    for (v,) in rows:
        try:
            out.append(json.loads(v))
        except (ValueError, TypeError):
            pass
    return out


async def record_event(db, event: Dict[str, Any], keep: int = 200) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    key = f"{PREFIX}event__{ts}_{str(event.get('run_id', ''))[:8]}_{event.get('task', '')}"
    await _put(db, key, json.dumps({**event, "at": _now_iso()}, default=str))
    await db.execute(text("""
        DELETE FROM system_settings WHERE key LIKE :p AND key NOT IN (
            SELECT key FROM system_settings WHERE key LIKE :p ORDER BY key DESC LIMIT :n)"""),
        {"p": f"{PREFIX}event\\_\\_%", "n": keep})
    await db.commit()


async def reset(db, scope: str, *, operator: str = "cli", note: str = "") -> bool:
    """The ONLY way a latching stop is cleared. Also zeroes the consecutive-error counter of a task scope.
    Returns True when a latch was removed."""
    res = await db.execute(text("DELETE FROM system_settings WHERE key = :k"), {"k": latch_key(scope)})
    removed = bool(res.rowcount)
    if scope in SPECS:
        await db.execute(text("DELETE FROM system_settings WHERE key = :k"), {"k": f"{PREFIX}errs__{scope}"})
    await db.commit()
    await record_event(db, {"kind": "reset", "task": scope, "run_id": "", "scope": scope,
                            "operator": operator, "note": note, "removed": removed})
    return removed


async def trip(db, scope: str, reason: str, *, operator: str = "cli") -> bool:
    """Manual kill switch (no restart needed). scope: 'all' | 'price' | a guarded task name."""
    if scope not in {"all", "price"} and scope not in SPECS:
        raise ValueError(f"unknown scope {scope!r}")
    dec = StopDecision(scope, "cycle" if scope == "all" else ("price" if scope == "price" else "task"),
                       f"manual: {reason}", latch=True, measurements={"operator": operator})
    return await _enact(db, dec, task=scope, run_id="manual")


async def _enact(db, decision: StopDecision, *, task: str, run_id: str, counters: Optional[Dict[str, Any]] = None,
                 notify: Optional[Callable[[str, str], Awaitable[None]]] = None) -> bool:
    """Persist the event (+ latch). Returns True when a NEW latch was created. Never raises."""
    created = False
    try:
        try:
            await db.rollback()                 # nothing of the interrupted batch may be left pending
        except Exception:
            pass
        if decision.latch:
            payload = {"active": True, **decision.as_dict(), "task": task, "run_id": run_id, "at": _now_iso()}
            created = await _insert_once(db, latch_key(decision.scope), json.dumps(payload, default=str))
        await record_event(db, {"kind": "stop", "task": task, "run_id": run_id, "counters": counters or {},
                                "new_latch": created, **decision.as_dict()})
    except Exception as exc:
        logger.error("maintenance_guard: could not persist stop %s for %s: %s", decision.reason, task, exc)
    if created and notify is not None:
        try:
            await notify(f"maintenance stop [{decision.classification}/{decision.scope}]",
                         f"{task}: {decision.reason}\nrun {run_id}\n{json.dumps(decision.measurements, default=str)[:400]}\n"
                         f"reset only with: maintenance/maint_guard_cli.py reset {decision.scope}")
        except Exception as exc:                  # a missing notifier never blocks or undoes the stop
            logger.warning("maintenance_guard: notification failed (stop stays in force): %s", exc)
    logger.warning("maintenance_guard STOP %s", {"task": task, "run_id": run_id, **decision.as_dict(), "new_latch": created})
    return created


async def default_notify(title: str, body: str) -> None:
    from BACKEND_API_ROUTES import notify_owner
    await notify_owner("health", title, body, severity="error")


# ── the guard ───────────────────────────────────────────────────────────────────────────────────────────────────
_DEFAULT = object()


class MaintenanceGuard:
    def __init__(self, task: str, *, run_id: Optional[str] = None, cap: Optional[int] = None,
                 load_probe: Optional[Callable[[], float]] = None,
                 cpu_probe: Optional[Callable[[], Awaitable[float]]] = None,
                 notifier: Any = _DEFAULT) -> None:
        if task not in SPECS:
            raise ValueError(f"{task} is not a guarded maintenance task")
        self.task = task
        self.run_id = run_id or uuid.uuid4().hex
        self.cap = cap_for(task) if cap is None else cap
        self.load_probe = load_probe or default_load_probe
        self.cpu_probe = cpu_probe or default_cpu_probe
        self.notifier = default_notify if notifier is _DEFAULT else notifier   # looked up at call time
        self.committed = 0
        self.reserved = 0
        self.selected = self.attempted = self.skipped = self.failed = 0
        self.lock_lost = False
        self.started_at = _now_iso()
        self.verifier: Optional["PriceVerifier"] = None

    @property
    def remaining(self) -> int:
        return max(self.cap - self.committed - self.reserved, 0)

    def counters(self) -> Dict[str, Any]:
        return {"cap": self.cap, "committed": self.committed, "reserved": self.reserved, "selected": self.selected,
                "attempted": self.attempted, "skipped": self.skipped, "failed": self.failed}

    async def _persist_run(self, db, status: str = "running") -> None:
        try:
            await _put(db, f"{PREFIX}run__{self.task}",
                       json.dumps({"run_id": self.run_id, "status": status, "started_at": self.started_at,
                                   "updated_at": _now_iso(), **self.counters()}))
        except Exception as exc:
            raise GuardUnavailable(f"run counters not persisted: {type(exc).__name__}: {exc}") from exc

    # -- the stop check (read-only; never commits) ------------------------------------------------------------
    async def check(self, db) -> Optional[StopDecision]:
        try:
            return await self._check(db)
        except Exception as exc:                       # includes GuardUnavailable: the controller fails closed
            return StopDecision(self.task, "task", f"controller unavailable: {type(exc).__name__}: {exc}")

    async def _check(self, db) -> Optional[StopDecision]:
        if self.lock_lost:
            return StopDecision(self.task, "task", "task lock lost (expired or taken by another worker)")
        latches = await active_latches(db, self.task)
        for scope in _scopes_for(self.task):             # most global first
            if scope in latches:
                cls = "cycle" if scope == "all" else ("price" if scope == "price" else "task")
                return StopDecision(scope, cls, f"kill switch active ({scope}): {latches[scope].get('reason', '?')}",
                                    latch=True, measurements={"since": latches[scope].get("at"),
                                                              "by_run": latches[scope].get("run_id")})
        ekey = f"{PREFIX}errs__{self.task}"
        n_err = int((await _get_many(db, [ekey])).get(ekey) or 0)
        if n_err >= max_consecutive_errors():
            return StopDecision(self.task, "task", f"{n_err} consecutive errors", latch=True,
                                measurements={"consecutive_errors": n_err})
        if self.committed >= self.cap:           # the reservation is already bounded by reserve(); only commits count
            return StopDecision(self.task, "task", "per-run write cap reached", latch=True,
                                measurements={"cap": self.cap, "committed": self.committed, "reserved": self.reserved})
        load = float(self.load_probe())
        if load > max_load():
            return StopDecision("all", "cycle", "system load above limit",
                                measurements={"load1": round(load, 2), "limit": max_load()})
        cpu = float(await self.cpu_probe())
        if cpu > max_cpu_pct():
            return StopDecision("all", "cycle", "host CPU above limit",
                                measurements={"cpu_pct": round(cpu), "limit": max_cpu_pct()})
        return None

    async def enact(self, db, decision: StopDecision) -> None:
        await _enact(db, decision, task=self.task, run_id=self.run_id, counters=self.counters(), notify=self.notifier)
        try:
            await self._persist_run(db, status="stopped")
        except GuardUnavailable:
            pass

    def stopped_report(self, decision: StopDecision, **extra) -> Dict[str, Any]:
        return {"task": self.task, "status": "stopped", "run_id": self.run_id, "stop": decision.as_dict(),
                "reason": decision.reason, "write_cap": self.counters(), **extra}

    # -- write cap ----------------------------------------------------------------------------------------------
    async def reserve(self, db, want: int) -> int:
        """Reserve up to `want` writes for the batch about to run and persist the reservation. Returns the grant
        (0 = no capacity left). Call at a transaction boundary (it commits)."""
        grant = max(min(int(want), self.remaining), 0)
        if grant:
            self.reserved += grant
            await self._persist_run(db)
        return grant

    async def settle(self, db, granted: int, committed: int, *, selected: int = 0, attempted: int = 0,
                     skipped: int = 0, failed: int = 0) -> None:
        """After the batch transaction ended: release the reservation and count the rows REALLY committed."""
        self.reserved = max(self.reserved - granted, 0)
        self.committed += max(int(committed), 0)
        self.selected += selected
        self.attempted += attempted
        self.skipped += skipped
        self.failed += failed
        await self._persist_run(db)

    # -- outcome ------------------------------------------------------------------------------------------------
    async def finish(self, db, result: Dict[str, Any]) -> None:
        """Run bookkeeping: consecutive-error counter (+ latch/escalation at the threshold) and the run record."""
        status = result.get("status")
        try:
            key = f"{PREFIX}errs__{self.task}"
            if status == "ok":
                await _put(db, key, "0")
            elif status == "error":
                try:
                    await db.rollback()
                except Exception:
                    pass
                row = (await db.execute(text("""
                    INSERT INTO system_settings (id, key, value, value_type, is_public, updated_at)
                    VALUES (gen_random_uuid(), :k, '1', 'string', false, NOW())
                    ON CONFLICT (key) DO UPDATE
                        SET value = (COALESCE(NULLIF(system_settings.value, '')::int, 0) + 1)::text, updated_at = NOW()
                    RETURNING value"""), {"k": key})).first()
                await db.commit()
                n = int(row[0])
                result["consecutive_errors"] = n
                if n >= max_consecutive_errors():
                    dec = StopDecision(self.task, "task", f"{n} consecutive errors", latch=True,
                                       measurements={"consecutive_errors": n,
                                                     "last_error": str(result.get("error"))[:200]})
                    await _enact(db, dec, task=self.task, run_id=self.run_id, counters=self.counters(),
                                 notify=self.notifier)
                    result["escalated"] = dec.as_dict()
            await self._persist_run(db, status=str(status))
        except Exception as exc:
            logger.error("maintenance_guard: run bookkeeping failed for %s: %s", self.task, exc)
            result.setdefault("guard_warning", f"bookkeeping failed: {type(exc).__name__}")


# ── Redis lock ──────────────────────────────────────────────────────────────────────────────────────────────────
_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_RENEW_LUA = ("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) "
              "else return 0 end")


class TaskLock:
    def __init__(self, redis, name: str, *, ttl_s: Optional[float] = None,
                 on_lost: Optional[Callable[[], None]] = None) -> None:
        self.redis = redis
        self.key = f"{LOCK_NAMESPACE}{name}"
        self.ttl_s = ttl_s or lock_ttl_s()
        self.token = uuid.uuid4().hex
        self.on_lost = on_lost
        self.held = False
        self._beat: Optional[asyncio.Task] = None

    async def acquire(self) -> bool:
        self.held = bool(await self.redis.set(self.key, self.token, px=int(self.ttl_s * 1000), nx=True))
        if self.held:
            self._beat = asyncio.create_task(self._heartbeat())
        return self.held

    async def holder_token(self) -> Optional[str]:
        v = await self.redis.get(self.key)
        return v.decode() if isinstance(v, bytes) else v

    async def _heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(max(self.ttl_s / 3.0, 0.05))
                ok = await self.redis.eval(_RENEW_LUA, 1, self.key, self.token, str(int(self.ttl_s * 1000)))
                if not ok:
                    self.held = False
                    if self.on_lost:
                        self.on_lost()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.held = False
            logger.error("maintenance lock heartbeat failed (%s): %s", self.key, exc)
            if self.on_lost:
                self.on_lost()

    async def release(self) -> bool:
        """Compare-and-delete: never removes a lock another worker now owns."""
        if self._beat:
            self._beat.cancel()
            try:
                await self._beat
            except (asyncio.CancelledError, Exception):
                pass
            self._beat = None
        was_held, self.held = self.held, False
        if not was_held:
            return False
        try:
            return bool(await self.redis.eval(_RELEASE_LUA, 1, self.key, self.token))
        except Exception as exc:
            logger.warning("maintenance lock release failed (%s): %s", self.key, exc)
            return False


# ── price workflow verification (A3) ────────────────────────────────────────────────────────────────────────────
class PriceVerifier:
    """Bounded post-run check of the base_price workflow, run by a price task after its last batch.

    snapshot(): before the first write, the SAMPLE_SIZE parts that come next in primary-key order (the run processes
    them first unless it stops early): base_price, ownership source, the customer-visible price from the REAL customer
    surface (routes.parts._current_part_offer, a pure SELECT), the catalogue estimate and a fingerprint of the part's
    offers.
    verify(): after the run, for every sampled part the run really passed
      1. base_price == the policy target when the part is owned by THIS task's scope;
      2. base_price unchanged when the part is owned by the OTHER task or by nobody (disjoint ownership);
      3. the customer-visible offer price / estimate is identical to the snapshot unless the offers themselves changed
         meanwhile (a harvester moving a supplier price is not an effect of the task).
    Cost: three keyset/ANY(ids) queries of SAMPLE_SIZE rows per call + SAMPLE_SIZE cheap lookups, 10 s timeout."""

    SAMPLE_SIZE = 100

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self.rows: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    async def _surface(db, part_id: str, row) -> Dict[str, Any]:
        from routes.parts import _current_part_offer
        import price_policy as pp
        offer = await _current_part_offer(db, part_id)
        est = pp.catalog_estimate_price(row.importer_price_ils, row.max_price_ils, row.online_price_ils)
        fp = (await db.execute(text("""
            SELECT COALESCE(md5(string_agg(concat_ws('|', id, price_ils, price_usd, is_available, supplier_url),
                                           ',' ORDER BY id)), '')
            FROM supplier_parts WHERE part_id = CAST(:p AS uuid)"""), {"p": part_id})).scalar()
        return {"offer": (offer or {}).get("price_ils"), "estimate": est.total if est else None, "fp": fp}

    @staticmethod
    async def _inputs(db, ids: List[str], rate: float):
        import db_update_agent as dua
        await db.execute(text("SET LOCAL statement_timeout = '10s'"))
        rows = (await db.execute(text(dua._base_price_inputs_sql(True)), {"ids": ids, "rate": rate})).fetchall()
        return {r.id: r for r in rows}

    async def snapshot(self, db, cursor: str, rate: float) -> None:
        import db_update_agent as dua
        await db.execute(text("SET LOCAL statement_timeout = '10s'"))
        ids = [r[0] for r in (await db.execute(text("""
            SELECT id::text FROM parts_catalog WHERE is_active AND id > CAST(:c AS uuid) ORDER BY id LIMIT :n"""),
            {"c": cursor, "n": self.SAMPLE_SIZE})).fetchall()]
        for pid, r in (await self._inputs(db, ids, rate)).items():
            _, source = dua.base_price_target(r, rate)
            self.rows[pid] = {"base_price": None if r.base_price is None else round(float(r.base_price), 2),
                              "source": source, "surface": await self._surface(db, pid, r)}
        await db.commit()

    async def verify(self, db, rate: float, last_processed: Optional[str]) -> List[str]:
        """Violations among the sampled parts the run has passed (empty list = verified)."""
        import db_update_agent as dua
        problems: List[str] = []
        ids = sorted(p for p in self.rows if last_processed is not None and p <= last_processed)
        if not ids:
            return problems
        rows = await self._inputs(db, ids, rate)
        for pid in ids:
            before, r = self.rows[pid], rows.get(pid)
            if r is None:
                continue
            target, source = dua.base_price_target(r, rate)
            now = None if r.base_price is None else round(float(r.base_price), 2)
            if source == self.scope and before["source"] == self.scope:
                if now != target:
                    problems.append(f"{pid}: owned base_price {now} != policy target {target}")
            elif before["source"] != self.scope and now != before["base_price"]:
                problems.append(f"{pid}: base_price changed {before['base_price']} -> {now} on a part this task does not own")
            surf = await self._surface(db, pid, r)
            if surf["fp"] == before["surface"]["fp"] and (surf["offer"], surf["estimate"]) != (
                    before["surface"]["offer"], before["surface"]["estimate"]):
                problems.append(f"{pid}: customer price changed {before['surface']['offer']}/{before['surface']['estimate']}"
                                f" -> {surf['offer']}/{surf['estimate']} with identical offers")
        await db.commit()
        return problems


# ── the single entry point ─────────────────────────────────────────────────────────────────────────────────────
async def _default_redis():
    from BACKEND_AUTH_SECURITY import get_redis
    return await get_redis()


async def run_guarded(db, task: str, runner: Callable[[Any, MaintenanceGuard], Awaitable[Dict[str, Any]]], *,
                      only_part_ids: Optional[list] = None, guard: Optional[MaintenanceGuard] = None,
                      redis_factory: Optional[Callable[[], Awaitable[Any]]] = None) -> Dict[str, Any]:
    """Run a guarded task through the shared contract. `only_part_ids` (an explicit short id list: tests, healers)
    keeps the kill switch / load / cap checks but takes no lock - it has no cursor and writes only those ids."""
    guard = guard or MaintenanceGuard(task)
    decision = await guard.check(db)
    if decision:
        await guard.enact(db, decision)
        return guard.stopped_report(decision)
    lock: Optional[TaskLock] = None
    if only_part_ids is None:
        try:
            redis = await (redis_factory or _default_redis)()
            if redis is None:
                raise RuntimeError("redis unavailable")
            lock = TaskLock(redis, lock_name_for(task), on_lost=lambda: setattr(guard, "lock_lost", True))
            if not await lock.acquire():
                holder = None
                try:
                    holder = await lock.holder_token()
                except Exception:
                    pass
                lock = None
                return {"task": task, "status": "skipped", "lock_conflict": True, "run_id": guard.run_id,
                        "reason": f"lock_conflict: {LOCK_NAMESPACE}{lock_name_for(task)} is held by another run",
                        "holder": (holder or "")[:8]}
        except Exception as exc:                          # fail closed: no lock, no writes
            dec = StopDecision(task, "task", f"lock unavailable: {type(exc).__name__}: {exc}")
            await guard.enact(db, dec)
            return guard.stopped_report(dec)
    try:
        result = await runner(db, guard)
    except asyncio.CancelledError:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.error("%s raised: %s", task, exc, exc_info=True)
        result = {"task": task, "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    finally:
        if lock is not None:
            await lock.release()
    result.setdefault("run_id", guard.run_id)
    result.setdefault("write_cap", guard.counters())
    await guard.finish(db, result)
    return result
