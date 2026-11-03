#!/bin/bash
# Immediately before the downgrade: the live 0042 data must still equal the validated backup (no writes since).
# Usage: verify_unchanged_0042.sh BACKUP_DIR     Exit 0 = unchanged; 5 = changed -> STOP and take a new backup.
set -euo pipefail
D=${1:?usage: verify_unchanged_0042.sh BACKUP_DIR}; PII=${PII_CONTAINER:-autospare_postgres_pii}
(cd "$D" && sha256sum -c --quiet SHA256SUMS) || { echo "STOP: backup files fail their checksums"; exit 6; }
now=$(docker exec -i "$PII" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -qtA -F,' <<'SQL'
SELECT 'agent_authorizations', count(*) FROM agent_authorizations UNION ALL
SELECT 'supplier_outreach', count(*) FROM supplier_outreach UNION ALL
SELECT 'procurement_orders', count(*) FROM procurement_orders UNION ALL
SELECT 'procurement_events', count(*) FROM procurement_events UNION ALL
SELECT 'email_agent_messages_supplier_cols', count(*) FROM email_agent_messages UNION ALL
SELECT 'email_rows_with_supplier_data', count(*) FROM email_agent_messages WHERE supplier_route IS NOT NULL OR source_folder <> 'inbox';
SQL
)
if [ "$now" = "$(cat "$D/counts.csv")" ]; then echo "UNCHANGED since backup"; else echo "STOP: data changed since the backup:"; diff <(cat "$D/counts.csv") <(echo "$now") || true; exit 5; fi
