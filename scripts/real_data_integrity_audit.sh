#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB_CONTAINER="${DB_CONTAINER:-autospare_postgres_catalog}"
DB_USER="${DB_USER:-autospare}"
DB_NAME="${DB_NAME:-autospare}"

STAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

AUDIT_ROW="$(docker exec -i "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 -t -A -F',' <<'SQL'
WITH verified AS (
    SELECT DISTINCT sp.part_id
    FROM supplier_parts sp
    JOIN suppliers s ON s.id = sp.supplier_id
    WHERE s.is_active = TRUE
      AND s.name NOT IN ('Official Manufacturer Sites', 'Sandbox Supplier QA')
      AND NULLIF(BTRIM(sp.supplier_url), '') IS NOT NULL
),
inventory AS (
    SELECT
        COUNT(*) FILTER (WHERE pc.is_active) AS active_parts,
        COUNT(*) FILTER (WHERE pc.is_active AND pc.id IN (SELECT part_id FROM verified)) AS active_with_verified_source,
        COUNT(*) FILTER (WHERE pc.is_active AND pc.id NOT IN (SELECT part_id FROM verified)) AS active_without_verified_source,
        COUNT(*) FILTER (WHERE pc.is_active AND COALESCE(pc.needs_oem_lookup, FALSE) = TRUE) AS active_needs_oem_lookup
    FROM parts_catalog pc
),
synthetic_suppliers AS (
    SELECT
        COUNT(*) FILTER (WHERE s.is_active = TRUE) AS synthetic_suppliers_active,
        COALESCE(SUM(CASE WHEN sp.is_available = TRUE THEN 1 ELSE 0 END), 0) AS synthetic_supplier_available_rows
    FROM suppliers s
    LEFT JOIN supplier_parts sp ON sp.supplier_id = s.id
    WHERE s.name IN ('Official Manufacturer Sites', 'Sandbox Supplier QA')
)
SELECT
    inv.active_parts,
    inv.active_with_verified_source,
    inv.active_without_verified_source,
    inv.active_needs_oem_lookup,
    syn.synthetic_suppliers_active,
    syn.synthetic_supplier_available_rows,
    (
      inv.active_without_verified_source
      + inv.active_needs_oem_lookup
      + syn.synthetic_suppliers_active
      + syn.synthetic_supplier_available_rows
    ) AS violation_count
FROM inventory inv
CROSS JOIN synthetic_suppliers syn;
SQL
)"

IFS=',' read -r ACTIVE_PARTS ACTIVE_VERIFIED ACTIVE_UNVERIFIED ACTIVE_NEEDS_OEM SYNTHETIC_ACTIVE_SYNTH_SUPPLIERS SYNTHETIC_AVAILABLE_ROWS VIOLATION_COUNT <<<"${AUDIT_ROW}"

STATUS="pass"
if [[ "${VIOLATION_COUNT}" -gt 0 ]]; then
  STATUS="fail"
fi

cat <<REPORT
generated_at: ${STAMP}
active_parts: ${ACTIVE_PARTS}
active_with_verified_source: ${ACTIVE_VERIFIED}
active_without_verified_source: ${ACTIVE_UNVERIFIED}
active_needs_oem_lookup: ${ACTIVE_NEEDS_OEM}
synthetic_suppliers_active: ${SYNTHETIC_ACTIVE_SYNTH_SUPPLIERS}
synthetic_supplier_available_rows: ${SYNTHETIC_AVAILABLE_ROWS}
violation_count: ${VIOLATION_COUNT}
status: ${STATUS}
REPORT

if [[ "${STATUS}" != "pass" ]]; then
  exit 2
fi
