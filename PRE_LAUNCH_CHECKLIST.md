# Pre-Launch Checklist

## 🔴 BLOCKERS — must be done before first real user

- [x] **JWT_SECRET_KEY** — replace dev placeholder in `backend/.env`
      ```
      python -c "import secrets; print(secrets.token_hex(32))"
      ```

- [x] **JWT_REFRESH_SECRET_KEY** — replace dev placeholder in `backend/.env`
      ```
      python -c "import secrets; print(secrets.token_hex(32))"
      ```

- [x] **ENCRYPTION_KEY** — currently empty; PII field encryption broken without it.
      Set before ANY user registers — fields written with no key are unreadable after adding one.
      ```
      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
      ```

- [x] **STRIPE_SECRET_KEY** — confirmed `sk_live_*` in production .env (verified 2026-06-04)

- [x] **STRIPE_WEBHOOK_SECRET** — `whsec_*` is set in production .env (verified 2026-06-04)

- [ ] **SENDGRID_API_KEY** — key IS set in .env but API returns 401 (SendGrid account-level issue on provider side, not a missing key). Emails currently non-functional — contact SendGrid support or migrate to alternative provider (Resend, AWS SES)

- [x] **SUPERUSER_PASSWORD** — set

- [x] **ENVIRONMENT + DEBUG flags** — change in `backend/.env`:
      ```
      ENVIRONMENT=production
      DEBUG=false
      ```

- [x] **SSL** — handled by Cloudflare; nginx now listens on port 80 only. Set Cloudflare SSL/TLS mode to **Full**.

---

## 🟡 SHOULD-DO — before marketing / public traffic

- [ ] **Frontend production API URL** — add `VITE_API_URL=https://autosparefinder.co.il` as a build arg
      in `frontend/Dockerfile` so the frontend doesn't rely on relative paths in production

- [x] **DB password on VPS** — verified 2026-06-04: DB_PASSWORD is a 64-char hex key (256-bit entropy), NOT the dev default

- [ ] **Run populate_supplier_parts** — links all parts to all suppliers (run once after first deploy):
      ```
      POST /api/v1/admin/db-agent/run/populate_supplier_parts
      ```

- [ ] **Run validate_migrations** — pre-flight check before first `alembic upgrade head` on production:
      ```
      POST /api/v1/admin/db-agent/run/validate_migrations
      ```

- [ ] **COMPANY_PHONE** — currently placeholder `+972-XX-XXXXXXX` in `backend/.env`

- [ ] **SENDGRID_FROM_EMAIL** — confirm `support@autosparefinder.co.il` is a verified sender in SendGrid once account issue is resolved

- [x] **Stripe live mode** — `sk_live_*` confirmed active; end-to-end checkout has been validated (verified 2026-06-04)

---

## 🟢 ALREADY DONE

- [x] HTTPS nginx — TLS 1.2/1.3, HSTS, security headers, WebSocket + SSE locations
- [x] 333 tests passing, 0 failing
- [x] HF client — connection pool, retry on 503/429, Redis cache (736× speedup proven)
- [x] Dual DB migrations at head (catalog + PII)
- [x] populate_supplier_parts + validate_migrations wired as admin workers
- [x] All docker services with `restart: unless-stopped`
- [x] .gitignore clean, no secrets in git history
- [x] Dead code and duplicate files removed
- [x] Server migrated to vmi3190597 (207.180.217.129) — all services running
- [x] eBay OAuth2 auto-refresh implemented in external_fitment_providers.py (2026-06-04)
- [x] Jaguar fitment backfill function added — parses model names from part names (2026-06-04)
- [x] db_update_agent heartbeat fixed — no more zombie watchdog kills (2026-06-04)
- [x] REAL_DATA_ONLY split: synthetic guard kept, REX_HARVEST_ENABLED=true added (2026-06-04)
- [x] Search cache upgraded to Redis write-through with in-memory L1 fallback (2026-06-04)
- [x] COMPANY_PHONE set to +972-53-242-6920, company vars added to docker-compose (2026-06-04)
- [x] populate_supplier_parts implemented and running (78%+ parts now have supplier links) (2026-06-04)
- [x] normalize_categories optimized — skips already-canonical 804K rows (2026-06-04)
- [x] Golden rules added to claude.md (2026-06-04)

## ✅ 2026-06-17 Pre-Launch Batch

- [x] **Backend OOM fixed** — `mem_limit: 2g → 4g` in docker-compose. `normalize_part_types` rewritten from 3.45M fetchall() to SQL CASE WHEN (25 min → ~1s, ~2GB spike removed)
- [x] **6 OOM-causing tasks disabled** — `merge_catalog_fitment`, `backfill_bmw/ford/jaguar_fitment`, `fix_base_prices`, `normalize_base_price` all commented out in `db_update_agent.py` (lines 4368-4383). Were doing 0 useful work per cycle.
- [x] **auto_backup.py fixed** — `db_url.replace("+asyncpg","")` at line 31. Backups were silently failing.
- [x] **Meilisearch rebuild loop fixed** — `REBUILD_DEFAULT "1"→"0"`, checkpoint saved at offset=total (no more 6-hour accidental rebuilds). Added `MEILI_REBUILD: '0'` to docker-compose.
- [x] **VAT 0.17 pricing corrected** — 389,750 parts recalculated with 18% VAT. `vat_rate=0.17` count: 0.
- [x] **Wrong margin corrected** — 196,501 parts corrected to 45% margin. `wrong_margin` count: 0.
- [x] **run_brand_discovery() data completeness** — now writes fitment rows + rich JSONB specs on every discovery run (previously only wrote part + supplier price, no fitment, no structured specs)
- [x] **Meilisearch new filterable fields** — `part_condition`, `importer_price_ils`, `has_il_price` added to index (rebuild in progress)
- [ ] **Categories** — 74% parts in כללי/general (categorize_parts_batch.py running overnight, ETA 22:30 UTC 2026-06-17)
- [ ] **part_condition normalization** — `New` (capital) → `new` (fix_condition.py running, ETA overnight)
- [ ] **Price comparison** — supplier_parts 2.3M records not yet surfaced in search/API
- [ ] **SendGrid** — still broken (account-level issue, not code)
