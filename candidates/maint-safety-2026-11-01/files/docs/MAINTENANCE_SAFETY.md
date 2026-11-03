# Maintenance safety controller (#119, 2026-11-01)

Applies to the six tasks released by A3: `fix_base_prices`, `normalize_base_price`,
`merge_catalog_fitment_from_part_vehicle_fitment`, `backfill_bmw_fitment_from_name_he`,
`backfill_ford_fitment_from_name_he`, `backfill_jaguar_fitment_from_name`.
Code: `backend/maintenance_guard.py`, the runners in `backend/db_update_agent.py`, CLI `backend/maintenance/maint_guard_cli.py`.
Tests: `tests/test_maintenance_guard.py` (guard, caps, locks, recovery, `run_all_tasks` halting, unavailable metrics), `tests/test_price_audit_fix.py`, `tests/test_base_price_snapshot.py`, `tests/test_base_price_writers.py`, `tests/test_importers_e2e.py`.

## One contract for every entry point

The guard is inside the task functions, not around `run_all_tasks`. `run_all_tasks`, `run_task()` (admin API
`/api/v1/admin/db-agent/run/{task}`, the owner console, `run_step4_worker_cycle`, `run_targeted_*` scripts) and a direct
call all end in `maintenance_guard.run_guarded()`:

