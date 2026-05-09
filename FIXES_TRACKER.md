# AutoSpareFinder — Bug & Breaking Points Fix Tracker
> Last scan: 2026-03-28 | Total issues found: 49 | Fixed: 49 | In Progress: 0 | Open: 0

---

## Legend
- ✅ Fixed
- 🔄 In Progress
- ❌ Not Started
- ⚠️ Noted (no code change needed / external action required)

---

## Session — 2026-04-16 (Fitment C1 Continuation)

| Item | Status | Summary | Evidence |
|---|---|---|---|
| Step 5 preflight automation | ✅ | Added `backend/run_step5_preflight.py` and `scripts/run_step5_preflight.sh`; fixed unset-env fallback bug under `set -u`. | `logs/phase_c1_step5/preflight_latest.json` |
| Step 4 worker batch automation | ✅ | Added reusable worker runner/wrappers and nightly cron installer. | `backend/run_step4_worker_cycle.py`, `scripts/run_step4_worker_batch.sh`, `scripts/run_step4_nightly.sh`, `scripts/install_step4_worker_cron.sh` |
| Step 4 transient deadlock resilience | ✅ | Added deadlock-aware retry/rollback in worker cycle runner after first batch-6 deadlock. | `backend/run_step4_worker_cycle.py`, `logs/phase_c1_step4_batch_6/worker_pass_report.json` |
| Step 4 batch 6 execution | ✅ | Successful rerun after resilience patch (`backfill updated_parts=35546`, `merge updated_parts=17243`). | `logs/phase_c1_step4_batch_6/worker_pass_report.json` |
| Step 4 batch 7 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=17297`). | `logs/phase_c1_step4_batch_7/worker_pass_report.json` |
| Step 4 batch 8 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=17280`). | `logs/phase_c1_step4_batch_8/worker_pass_report.json` |
| Step 4 batch 9 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=17287`). | `logs/phase_c1_step4_batch_9/worker_pass_report.json` |
| Step 4 batch 10 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=17289`). | `logs/phase_c1_step4_batch_10/worker_pass_report.json` |
| Step 4 batch 11 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=16999`) with +4 JSON-fitment parts in KPI delta. | `logs/phase_c1_step4_batch_11/worker_pass_report.json`, `logs/phase_c1_step4_batch_11/kpi_delta.diff` |
| Step 4 batch 12 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=16333`) with +6 JSON-fitment parts in KPI delta. | `logs/phase_c1_step4_batch_12/worker_pass_report.json`, `logs/phase_c1_step4_batch_12/kpi_delta.diff` |
| Step 4 batch 13 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=16329`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_13/worker_pass_report.json`, `logs/phase_c1_step4_batch_13/kpi_delta.diff` |
| Step 4 batch 14 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=16326`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_14/worker_pass_report.json`, `logs/phase_c1_step4_batch_14/kpi_delta.diff` |
| Step 4 batch 15 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=14176`) with +2 JSON-fitment parts in KPI delta. | `logs/phase_c1_step4_batch_15/worker_pass_report.json`, `logs/phase_c1_step4_batch_15/kpi_delta.diff` |
| Step 4 batch 16 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12667`) with +5 JSON-fitment parts and rounded JSON-fitment percentage reaching 11.39. | `logs/phase_c1_step4_batch_16/worker_pass_report.json`, `logs/phase_c1_step4_batch_16/kpi_delta.diff` |
| Step 4 batch 17 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12644`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_17/worker_pass_report.json`, `logs/phase_c1_step4_batch_17/kpi_delta.diff` |
| Step 4 batch 18 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12641`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_18/worker_pass_report.json`, `logs/phase_c1_step4_batch_18/kpi_delta.diff` |
| Step 4 batch 19 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12643`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_19/worker_pass_report.json`, `logs/phase_c1_step4_batch_19/kpi_delta.diff` |
| Step 4 batch 20 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12650`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_20/worker_pass_report.json`, `logs/phase_c1_step4_batch_20/kpi_delta.diff` |
| Step 4 batch 21 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12632`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_21/worker_pass_report.json`, `logs/phase_c1_step4_batch_21/kpi_delta.diff` |
| Step 4 batch 22 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12643`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_22/worker_pass_report.json`, `logs/phase_c1_step4_batch_22/kpi_delta.diff` |
| Step 4 batch 23 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12632`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_23/worker_pass_report.json`, `logs/phase_c1_step4_batch_23/kpi_delta.diff` |
| Step 4 batch 24 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12650`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_24/worker_pass_report.json`, `logs/phase_c1_step4_batch_24/kpi_delta.diff` |
| Step 4 batch 25 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12643`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_25/worker_pass_report.json`, `logs/phase_c1_step4_batch_25/kpi_delta.diff` |
| Step 4 batch 26 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12996`) with +3 JSON-fitment parts in KPI delta. | `logs/phase_c1_step4_batch_26/worker_pass_report.json`, `logs/phase_c1_step4_batch_26/kpi_delta.diff` |
| Step 4 batch 27 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12935`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_27/worker_pass_report.json`, `logs/phase_c1_step4_batch_27/kpi_delta.diff` |
| Step 4 batch 28 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12942`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_28/worker_pass_report.json`, `logs/phase_c1_step4_batch_28/kpi_delta.diff` |
| Step 4 batch 29 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12941`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_29/worker_pass_report.json`, `logs/phase_c1_step4_batch_29/kpi_delta.diff` |
| Step 4 batch 30 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12937`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_30/worker_pass_report.json`, `logs/phase_c1_step4_batch_30/kpi_delta.diff` |
| Step 4 batch 31 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12922`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_31/worker_pass_report.json`, `logs/phase_c1_step4_batch_31/kpi_delta.diff` |
| Step 4 batch 32 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12936`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_32/worker_pass_report.json`, `logs/phase_c1_step4_batch_32/kpi_delta.diff` |
| Step 4 batch 33 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=12213`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_33/worker_pass_report.json`, `logs/phase_c1_step4_batch_33/kpi_delta.diff` |
| Step 4 batch 34 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=11903`) with +3 JSON-fitment parts in KPI delta. | `logs/phase_c1_step4_batch_34/worker_pass_report.json`, `logs/phase_c1_step4_batch_34/kpi_delta.diff` |
| Step 4 batch 35 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=11899`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_35/worker_pass_report.json`, `logs/phase_c1_step4_batch_35/kpi_delta.diff` |
| Step 4 batch 36 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=11894`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_36/worker_pass_report.json`, `logs/phase_c1_step4_batch_36/kpi_delta.diff` |
| Step 4 batch 37 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=10115`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_37/worker_pass_report.json`, `logs/phase_c1_step4_batch_37/kpi_delta.diff` |
| Step 4 batch 38 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9774`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_38/worker_pass_report.json`, `logs/phase_c1_step4_batch_38/kpi_delta.diff` |
| Step 4 batch 39 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9705`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_39/worker_pass_report.json`, `logs/phase_c1_step4_batch_39/kpi_delta.diff` |
| Step 4 batch 40 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9716`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_40/worker_pass_report.json`, `logs/phase_c1_step4_batch_40/kpi_delta.diff` |
| Step 4 batch 41 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9733`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_41/worker_pass_report.json`, `logs/phase_c1_step4_batch_41/kpi_delta.diff` |
| Step 4 batch 42 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9718`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_42/worker_pass_report.json`, `logs/phase_c1_step4_batch_42/kpi_delta.diff` |
| Step 4 batch 43 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9719`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_43/worker_pass_report.json`, `logs/phase_c1_step4_batch_43/kpi_delta.diff` |
| Step 4 batch 44 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9715`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_44/worker_pass_report.json`, `logs/phase_c1_step4_batch_44/kpi_delta.diff` |
| Step 4 batch 45 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9723`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_45/worker_pass_report.json`, `logs/phase_c1_step4_batch_45/kpi_delta.diff` |
| Step 4 batch 46 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9716`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_46/worker_pass_report.json`, `logs/phase_c1_step4_batch_46/kpi_delta.diff` |
| Step 4 batch 47 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9725`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_47/worker_pass_report.json`, `logs/phase_c1_step4_batch_47/kpi_delta.diff` |
| Step 4 batch 48 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9715`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_48/worker_pass_report.json`, `logs/phase_c1_step4_batch_48/kpi_delta.diff` |
| Step 4 batch 49 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=9720`, `merged_fitment_rows=9721`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_49/worker_pass_report.json`, `logs/phase_c1_step4_batch_49/kpi_delta.diff` |
| Step 4 batch 50 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=7724`, `merged_fitment_rows=7725`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_50/worker_pass_report.json`, `logs/phase_c1_step4_batch_50/kpi_delta.diff` |
| Step 4 batch 51 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=7739`, `merged_fitment_rows=7740`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_51/worker_pass_report.json`, `logs/phase_c1_step4_batch_51/kpi_delta.diff` |
| Step 4 batch 52 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=6593`, `merged_fitment_rows=6593`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_52/worker_pass_report.json`, `logs/phase_c1_step4_batch_52/kpi_delta.diff` |
| Step 4 batch 53 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=5125`, `merged_fitment_rows=5125`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_53/worker_pass_report.json`, `logs/phase_c1_step4_batch_53/kpi_delta.diff` |
| Step 4 batch 54 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=5138`, `merged_fitment_rows=5138`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_54/worker_pass_report.json`, `logs/phase_c1_step4_batch_54/kpi_delta.diff` |
| Step 4 batch 55 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=17147`, `merged_fitment_rows=17147`) with +1 JSON-fitment part in KPI delta. | `logs/phase_c1_step4_batch_55/worker_pass_report.json`, `logs/phase_c1_step4_batch_55/kpi_delta.diff` |
| Step 4 batch 56 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=17400`, `merged_fitment_rows=17401`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_56/worker_pass_report.json`, `logs/phase_c1_step4_batch_56/kpi_delta.diff` |
| Step 4 batch 57 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=17447`, `merged_fitment_rows=17448`) with +1 JSON-fitment part in KPI delta. | `logs/phase_c1_step4_batch_57/worker_pass_report.json`, `logs/phase_c1_step4_batch_57/kpi_delta.diff` |
| Step 4 batch 58 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=17962`, `merged_fitment_rows=17963`) with +1 JSON-fitment part in KPI delta. | `logs/phase_c1_step4_batch_58/worker_pass_report.json`, `logs/phase_c1_step4_batch_58/kpi_delta.diff` |
| Step 4 batch 59 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=18131`, `merged_fitment_rows=18132`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_59/worker_pass_report.json`, `logs/phase_c1_step4_batch_59/kpi_delta.diff` |
| Step 4 batch 60 execution | ✅ | Successful worker cycle (`backfill updated_parts=35546`, `merge updated_parts=18710`, `merged_fitment_rows=18711`) with timestamp-only KPI delta. | `logs/phase_c1_step4_batch_60/worker_pass_report.json`, `logs/phase_c1_step4_batch_60/kpi_delta.diff` |
| Step 5 execution pause | ⚠️ | Step 5 reruns paused by operator request until updated credentials are provided. | `logs/phase_c1_step5/preflight_20260416T053259Z.json`, `logs/phase_c1_step5/blocker_playbook_20260416T053259Z.json`, `logs/phase_c1_step5/step5_pass_report_20260416T053302Z.json` |
| Nightly worker scheduling | ✅ | Installed nightly cron entry for automated Step 4 batches at `35 2 * * *`. | `crontab -l` entry tagged `autospare-step4-worker-nightly` |
| Nightly worker path validation | ✅ | Manual run of nightly wrapper succeeded (`backfill updated_parts=35546`, `merge updated_parts=17284`) with timestamp-only KPI diff. | `logs/phase_c1_step4_batch_nightly_20260416T051006Z/worker_pass_report.json`, `logs/phase_c1_step4_batch_nightly_20260416T051006Z/kpi_delta.diff` |

---

## CRITICAL (9) — App-Breaking / Security

| # | Status | Description | File(s) | Fix |
|---|--------|-------------|---------|-----|
| C-1 | ✅ | Duplicate Alembic revision ID `0001_initial` → `MultipleHeads` crash | `alembic/versions/0001_initial.py` & `0001_initial_schema.py` | Rename `0001_initial.py` → empty pass-through; give `0001_initial_schema.py` a unique revision ID |
| C-2 | ✅ | `DATABASE_PII_URL` missing from docker-compose → auth/orders fail to connect | `docker-compose.yml` | Add `DATABASE_PII_URL` env var pointing to `postgres` service |
| C-3 | ✅ | `frontend/Dockerfile` missing → `docker-compose build` fails | `docker-compose.yml`, `frontend/` | Create `frontend/Dockerfile` |
| C-4 | ✅ | WebSocket `/api/v1/chat/ws` has no auth → anonymous access to AI stack | `backend/BACKEND_API_ROUTES.py:687` | Add token query-param auth check inside ws handler |
| C-5 | ✅ | `/api/v1/parts/identify-from-image` has no auth → free GPT-4o usage | `backend/BACKEND_API_ROUTES.py:1308` | Add `current_user: User = Depends(get_current_user)` |
| C-6 | ✅ | JWT secrets regenerate on every restart if env vars missing → all sessions invalidated | `backend/BACKEND_AUTH_SECURITY.py:44` | Add startup validation that raises if secrets not set in production |
| C-7 | ✅ | Stripe webhook accepts forged events when secret unset → free order fulfillment | `backend/BACKEND_API_ROUTES.py:2344` | Raise 400 if webhook secret not configured; never fall through |
| C-8 | ✅ | Django `SECRET_KEY` hardcoded in plaintext | `autosparefinder/settings.py:23` | Load from `os.environ` |
| C-9 | ✅ | Healthcheck URL `/health` is wrong → backend restarted infinitely | `docker-compose.yml:55` | Change to `/api/v1/system/health` |

---

## HIGH (13) — Functional Failures / Vulnerabilities

| # | Status | Description | File(s) | Fix |
|---|--------|-------------|---------|-----|
| H-1 | ✅ | Logout doesn't revoke token | `backend/BACKEND_API_ROUTES.py:401` | Call `logout_user(token, db)` in logout endpoint |
| H-2 | ✅ | `get_current_user` never checks session revocation | `backend/BACKEND_AUTH_SECURITY.py:639` | Query `UserSession` and reject if `revoked_at` is set |
| H-3 | ✅ | Password reset only prints to stdout, no email sent | `backend/BACKEND_AUTH_SECURITY.py:577` | Add SendGrid email delivery for reset link |
| H-4 | ✅ | `/api/v1/auth/verify-email` is a no-op stub, any token passes | `backend/BACKEND_API_ROUTES.py:379` | Actually validate token against `PasswordReset` table |
| H-5 | ✅ | `DEV_2FA_CODE` backdoor — env var bypasses 2FA for all accounts | `backend/BACKEND_AUTH_SECURITY.py:241` | Disallow in `ENVIRONMENT=production`; warn loudly |
| H-6 | ✅ | `alembic/env.py` imports non-existent module → empty migrations | `alembic/env.py:23` | Import from `backend.BACKEND_DATABASE_MODELS` |
| H-7 | ✅ | Carrier always "Israel Post" — supplier country is always `""` | `backend/BACKEND_AI_AGENTS.py` | Pass real supplier country to fulfillment stub |
| H-8 | ✅ | Delete order crashes (FK violation) when Returns exist | `backend/BACKEND_API_ROUTES.py:1752` | Delete Return records before deleting Order |
| H-9 | ✅ | Plaintext DB password in `alembic.ini` | `alembic.ini:3` | Replace with `%(DATABASE_URL)s` env interpolation |
| H-10 | ✅ | IDOR on payments — any user can fetch any payment by UUID | `backend/BACKEND_API_ROUTES.py:2330` | Add `Payment.order.user_id == current_user.id` check |
| H-11 | ✅ | Font TTFError crashes backend on startup if fonts not installed | `backend/invoice_generator.py:33` | Wrap font registration in try/except; lazy load |
| H-12 | ✅ | Root `app.py` hardcodes DB URL and uses `debug=True` | `app.py` | Load URI from env; set debug from env |
| H-13 | ✅ | `_supplier_mask_counter` not process-safe (4 workers = inconsistent aliases) | `backend/BACKEND_API_ROUTES.py:181` | Use Redis-backed counter or hash-based deterministic alias |

---

## MEDIUM (11) — Incorrect Behavior / Missing Features

| # | Status | Description | File(s) | Fix |
|---|--------|-------------|---------|-----|
| M-1 | ✅ | "OEM" and "Original" search buckets run identical queries → duplicate results | `backend/BACKEND_API_ROUTES.py:877` | OEM bucket uses `["OEM"]`, Original uses `["Original"]` |
| M-2 | ✅ | `asyncio.get_event_loop()` deprecated (8 places) → breaks Python 3.12+ | `backend/BACKEND_API_ROUTES.py` | Replace with `asyncio.get_running_loop()` |
| M-3 | ✅ | `create_tables()` never creates PII tables | `backend/BACKEND_DATABASE_MODELS.py` | Add `pii_engine.begin()` block for `PiiBase.metadata.create_all` |
| M-4 | ✅ | `get_manufacturers` missing `@app.get` decorator → dead endpoint | `backend/BACKEND_API_ROUTES.py:947` | Add `@app.get("/api/v1/parts/manufacturers")` |
| M-5 | ✅ | Root `requirements.txt` is out of sync — missing all FastAPI deps | `requirements.txt` | Pre-fixed: root requirements.txt already cleaned up |
| M-6 | ✅ | Docker `alembic upgrade head` finds no migration files in `backend/alembic/` | `docker-compose.yml`, `backend/alembic/` | Docker command already uses `create_tables()` (not alembic) |
| M-7 | ✅ | CORS blocks production frontend if `CORS_ORIGINS` env var missing | `backend/BACKEND_API_ROUTES.py:173` | `CORS_ORIGINS` passed via docker-compose env var |
| M-8 | ✅ | Return reason/description sent as query params → appears in logs | `backend/BACKEND_API_ROUTES.py:1738` | Move to request body via Pydantic model |
| M-9 | ✅ | Cart shipping hardcoded to 91₪ — diverges from backend calculation | `frontend/src/stores/cartStore.js:40` | Remove hardcoded 91₪; use dynamic shipping from cart items |
| M-10 | ✅ | SQLite connection leaks in `src/app.py` (no `try/finally`) | `src/app.py` | Wrap in `try/finally` or use context manager |
| M-11 | ✅ | Alembic `env.py` uses sync `create_engine` with asyncpg DSN | `alembic/env.py:44` | Strip asyncpg driver; use `postgresql://` for sync alembic runner |

