# Maintenance safety controller + base_price writer alignment — NOT DEPLOYED (closure pass 2026-11-01)

Candidate tree `files/` (manifest: `files.status`; hashes: `SHA256SUMS`; replaced-file baseline: `BASE.sha256`).
Evidence: `evidence/` (old-vs-new, full-suite outputs, registry generator). Writer inventory: `files/docs/BASE_PRICE_WRITERS.{md,json}`.
Safety design: `files/docs/MAINTENANCE_SAFETY.md`. A3 (releasing the six tasks) is the separate `scheduler_A3.patch` — **not part of the code deployment**.
Nothing here was applied to `/opt/autosparefinder/backend`, the frontend, any database or any container.

## Approval 1 — CODE deployment (restart of autospare_backend; not 10:00–14:00 IL, not inside run_all_tasks 21:30/00:15/07:15)
1. `cd candidates/maint-safety-2026-11-01/files && sha256sum -c --quiet ../SHA256SUMS`; `cd /opt/autosparefinder && sha256sum -c --quiet candidates/maint-safety-2026-11-01/BASE.sha256` (both exit 0; if BASE fails a file changed since: re-base first).
2. Code backup: `D=/root/asf-maintsafety-$(date -u +%Y%m%dT%H%M%SZ); mkdir -m 700 $D; tar czf $D/replaced.tgz $(awk '{print $2}' candidates/maint-safety-2026-11-01/BASE.sha256); chmod 400 $D/*`
3. `docker exec autospare_backend python3 /app/scripts/restart_preflight.py` → SAFE; `bash backend/scripts/pre_restart.sh` (never PRE_RESTART_FORCE).
4. `(cd candidates/maint-safety-2026-11-01/files && tar cf - backend docs) | tar xf -`; the frontend change (`files/frontend/src/pages/Admin.jsx`, `adminImportWarnings.js`) needs `npm run build` + the frontend container's normal deploy — **the JSX was not built here**; build it first.
5. `docker restart autospare_backend` → health 200, 0 Traceback; `docker exec -i autospare_backend python3 /app/maintenance/maint_guard_cli.py status` shows no latch.
6. `maintenance/runtime_identity_check.py --pid 1 --expect maintenance_guard=<sha>`; `verify_live_prices.py` all PASS.
Live effect at once: REX / car-parts.ie / OEMPartsOnline / relay importers stop overwriting `base_price`; the six tasks stay blocked and unscheduled.

### Code rollback (files only — does NOT touch data)
`tar xzf $D/replaced.tgz -C /opt/autosparefinder`; remove the NEW files listed `NEW` in `files.status`; preflight; `docker restart autospare_backend`; rebuild/redeploy the frontend from the previous commit.
Residual state: `system_settings` keys `maint_guard__*` (harmless; `DELETE FROM system_settings WHERE key LIKE 'maint_guard\_\_%'` after rollback).

## Approval 2 — production DATA repair
There is no separate repair script: wrong values from the corrected writers are inside the A3 backlog (`BASE_PRICE_WRITERS.md` §5). Possible exceptions: parts with a base_price and no offer and no
estimate input (0 of an 8,317-part sample) and unidentifiable Admin-upload rows. Decide after A3's first run.

### Data recovery (separate from code rollback) — pre-change export, verify, restore
1. Before A3: `docker exec -i autospare_backend python3 /app/maintenance/base_price_snapshot.py export --out /app/state/bp_snapshot_<UTC-stamp>` (one READ ONLY REPEATABLE READ transaction, streamed, ≈ 4.6 M rows).
   Record the printed `rows`, `nonnull`, `sha256`, `taken_at`; copy the directory off the volume (`docker cp`) — a snapshot only on the same volume is not a backup.
2. `… base_price_snapshot.py verify --dir <dir>` → `ok: true` (sha256 + row count vs the manifest, no DB access).
3. Restore (only if an A3 outcome must be undone, owner decision): `plan --dir <dir>` (how many live rows differ), then `MAINT_RESTORE_CONFIRM=<manifest sha256> … restore --dir <dir> --apply --batch 5000` — only base_price/updated_at of rows that differ; rows created after the snapshot are never touched; idempotent. Meilisearch re-indexes the touched rows on its normal incremental pass.
4. The A2 fitment undo artefacts are untouched: `pvf_repair_119_backup` (13,126 rows, read-only checked 2026-11-01), `/root/asf-maint-20261101T181200Z/replaced.tgz`, undo SQL at the bottom of `candidates/maint-policy-2026-10-31/REPAIR_FITMENT.sql`.

## Approval 3 — A3 (scheduling and activation)
Prerequisites: Approval 1 live and verified; the data export of step 1 taken and verified; owner decisions below. Then `patch -p1 < scheduler_A3.patch` at the repo root (dry-run on a copy of the candidate tree: applies, gate + guard tests pass with the six unblocked), preflight, restart.
First run: watch `maint_guard_cli.py status`; leave nothing unattended until `verify_live_prices.py` passes after the first price run. Instant stop without a restart: `maint_guard_cli.py trip all "reason"`.

## Open owner decisions (NOT technical passes)
1. CPU threshold — host-wide busy CPU, default 480 of 600 (the documented "postgres_catalog > 300 %" cannot be observed from the backend container).
2. Admin Excel price column — currently unsupported (never written, reported). Decide whether a sheet price should become an ex-VAT cost on a supplier offer.
3. Name-backfill write cap — 5,000 each (not in DEPLOY.md before; measured backlog 71 / 298 / 61).
4. `import_from_excel.py` fabricates a 100.0 fallback supplier-offer price (not base_price) — follow-up, not touched.
5. Importer end-to-end status: all 46 former BLOCKED importers now have a real script-level run against the isolated DB (45 with synthetic fixtures, 1 with the saved workbook; none against a live source). 0 remain BLOCKED.

## Addendum 2026-11-02 — importer path audit (`files/docs/IMPORTER_PATH_AUDIT.md`)
Code-only; rides Approval 1 (same restart). New root module `backend/app_paths.py` (on `PYTHONPATH=/app`, nothing else to mount). Data placement changes: Jaguar NDJSON/progress/logs and Champion JSON now under `/app/state` (volume); LR/Mini/BYD/Delek-brand JSONs under `/app` (bind). New env (additive): `JAGUAR_NDJSON`, `DELEK_BRANDS_DATA_DIR`, `MINI_PARTS_JSON`, `BYDIL_PARTS_JSON`, `CM_JSON` (existing name), `JSON_FILE` (existing). Behaviour change: 10 importers (+ repair_manufacturer_import, import_delek_brands) now EXIT NON-ZERO when none of their source files exist (they exited 0 before) — check any wrapper that treated rc 0 as "ran". No schedule, setting or database change.

## Addendum 2026-11-02 (pass 2) — state-file paths (`files/docs/IMPORTER_PATH_AUDIT.md` §8)
No mount, volume, permission, schedule or setting change. Defaults are byte-identical (`/app/state/...`). Behaviour changes riding Approval 1: (a) a RELATIVE value of any of the 17 overrides now resolves against the app root (`/app`), not the cwd — no override is set in `docker-compose.yml`/`.env` today (checked), so no live effect; (b) `MEILI_SYNC_CHECKPOINT` now also moves the writer (`meili_sync.py`); (c) new WARNING/stderr lines when a state write fails (inproc marker, cursor, caches, runtime-identity). `scripts/restart_workers.py` now imports `app_paths` from its own app root (works without `PYTHONPATH`) — verify `python3 /app/scripts/restart_workers.py list` after the restart.
