# Alembic – PII Database

Manages schema migrations for the `autospare_pii` PostgreSQL database.

This database stores all personally-identifiable information (GDPR scope):
- users, user_profiles, user_sessions, two_factor_codes, login_attempts, password_resets
- vehicles (encrypted VIN / license_plate), user_vehicles
- orders, order_items, payments, invoices, returns
- conversations, messages, notifications

The catalog database (`autospare`) never contains PII.

## Usage

```bash
# run from backend/
DATABASE_PII_URL=... alembic -c alembic_pii.ini upgrade head
```