---

## LOW (10) — Code Quality / Dead Code / Minor Issues

| # | Status | Description | File(s) | Fix |
|---|--------|-------------|---------|-----|
| L-1 | ✅ | Both `pyjwt` and `python-jose` installed — redundant conflict | `backend/requirements.txt` | Pre-fixed: `PyJWT` already removed |
| L-2 | ✅ | `anthropic` SDK installed but never used | `backend/requirements.txt` | Pre-fixed: `anthropic` already removed |
| L-3 | ✅ | `facebook-sdk`, `tweepy`, `python-telegram-bot`, `selenium` unused | `backend/requirements.txt` | Pre-fixed: all four already removed |
| L-4 | ✅ | Django scaffold: hardcoded SECRET_KEY, DEBUG=True, ALLOWED_HOSTS=[] | `autosparefinder/settings.py` | Load from env; guard DEBUG/ALLOWED_HOSTS |
| L-5 | ✅ | Root `models.py` is dead code (superseded by BACKEND_DATABASE_MODELS) | `models.py` | Deleted root `models.py` |
| L-6 | ✅ | Root `BACKEND_API_ROUTES.py` shadows `backend/BACKEND_API_ROUTES.py` | `BACKEND_API_ROUTES.py` (root) | Deleted root `BACKEND_API_ROUTES.py` |
| L-7 | ✅ | `USD_TO_ILS = 3.65` hardcoded in 4 inconsistent places | Multiple files | Pre-fixed: already centralised in `BACKEND_DATABASE_MODELS.py` |
| L-8 | ✅ | `clamd` installed but virus scan never runs; files stay `"pending"` | `backend/BACKEND_API_ROUTES.py`, `docker-compose.yml` | Added `_scan_bytes_for_virus()` helper; wired into `/api/v1/files/upload` and `/api/v1/chat/upload-image`; rejects infected files with 400; saves `virus_scan_status`; added `clamav/clamav:stable` service to docker-compose |
| L-9 | ✅ | Font registration at module-level causes double-import KeyError | `backend/invoice_generator.py` | Guard with `if "DV" not in pdfmetrics.getRegisteredFontNames()` |
| L-10 | ✅ | Three competing entry-point `app.py` files | `app.py`, `src/app.py`, `backend/BACKEND_API_ROUTES.py` | Deleted legacy `app.py` and `src/app.py`; `backend/BACKEND_API_ROUTES.py` is the sole entry point |