1. **stop check** (kill switch, consecutive errors, cap, load, CPU) — a refusal returns `status: "stopped"`.
2. **Redis lock** `autospare:lock:maint:<name>` — token-owned, `MAINT_LOCK_TTL_S` (120 s) expiry, heartbeat renewal every
   TTL/3, compare-and-delete release (a worker can never release or extend another worker's lock). Both price tasks share
   the lock `price`; every other task has its own. A held lock returns `status: "skipped", lock_conflict: true` (visible,
   never reported as success). No Redis ⇒ fail closed (`stopped`, "lock unavailable").
   `only_part_ids=[...]` calls (tests, healers) check the stop conditions but take no lock: no cursor, explicit ids.
3. **runner** — per batch: stop-check → reserve write capacity (persisted) → `SET LOCAL lock_timeout '5s'` +
   `statement_timeout '120s'` → read/decide → stop-check again immediately before the write → commit → settle the rows
   really committed → save the cursor.
4. **outcome** — consecutive-error counter, escalation, run record.

`run_task()` still refuses the six names while they are in `OOM_BLOCKED_TASKS`; **nothing in this change releases them.**

## Stop conditions

| Condition | Threshold (env) | Class | Latches? | Notifies |
|---|---|---|---|---|
| Kill switch (`trip all`) | persistent row | cycle | yes | no (operator did it) |
| Kill switch (`trip price` / `trip <task>`) | persistent row | price / task | yes | no |
| Two consecutive errors | `MAINT_GUARD_MAX_CONSECUTIVE_ERRORS`=2 | task | yes | once |
| Per-run write cap reached | per task, below | task | yes | once |
| Failed price verification (or verification could not run) | — | **price** (both price tasks) | yes | once |
| System load (1-min `os.getloadavg()`) | `MAINT_GUARD_MAX_LOAD`=18 | cycle | no — re-evaluated each run | no (event only) |
| Host CPU busy | `MAINT_GUARD_MAX_CPU_PCT`=480 (of 600) | cycle | no | no (event only) |
| Lock lost / lock unavailable / controller unavailable (settings store, run counters, **load or CPU metric unreadable**) | — | task | no (cannot write) | no |

* **Task stop**: only that task stops. **Price stop**: `fix_base_prices` and `normalize_base_price` both stop (the fitment
  tasks keep running). **Cycle stop**: every guarded task stops; inside `run_all_tasks` the remaining guarded tasks are not
  started (`HALTED`), the unguarded tasks of the cycle still run (they are independent). `run_all_tasks` reports
  `tasks_stopped` separately from `tasks_error`; it no longer logs a critical stop as "errored, continuing".
* **Latches live in `system_settings`** (`maint_guard__latch__<scope>`), so they survive restarts and new scheduled
  cycles. Nothing clears one except `reset`. A latching stop writes a stop event and — only when it created the latch —
  sends ONE owner notification (`notify_owner`, category `health`); a notification failure never undoes the stop.
* **The CPU threshold is NOT the documented "postgres_catalog > 300 %".** The backend container has no docker socket, so the
  postgres container's CPU cannot be read from inside it. The guard uses host-wide busy CPU (`/proc/stat`), default 80 % of
  the 6 vCPUs, separately tunable. Measured idle-to-working host CPU on 2026-11-01: 86–341 % — a literal 300 % host limit
  would trip constantly. The owner decides the value.
* **Load is not latched** on purpose: it is a condition, not a defect; the next run evaluates it again. Every load stop
  is persisted as an event.

## Write caps (per run, committed rows)

| Task | Default cap | Source |
|---|---|---|
| fix_base_prices | 600,000 | DEPLOY.md stop list (measured backlog ≈ 0.91–0.93 M over several runs; one run reads ≤ ~514 k at 3.5 s/batch) |
| normalize_base_price | 150,000 | DEPLOY.md (backlog ≈ 0.08–0.10 M) |
| merge_catalog_fitment… | 800,000 | DEPLOY.md (backlog 721,033 parts) |
| BMW / Ford / Jaguar | 5,000 each | NOT in DEPLOY.md; measured backlog 71 / 298 / 61 rows → generous ceiling |

`MAINT_CAP_FIX_BASE_PRICES`, `…_NORMALIZE_BASE_PRICE`, `…_MERGE_FITMENT`, `…_BACKFILL_BMW|FORD|JAGUAR`.
Batch size (2000 / 1000) and time budget (900 s / 600 s) are unchanged and separate. A batch reserves
`min(batch, remaining)` before it reads; the writer is handed that as a hard limit (`write_limit`): the first N changes in
primary-key order are written, the cursor stops on the last written part, the rest is read again by the next batch —
nothing is skipped. The counter counts `rowcount` of the committed statement, never selected/attempted rows
(`selected`, `attempted`, `committed`, `skipped`, `failed` are kept separately; `maint_guard__run__<task>` holds the last
run). Reaching the cap latches the task: a run that legitimately fills its cap is anomalous by construction.

## Recovery rules

* The cursor moves only after the batch is committed **and** its counters are persisted. A crash between commit and cursor
  save replays the batch; unchanged rows are never rewritten (`IS DISTINCT FROM`, `ON CONFLICT DO NOTHING`).
* A failed batch (timeout, error) rolls back, keeps the cursor, releases its reservation and ends the run as `error`. The
  second consecutive `error` latches the task: a permanently failing batch is escalated, never retried forever and never
  skipped. The name backfills do not advance the cursor past a batch that had a failed insert.
* The fitment tasks no longer run `ensure_part_vehicle_fitment_table()` (ALTER TABLE, CREATE INDEX, a whole-table
  self-join DELETE) on every run; they only check the table and its unique index exist (read-only).

## Price verification (price tasks, after the last batch)

`PriceVerifier` samples the next 100 parts after the run's start cursor (processed first) and checks, for the ones the run
passed: owned base_price == policy target; base_price **unchanged** on parts the task does not own (ownership boundaries
disjoint); the customer-visible offer price/estimate (read through `routes.parts._current_part_offer`) identical when
the part's offers are identical. Bounded: 3 queries of ≤100 rows + 100 pure SELECT lookups, 10 s timeout. A violation, or
an inability to verify, latches the **price** scope.

## Operating it (no restart)

```
docker exec -i autospare_backend python3 /app/maintenance/maint_guard_cli.py status
docker exec -i autospare_backend python3 /app/maintenance/maint_guard_cli.py trip all "reason"      # or price | <task>
docker exec -i autospare_backend python3 /app/maintenance/maint_guard_cli.py reset <all|price|task> "note"
```
`reset` is the only way a latch is cleared; it also zeroes that task's consecutive-error counter and logs an event.
The kill switch needs no restart. Thresholds and caps are environment values (`docker-compose.yml`), read when a run starts and independent of each other; changing one is a container recreate (a separate approval).

## Related tools (closure pass)
* `maintenance/price_audit_fix.py --apply` is the ONLY manual way to converge base_price in bulk: it runs `fix_base_prices` then `normalize_base_price` through `run_task()`, i.e. through everything above (and `OOM_BLOCKED_TASKS` while the gate is closed). Default is a read-only dry run. `margin_fix_batched.py` delegates to it; `vat_fix_batched.py` no longer touches base_price.
* `maintenance/base_price_snapshot.py` — export / verify / plan / restore of `(id, base_price)`; the DATA undo for A3 (see `DEPLOY.md`). Code rollback and data recovery are separate procedures.
