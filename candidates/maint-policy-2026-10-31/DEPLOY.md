# #119 — maintenance tasks aligned with the price policy (2026-10-31) — NOT DEPLOYED

Three steps, **each a separate owner approval**, in this order. Any result other than the stated success → stop,
roll back that step, report. Never `PRE_RESTART_FORCE`. Not 10:00–14:00 Israel, not inside `run_all_tasks`
(21:30 / 00:15 / 07:15 Israel, ~35 min). No schema change.

## A1 — deploy the code (restart of `autospare_backend`)
Changes `db_update_agent.py`, `db_cleanup_agent.py` (+ tests, `docs/PRICE_POLICY.md`). The six tasks stay in
`OOM_BLOCKED_TASKS` (nothing new runs on a schedule). What changes live at once: the cleanup agent's four
base_price writers (`task_normalize_base_price_batched`, `task_recover_priced_inactive`,
`task_recover_motorstore_prices`, `task_heal_importer_price`) write the ownership-rule value; volumes measured
2026-10-24..31: 2 parts / 7 days.
1. `cd candidates/maint-policy-2026-10-31/files && sha256sum -c --quiet ../SHA256SUMS` → exit 0
2. `cd /opt/autosparefinder && sha256sum -c --quiet candidates/maint-policy-2026-10-31/BASE.sha256` → exit 0
3. `D=/root/asf-maint-$(date -u +%Y%m%dT%H%M%SZ); mkdir -m 700 $D; tar czf $D/replaced.tgz $(cut -d' ' -f3 candidates/maint-policy-2026-10-31/BASE.sha256); chmod 400 $D/*`
4. `docker exec autospare_backend python3 /app/scripts/restart_preflight.py` (inside the container) → SAFE; then `bash backend/scripts/pre_restart.sh`
5. `(cd candidates/maint-policy-2026-10-31/files && tar cf - .) | tar xf -` ; `sed 's#  ./#  #' candidates/maint-policy-2026-10-31/SHA256SUMS | sha256sum -c --quiet`
6. `docker restart autospare_backend` → health 200, startup log 0 Traceback
7. `docker exec -i autospare_backend python3 - < candidates/price-policy-2026-10-29/evidence/verify_live_prices.py` → all PASS (customer prices unchanged)
Rollback: `tar xzf $D/replaced.tgz && rm -f backend/tests/test_maintenance_policy_alignment.py` → preflight → restart.

## A2 — data repairs of wrongly written fitment rows (production write)
`REPAIR_FITMENT.sql` (audit table `pvf_repair_119_backup` first; undo at the bottom). Measured read-only 2026-10-31:
R1 12,360 Ford rows (12,156 parts) "Ford <model>" → "<model>" (0 duplicates); R2 95 U25 rows → MINI Countryman;
R3 644 tyre rows (R17/R18/R19 motorcycle codes) removed; R4 27 Jaguar merchandise / classic-era rows removed.
After: search "Ford Focus" (`/api/v1/parts/search?vehicle_manufacturer=Ford&vehicle_model=Focus&vehicle_year=2015`) lists repaired parts.

## A3 — scheduler: release the six tasks (code change + restart)
`scheduler_A3.patch` (apply with `patch -p1` at the repo root after A1): empties `OOM_BLOCKED_TASKS`, puts the six
back into `run_all_tasks` in their original order, updates the three gate tests (tested: evidence/scheduler_variant_tests.raw.gz,
89 passed). Same preflight/restart/rollback as A1.

### Batch plan (from the read-only production dry run, evidence/dry_big.json, dry_names3.json)
| Task | Backlog (rows that would change) | Batch / budget | Expected duration |
|---|---|---|---|
| fix_base_prices | ≈ 0.91–0.93 M parts (of 1.22 M with a visible offer; 92% raise, 7% lower; 2.3% change > 50%) | 2,000 parts, `BASE_PRICE_TASK_MAX_S=900` | read 3.5 s/batch (p95 8.2 s) → ≈ 2 h per full pass → 3 days at 3 runs/day |
| normalize_base_price | ≈ 0.08–0.10 M parts (97% within 1%) | same | same scan |
| merge (compatible_vehicles) | 721,033 parts (657,431 first copy, 63,602 refresh; 5,983 multi-make parts restored) | 1,000 parts, `FITMENT_MERGE_MAX_S=900` | 0.5 s/batch read → 2–3 runs |
| BMW / Ford / Jaguar | 71 / 298 / 61 rows | 1,000 parts, `NAME_FITMENT_MAX_S=600` | one run each |
Every batch is one short transaction, `SKIP LOCKED`, `lock_timeout 5s`, `statement_timeout 120s`; the cursor is saved
after each batch (`system_settings cursor__<task>`), a failed batch stops the run and is retried next run.
Each written row bumps `updated_at` → Meilisearch incremental re-index of ≈ 1.7 M docs over the backfill days.

### Stop conditions (check the `run_all_tasks` report of each run)
- `failed > 0` or `status = error` twice in a row → stop (re-add the names to `OOM_BLOCKED_TASKS`, restart).
- fix_base_prices `updated` in one run > 600,000, or normalize > 150,000, or merge > 800,000 → stop and investigate.
- `verify_live_prices.py` after the first run must still be all PASS (base_price is never a customer price).
- load average > 18 or `postgres_catalog` CPU > 300% during the run → stop.
