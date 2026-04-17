#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${1:-autospare_postgres_catalog}"
DB_USER="${DB_USER:-autospare}"
DB_NAME="${DB_NAME:-autospare}"

echo "== Manufacturer Fitment Gap Audit =="
echo "generated_at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "db_container: ${CONTAINER_NAME}"
echo

docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -P pager=off <<'SQL'
SELECT 'parts_catalog_active' AS src, COUNT(DISTINCT LOWER(TRIM(manufacturer))) AS distinct_mfr
FROM parts_catalog
WHERE is_active = TRUE AND manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
UNION ALL
SELECT 'parts_catalog_all', COUNT(DISTINCT LOWER(TRIM(manufacturer)))
FROM parts_catalog
WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
UNION ALL
SELECT 'part_vehicle_fitment', COUNT(DISTINCT LOWER(TRIM(manufacturer)))
FROM part_vehicle_fitment
WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
UNION ALL
SELECT 'vehicle_hierarchy_xls', COUNT(DISTINCT LOWER(TRIM(manufacturer)))
FROM vehicle_hierarchy_xls
WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
UNION ALL
SELECT 'vehicle_market_il', COUNT(DISTINCT LOWER(TRIM(manufacturer)))
FROM vehicle_market_il
WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
UNION ALL
SELECT 'vehicles', COUNT(DISTINCT LOWER(TRIM(manufacturer)))
FROM vehicles
WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
UNION ALL
SELECT 'search_misses', COUNT(DISTINCT LOWER(TRIM(vehicle_manufacturer)))
FROM search_misses
WHERE vehicle_manufacturer IS NOT NULL AND TRIM(vehicle_manufacturer) <> ''
UNION ALL
SELECT 'car_brands_names', COUNT(DISTINCT LOWER(TRIM(name)))
FROM car_brands
WHERE name IS NOT NULL AND TRIM(name) <> '';
SQL

echo

docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -P pager=off <<'SQL'
WITH b AS (
  SELECT manufacturer AS brand, COUNT(*) AS active_parts
  FROM parts_catalog
  WHERE is_active = TRUE
  GROUP BY manufacturer
),
json_fit AS (
  SELECT manufacturer AS brand,
         COUNT(*) FILTER (
           WHERE compatible_vehicles IS NOT NULL
             AND jsonb_typeof(compatible_vehicles)='array'
             AND jsonb_array_length(compatible_vehicles)>0
         ) AS parts_with_json,
         COUNT(*) FILTER (
           WHERE EXISTS (
             SELECT 1 FROM jsonb_array_elements(COALESCE(compatible_vehicles,'[]'::jsonb)) e
             WHERE e->>'source'='parts_database.xlsx'
           )
         ) AS parts_with_xls_source,
         COUNT(*) FILTER (
           WHERE EXISTS (
             SELECT 1 FROM jsonb_array_elements(COALESCE(compatible_vehicles,'[]'::jsonb)) e
             WHERE e->>'source'='part_vehicle_fitment'
           )
         ) AS parts_with_pvf_source
  FROM parts_catalog
  WHERE is_active = TRUE
  GROUP BY manufacturer
),
pvf AS (
  SELECT manufacturer AS brand, COUNT(DISTINCT part_id) AS pvf_parts
  FROM part_vehicle_fitment
  GROUP BY manufacturer
),
xls AS (
  SELECT manufacturer AS brand, COUNT(*) AS xls_hierarchy_rows
  FROM vehicle_hierarchy_xls
  GROUP BY manufacturer
)
SELECT
  b.brand,
  b.active_parts,
  COALESCE(j.parts_with_json,0) AS parts_with_json,
  COALESCE(j.parts_with_xls_source,0) AS parts_with_xls_source,
  COALESCE(j.parts_with_pvf_source,0) AS parts_with_pvf_source,
  COALESCE(p.pvf_parts,0) AS pvf_parts,
  COALESCE(x.xls_hierarchy_rows,0) AS xls_hierarchy_rows,
  ROUND((COALESCE(j.parts_with_json,0)::numeric / NULLIF(b.active_parts,0))*100, 2) AS json_fit_pct,
  CASE
    WHEN COALESCE(j.parts_with_json,0) = 0 AND COALESCE(p.pvf_parts,0)=0 THEN 'CRITICAL_NO_FITMENT'
    WHEN (COALESCE(j.parts_with_json,0)::numeric / NULLIF(b.active_parts,0)) < 0.05 THEN 'LOW_COVERAGE'
    ELSE 'OK_OR_PARTIAL'
  END AS status
FROM b
LEFT JOIN json_fit j ON j.brand=b.brand
LEFT JOIN pvf p ON p.brand=b.brand
LEFT JOIN xls x ON x.brand=b.brand
ORDER BY
  CASE
    WHEN COALESCE(j.parts_with_json,0) = 0 AND COALESCE(p.pvf_parts,0)=0 THEN 1
    WHEN (COALESCE(j.parts_with_json,0)::numeric / NULLIF(b.active_parts,0)) < 0.05 THEN 2
    ELSE 3
  END,
  b.active_parts DESC;
SQL

echo

docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -P pager=off <<'SQL'
WITH all_labels AS (
  SELECT LOWER(TRIM(manufacturer)) AS m FROM parts_catalog WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
  UNION
  SELECT LOWER(TRIM(manufacturer)) FROM part_vehicle_fitment WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
  UNION
  SELECT LOWER(TRIM(manufacturer)) FROM vehicle_hierarchy_xls WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
  UNION
  SELECT LOWER(TRIM(manufacturer)) FROM vehicle_market_il WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
  UNION
  SELECT LOWER(TRIM(manufacturer)) FROM vehicles WHERE manufacturer IS NOT NULL AND TRIM(manufacturer) <> ''
  UNION
  SELECT LOWER(TRIM(vehicle_manufacturer)) FROM search_misses WHERE vehicle_manufacturer IS NOT NULL AND TRIM(vehicle_manufacturer) <> ''
), alias_labels AS (
  SELECT LOWER(TRIM(name)) AS m FROM car_brands WHERE name IS NOT NULL AND TRIM(name) <> ''
  UNION
  SELECT LOWER(TRIM(name_he)) AS m FROM car_brands WHERE name_he IS NOT NULL AND TRIM(name_he) <> ''
  UNION
  SELECT LOWER(TRIM(a)) AS m
  FROM car_brands c
  CROSS JOIN LATERAL unnest(COALESCE(c.aliases, ARRAY[]::text[])) a
  WHERE a IS NOT NULL AND TRIM(a) <> ''
)
SELECT
  (SELECT COUNT(*) FROM all_labels) AS total_distinct_labels_all_sources,
  (SELECT COUNT(*) FROM alias_labels) AS total_distinct_labels_in_brand_dictionary,
  (SELECT COUNT(*) FROM all_labels l LEFT JOIN alias_labels a ON a.m = l.m WHERE a.m IS NULL) AS labels_missing_from_dictionary;
SQL