---

## Fix Progress Log

| Date | Issues Fixed | Notes |
|------|-------------|-------|
| 2026-03-11 | — | Initial scan complete, tracking file created |
| 2026-03-11 | C-1 to C-9, H-1 to H-13, M-1 to M-11, L-1 to L-4, L-7, L-9 (39 total) | All code fixes applied; 13/13 automated checks pass; L-5, L-6, L-8, L-10 noted (no-code-change) |
| 2026-03-11 | L-5, L-6, L-8, L-10 (4 remaining) | Deleted 4 dead files; ClamAV fully integrated; 6/6 new checks pass; 0 regressions |

---

## Infrastructure Added

| File | Purpose |
|------|---------|
| `frontend/Dockerfile` | Multi-stage nginx build (C-3) |
| `database/init.sql` | Creates `autospare_pii` PostgreSQL DB on first start (C-2) |
| `.env.example` | Documents all required env vars |
| `backend/Dockerfile` | Added `fonts-dejavu-core` for invoice PDF generation (H-11) |

---

## Session — 2026-03-20 (Social Media & Messaging)

| Phase | Status | Description | Files |
|-------|--------|-------------|-------|
| S-1 | ✅ | `social_posts` table — migration `0013_add_social_posts`, ORM model `SocialPost(Base)`, Pydantic schemas `CreateSocialPostRequest` / `UpdateSocialPostRequest` | `backend/alembic/versions/0013_add_social_posts.py`, `BACKEND_DATABASE_MODELS.py` |
| S-2 | ✅ | 5 social endpoints rewritten with real DB logic: `POST /api/v1/admin/social/posts` (persist + `ApprovalQueue(entity_type='social_post')`), `GET` (status filter), `PUT` (update content/schedule), `DELETE` (soft — sets `status='rejected'`), `GET /analytics` (`GROUP BY` counts + `scheduled_next_7d` via `timedelta(days=7)`) | `BACKEND_API_ROUTES.py` |
| S-3 | ✅ | Telegram publisher `backend/social/telegram_publisher.py` — `publish_to_telegram(content, image_url?)` via raw `httpx`; `POST /api/v1/admin/social/publish/{post_id}` endpoint validates `status=='approved'`, publishes, then sets `status='published'` + stores `external_post_ids={'telegram': message_id}` | `backend/social/telegram_publisher.py`, `BACKEND_API_ROUTES.py` |
| S-4 | ✅ | `resolve_approval` updated: when `entity_type='social_post'` and `decision='approved'` → syncs `social_posts.status='approved'` and `approved_by` in catalog DB (dual-session cross-DB pattern) | `BACKEND_API_ROUTES.py` |
| S-5 | ✅ | WhatsApp abstraction layer: `WhatsAppProvider` ABC + `TwilioWhatsAppProvider` (async httpx + Twilio signature validation via HMAC-SHA1); `POST /api/v1/webhooks/whatsapp` (sig check→parse→user lookup→find/create Conversation→Avi agent→send reply→persist messages→empty TwiML `<Response/>`) | `backend/social/whatsapp_provider.py`, `BACKEND_API_ROUTES.py` |
| S-6 | ✅ | Sentinel user `00000000-0000-0000-0000-000000000001` upserted at `startup()` via `ON CONFLICT (id) DO NOTHING` for anonymous WhatsApp sessions (avoids `Conversation.user_id NOT NULL` violation) | `BACKEND_API_ROUTES.py` |
| S-7 | ✅ | `send_sms_2fa()` fix — blocking sync Twilio SDK call wrapped in `asyncio.to_thread()` to prevent event-loop stalls during 2FA sends | `BACKEND_AUTH_SECURITY.py` |
| S-8 | ✅ | `upload_audio` fix — added `conversation_id: Optional[str] = None` query param and passed through to `process_user_message()` (previously hardcoded `None`, always starting a new conversation on every voice message) | `BACKEND_API_ROUTES.py` |
| S-9 | ✅ | Git history cleaned — `backups/*.sql` (160MB+) removed from all commits via `git filter-branch --index-filter`; `backups/` added to `.gitignore`; force-pushed to remote | `.gitignore` |

