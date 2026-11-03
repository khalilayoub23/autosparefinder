# Supplier lifecycle candidate — deploy & rollback runbook (v2, 2026-10-27)

**Not deployed.** The candidate lives here, OUTSIDE the bind-mounted `backend/` (`restart: unless-stopped`; every
container start runs `alembic … upgrade head` and then uvicorn, and every lazy import reads whatever is on disk).

**Rule for the whole runbook: any result that is not exactly the stated success → STOP, change nothing further,
report to the owner.** Never use `PRE_RESTART_FORCE`, never restart/recreate any container other than
`autospare_backend`, never interrupt running jobs, never retry a step "to see if it works this time" except where a
retry is written below.

Approvals (each separate, from the owner): (A1) deploy · (A2) rollback · (A3) Recovery B (keep 0042, old code) ·
(A4) re-deploy + data restore · (A5) each feature switch after a successful deploy.

## 0. Data that a 0042 downgrade destroys (verified against the migration, rehearsed)

| Object | What is lost on downgrade | Written by (candidate code) |
|---|---|---|
| table `agent_authorizations` (+PK, `ix_agent_auth_lookup`, CHECK domain) | every owner approval record (audit trail, one-shot consumption state) | console approvals, activation, outreach grants |
| table `supplier_outreach` (+UNIQUE idempotency_key, 2 indexes, CHECK sent⇒authorization) | inquiry history **and the dedupe that prevents re-sending** | NIR outreach (flag), routing |
| table `procurement_orders` (+UNIQUE idempotency_key, index, 2 CHECKs) | procurement state incl. **supplier order numbers of real orders** and in-flight markers | payment-webhook gate (always on), console |
| table `procurement_events` (+FK→procurement_orders ON DELETE CASCADE, bigserial sequence) | the procurement audit trail, sequence position | procurement |
| columns `email_agent_messages.source_folder` (NOT NULL default 'inbox'), `supplier_routed_at`, `supplier_route` | which mail came from Spam and who it was routed to (rows stay) | Spam pass, routing |
| upgrade transform: historic rows get `supplier_routed_at = created_at` | reversed by dropping the column | — |
NOT touched by the downgrade (catalog DB): `agent_todos` rows that reference outreach/procurement ids,
`suppliers.credentials.{qualification,nir_rediscovery,boaz,activation_authorization_id}` — they survive with
dangling references. External effects (an AliExpress order, a sent email) are never undone by a downgrade.
The nightly `auto_backup` PII dump (23:00, last seen `autospare_pii_20261026_230000.sql`) cannot replace the
dedicated backup: it is up to 24 h old and older than 0042.

## 1. Preconditions (read-only) — all must hold, else STOP

1. Window: not 10:00–14:00 IL; owner reachable; no console commands by the owner during the procedure.
2. Bundle integrity: `cd candidates/supplier-lifecycle-2026-10-27/files && sha256sum -c --quiet ../SHA256SUMS` → no output, exit 0.
3. Live state: `docker exec autospare_postgres_pii sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'` → exactly `0041_email_agent_owner_flow`.
4. Record the deployed code (the rollback target): `D=/root/asf-deploy-$(date -u +%Y%m%dT%H%M%SZ); mkdir -m 700 $D`
   `cd /opt/autosparefinder/backend && find . -type f -not -path './backups/*' -not -path './uploads/*' -not -path './state/*' -not -path '*/__pycache__/*' -print0 | sort -z | xargs -0 sha256sum > $D/deployed.sha256`
   `cd /opt/autosparefinder && tar czf $D/deployed_manifest_files.tgz $(for f in $(cat candidates/supplier-lifecycle-2026-10-27/MANIFEST.txt); do [ -e "$f" ] && echo "$f"; done)`
   `for f in $(cat candidates/supplier-lifecycle-2026-10-27/MANIFEST.txt); do [ -e "$f" ] || echo "$f"; done > $D/new_paths.txt` ; `chmod 400 $D/*`
5. `df -h /root /opt` → ≥ 2 GB free on both.
6. `bash backend/scripts/pre_restart.sh` → prints `RESTART PREFLIGHT: SAFE` (exit 4 = STOP).
7. Owner approval A1 recorded.

## 2. Deploy

| Step | Command | Success | Else |
|---|---|---|---|
| 2.1 | `(cd candidates/supplier-lifecycle-2026-10-27/files && tar cf - backend) \| tar xf -` | exit 0 | → **F-C** |
| 2.2 | `cd candidates/supplier-lifecycle-2026-10-27/files && sha256sum -c --quiet ../SHA256SUMS` then the same list against `/opt/autosparefinder` (`cd /opt/autosparefinder && sed 's#  ./#  #' candidates/supplier-lifecycle-2026-10-27/SHA256SUMS \| sha256sum -c --quiet`) | no output | → **F-C** |
| 2.3 | `docker compose up --dry-run -d --no-deps backend` | only `autospare_backend` listed | STOP (restore code, F-C) |
| 2.4 | `docker compose up -d --no-deps --force-recreate backend` | exit 0 | → **F-B** |
| 2.5 | `docker logs autospare_backend --since 5m 2>&1 \| grep -E "Running upgrade 0041_email_agent_owner_flow -> 0042\|ERROR\|Traceback"` | the 0042 line, no ERROR/Traceback | version 0041 → **F-A**; else → **F-B** |
| 2.6 | PII `alembic_version` (precondition 3 command) | `0042_supplier_lifecycle` | 0041 → **F-A**; other → STOP |
| 2.7 | `curl -fsS http://localhost:8000/api/health` from inside the container (`docker exec autospare_backend python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/health').status)"`), 3 tries over 60 s | `200` | → **F-B** |
| 2.8 | `docker exec autospare_backend sh -c 'env' \| grep -E "^(BOAZ_TASKS_ENABLED\|EMAIL_AGENT_SCAN_SPAM\|SUPPLIER_ROUTING_ENABLED\|NIR_OUTREACH_ENABLED\|SUPPLIER_AUTO_ORDER_ENABLED)=1$"` | no output (all OFF) | STOP |
| 2.9 | after 15 min: `docker logs autospare_backend --since 15m 2>&1 \| grep -c Traceback` and the Email Agent heartbeat | 0, heartbeat ok | → **F-B** |

