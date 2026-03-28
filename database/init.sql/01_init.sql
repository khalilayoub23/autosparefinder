-- =============================================================================
-- Auto Spare Finder — PostgreSQL Initialisation
-- Runs once on first container start (mounted at /docker-entrypoint-initdb.d/)
-- Executed as the POSTGRES_USER (autospare / superuser).
--
-- What this does:
--   1. Creates the PII database (catalog DB "autospare" is created automatically
--      by the POSTGRES_DB env var).
--   2. Enables uuid-ossp on both databases so gen_random_uuid() is available
--      even before Alembic migrations run.
--
-- Note: pgvector (vector), pg_trgm, and pgcrypto extensions are enabled by
-- Alembic migrations (0009_add_pgvector, 0016_add_pgtrgm, 0001_pii_initial)
-- using CREATE EXTENSION IF NOT EXISTS — no need to duplicate them here.
-- =============================================================================

-- ── 1. PII database ───────────────────────────────────────────────────────────
-- The catalog DB is already created by POSTGRES_DB env var.
-- We only need to create the second database.
CREATE DATABASE autospare_pii
    OWNER autospare
    ENCODING 'UTF8'
    LC_COLLATE 'en_US.UTF-8'
    LC_CTYPE   'en_US.UTF-8'
    TEMPLATE   template0;

-- ── 2. Extensions on the catalog DB (autospare) ───────────────────────────────
\c autospare
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── 3. Extensions on the PII DB ───────────────────────────────────────────────
\c autospare_pii
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
