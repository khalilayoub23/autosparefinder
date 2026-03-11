-- Postgres initialization script.
-- Runs on first container start (only when the data directory is empty).
-- Creates the secondary PII database alongside the primary catalog database.

SELECT 'CREATE DATABASE autospare_pii OWNER autospare'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'autospare_pii'
)\gexec
