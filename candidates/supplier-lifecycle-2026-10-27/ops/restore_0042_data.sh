#!/bin/bash
# Put backed-up 0042 data back into a database that is at 0042 again (re-deploy after a rollback, or a recovery DB).
# Usage: restore_0042_data.sh BACKUP_DIR     ONE transaction: commits only if every count matches; else nothing.
set -euo pipefail
D=${1:?usage: restore_0042_data.sh BACKUP_DIR}; PII=${PII_CONTAINER:-autospare_postgres_pii}
(cd "$D" && sha256sum -c --quiet SHA256SUMS) || { echo "STOP: backup files fail their checksums"; exit 6; }
psqlq(){ docker exec -i "$PII" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -qtA'; }
v=$(echo "SELECT version_num FROM alembic_version" | psqlq)
[ "$v" = "0042_supplier_lifecycle" ] || { echo "STOP: target schema is '$v' - re-apply 0042 first"; exit 3; }
nonempty=$(echo "SELECT (SELECT count(*) FROM agent_authorizations)+(SELECT count(*) FROM supplier_outreach)+(SELECT count(*) FROM procurement_orders)+(SELECT count(*) FROM procurement_events)" | psqlq)
[ "$nonempty" = "0" ] || { echo "STOP: target 0042 tables are not empty ($nonempty rows) - refusing to merge"; exit 7; }
TMP=/tmp/rs0042_$$; docker exec "$PII" sh -c "umask 077 && mkdir $TMP"; trap 'docker exec "$PII" rm -rf "$TMP" >/dev/null 2>&1 || true' EXIT
docker cp "$D/." "$PII:$TMP/" >/dev/null
read_count(){ grep "^$1," "$D/counts.csv" | cut -d, -f2; }
EA=$(read_count agent_authorizations); ES=$(read_count supplier_outreach); EP=$(read_count procurement_orders); EE=$(read_count procurement_events); EM=$(read_count email_rows_with_supplier_data)
SEQ=$(cut -d, -f1 "$D/procurement_events_seq.csv"); CALLED=$(cut -d, -f2 "$D/procurement_events_seq.csv")
case "$CALLED" in t|true) CALLED=true ;; f|false) CALLED=false ;; *) echo "STOP: unreadable sequence state '$CALLED'"; exit 8 ;; esac
[[ "$SEQ" =~ ^[0-9]+$ ]] || { echo "STOP: unreadable sequence value '$SEQ'"; exit 8; }
set +e
docker exec -i "$PII" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q' <<SQL
BEGIN;
\copy agent_authorizations FROM '$TMP/agent_authorizations.csv' WITH CSV HEADER
\copy supplier_outreach FROM '$TMP/supplier_outreach.csv' WITH CSV HEADER
\copy procurement_orders FROM '$TMP/procurement_orders.csv' WITH CSV HEADER
\copy procurement_events FROM '$TMP/procurement_events.csv' WITH CSV HEADER
CREATE TEMP TABLE r_email (id uuid PRIMARY KEY, source_folder varchar(16), supplier_routed_at timestamptz, supplier_route jsonb) ON COMMIT DROP;
\copy r_email FROM '$TMP/email_agent_messages_supplier_cols.csv' WITH CSV HEADER
UPDATE email_agent_messages m SET source_folder = r.source_folder, supplier_routed_at = r.supplier_routed_at,
       supplier_route = r.supplier_route FROM r_email r WHERE m.id = r.id;
SELECT setval('procurement_events_id_seq', $SEQ, $CALLED);
DO \$\$ BEGIN
  IF (SELECT count(*) FROM agent_authorizations) <> $EA OR (SELECT count(*) FROM supplier_outreach) <> $ES
     OR (SELECT count(*) FROM procurement_orders) <> $EP OR (SELECT count(*) FROM procurement_events) <> $EE
     OR (SELECT count(*) FROM email_agent_messages WHERE supplier_route IS NOT NULL OR source_folder <> 'inbox') <> $EM
     OR (SELECT count(*) FROM r_email r LEFT JOIN email_agent_messages m ON m.id = r.id WHERE m.id IS NULL) <> 0
  THEN RAISE EXCEPTION 'restore verification failed - rolled back, nothing restored'; END IF;
END \$\$;
COMMIT;
SQL
rc=$?; set -e
[ $rc -eq 0 ] || { echo "RESTORE FAILED (psql exit $rc) - transaction rolled back, NOTHING was restored"; exit 9; }
echo "RESTORE OK from $D (authorizations=$EA outreach=$ES procurement=$EP events=$EE email_rows=$EM)"