---

## Refactor Risks — BACKEND_API_ROUTES.py → routes/

| # | Status | Risk | Resolution |
|---|--------|------|------------|
| R-1 | ✅ Resolved | `routes/parts.py` previously imported `_mask_supplier` from `BACKEND_API_ROUTES`, creating circular import risk. | Moved `_mask_supplier` to `backend/routes/utils.py`; `routes/parts.py` now imports from `routes.utils`, so module import is one-way and safe in isolation. |
| R-2 | ✅ Resolved | `routes/chat.py` would have needed **two** symbols from `BACKEND_API_ROUTES` (`_scan_bytes_for_virus` + `_guarded_task`), increasing circular-import risk compared to R-1. | Both functions are fully self-contained (stdlib + `clamd` only). Moved to `routes/utils.py`. `BACKEND_API_ROUTES` now imports them via `from routes.utils import ...` (one-way, no circular). `routes/chat.py` imports from `routes.utils` with zero circularity. |

---

## Lessons Learned — Refactor Execution

| # | Step | Lesson | Prevention |
|---|------|--------|------------|
| L-1 | Auth (STEP 6) | `str_replace` inserted stub comments WITHOUT removing the original endpoint bodies when the removal hunk exceeded ~50 lines. Left duplicate endpoint definitions and a spurious section header. Detected by `read_file` around the insertion point. Fixed by Python line-slice script. | For any removal block > 50 lines, skip `str_replace` entirely. Use Python line-slice script. Document target 1-indexed line numbers via `grep_search` before executing. |

