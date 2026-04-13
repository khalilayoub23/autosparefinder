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

- [ ] **STRIPE_SECRET_KEY** — sandbox key in place; swap for `sk_live_***` at go-live

- [ ] **STRIPE_WEBHOOK_SECRET** — register webhook URL `https://autospare.com/api/v1/payments/webhook`
      in Stripe dashboard (live mode) → copy `whsec_***` → paste into `backend/.env` at go-live

- [ ] **SENDGRID_API_KEY** — left empty; fill at go-live (emails fail gracefully until set)

- [x] **SUPERUSER_PASSWORD** — set

- [x] **ENVIRONMENT + DEBUG flags** — change in `backend/.env`:
      ```
      ENVIRONMENT=production
      DEBUG=false
      ```

- [x] **SSL** — handled by Cloudflare; nginx now listens on port 80 only. Set Cloudflare SSL/TLS mode to **Full**.

---

## 🟡 SHOULD-DO — before marketing / public traffic

- [ ] **Frontend production API URL** — add `VITE_API_URL=https://autospare.com` as a build arg
      in `frontend/Dockerfile` so the frontend doesn't rely on relative paths in production

- [ ] **DB password on VPS** — ensure `docker-compose.yml` env on VPS uses a strong `DB_PASSWORD`,
      not the dev default `autospare_dev`

- [ ] **Run populate_supplier_parts** — links all parts to all suppliers (run once after first deploy):
      ```
      POST /api/v1/admin/db-agent/run/populate_supplier_parts
      ```

- [ ] **Run validate_migrations** — pre-flight check before first `alembic upgrade head` on production:
      ```
      POST /api/v1/admin/db-agent/run/validate_migrations
      ```

- [ ] **COMPANY_PHONE** — currently placeholder `+972-XX-XXXXXXX` in `backend/.env`

- [ ] **SENDGRID_FROM_EMAIL** — confirm `support@autospare.com` is a verified sender in SendGrid

- [ ] **Stripe live mode** — after switching to `sk_live_***`, test one real checkout end-to-end
      before opening to customers

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
