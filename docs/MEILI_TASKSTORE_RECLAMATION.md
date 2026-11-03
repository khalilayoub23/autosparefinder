# Meilisearch task-store reclamation plan (FIXES_TRACKER #118)

> ## FINAL DECISION (2026-11-03, supersedes §4 Option A and the "Expected recovery" estimate below)
> **STATUS: approval-ready plan. NOTHING in it has been executed against production.**
>
> ### Verified by a throwaway rehearsal (no production contact)
> Two scratch `getmeili/meilisearch:v1.7` containers on a private Docker network and a scratch volume (all removed afterwards; volume count back to 51, no stopped containers). 20,000 docs + 5 `documentDeletion` tasks of 10,000 uuid-like absent ids (same shape as production):
>
> | Step | `tasks/data.mdb` | dump | tasks after |
> |---|---|---|---|
> | source after load | 2,871,296 B | - | 8 (5 deletions) |
> | **dump -> fresh import (as in old Option A)** | **2,048,000 B** | 486,773 B | 8 - **all 5 deletion tasks and their id lists carried over** |
> | DELETE the 5 succeeded deletion tasks via API | 2,871,296 B (**file unchanged**, confirms LMDB never shrinks) | - | 4 |
> | **delete deletion tasks -> dump -> fresh import** | **81,920 B** | 56,920 B | 5 |
>
> **Conclusion:** the old plan (dump -> import alone) would NOT reclaim the task store: the dump re-imports finished tasks together with their id lists (~71% of the file remained). Reclamation works only in the order **(1) delete the finished `documentDeletion` tasks through the API, (2) dump, (3) import into a fresh volume**. Verified on the installed version (1.7.6 image); scale of the real data (23,150 tasks, 11 GB, 17.8 GB index, 4.14 M docs) is NOT rehearsed - the production dump/import duration and sizes are still estimates.
>
> ### Measured baseline (read-only)
> `tasks/data.mdb` 11,001,106,432 B at 2026-11-03 17:20 UTC (11,001,094,144 B earlier the same day: **+12 KB, growth stopped**); 31,577 tasks, all succeeded (23,150 `documentDeletion`, 7,954 `documentAdditionOrUpdate`, 472 `settingsUpdate`); 0 deletion tasks created since the fix (including the manual purge: 495,134 checked, 0 present); indexes 17.83 GB; Meilisearch 1.7.6; no snapshots/dumps present; free on `/` 62.0 GB (71% used). Attribution (measured + arithmetic): 11.0 GB / 23,150 deletion tasks ~= 475 KB per deletion task vs 10,000 uuids x ~36 B = 360 KB of raw id text, so the id lists explain the bulk of the file; the rehearsal confirms ids are stored in the task record. The remaining ~8,400 other tasks are small by comparison (estimate, not separately measured).
>
> ### Is reclamation needed?
> **Optional, not urgent.** Growth has stopped, the file is reused by LMDB, `/` has 62 GB free, and the new disk alert fires at 80%. Reclaiming ~11 GB (and probably some of the 17.8 GB index bloat) buys headroom but costs a search-downtime window and risk. Recommended: **defer** unless disk reaches ~80%, or do it in a planned window. If approved, the plan is:
>
> ### Supported options (1.7.6)
> | Option | Reclaims | Risk | Verdict |
> |---|---|---|---|
> | **A'. delete finished deletion tasks -> dump -> import to fresh volume** | task store ~11 GB -> ~0.1 GB; index file re-written compactly (size unverified) | search downtime for import; rehearsed only at 20k docs | **recommended if reclaiming** |
> | B. rebuild index from Postgres (new volume, `MEILI_REBUILD=1 meili_sync.py`) | both files | search empty/stale for hours; DB load; no Meili artefact dependency | fallback |
> | C. `DELETE /tasks` only | 0 bytes (LMDB keeps file) | slow large write txn | useless alone; only step 1 of A' |
> | D. dump import WITHOUT deleting tasks | ~0 (rehearsal: 29% smaller) | downtime for nothing | **rejected** |
> | E. `mdb_copy -c` | tasks file | unsupported, tool absent | rejected |
>
> ### Prerequisites / abort conditions
> 1. Owner approval for each of: task deletion, dump creation, stopping/recreating `autospare_meilisearch`, switching volumes, deleting the old volume (separate, >= 7 days later).
> 2. `restart_preflight.py` (inside backend) says SAFE; not 10:00-14:00 IL; no `meili_sync`/`meili_purge` running (check `ps`); not within the 21:30/00:15/07:15 `run_all_tasks` windows; `autospare_postgres_backup_catalog` healthy.
> 3. Disk: peak extra = dump (<= ~6 GB, bound: gzip of the documents; rehearsal ratio dump/db ~ 0.02-0.17) + new volume (<= 17.8 GB, bound = current index file; expected less) <= **~24 GB**; the old 28.8 GB volume stays untouched. Required: free >= 40 GB at start (62 GB now) - abort below that. Source and destination coexist safely: separate volumes, old one read-only after stop.
> 4. Capture before: `/stats`, `/indexes/parts/settings`, `/keys`, 20 stored queries' top-5 ids, `SELECT COUNT(*) FROM parts_catalog WHERE is_active`, task counts.
>
> ### Execution plan (approval-gated; commands are drafts to confirm at execution time)
> 1. Task deletion in slices (inside backend, `MEILI_MASTER_KEY` from env; each `DELETE` returns a `taskDeletion` task - wait for `succeeded` and watch `docker stats` / `/stats` between slices):
>    `DELETE /tasks?types=documentDeletion&statuses=succeeded&beforeFinishedAt=2026-10-04T00:00:00Z` -> then `2026-10-20`, `2026-11-01`, `2026-11-04`. Never touch `enqueued`/`processing`/`failed`; keep `settingsUpdate` and `documentAdditionOrUpdate`. Expect `matchedTasks` totals = 23,150 - (any after cutoff). Disk usage does not change at this step.
> 2. `POST /dumps` -> wait for `dumpCreation` succeeded; copy `/meili_data/dumps/<uid>.dump` out of the volume to a host path (e.g. `/root/meili-reclaim/`), record size + sha256. This is the rollback artefact for the index content.
> 3. `docker stop autospare_meilisearch` (backend search degrades to its documented fallback - verify in the window). Do NOT delete the volume.
> 4. Create `autosparefinder_meili_data_new`; start Meilisearch 1.7.6 with `--import-dump <path>` on it (same image id `010341a778fe`, same env incl. `MEILI_MASTER_KEY`); compose change touches only the `meilisearch` service volume name; first `docker compose up --dry-run -d --no-deps meilisearch`, then `up -d --no-deps meilisearch` (never without `--no-deps`).
> 5. Wait for import (`/health`, `/tasks?statuses=enqueued,processing` empty). Duration unknown (estimate 30-120 min for 4.14 M docs; if > 4 h, abort to rollback and use Plan B).
> 6. Verify (all must pass): `numberOfDocuments` == pre-change count (4,137,772 +/- sync delta); settings JSON identical; `/keys` present; 200 random ids fetched match Postgres (`oem_number`, `is_active`); the 20 stored queries return the same top-5 ids; website + chat search live; `tasks/data.mdb` < 1 GB; next `meili_sync` cycle + `_meili_verify_parity()` pass; purge run record shows 0 submitted.
> 7. Keep the old volume 7 days; delete only on a separate owner approval.
>
> ### Rollback
> Before step 6 passes: `docker stop` the new container, point compose `meili_data` back to `autosparefinder_meili_data`, `docker compose up -d --no-deps meilisearch` (old data untouched, only missing tasks deleted in step 1 - a loss of purge-noise history only). If the import fails or disk < 15 GB free: same rollback; Plan B needs no Meilisearch artefact.
>
> ### Downtime / impact
> Search unavailable from step 3 until step 5 completes (est. 0.5-2 h, unverified at scale); no impact on orders, Postgres, harvesters; `meili_sync`/purge must not be running (they would target a stopped Meilisearch and fail harmlessly, but are blocked by the preflight anyway).