---

### New Files Added (2026-03-20)

| File | Purpose |
|------|---------|
| `backend/alembic/versions/0013_add_social_posts.py` | Creates `social_posts` table with `status` CHECK constraint, `external_post_ids JSONB`, composite index `(status, scheduled_at)` |
| `backend/social/__init__.py` | Package marker for social/ module |
| `backend/social/telegram_publisher.py` | Async Telegram Bot API publisher — no extra SDK, pure httpx |
| `backend/social/whatsapp_provider.py` | `WhatsAppProvider` ABC + `TwilioWhatsAppProvider` — pluggable; swap to Meta Cloud API without changing webhook logic |

### Recent Extraction (2026-03-25)

| Step | Domain | Files created | Status |
|------|--------|---------------|--------|
| 15 | Notifications | `backend/routes/notifications.py` | ✅ Completed — moved 6 endpoints (stream, list, unread-count, read, read-all, delete) from `BACKEND_API_ROUTES.py` into `routes/notifications.py` and wired via `app.include_router(notifications_router)` |

**Note:** `_SSE_HEARTBEAT_INTERVAL` moved into `backend/routes/notifications.py` (module-scoped constant). See REFACTOR LOG updates below.

### REFACTOR LOG — Pending Steps Update

- 2026-03-25: Notifications (Step 15) extracted and included. Remove from pending extraction list.


---

## Refactor Step Summary

| Step | Domain | Files created | Lines removed | Lines added | Tests broken+fixed | Risks identified |
|------|--------|---------------|---------------|-------------|--------------------|------------------|
| 8 | Orders + Shared Schemas | `backend/routes/orders.py`, `backend/routes/schemas.py` | 2459 | 734 | `pytest tests/test_security.py`: 35 passed, 49 skipped, 0 broken; full `pytest -q` still blocked by pre-existing `pytest_asyncio` missing in `tests/test_system.py` | R-1 resolved (parts/utils circular removed); checkout now uses lazy import from `routes.orders` and must move with checkout in Step 15 |
| 9 | Payments + Shared fulfillment/frontend helpers | `backend/routes/payments.py` | 1037 | 1069 | Exact baseline re-run: `pytest tests/ -q --tb=no --ignore=tests/test_system.py` -> 91 failed / 120 passed (after one temporary regression in clamd source-string test was fixed) | Avoided new circular import by not importing fulfillment from monolith; `trigger_supplier_fulfillment` + `_get_frontend_url` now centralized in `routes/utils.py` |
| 10 | Invoices | `backend/routes/invoices.py` | 32 | 52 | Exact baseline re-run: `pytest tests/ -q --tb=no --ignore=tests/test_system.py` -> 91 failed / 120 passed (no new regressions) | No new circular dependency; endpoints were self-contained and required no shared schema/utils extraction |

| 11 | Returns | `backend/routes/returns.py` | 412 | 414 | Exact baseline re-run: `pytest -q --tb=no --ignore=tests/test_system.py` -> 91 failed / 120 passed (no new regressions) | No new circular dependency; all endpoints and logic moved verbatim, with import hygiene and type annotation fixes. |
| 12 | Files | `backend/routes/files.py` | 49 | 67 | Exact baseline re-run: `pytest -q --tb=no --ignore=tests/test_system.py` -> 91 failed / 120 passed (no new regressions) | No new circular dependency; _scan_bytes_for_virus already in utils, FileModel import alias resolved. |
| 13 | Profile | `backend/routes/profile.py` | 143 | 180 | Exact baseline re-run: `pytest -q --tb=no --ignore=tests/test_system.py` -> 91 failed / 120 passed (no new regressions) | No new circular dependency; all endpoints and logic moved verbatim, with import hygiene and type annotation fixes. |
| 14 | Marketing | `backend/routes/marketing.py` | 87 | 92 | Exact baseline re-run: `pytest -q --tb=no --ignore=tests/test_system.py` -> 91 failed / 120 passed (no new regressions) | No new circular dependency; all endpoints and logic moved verbatim, with import hygiene and type annotation fixes. |

---

## REFACTOR LOG

### Agent Prompt (Operating Prompt Snapshot)

```
You are an expert AI programming assistant, working with a user in the VS Code editor.
Your name is GitHub Copilot.

Core workflow constraints used in this refactor:
- Strict PROPOSE -> APPROVE -> EXECUTE -> TEST cycle for each extraction step.
- Preserve behavior while extracting domains from BACKEND_API_ROUTES.py into routes/*.py.
- Keep include_router wiring in BACKEND_API_ROUTES.py after symbol definitions.
- Use routes/utils.py for shared helpers to reduce circular imports.
- Keep changes minimal and avoid unrelated formatting.
- Do not revert unrelated user changes.
- Validate after edits via import checks and pytest.
- Track risks and lessons in FIXES_TRACKER.md.

Execution policy:
- Persist until task is fully handled end-to-end.
- Prefer direct implementation over only proposing when approved.
- Surface blockers explicitly (for this repo: pre-existing missing pytest_asyncio for tests/test_system.py).
```

### Completed Steps

| Step | Domain | Files created | Status |
|------|--------|---------------|--------|
| 3 | Parts | `backend/routes/parts.py` | ✅ Completed |
| 4 | Reviews | `backend/routes/reviews.py` | ✅ Completed |
| 5 | Vehicles | `backend/routes/vehicles.py` | ✅ Completed |
| 6 | Auth | `backend/routes/auth.py` | ✅ Completed |
| 7 | Chat | `backend/routes/chat.py`, `backend/routes/utils.py` | ✅ Completed |
| 8 | Orders + Schemas consolidation | `backend/routes/orders.py`, `backend/routes/schemas.py` | ✅ Completed |
| 9 | Payments | `backend/routes/payments.py` | ✅ Completed |
| 10 | Invoices | `backend/routes/invoices.py` | ✅ Completed |
| 11 | Returns | `backend/routes/returns.py` | ✅ Completed |

