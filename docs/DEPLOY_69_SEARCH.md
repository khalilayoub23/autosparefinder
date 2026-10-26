# Deploy / rollback — FIXES_TRACKER #69 §7–§16 (search, fitment rule, alerts)

**Status: NOT AUTHORIZED.** Run only after the owner's explicit approval for the stated window.
Every step is a command with an expected result; a step that does not give it is a STOP (do not continue,
do not improvise, report).

## 1. What is deployed

A backend **restart** (code is bind-mounted, `./backend:/app`). No image rebuild, no compose change, no
migration, no dependency change, no DDL/DML in any changed file (verified 2026-10-20).

| Module (live → repo) | Change | Section |
|---|---|---|
| `vehicle_model_match.py` (NEW) | the one fitment model rule (D1–D5); `bounded()` per-statement limit | §9, §16 |
| `manufacturer_normalization.py` | `customer_vehicle_model()` / `customer_vehicle_model_forms()` (numeric models, registry forms) | §16 |
| `routes/parts.py` | rule in strict + JSON + fast lanes; fitting-id pre-resolution (cap 200,000); per-bucket sessions + 30 s limit; fitment lookups under 30 s (`bounded`); empty model set → FALSE; `/api/parts/by-license-plate` model (§16-b viii/ix) | §9, §14, §16 |
| `BACKEND_AI_AGENTS.py` | chat fitment uses the rule + every model form (the "no model → no parts" change was REVERTED, §16-b ix) | §9, §16 |
| `routes/public_api.py` | partner fitment uses the rule | §9 |
| `routes/brands.py` | `/brands/with-parts` 10-min cache + single-flight | §11 |
| `hf_client.py` | Cerebras 402 billing breaker (no retry storm) | §12 |
| `BACKEND_API_ROUTES.py` | health Check 2b (daily Cerebras alert), NOA weekly-plan validation, EOD group-scan status, crash-alert loop fix | §12, §13 |
| `db_update_agent.py`, `agents/owner_console.py` | OOM-blocked tasks gated in `run_task`; console task list | §7/§8 |

Every other backend file is unchanged since the running process started (2026-10-18 22:34:13 UTC).
`maintenance/fitment_impact_check.py`, `maintenance/fitment_decision_package.py` and `devtests/*` are
standalone tools (nothing imports them).

