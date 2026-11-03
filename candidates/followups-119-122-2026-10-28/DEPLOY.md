# Follow-ups #121 + #122 (+ #119 cleanup) — deploy & rollback runbook (v1, 2026-10-28)

**NOT DEPLOYED.** Built and verified in isolation only. Deploying needs the owner's explicit approval (it recreates the
backend container — the compose command changes). Nothing here changes the database schema; PII stays
`0042_supplier_lifecycle`, catalog `0062_version_oob_schema`.

Rule: any result other than the stated success → STOP, change nothing further, report. Never `PRE_RESTART_FORCE`, never
touch another container, never interrupt running work.

## What changes

| Path | Change |
|---|---|
| `docker-compose.yml` (backend `command:` only) | `["/bin/bash", "/app/scripts/start_backend.sh", "--workers", "${API_WORKERS:-1}"]` instead of `A && B && container_start.sh & uvicorn` (#121). Resolved config: the command is the ONLY difference (`compose/docker-compose.patch`). |
| `backend/scripts/start_backend.sh` (new) | migrations in the foreground with exit-code check, bounded retry + timeout, then `container_start.sh &` + `exec uvicorn` (PID 1 as before); on failure the API is never started and the container HOLDS, unhealthy, no restart loop (#121) |
| `backend/boaz_tasks.py` | lease + phase + token fencing + stale-task recovery (#122). BOAZ_TASKS_ENABLED stays OFF. |
| `backend/tests/test_boaz_recovery.py` (new), `backend/tests/test_supplier_lifecycle.py` | #122 tests; one exact-dict assertion extended with the new zero counters |
| `backend/db_update_agent.py`, `backend/tests/test_db_agent_task_counting.py`, `backend/maintenance/run_step4_worker_cycle.py` | #119: the three measured-dead name backfills (BMW / Ford / Jaguar) deleted — already blocked by `OOM_BLOCKED_TASKS` and commented out of the run, so nothing that runs changes; guard test updated + `test_deleted_119_steps_cannot_run` |

## New start sequence (#121) — what the operator sees

1. `[start_backend] … migration catalog: attempt 1/3 …` → `OK`, then `migration pii: … OK`.
2. `[start_backend] … migrations complete - starting container_start.sh and the API`, then uvicorn's own lines.
3. `/app/state/startup_status.json` → `{"state":"starting_api", …}`.

**If a migration fails:** after `MIGRATION_RETRIES` (3) attempts `MIGRATION_RETRY_DELAY_S` (20 s) apart, each bounded by
`MIGRATION_TIMEOUT_S` (900 s), the log shows `FATAL: <catalog|pii> migration failed (exit N) … the API was NOT started`,
`startup_status.json` → `"state":"migration_failed"`, the healthcheck fails (`unhealthy`), nginx returns 502 for the API,
the container keeps running (RestartCount stays 0 — `restart: unless-stopped` cannot loop it). Recovery: read the alembic
error above the FATAL line; a lock timeout → find the blocker read-only
(`SELECT pid, state, xact_start, left(query,80) FROM pg_stat_activity WHERE datname = current_database() AND state <> 'idle'`),
let it finish; then `bash backend/scripts/pre_restart.sh` (SAFE) → `docker restart autospare_backend`. Every migration here is
transactional: a failed or killed attempt leaves the schema unchanged (isolated tests T2/T4/T5).
`STARTUP_ON_MIGRATION_FAILURE=exit` makes it exit instead (only for an orchestrator that should restart/page; with
`unless-stopped` that would retry the migration in a loop — not set in production).

One-off commands (`docker compose run -T --rm --no-deps --entrypoint "" backend …`, used by the 0042 rollback branch F-B)
replace the command entirely and are unaffected (test T9).

## 1. Preconditions (read-only)

1. Owner approval for this deploy recorded. Window not 10:00–14:00 IL and not during `run_all_tasks` (21:30 / 00:15 / 07:15 IL, ~35 min).
2. `cd candidates/followups-119-122-2026-10-28/files && sha256sum -c --quiet ../SHA256SUMS` → exit 0.
3. Live tree unchanged since the bundle was built: `cd /opt/autosparefinder && sha256sum -c --quiet candidates/followups-119-122-2026-10-28/BASE.sha256` → exit 0
   (the deployed versions of every file the bundle replaces, plus `docker-compose.yml`).
4. `D=/root/asf-followups-$(date -u +%Y%m%dT%H%M%SZ); mkdir -m 700 $D; cp docker-compose.yml $D/; tar czf $D/replaced.tgz $(cat candidates/followups-119-122-2026-10-28/MANIFEST.txt | while read f; do [ -e "$f" ] && echo "$f"; done); chmod 400 $D/*`
5. `bash backend/scripts/pre_restart.sh` → `RESTART PREFLIGHT: SAFE`.

## 2. Deploy

| Step | Command | Success | Else |
|---|---|---|---|
| 2.1 | `(cd candidates/followups-119-122-2026-10-28/files && tar cf - backend) \| tar xf -` and `cp candidates/followups-119-122-2026-10-28/compose/docker-compose.yml.candidate docker-compose.yml` | exit 0 | R |
| 2.2 | `sed 's#  ./#  #' candidates/followups-119-122-2026-10-28/SHA256SUMS \| sha256sum -c --quiet` | no output | R |
| 2.3 | `docker compose up --dry-run -d --no-deps backend` | only `autospare_backend` recreated | R |
| 2.4 | `docker compose up -d --no-deps --force-recreate backend` | exit 0 | R |
| 2.5 | `docker logs autospare_backend --since 3m 2>&1 \| grep -E "start_backend\|FATAL\|Traceback"` | both migrations `OK`, `migrations complete`, no FATAL/Traceback | FATAL → recovery above; else R |
| 2.6 | `docker exec autospare_backend sh -c 'cat /proc/1/cmdline \| tr "\0" " "; cat /app/state/startup_status.json'` | `uvicorn BACKEND_API_ROUTES:app … --workers 1`; `"state":"starting_api"` | R |
| 2.7 | health 200 (3 tries / 60 s) + `docker inspect -f '{{.State.Health.Status}}'` → `healthy` | yes | R |
| 2.8 | supplier switches: `docker exec autospare_backend env \| grep -E "^(BOAZ_TASKS_ENABLED\|EMAIL_AGENT_SCAN_SPAM\|SUPPLIER_ROUTING_ENABLED\|NIR_OUTREACH_ENABLED\|SUPPLIER_AUTO_ORDER_ENABLED)=1$"` | no output | STOP |
| 2.9 | `runtime_identity_check.py --pid 1 --expect agents.owner_console=1e1458c7389af2759c48ff3a0161f7622573bf33f6e294436e61c03775078059` | LOADED_MATCH | R |
| 2.10 | after 15 min: 0 Traceback; Email Agent heartbeat ok | yes | R |

**R (rollback, code + compose only, no schema involved):** `cp $D/docker-compose.yml docker-compose.yml && tar xzf $D/replaced.tgz`
and remove the bundle's new files (`backend/scripts/start_backend.sh`, `backend/tests/test_boaz_recovery.py`) → verify with
`BASE.sha256` → preflight → `docker compose up -d --no-deps --force-recreate backend` → health 200, old command in `docker inspect`.

## 3. Before BOAZ is ever switched on (#122 activation gate — separate owner approval A5)

1. This bundle deployed (the deployed BOAZ code has NO stale-task recovery).
2. `docker exec autospare_backend python3 -c "import boaz_tasks as b; print(b.LEASE_S, b.HANDLER_TIMEOUT_S, sorted(b.RETRY_SAFE))"` → `660 300 ['email', 'procurement_reply', 'supplier_handoff']`.
3. Read-only: BOAZ tasks `in_progress` WITHOUT a lease (legacy / escalated) are listed and decided by the owner first —
   recovery never touches them by design.
4. First enable for ONE pass with `BOAZ_TASK_BATCH=1`, read `metrics.boaz.history` of that task, then widen.

## 2026-10-31 — #119 part WITHDRAWN
The deletion of the BMW/Ford/Jaguar tasks in `files/backend/db_update_agent.py` (and the matching edits in
`maintenance/run_step4_worker_cycle.py`, `tests/test_db_agent_task_counting.py`) is withdrawn: the owner directive of 2026-10-31
is to fix, not delete. Superseded by `candidates/maint-policy-2026-10-31/`. Do NOT deploy those three files; #121/#122 files only.
