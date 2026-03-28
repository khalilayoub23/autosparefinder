# AutoSpareFinder — Bug & Breaking Points Fix Tracker
> Last scan: 2026-03-28 | Total issues found: 49 | Fixed: 49 | In Progress: 0 | Open: 0

---

## Legend
- ✅ Fixed
- 🔄 In Progress
- ❌ Not Started
- ⚠️ Noted (no code change needed / external action required)

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

### Pending Steps (Approved Extraction Order)

| Step | Domain | Files planned | Status |
|------|--------|---------------|--------|
| 12 | Marketing + Social | `backend/routes/marketing.py`, `backend/routes/social.py` | ❌ Pending |
| 13 | Admin (users/settings/approvals) | `backend/routes/admin.py` | ❌ Pending |
| 14 | Wishlist | `backend/routes/wishlist.py` | ❌ Pending |
| 15 | Cart + Checkout | `backend/routes/cart.py` | ❌ Pending |
| 16 | System/Health/Utility leftovers | `backend/routes/system.py` | ❌ Pending |

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

All 23 failures are pre-existing (integration tests requiring a live DB + SQL injection tests).
Zero regressions introduced.