**Configuration.** New variables, all with code defaults and **not** in `docker-compose.yml`:
`SEARCH_STATEMENT_TIMEOUT_MS=30000`, `SEARCH_FIT_ID_RESOLVE_CAP=200000`.
The backend service has an explicit `environment:` list and **no `env_file`**: a value put only in `.env`
never reaches the process (verified: none of these, nor `OWNER_WHATSAPP_ROUTE_VIA_LID`, is in the live
container's environment). Changing one = add it to the backend `environment:` list in `docker-compose.yml`
AND recreate (`docker compose up -d --no-deps backend` after `--dry-run`). There is therefore **no
env-flag rollback without a recreate**; the rollback below is a code rollback.

**Also carried by this restart (#70, WhatsApp, `docs/WHATSAPP_NOTIFICATIONS.md`):** `BACKEND_API_ROUTES.py` queue
validation / permanent-error drop / overflow + dead letters / daily summary / digest local time + restart marker;
`social/whatsapp_provider.py` (missing recipient refused); `routes/utils.py` (post-payment send result logged).
The bridge change (`whatsapp-bridge/index.js`, log masking) needs its OWN restart — §6.
**Also (#71):** `notify_backlog.py` (new), `db_cleanup_agent.py` (honest keyword alert, task3b targeting), `category_learning.py`
(`since` in pending list), `routes/webhooks.py` (late chat answers), `BACKEND_API_ROUTES.py` (posts notice, digest night runs, idle cadence).

**Pending deploy (#72, repository only — changed after the 2026-10-20 17:33/17:36 restarts):** `agents/owner_console.py` +
`category_learning.py` (multi-word keyword commands) need a backend restart; `whatsapp-bridge/index.js` + `safe_logging.js`
(`maskNumbers` on error paths) need a bridge restart (§6). The new `EUROSENDER_PRODUCTION_API_KEY` in `.env` needs a backend
RECREATE (`docker compose up --dry-run -d --no-deps backend`, then without `--dry-run`) — do this BEFORE the old API key
("Production Webhook Signing Secret ***0f27" in the Eurosender key list) is deleted.

## 2. Preconditions (all required, checked immediately before)

1. Owner approval for this window, in writing.
2. Window: NOT 07:00–~10:30 UTC (daily Facebook group scan, ~3 h, no catch-up) and NOT 22:00–05:00 UTC
   (night pipeline). Recommended: 11:00–19:00 UTC, outside a `run_all_tasks` cycle.
3. Group scan finished TODAY — evidence, not the clock:
   `docker exec autospare_backend python3 /app/scripts/restart_preflight.py --json` → no `group_scan` blocker,
   and the in-process marker for today says finished.
4. `docker exec autospare_backend python3 /app/scripts/restart_preflight.py; echo $?` → **0**.
5. DB: `SELECT count(*) FROM pg_stat_activity WHERE datname='autospare' AND state<>'idle' AND
   now()-query_start > interval '60 seconds'` → 0; `SELECT count(*) FROM pg_locks WHERE NOT granted` → 0;
   load average (1 min) < 4.
6. No comparison/impact/backup run of ours active: `docker exec autospare_backend sh -c 'ps -eo args | grep -E "search_fitment_comparison|numeric_model_e2e|fitment_impact_check|auto_backup" | grep -v grep'` → empty.
7. Rollback source intact: `cd /opt/autosparefinder/archive/rollback/69_live_source_2026-10-18T2234Z && sha256sum -c MANIFEST.sha256` → all 15 OK.
8. Snapshot of what is about to go live:
   `D=/opt/autosparefinder/archive/rollback/69_candidate_$(date -u +%Y%m%dT%H%MZ); mkdir -p $D && cd /opt/autosparefinder/backend && cp --parents BACKEND_API_ROUTES.py BACKEND_AI_AGENTS.py agents/owner_console.py db_update_agent.py hf_client.py routes/parts.py routes/public_api.py routes/brands.py manufacturer_normalization.py social/whatsapp_provider.py routes/utils.py routes/webhooks.py db_cleanup_agent.py category_learning.py vehicle_model_match.py notify_backlog.py $D/ && mkdir -p $D/whatsapp-bridge && cp /opt/autosparefinder/whatsapp-bridge/index.js $D/whatsapp-bridge/ && (cd $D && find . \( -name '*.py' -o -name '*.js' \) | sort | xargs sha256sum > MANIFEST.sha256)`
9. Syntax: `cd /opt/autosparefinder/backend && python3 -m py_compile <the 10 modules>` → no output.

## 3. Deploy

```bash
bash /opt/autosparefinder/backend/scripts/pre_restart.sh      # exit 4 = preflight blocked -> STOP
docker restart autospare_backend
bash /opt/autosparefinder/backend/scripts/post_restart.sh     # exit 3 = a worker not resumed -> report
```
Never `PRE_RESTART_FORCE=1` without a separate owner decision. Do not restart `whatsapp_bridge`, nginx or
Postgres (not part of this change).

## 4. Post-deploy checks (STOP conditions in bold)

1. Within 120 s: `docker inspect -f '{{.State.Health.Status}}' autospare_backend` → `healthy`. **Not healthy after 180 s → rollback.**
2. `docker logs --since 5m autospare_backend 2>&1 | grep -E "Traceback|ImportError|SyntaxError|NameError"` → empty. **Any → rollback.**
3. Startup lines present: night controller started with its 7 jobs; `HealthMonitor` pass complete; supervised
   tasks started (harvester, thumbnails, meili loop).
4. Live search API probe (real HTTP, API latency, independent fitment check):
   `docker exec -w /app autospare_backend python3 devtests/post_deploy_search_probe.py --out /tmp/post_deploy_probe.jsonl; echo $?`
   → **exit 0**. **Exit 2 (5xx, a part outside the allowed models, or > 30 s) → rollback.**
5. 30 min watch: no `statement timeout` burst in `docker logs` for `/api/v1/parts/search`; DB lock waiters 0;
   owner WhatsApp alerts normal (no new alert class).
6. Record evidence (probe JSONL, health, log excerpt, times) in FIXES_TRACKER #69.

## 5. Rollback (code)

```bash
bash /opt/autosparefinder/backend/scripts/pre_restart.sh                     # same preflight rules
cd /opt/autosparefinder/archive/rollback/69_live_source_2026-10-18T2234Z/backend
(cd .. && sha256sum -c MANIFEST.sha256)          # all 15 OK, else STOP
cp --parents BACKEND_API_ROUTES.py BACKEND_AI_AGENTS.py agents/owner_console.py db_update_agent.py hf_client.py routes/parts.py routes/public_api.py routes/brands.py manufacturer_normalization.py social/whatsapp_provider.py routes/utils.py routes/webhooks.py db_cleanup_agent.py category_learning.py /opt/autosparefinder/backend/
mv /opt/autosparefinder/backend/vehicle_model_match.py /opt/autosparefinder/archive/rollback/vehicle_model_match.py.rolled_back
mv /opt/autosparefinder/backend/notify_backlog.py /opt/autosparefinder/archive/rollback/notify_backlog.py.rolled_back
docker restart autospare_backend
bash /opt/autosparefinder/backend/scripts/post_restart.sh
```
Then checks 4.1–4.3. The rollback source was reconstructed from the session transcript (43 recorded edits
undone, each step proven by forward replay) and checked against all 8 traceback frames the live process
logged since its start (README in that folder). Re-deploy after a rollback = copy the `69_candidate_*`
snapshot back the same way.

Rolling back restores the known production defects (numeric models → every model of the make; unbounded
search transactions; Cerebras 402 retry storm) — it is a return to the 2026-10-18 state, not a fix.


## 6. Bridge restart (#70 log masking) — separate approval

Only `index.js` console lines change (recipient → last 4 digits). Preconditions: §2 items 1–5, queue empty or the
window open (a restart mid-flush is safe — the item stays at the head), and no send in the last minute
(`docker logs --since 1m whatsapp_bridge | grep -c "Sending to"` → 0).
```bash
docker restart whatsapp_bridge
```
Checks: `connection_events.log` shows `PROCESS_START` then `CONNECTED` within 60 s (no `QR_DISPLAYED`, no
`WRONG_ACCOUNT`); `curl -s 127.0.0.1:3001/health` → connected, account 972532426920; the next real send logs
`Sending to ...NNNN` (4 digits). **QR_DISPLAYED / logged out → STOP**, owner re-link (`show_qr.sh`), no retries.
Rollback: copy `archive/rollback/69_live_source_2026-10-18T2234Z/whatsapp-bridge/index.js` back, restart the bridge.