### Pending Steps (Approved Extraction Order)

| Step | Domain | Files planned | Status |
|------|--------|---------------|--------|
| 12 | Profile | `backend/routes/profile.py` | ❌ Pending |
| 13 | Marketing + Social | `backend/routes/marketing.py`, `backend/routes/social.py` | ❌ Pending |
| 14 | Admin (users/settings/approvals) | `backend/routes/admin.py` | ❌ Pending |
| 15 | Wishlist | `backend/routes/wishlist.py` | ❌ Pending |
| 16 | Cart + Checkout | `backend/routes/cart.py` | ❌ Pending |
| 17 | System/Health/Utility leftovers | `backend/routes/system.py` | ❌ Pending |

### Required Note

checkout lazy-imports create_order from routes.orders — must move with checkout to routes/cart.py in Step 15

---

## Session — 2026-03-28 (Post-Refactor Audit)

Full audit of all extracted `routes/*.py` modules, `social/` module, background loops, and remaining
`BACKEND_API_ROUTES.py` code. 7 new issues found and all fixed in this session.

| # | Severity | Status | Description | File(s) | Fix |
|---|----------|--------|-------------|---------|-----|
| N-1 | HIGH | ✅ | `_clamd` not imported in `BACKEND_API_ROUTES.py` → `NameError` in `_health_monitor_loop` every ClamAV probe | `backend/BACKEND_API_ROUTES.py` | Added `import clamd as _clamd` next to existing `import httpx as _httpx` |
| N-2 | HIGH | ✅ | `routes/orders.py` circular import — `from BACKEND_API_ROUTES import publish_notification` inside `cancel_order()` body; wrong module, creates hidden circular dep | `backend/routes/orders.py:300` | Changed to `from BACKEND_AUTH_SECURITY import publish_notification` |
| N-3 | HIGH | ✅ | Rate limiting disabled for `upload_image` and `upload_audio` — `check_rate_limit()` return value not checked, 429 never raised | `backend/routes/chat.py:141,210` | Assigned result to `allowed`; added `if not allowed: raise HTTPException(429, ...)` |
| N-4 | MEDIUM | ✅ | `_vip_detection_loop` bypasses `_guarded_task` semaphore — `asyncio.create_task(publish_notification(...))` called directly, unlimited concurrency during bulk VIP promotions | `backend/BACKEND_API_ROUTES.py:473` | Wrapped in `asyncio.create_task(_guarded_task(publish_notification(...)))` |
| N-5 | MEDIUM | ✅ | `POST /api/v1/support/report` (public endpoint) has no rate limiting → unlimited TechAgent calls possible | `backend/routes/support.py` | Added `redis=Depends(get_redis)` + IP-based rate limit `rate:bug_report:{ip}` 10/min |
| N-6 | MEDIUM | ✅ | `PUT /api/v1/admin/supplier-orders/{id}/done` sends `tracking_number`, `tracking_url`, `carrier` as query params → appear in server logs / browser history | `backend/routes/admin.py:145` | Moved all three to JSON request body (read via `request.json()`) |
| N-7 | LOW | ✅ | `CartAddRequest.quantity` has no minimum → `quantity=0` or negative accepted, silently corrupting cart totals | `backend/routes/schemas.py:146` | Changed to `Field(default=1, ge=1, le=100)` |

### Test results after N-1 to N-7

```
pytest tests/ -q --tb=no --ignore=tests/test_system.py
23 failed, 207 passed, 32 skipped
```

---

## Deferred Cleanup Backlog (2026-04-01)

Scope: keep current search/data fix work focused; defer unrelated files below to a dedicated cleanup pass.

### A) Accidental terminal artifact files (safe delete later)

- [ ] Remove [": print(dict(r))"](:%20print(dict(r)))
- [ ] Remove ["= await c.fetch('SELECT name, name_he FROM car_brands LIMIT 5')"](=%20await%20c.fetch('SELECT%20name,%20name_he%20FROM%20car_brands%20LIMIT%205'))
- [ ] Remove ["=(await c.execute(q,{\"t\":table})).fetchall()"](=(await%20c.execute(q,%7B%22t%22:table%7D)).fetchall())
- [ ] Remove ["_db import parse_manufacturer_fields"](_db%20import%20parse_manufacturer_fields)
- [ ] Remove ["a.text(\"\"\""](a.text(%22%22%22))
- [ ] Remove ["actionError:\";"](actionError:%22;)
- [ ] Remove ["aux | grep -E 'import_parts_db.py|python .*import_parts_db.py' | grep -v grep || true"](aux%20%7C%20grep%20-E%20'import_parts_db.py%7Cpython%20.*import_parts_db.py'%20%7C%20grep%20-v%20grep%20%7C%7C%20true)
- [ ] Remove [e](e)
- [ ] Remove ["et -e"](et%20-e)
- [ ] Remove ["leep 6"](leep%206)
- [ ] Remove [ult.stdout)](ult.stdout))
- [ ] Remove ["upplier_parts sp JOIN suppliers s ON s.id=sp.supplier_id LIMIT 9"](upplier_parts%20sp%20JOIN%20suppliers%20s%20ON%20s.id=sp.supplier_id%20LIMIT%209)
- [ ] Remove [yncio](yncio)
- [ ] Remove ["yncio, asyncpg"](yncio,%20asyncpg)

### B) Unrelated feature/UI changes to review later (do not edit in current pass)

- [ ] Review auth + social login additions in [frontend/src/components/SocialLoginButtons.jsx](frontend/src/components/SocialLoginButtons.jsx), [frontend/src/stores/authStore.js](frontend/src/stores/authStore.js), [backend/routes/auth.py](backend/routes/auth.py)
- [ ] Review cart/admin/payment UX/logic deltas in [frontend/src/pages/Cart.jsx](frontend/src/pages/Cart.jsx), [frontend/src/pages/Admin.jsx](frontend/src/pages/Admin.jsx), [backend/routes/payments.py](backend/routes/payments.py)
- [ ] Review 2FA/branding/env changes in [backend/BACKEND_AUTH_SECURITY.py](backend/BACKEND_AUTH_SECURITY.py), [backend/.env.example](backend/.env.example), [frontend/.env.example](frontend/.env.example)
- [ ] Review route/model migration changes in [backend/BACKEND_DATABASE_MODELS.py](backend/BACKEND_DATABASE_MODELS.py), [backend/alembic_pii/versions/0027_add_oauth_columns.py](backend/alembic_pii/versions/0027_add_oauth_columns.py)