## 3. Failure branches (each rehearsed in isolation — `evidence/rehearsal2_result.txt`)

**F-A — migration failed, database still 0041** (R1). Nothing to downgrade; 0042 is one transaction, nothing is half-applied.
The candidate code is safe on 0041 meanwhile (supplier features refuse; owner flow works). Restore code:
`cd /opt/autosparefinder && tar xzf $D/deployed_manifest_files.tgz && xargs -a $D/new_paths.txt rm -f` →
`cd backend && sha256sum -c --quiet $D/deployed.sha256` (no output, else F-C) → preflight → recreate (2.3–2.4) → 2.6 shows 0041, 2.7 healthy.

**F-B — 0042 applied but the app is unhealthy / crash-looping** (R2). Approval A2. Keep the CANDIDATE code on disk until step 5.
1. Freeze writers: if the container answers, `docker exec autospare_backend python3 /app/maintenance/email_agent_cli.py halt`; all supplier switches stay OFF; the owner sends no console commands.
2. Back up (works with the backend down — it only uses the Postgres container): `bash candidates/supplier-lifecycle-2026-10-27/ops/backup_0042_data.sh $D/0042_data` → must end `BACKUP OK`, exit 0 (else **F-E**).
3. `bash candidates/supplier-lifecycle-2026-10-27/ops/verify_unchanged_0042.sh $D/0042_data` → `UNCHANGED` (exit 5 = new writes: back up again into a NEW dir, max 3 times, else STOP).
4. Downgrade with the candidate code, in a one-off container (also works while the service crash-loops):
   `docker compose run --rm --no-deps --entrypoint "" backend alembic -c alembic_pii.ini downgrade 0041_email_agent_owner_flow` → exit 0 (else **F-D**). *(Production form UNVERIFIED; the rehearsal ran the same image + mount via `docker run`.)*
5. Restore code exactly as in F-A and verify `deployed.sha256` (else **F-C**). 6. Preflight → recreate → 0041 + healthy.
Never restore the old code first: old code cannot downgrade (`Can't locate revision 0042`, exit 255) and every later container start would fail its migration step and skip `container_start.sh`.

**F-C — code copy/restoration incomplete (mixed tree)** (R6). Do NOT start or recreate the container. Re-run the extraction
(`tar xzf $D/deployed_manifest_files.tgz` + `new_paths.txt` removal, or the bundle copy for a deploy) and re-verify the hash list.
Still mismatched after one re-run → STOP and escalate; the running process keeps whatever it already loaded, but lazy imports read the mixed tree — owner must not use the console until resolved.

**F-D — downgrade failed, database still 0042, data intact** (R4). The downgrade has a 15 s lock timeout and is one transaction.
Find the blocker read-only (`SELECT pid, state, xact_start, left(query,80) FROM pg_stat_activity WHERE datname = current_database() AND state <> 'idle'`), wait for it to finish (never kill it without A2), retry at most 3 times. Still failing → **Recovery B** (approval A3):
restore the deployed code **but keep** `backend/alembic_pii/versions/0042_supplier_lifecycle.py`; the container start then runs `upgrade head` as a no-op and the deployed code runs on the 0042 schema (rehearsed: exit 0, owner flow OK). All 0042 data stays in place; schedule the downgrade later.

**F-E — backup failed or invalid** (R5). Do NOT downgrade (it would destroy unrecoverable data). Use **Recovery B** instead (A3).

**F-F — re-deploy later + data restore** (approval A4): after a clean re-deploy (2.x passes, tables empty):
`bash candidates/supplier-lifecycle-2026-10-27/ops/restore_0042_data.sh $D/0042_data` → `RESTORE OK` with the expected counts.
Exit ≠ 0 = the transaction rolled back and NOTHING was restored (rehearsed: checksum failure exit 6, FK/count failure exit 9) → keep every supplier switch OFF and escalate. Mail that arrived while rolled back stays unrouted (inbox / no route) — correct, it was never routed.

## 4. Code × schema compatibility (rehearsed)

| Code \ schema | 0041 | 0042 |
|---|---|---|
| deployed | ✅ | ✅ runs; container start fails the migration step unless the 0042 file is kept (Recovery B) |
| candidate | ✅ owner flow; supplier features refuse (`SupplierSchemaNotReady`) | ✅ |

## 5. Final verification (after deploy, rollback or recovery)

Code: `sha256sum -c` of the bundle list (deploy) or `$D/deployed.sha256` (rollback) → no output. Schema: `alembic_version` as expected.
Data (rollback): backup `MANIFEST.json` counts preserved in `$D/0042_data` (chmod 400); after F-F the restored counts equal it.
Health: 2.7 + 2.9. Report the results to the owner with the `$D` path. Keep `$D` until the owner approves deleting it.
