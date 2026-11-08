-- CANDIDATE - NOT APPLIED. OFFER_DEDUPLICATION_POLICY 1.0.0 section 6 (class B storage).
-- Identity model: catalog part = part_id | supplier = supplier_id | commercial offer = (supplier_id, supplier_sku) [existing
-- unique constraint, unchanged] | variant = normalized part_type | source = specs/import stamp (not a column; out of scope).
-- Replaces UNIQUE(part_id, supplier_id) with two PARTIAL unique indexes that keep "one offer per supplier+part+variant":
--   typed offers  : unique (part_id, supplier_id, variant)       -> genuine and aftermarket may coexist
--   untyped (NULL): unique (part_id, supplier_id)                -> legacy/unknown variants are NEVER silently combined
-- Every existing row satisfies both (old index is strictly stronger) -> index creation cannot fail on current data.
-- Run each statement OUTSIDE a transaction (CONCURRENTLY). Preflight/approval: docs/OFFER_POLICY_PRODUCTION_READINESS.md.

-- STAGE 0 (read-only preflight)  expect 0 / 0
SELECT count(*) FROM (SELECT 1 FROM supplier_parts WHERE part_type IS NOT NULL
   GROUP BY part_id, supplier_id, (CASE WHEN lower(part_type) IN ('original','genuine','oem','oe') THEN 'oem' ELSE lower(part_type) END) HAVING count(*) > 1) t;
SELECT count(*) FROM (SELECT 1 FROM supplier_parts WHERE part_type IS NULL GROUP BY part_id, supplier_id HAVING count(*) > 1) t;

-- STAGE 1 (additive, no write blocking)
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_sp_part_supplier_variant
  ON supplier_parts (part_id, supplier_id, (CASE WHEN lower(part_type) IN ('original','genuine','oem','oe') THEN 'oem' ELSE lower(part_type) END))
  WHERE part_type IS NOT NULL;
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_sp_part_supplier_untyped
  ON supplier_parts (part_id, supplier_id) WHERE part_type IS NULL;

-- STAGE 2 verify: both indexes valid
SELECT indexrelid::regclass, indisvalid, indisready FROM pg_index WHERE indexrelid::regclass::text IN ('uq_sp_part_supplier_variant','uq_sp_part_supplier_untyped');

-- STAGE 3 (separate approval, after code deploy of: importer variant_conflict handling, db_update_agent:3826 ON CONFLICT target,
--          scripts/import_merged_catalog.py:134 CREATE UNIQUE INDEX uq_supplier_parts_part_supplier, aliexpress/ebay lookups scoped by variant)
-- DROP INDEX CONCURRENTLY IF EXISTS uq_supplier_parts_part_supplier;

-- ROLLBACK (any stage): recreate the original, drop the new ones
-- CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_supplier_parts_part_supplier ON supplier_parts (part_id, supplier_id);   -- only if stage 3 ran
-- DROP INDEX CONCURRENTLY IF EXISTS uq_sp_part_supplier_variant;
-- DROP INDEX CONCURRENTLY IF EXISTS uq_sp_part_supplier_untyped;
-- Resume after interruption: a failed CONCURRENTLY build leaves an INVALID index (indisvalid=false): DROP INDEX CONCURRENTLY it, rerun.
