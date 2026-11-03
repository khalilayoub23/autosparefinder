#!/bin/bash
# Back up EVERYTHING migration 0042's downgrade destroys, from ONE consistent snapshot, then validate it.
# Usage: backup_0042_data.sh NEW_DIR        (env PII_CONTAINER, default autospare_postgres_pii)
# Exit 0 only when the backup is complete and validated. Never overwrites: NEW_DIR must not exist.
set -euo pipefail
OUT=${1:?usage: backup_0042_data.sh NEW_DIR}
PII=${PII_CONTAINER:-autospare_postgres_pii}
[ -e "$OUT" ] && { echo "STOP: $OUT already exists - refusing to overwrite a backup"; exit 2; }
psqlc(){ docker exec -i "$PII" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -qtA '"$1"; }
v=$(psqlc "-c 'SELECT version_num FROM alembic_version'")
[ "$v" = "0042_supplier_lifecycle" ] || { echo "STOP: schema is '$v', not 0042_supplier_lifecycle - nothing to back up"; exit 3; }
TMP=/tmp/bk0042_$(date -u +%Y%m%dT%H%M%SZ)_$$
docker exec "$PII" sh -c "umask 077 && mkdir $TMP"
trap 'docker exec "$PII" rm -rf "$TMP" >/dev/null 2>&1 || true' EXIT
# data + counts in ONE repeatable-read read-only transaction (a single consistent snapshot)
docker exec -i "$PII" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q' <<SQL
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
\o $TMP/counts.csv
COPY (SELECT 'agent_authorizations', count(*) FROM agent_authorizations UNION ALL
      SELECT 'supplier_outreach', count(*) FROM supplier_outreach UNION ALL
      SELECT 'procurement_orders', count(*) FROM procurement_orders UNION ALL
      SELECT 'procurement_events', count(*) FROM procurement_events UNION ALL
      SELECT 'email_agent_messages_supplier_cols', count(*) FROM email_agent_messages UNION ALL
      SELECT 'email_rows_with_supplier_data', count(*) FROM email_agent_messages
             WHERE supplier_route IS NOT NULL OR source_folder <> 'inbox') TO STDOUT WITH CSV;
\o $TMP/agent_authorizations.csv
COPY agent_authorizations TO STDOUT WITH CSV HEADER;
\o $TMP/supplier_outreach.csv
COPY supplier_outreach TO STDOUT WITH CSV HEADER;
\o $TMP/procurement_orders.csv
COPY procurement_orders TO STDOUT WITH CSV HEADER;
\o $TMP/procurement_events.csv
COPY procurement_events TO STDOUT WITH CSV HEADER;
\o $TMP/email_agent_messages_supplier_cols.csv
COPY (SELECT id, source_folder, supplier_routed_at, supplier_route FROM email_agent_messages) TO STDOUT WITH CSV HEADER;
\o $TMP/procurement_events_seq.csv
COPY (SELECT last_value, is_called FROM procurement_events_id_seq) TO STDOUT WITH CSV;
\o
COMMIT;
SQL
# schema definitions (tables, CHECK/UNIQUE/FK constraints, indexes, sequence) of the objects the downgrade drops
docker exec "$PII" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --schema-only --no-owner --no-acl -t agent_authorizations -t supplier_outreach -t procurement_orders -t procurement_events > '"$TMP"'/schema_0042_tables.sql'
mkdir -m 700 -p "$OUT"
docker cp "$PII:$TMP/." "$OUT/" >/dev/null
# validate: every file present, row counts in the CSVs == counts taken inside the same snapshot
python3 - "$OUT" <<'PY'
import csv, json, sys, os, datetime
d = sys.argv[1]
exp = {r[0]: int(r[1]) for r in csv.reader(open(os.path.join(d, "counts.csv")))}
got = {}
for name in ("agent_authorizations", "supplier_outreach", "procurement_orders", "procurement_events",
             "email_agent_messages_supplier_cols"):
    with open(os.path.join(d, name + ".csv"), newline="") as f:
        got[name] = sum(1 for _ in csv.reader(f)) - 1           # minus header (csv handles multi-line values)
bad = {k: (exp[k], got[k]) for k in got if exp[k] != got[k]}
schema = open(os.path.join(d, "schema_0042_tables.sql")).read()
need = ["CREATE TABLE public.agent_authorizations", "CREATE TABLE public.supplier_outreach",
        "CREATE TABLE public.procurement_orders", "CREATE TABLE public.procurement_events",
        "procurement_submitted_requires_po_authorization", "procurement_paid_requires_payment_authorization",
        "supplier_outreach_sent_requires_authorization", "FOREIGN KEY (procurement_id)"]
missing = [n for n in need if n not in schema]
if bad or missing:
    print("STOP: backup INVALID", {"count_mismatch": bad, "schema_missing": missing}); sys.exit(4)
json.dump({"schema_version": "0042_supplier_lifecycle", "counts": exp, "created_utc":
           datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")},
          open(os.path.join(d, "MANIFEST.json"), "w"), indent=1)
print("backup VALID", exp)
PY
(cd "$OUT" && sha256sum *.csv *.sql MANIFEST.json > SHA256SUMS && chmod 400 *.csv *.sql MANIFEST.json SHA256SUMS)
echo "BACKUP OK: $OUT"
