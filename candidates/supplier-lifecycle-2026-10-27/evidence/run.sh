#!/bin/bash
# Migration + rollback rehearsal for 0042_supplier_lifecycle on a throwaway Postgres (no internet, no production).
S=/tmp/claude-0/-opt-autosparefinder/2b46ba28-2c33-4d4f-b9b3-7c878aed1e70/scratchpad; R=$S/rehearsal; NET=rehearsalnet
IMG=autosparefinder-backend:latest; DB=postgresql+asyncpg://postgres@pg:5432/postgres
docker rm -f rpg >/dev/null 2>&1; docker network rm $NET >/dev/null 2>&1
docker network create --internal $NET >/dev/null
docker run -d --name rpg --network $NET --network-alias pg -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine >/dev/null; sleep 7
alem(){ tree=$1; shift; docker run --rm --network $NET -v $tree:/app:ro -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e DATABASE_PII_URL=$DB -w /app $IMG alembic -c alembic_pii.ini "$@" 2>&1 | grep -v "^INFO  \[alembic.runtime.migration\] \(Context impl\|Will assume\)"; echo "  exit=${PIPESTATUS[0]}"; }
psql(){ docker exec -i rpg psql -U postgres -tAq -v ON_ERROR_STOP=1 "$@"; }
ver(){ echo "  version=$(psql -c 'SELECT version_num FROM alembic_version' 2>/dev/null)"; }
newtables(){ echo "  new_tables=$(psql -c "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('agent_authorizations','supplier_outreach','procurement_orders','procurement_events')") new_cols=$(psql -c "SELECT count(*) FROM information_schema.columns WHERE table_name='email_agent_messages' AND column_name IN ('source_folder','supplier_routed_at','supplier_route')")"; }
echo "== 1. baseline code: full PII chain from an empty database"; alem $S/base2/backend upgrade head; ver
psql -c "INSERT INTO email_agent_messages (gmail_message_id, gmail_thread_id, status) VALUES ('pre1','t1','processed')"
echo "== 2. candidate code: upgrade head (as the container start would)"; alem $S/cand/backend upgrade head; ver; newtables
echo "  historic row routed-marked: $(psql -c "SELECT supplier_routed_at IS NOT NULL FROM email_agent_messages WHERE gmail_message_id='pre1'")"
echo "== 3. candidate code: upgrade head again (restart loop)"; alem $S/cand/backend upgrade head; ver
echo "== 4. write data into the new tables"
psql -c "INSERT INTO agent_authorizations (domain,scope,entity_type,entity_id,approved_by,source) VALUES ('purchase_order','create_po','procurement','p1','owner','test')"
psql -c "INSERT INTO procurement_orders (order_id,supplier_id,state,terms_hash,idempotency_key) VALUES ('o1','s1','AWAITING_PO_AUTHORIZATION','h','k1')"
psql -c "UPDATE email_agent_messages SET source_folder='spam', supplier_route='{\"owner\":\"NIR\"}' WHERE gmail_message_id='pre1'"
echo "  rows: auth=$(psql -c 'SELECT count(*) FROM agent_authorizations') proc=$(psql -c 'SELECT count(*) FROM procurement_orders')"
echo "== 5. baseline (deployed) code against the 0042 schema (code restored WITHOUT downgrade)"
docker run --rm --network $NET -v $S/base2/backend:/app:ro -v $R/oldcode.py:/t.py:ro -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e DATABASE_PII_URL=$DB -e DATABASE_URL=$DB -w /app $IMG python3 /t.py 2>&1 | tail -1
echo "  baseline 'upgrade head' on a 0042 database:"; alem $S/base2/backend upgrade head 2>&1 | tail -2
echo "== 6. WRONG order: restore old code first, then downgrade with it"; alem $S/base2/backend downgrade 0041_email_agent_owner_flow | tail -2; ver
echo "== 7. documented order: downgrade with the CANDIDATE code"; psql -c "\copy (SELECT * FROM procurement_orders) TO '/tmp/proc_backup.csv' CSV HEADER"; echo "  pre-downgrade backup: $(docker exec rpg sh -c 'wc -l < /tmp/proc_backup.csv') lines (header + rows)"
alem $S/cand/backend downgrade 0041_email_agent_owner_flow; ver; newtables
echo "  email row kept: $(psql -c "SELECT count(*) FROM email_agent_messages WHERE gmail_message_id='pre1'")  (its source_folder/supplier_route values are gone with the columns)"
echo "== 8. baseline code against the downgraded schema"
docker run --rm --network $NET -v $S/base2/backend:/app:ro -v $R/oldcode.py:/t.py:ro -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e DATABASE_PII_URL=$DB -e DATABASE_URL=$DB -w /app $IMG python3 /t.py 2>&1 | tail -1
echo "  baseline 'upgrade head' after downgrade (container restart on old code):"; alem $S/base2/backend upgrade head | tail -1; ver
echo "== 9. re-upgrade with the candidate (deploy after a rollback)"; alem $S/cand/backend upgrade head | tail -1; ver; newtables
echo "== 10. interrupted upgrade: a failing 0042 must leave the database at 0041 with nothing half-applied"
alem $S/cand/backend downgrade 0041_email_agent_owner_flow | tail -1
rm -rf $R/broken && cp -r $S/cand/backend $R/broken && python3 - "$R/broken/alembic_pii/versions/0042_supplier_lifecycle.py" <<'PY'
import sys; p=sys.argv[1]; s=open(p).read()
s=s.replace('    "CREATE INDEX IF NOT EXISTS ix_procurement_orders_order', '    "SELECT 1/0",\n    "CREATE INDEX IF NOT EXISTS ix_procurement_orders_order',1); open(p,'w').write(s)
PY
alem $R/broken upgrade head | grep -E "DivisionByZero|division|exit" | head -3; ver; newtables
echo "== 11. after the failure, the real candidate upgrades cleanly"; alem $S/cand/backend upgrade head | tail -1; ver; newtables
docker rm -f rpg >/dev/null; docker network rm $NET >/dev/null; rm -rf $R/broken