### C) Search-fix validation follow-up (current track)

- [ ] Verify grouped search tabs/filters in [frontend/src/pages/Parts.jsx](frontend/src/pages/Parts.jsx)
- [ ] Confirm API query params alignment in [frontend/src/api/parts.js](frontend/src/api/parts.js) and [backend/routes/parts.py](backend/routes/parts.py)

All 23 failures are pre-existing (integration tests requiring a live DB + SQL injection tests).
Zero regressions introduced.

---
## Session — 2026-05-05 (REX Audit & Data Quality)
> Status: In Progress

| Item | Status | Summary |
|---|---|---|
| REX scheduling — runs at wrong time | ✅ | Currently runs ~08:31 UTC — peak hours. Change to 00:00 and 12:00 UTC fixed times |
| autodoc HTTP 403 | ❌ | Blocked by Cloudflare — remove from discovery sources |
| eBay HTTP 403 | ❌ | API keys exist but still blocked — needs investigation |
| ~270K parts missing prices | ❌ | supplier_parts has 304K rows but parts_catalog has 570K — gap of ~266K |
| HealthMonitor datetime bug | ❌ | `unsupported operand type(s) for -: datetime and dict` |
| Parts categories not verified | ❌ | 570K parts — unclear if in correct categories |
| DB data quality cleanup | ❌ | Duplicates, missing fields, unverified fitment data |
| Accessible scraping sources | ⚠️ | Only: motorstore.co.il, meyle.com, mann-filter.com, gates.com, brembo.com |
| alvadi.com | ⚠️ | Cloudflare Turnstile — fully blocked, no partnership possible |
| ALVADI contact | ⚠️ | support@alvadi.com rejected, phone appears inactive |

### Architecture Notes
- No Celery/Beat container — REX runs as background asyncio loop inside backend
- Server: Hetzner 94.130.150.23
- DB catalog: 570,240 parts | 304,646 supplier_parts
- vehicle_market_il: 36,831 Israeli vehicles

### Next Fix Priority
1. Fix REX schedule → 00:00 and 12:00 UTC
2. Fix HealthMonitor datetime bug
3. Investigate eBay 403
4. Fix missing prices for ~266K parts
5. Verify/fix categories
6. DB quality cleanup

---
## Session — 2026-05-06 (DB Cleanup & Categories)
> Status: In Progress

### DB Issues Found
| Issue | Details | Priority |
|-------|---------|----------|
| קטגוריות חסרות | 300,102 חלקים ללא קטגוריה, 270,138 עם "כללי" | 🔴 גבוה |
| Renault overflow | 230,269 חלקים — 161K ללא OEM, ספק ישראלי שגלש | 🔴 גבוה |
| part_type כפול | חליפיחליפי, מקורימקורי, משופץמשופץ | 🟡 בינוני |
| needs_oem_lookup שגוי | חלקים עם oem_number שעדיין מסומנים TRUE | 🟡 בינוני |
| oem_number חסר | 268K חלקים ללא OEM — חלקם ניתן למלא מ-cross_reference | 🟡 בינוני |
| brand_aliases ריק | טבלה ריקה לחלוטין | 🟢 נמוך |

### Category System — 28 Categories (Final, No Duplicates)
Cleanup agent auto-classifies all parts — no manual work.

