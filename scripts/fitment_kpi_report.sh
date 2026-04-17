#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${1:-autospare_postgres_catalog}"
DB_USER="${DB_USER:-autospare}"
DB_NAME="${DB_NAME:-autospare}"

echo "== Fitment KPI Report =="
echo "generated_at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "db_container: ${CONTAINER_NAME}"
echo

docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -P pager=off -c "
WITH totals AS (
  SELECT
    COUNT(*) AS active_parts,
    COUNT(*) FILTER (
      WHERE compatible_vehicles IS NOT NULL
        AND jsonb_typeof(compatible_vehicles) = 'array'
        AND jsonb_array_length(compatible_vehicles) > 0
    ) AS json_fitment_parts
  FROM parts_catalog
  WHERE is_active = TRUE
),
structured AS (
  SELECT COUNT(DISTINCT part_id) AS structured_fitment_parts
  FROM part_vehicle_fitment
)
SELECT
  t.active_parts,
  t.json_fitment_parts,
  s.structured_fitment_parts,
  ROUND((t.json_fitment_parts::numeric / NULLIF(t.active_parts, 0)) * 100, 2) AS json_fitment_pct,
  ROUND((s.structured_fitment_parts::numeric / NULLIF(t.active_parts, 0)) * 100, 2) AS structured_fitment_pct
FROM totals t
CROSS JOIN structured s;
"

echo
docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -P pager=off -c "
WITH base AS (
  SELECT
    id,
    LOWER(TRIM(manufacturer)) AS mkey,
    compatible_vehicles
  FROM parts_catalog
  WHERE is_active = TRUE
    AND manufacturer IS NOT NULL
    AND TRIM(manufacturer) <> ''
),
fit AS (
  SELECT DISTINCT part_id
  FROM part_vehicle_fitment
),
per_brand AS (
  SELECT
    mkey,
    COUNT(*) AS active_parts,
    COUNT(*) FILTER (
      WHERE compatible_vehicles IS NOT NULL
        AND jsonb_typeof(compatible_vehicles) = 'array'
        AND jsonb_array_length(compatible_vehicles) > 0
    ) AS json_fitment_parts,
    COUNT(*) FILTER (WHERE fit.part_id IS NOT NULL) AS structured_fitment_parts
  FROM base
  LEFT JOIN fit ON fit.part_id = base.id
  GROUP BY mkey
)
SELECT
  mkey AS manufacturer_key,
  active_parts,
  json_fitment_parts,
  structured_fitment_parts,
  ROUND((json_fitment_parts::numeric / NULLIF(active_parts, 0)) * 100, 2) AS json_fitment_pct,
  ROUND((structured_fitment_parts::numeric / NULLIF(active_parts, 0)) * 100, 2) AS structured_fitment_pct
FROM per_brand
ORDER BY active_parts DESC
LIMIT 20;
"

echo
docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -P pager=off -c "
SELECT
  COUNT(*) AS misses_last_7d,
  COUNT(*) FILTER (WHERE triggered_scrape = TRUE) AS misses_triggered_scrape,
  COUNT(*) FILTER (WHERE notified = TRUE) AS misses_notified
FROM search_misses
WHERE last_seen_at >= NOW() - INTERVAL '7 days';
"

echo
docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -P pager=off -c "
SELECT
  COUNT(*) AS fitment_rows_updated_last_7d
FROM part_vehicle_fitment
WHERE COALESCE(updated_at, created_at) >= NOW() - INTERVAL '7 days';
"