---

Written 2026-11-03. **PLAN ONLY — nothing here has been executed.** Production Meilisearch (`getmeili/meilisearch:v1.7`, server 1.7.6)
was not restarted, its index and task store were not modified. Items marked *(unverified)* come from general Meilisearch knowledge,
not from a check on this server; confirm them in the rehearsal (step R) before the real run.

## 1. Measured facts (read-only, 2026-11-03 06:3x IL)

| Item | Value |
|---|---|
| Volume `autosparefinder_meili_data` | 28.83 GB (`data.ms` 26.8 GiB) |
| `data.ms/tasks/data.mdb` (task store) | **11,001,094,144 B** |
| `data.ms/indexes/<uid>/data.mdb` (`parts`, 4,137,772 docs) | 17,827,700,736 B |
| Tasks | 31,493, all `succeeded` (0 failed / enqueued / processing); 23,150 `documentDeletion`, 7,875 `documentAdditionOrUpdate`, 467 `settingsUpdate`, 1 `taskDeletion` |
| Finished before −1 d / −7 d / −14 d / −30 d | 30,742 / 26,523 / 21,192 / 9,673 |
| Free disk on `/` | 49.4 GB (53,052,465,152 B avail) |
| Snapshots / dumps dirs | both empty (none configured) |
| Average task-store cost | ~349 KB per task (each `documentDeletion` stores its 10,000-id list) |