| # | קטגוריה | תת-קטגוריות בלעדיות | מילות מפתח עברית | מילות מפתח אנגלית |
|---|---------|---------------------|-----------------|-------------------|
| 1 | בלמים | רפידות, דיסקים, קליפרים, תופים, צינורות בלמים, בוסטר | בלם, רפידה, דיסק, קליפר, תוף, בוסטר | brake, caliper, rotor, pad, drum, booster |
| 2 | מתלה | בולמים, קפיצים, זרועות, מסבי גלגל, מייצב, בושינגים | בולם, קפיץ, זרוע, מסב, מייצב, מתלה, שטרוט | suspension, shock, strut, spring, arm, bushing, bearing, anti-roll |
| 3 | היגוי | גיר הגה, מוטות היגוי, מפרקי כדור, משאבת הגה | הגה, גיר הגה, מוט הגה, פולסה | steering, rack, tie rod, ball joint, power steering pump |
| 4 | מנוע | בלוק, בוכנות, שסתומים, גל ארכובה, גל זיזים | מנוע, בוכנה, שסתום, גל ארכובה, גל זיזים | engine, piston, valve, crankshaft, camshaft, block |
| 5 | קירור | רדיאטור, משאבת מים, תרמוסטט, מאוורר, צינורות קירור, נוזל קירור | רדיאטור, קירור, תרמוסטט, משאבת מים, מאוורר | radiator, water pump, thermostat, cooling fan, coolant hose |
| 6 | מערכת דלק | משאבת דלק, מזרקים, מיכל דלק, ריילי, שנורקל | משאבת דלק, מזרק, מיכל דלק, ריילי | fuel pump, injector, fuel tank, fuel rail |
| 7 | מערכת אוויר | מסנן אוויר, צינור אוויר, גוף מיתון, MAF | מסנן אוויר, צינור אוויר, גוף מיתון | air filter, air intake, MAF, throttle body |
| 8 | טורבו | טורבו, אינטרקולר, סופרשארז׳ר, צינורות טורבו | טורבו, אינטרקולר, סופרשארג'ר | turbocharger, supercharger, intercooler, boost pipe |
| 9 | פליטה | צינורות פליטה, מפלט, DPF, SCR, קטליזטור, EGR | פליטה, מפלט, קטליזטור, DPF, EGR | exhaust, muffler, catalytic converter, DPF, EGR |
| 10 | תיבת הילוכים וציר | תיבת הילוכים, ציר הנעה, מחצית ציר, דיפרנציאל | תיבת הילוכים, גיר, ציר, דיפרנציאל | transmission, gearbox, driveshaft, differential, CV joint |
| 11 | מצמד | ערכת מצמד, גלגל תנופה, מסב שחרור, מזלג | מצמד, גלגל תנופה, מסב שחרור | clutch kit, flywheel, release bearing, clutch fork |
| 12 | רצועות תזמון | רצועת תזמון, שרשרת תזמון, גלגלות, מותחן | רצועה, שרשרת תזמון, גלגלת, מותחן | timing belt, timing chain, tensioner, idler pulley |
| 13 | הצתה | מצתים, סלילי הצתה, מפלג, כבלי מצתים | מצת, סליל הצתה, מפלג | spark plug, ignition coil, distributor, plug wire |
| 14 | סינון | מסנן שמן, מסנן דלק, מסנן מזגן | מסנן שמן, מסנן דלק, מסנן מזגן | oil filter, fuel filter, cabin air filter, pollen filter |
| 15 | חשמל ואלקטרוניקה | אלטרנטור, מצת הנעה, ECU, ממסרים, פיוזים, צמת | אלטרנטור, מצת הנעה, ECU, מחשב, ממסר, פיוז, צמת | alternator, starter motor, ECU, relay, fuse, wiring harness |
| 16 | חיישנים | חיישן O2, ABS, MAP, טמפרטורה, לחץ, מהירות | חיישן, סנסור | sensor, O2 sensor, ABS sensor, MAP, speed sensor, temperature sensor |
| 17 | מצבר | סוללה, ניהול מצבר, כבלי מצבר | מצבר, סוללה, בטריה | battery, battery management, terminal |
| 18 | תאורה | פנסים קדמיים, פנסים אחוריים, נורות, אינדיקטורים, ערפל | פנס, נורה, תאורה, אינדיקטור | headlight, tail light, bulb, indicator, fog light |
| 19 | מזגן וחימום | קומפרסור, קונדנסר, אידיידור, תנור, מפוח | מזגן, קומפרסור, קונדנסר, אידיידור, תנור, מפוח | AC compressor, condenser, evaporator, heater core, blower |
| 20 | גוף הרכב | פגושים, כנפות, דלתות, מכסה מנוע, גריל, סף, קישוטים | פגוש, כנף, דלת, מכסה, גריל, סף, קישוט | bumper, fender, door, hood, grille, sill, trim |
| 21 | שמשות ומגבים | שמשות, מגבים, מווסתי חלון, מנועי מגב, משאבת שמשות | שמשה, מגב, חלון, זכוכית, מווסת | windscreen, wiper blade, window regulator, washer pump |
| 22 | פנים הרכב | דשבורד, מושבים, שטיחים, קונסולה, ידיות, כיסויים | דשבורד, מושב, שטיח, קונסולה, ידית | dashboard, seat, carpet, console, door handle, interior |
| 23 | גלגלים וצמיגים | חישוקים, צמיגים, TPMS, אומי גלגל | גלגל, חישוק, ג׳נט, צמיג | wheel, rim, tyre, tire, TPMS, lug nut |
| 24 | אטמים וצינורות | אטמי ראש, גיממות, O-rings, אוילים | אטם, גיממה, אוילים | gasket, seal, o-ring, head gasket |
| 25 | מערכת בטיחות | כריות אוויר, חגורות, חיישני התנגשות | איירבג, כרית אוויר, חגורה | airbag, seatbelt, crash sensor |
| 26 | מערכת היברידית וחשמלי | סוללת טרקציה, מנוע חשמלי, ממיר, PDU, כבלי טעינה | היברידי, חשמלי, סוללה גדולה, PDU | hybrid battery, electric motor, inverter, PDU, charging cable |
| 27 | שמנים ונוזלים | שמן מנוע, גריז, נוזל בלמים, ATF | שמן, גריז, נוזל בלמים | engine oil, grease, brake fluid, ATF, power steering fluid |
| 28 | כלי עבודה ואביזרים | ציוד מוסך, טיפוח, אביזרי חוץ/פנים | כלי, ציוד, אביזר, טיפוח | tools, accessories, car care, detailing |

כללים:
* הסוכן מסווג אוטומטית לפי מילות מפתח עברית ואנגלית
* אין קטגוריה כללי או אחר — כל חלק מקבל קטגוריה ספציפית
* אין כפילויות — כל מונח שייך לקטגוריה אחת בלבד
* חלק שלא מזוהה — הסוכן מנסה שוב בcycle הבא
### Cleanup Agent Tasks (db_cleanup_agent.py)
| Task | Description | Batch | Sleep |
|------|-------------|-------|-------|
| fix_part_types | תיקון כפולות: חליפיחליפי→חליפי | 100 | 2s |
| fill_oem_from_xref | מילוי OEM מ-cross_reference | 50 | 3s |
| categorize_by_name | סיווג לפי מילות מפתח בשם | 100 | 2s |
| fix_oem_lookup_flag | עדכון needs_oem_lookup=FALSE | 500 | 1s |
| fix_manufacturer_overflow | זיהוי חלקים שגלשו ליצרן שגוי | 50 | 5s |

### Renault Overflow Investigation
- manufacturer_id: d193f27e = Renault (legitimate)
- 161,466 חלקים עם part_condition='New' וללא OEM — ספק ישראלי
- 68,851 עם prefix 'RE' מזויף ב-OEM number
- part_type: Original, ללא, חליפי, משומש, משופץ — נראה לגיטימי
- צריך לבדוק ב-supplier_parts מי הספק שייבא אותם

### Next Steps (In Order)
1. ✅ רשום תוכנית ב-FIXES_TRACKER
2. 🔄 בדוק supplier_parts לזיהוי מקור Renault overflow
3. ✅ נוצר db_cleanup_agent.py עם 5 tasks
4. ✅ 28 קטגוריות ב-categories.py — מקור אחד לכל המערכת
5. ✅ חובר ל-BACKEND_API_ROUTES.py
6. ❌ בדוק ותקן Chevrolet overflow

### Noon Recovery Tasks
| Task | Status | Notes |
|------|--------|-------|
| resolve_inactive_parts | ✅ | משימה מתוזמנת ב-12:00 UTC לטיפול ב-RENA-* ו-needs_oem_lookup |
| bad import 2026-03-30 | ✅ | 268,288 חלקים מ-13 יצרנים סומנו is_active=FALSE + needs_oem_lookup=TRUE |
| resolve_inactive_parts | ✅ | משימה מתוזמנת בחצות — רצה ידנית: 200 processed, 0 reactivated — ממתין למקור נתונים |
| task3 is_active filter | ✅ | סוכן הקטלוג מתעלם מחלקים לא פעילים — index ירד ל-251K |

### Architecture Decision
- Cleanup agent: רץ תמיד ברקע — batches קטנים + sleep
- REX: רץ רק 00:00 ו-12:00 UTC — price sync + discovery
- GitHub Actions: פעיל — ממשיך לרוץ לבדיקת מקורות חדשים
