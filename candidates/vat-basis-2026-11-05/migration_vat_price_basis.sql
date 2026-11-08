-- PREPARED, NOT APPLIED (production DDL needs owner approval; suppliers is small, ADD COLUMN with no default is metadata-only).
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS vat_price_basis varchar(12)
  CHECK (vat_price_basis IN ('inclusive','exclusive','unknown'));
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS vat_basis_evidence jsonb;       -- {source_url, quote, method, confidence}
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS vat_basis_verified_at timestamptz;
-- ROLLBACK: ALTER TABLE suppliers DROP COLUMN vat_basis_verified_at, DROP COLUMN vat_basis_evidence, DROP COLUMN vat_price_basis;