## 2. Stop the growth first (prerequisite)

The corrected `maintenance/meili_purge_inactive.py` (probe `documents/fetch` with `id IN [...]`, delete only ids still present) must be
**active** before any reclamation, otherwise the freed space refills at ~600 tasks/day. It is on disk and the bind mount makes it live
for the NEXT purge subprocess: `_meili_sync_loop` launches `python3 maintenance/meili_purge_inactive.py` fresh each cycle, so **no backend
restart is needed** — the next 2 h cycle after the file is on disk uses it. Confirm with `[meili_purge] still in index: 0 of ~495,000`
in a manual run (`docker exec autospare_backend python3 /app/maintenance/meili_purge_inactive.py`, ~10 min of read probes, creates no
task) and `/tasks?types=documentDeletion` total not increasing. *Activation is by the file already being on disk (code path read: `BACKEND_API_ROUTES.py:2766-2779` spawns it per cycle); it is NOT yet observed live — the first post-fix cycle's log line is the confirmation. The owner should be told that the next 2 h cycle will run the new code.*
Note: the probe costs ~6 s per 5,000 ids (measured) ≈ 10 min per 2 h cycle of read load on Meilisearch; lower with a shorter inactive set
if that matters (e.g. only ids with `updated_at` since the last purge — separate change).

## 3. Deleting tasks through the API (no space returned to the OS)

- `DELETE /tasks?statuses=succeeded&beforeFinishedAt=<ISO>` (Meilisearch ≥ 1.2; one prior `taskDeletion` task exists on this server,
  so the mechanism has been used before). It enqueues a `taskDeletion` task.
- Safe: `succeeded` tasks finished more than 1 day ago (30,742 tasks); there are 0 failed/enqueued/processing tasks. **Never** delete
  `enqueued`/`processing`. Keep the last 24 h plus the 467 `settingsUpdate` tasks if an audit trail of settings changes is wanted
  (filter `types=documentAdditionOrUpdate,documentDeletion` instead of all types).
- **LMDB never shrinks the file.** Freed pages go to the free list and are reused by new tasks; `tasks/data.mdb` stays ≈ 11 GB. So this
  step alone recovers **0 bytes of disk** (consistent with the earlier report); it only stops further file growth for roughly the number
  of tasks deleted. A large delete is itself a big LMDB write transaction *(unverified)*: run it in slices (`beforeFinishedAt` stepping
  30 d → 14 d → 7 d → 1 d), watching `/stats` `databaseSize` and `docker stats`.

## 4. Actually reducing the files

LMDB cannot be compacted in place. Options for 1.7.6:

| Option | Reduces | Downtime | Notes |
|---|---|---|---|
| **A. Dump → fresh volume** (`POST /dumps`, start a new empty `v1.7` with `--import-dump`) | tasks file to near-empty (a dump re-creates the instance; whether finished tasks are carried over is *unverified*) AND the 17.8 GB index file (re-indexed compactly) | search unavailable during import: re-indexing 4.1 M docs is **hours** *(unverified; measure in rehearsal)* | Supported, version-portable. Needs dump size (≈ compressed docs, est. 2–4 GB *(unverified)*) + the new volume (est. 8–12 GB) |
| **B. Rebuild index from Postgres** (new volume, `MEILI_REBUILD=1` `meili_sync.py`) | both files | search stale/empty until done (hours); sync is the project's own proven path | No dependency on dump semantics; load on postgres_catalog |
| C. Snapshot (`--schedule-snapshot`/`POST /snapshots`) | nothing | — | Byte-copy of `data.ms` (same bloat); useful only as the rollback point |
| D. `mdb_copy -c` on the stopped LMDB file | tasks file only | stop Meilisearch for the copy (minutes) | Tool not in the image; unsupported by Meilisearch *(unverified safe)* — not recommended |

**Recommendation: A, with B as the fallback**, in this order:
1. R — **rehearsal on a copy**: `docker run` a second v1.7 on a *copy* of the volume is 28.8 GB (fits: 49 GB free, but leaves 20 GB — do it only
   after the journal/Docker work, never beside a backup run) — or rehearse with a dump imported into a scratch instance. Record: dump size, dump
   duration, import duration, resulting `tasks/data.mdb` and `indexes` sizes, document count and a search parity sample.
2. Pre-checks in the real window: the purge fix active; no `meili_sync`/`purge` subprocess running; disk free ≥ 15 GB; backups healthy; the
   owner approved; outside 10:00–14:00 IL (Facebook scan) and the 21:30/00:15/07:15 `run_all_tasks` windows (`restart_preflight.py` must say SAFE
   for any container recreate).
3. Create the dump (`POST /dumps`, wait for `dumpCreation` succeeded) → `snapshots`/dump file is the rollback artefact.
4. Stop the meilisearch container, rename the old volume contents (do NOT delete), start v1.7 with `--import-dump` on a fresh volume
   (compose change on `meilisearch` only: `docker compose up -d --no-deps meilisearch`; check `up --dry-run --no-deps` first, §2 Deployment).
5. Wait for indexing, then verify (below), then run one full `meili_sync` incremental cycle.

**Disk needed:** dump (≈ 2–4 GB) + new volume (≈ 8–12 GB) while the old 28.8 GB volume is kept = ~15 GB temporary; peak free must stay > 15 GB.
**Downtime:** from stop → import finished (hours, *unverified*). Site search degrades (the chat/website falls back as it does when Meilisearch
is down — verify that fallback in the rehearsal before relying on it). **Operational impact outside that window:** none.
**Expected recovery** (*estimate, unverified*): tasks file 11 GB → < 0.1 GB, index file 17.8 GB → ~8–12 GB → roughly 15–20 GB returned.

## 5. Integrity verification (must all pass before cutover is final)

1. `/stats` `numberOfDocuments` == `SELECT COUNT(*) FROM parts_catalog WHERE is_active` ± the usual stale margin (`meili_sync` parity check).
2. Index settings identical (filterable/sortable/searchable/ranking/synonyms) — diff `/indexes/parts/settings` before/after.
3. 200 random active ids fetched by `documents/fetch` match Postgres on `oem_number`, `sku`, `is_active`.
4. 20 stored search queries (HE/AR/EN, OEM, make/model/year filter) return the same top-5 ids as before the change.
5. Website search + chat search live check; `_meili_verify_parity()` passes in the next sync cycle.
6. API keys still work (the dump carries keys *(unverified)*; the master key comes from env regardless).

## 6. Rollback

Until verification passes the old volume is untouched: stop the new container, repoint compose `meili_data` back to the preserved volume,
`docker compose up -d --no-deps meilisearch`. The preserved 28.8 GB volume is deleted only after 7 days of clean running and the owner's say-so.
If the import fails midway: same rollback; Plan B (rebuild from Postgres) needs no Meilisearch artefact at all.

## 7. Approvals still required (none granted for these)

1. Production task deletion (`DELETE /tasks`) — and its slice schedule.
2. Dump creation (a `dumpCreation` task + I/O on the live index).
3. Stopping/recreating `autospare_meilisearch` and the maintenance window (outside protected jobs, preflight SAFE).
4. Deleting the old volume after verification.
5. Whether the next `meili_sync` cycle after the fix may be left to run the new purge unattended (it is armed by the file on disk).
