#!/bin/bash
# ISOLATED REHEARSAL - throwaway Postgres 16 (prod major), production names, production backend image, no internet.
S=/tmp/claude-0/-opt-autosparefinder/2b46ba28-2c33-4d4f-b9b3-7c878aed1e70/scratchpad; R=$S/rehearsal2; B=/opt/autosparefinder/candidates/supplier-lifecycle-2026-10-27
NET=rh2net; IMG=autosparefinder-backend:latest; DB=postgresql+asyncpg://autospare@pg:5432/autospare_pii; export PII_CONTAINER=rh2pg
CAND=$S/cand/backend; OLD=$S/base2/backend
fresh(){ docker rm -f rh2pg >/dev/null 2>&1; docker run -d --name rh2pg --network $NET --network-alias pg -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=autospare -e POSTGRES_DB=autospare_pii postgres:16-alpine >/dev/null; for i in $(seq 1 30); do docker exec rh2pg pg_isready -U autospare -q 2>/dev/null && sleep 1 && break; sleep 1; done; }
alem(){ t=$1; shift; docker run --rm --network $NET -v $t:/app:ro -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e DATABASE_PII_URL=$DB -w /app $IMG alembic -c alembic_pii.ini "$@" > $R/alem.log 2>&1; rc=$?; grep -E "Running|ERROR|FAILED|Error:|LockNotAvailable|canceling" $R/alem.log | sed 's/^/    /' | cut -c1-150 | tail -3; echo "    alembic $* -> exit=$rc"; return $rc; }
py(){ t=$1; f=$2; shift 2; docker run --rm --network $NET -v $t:/app:ro -v $f:/t.py:ro -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e DATABASE_PII_URL=$DB -e DATABASE_URL=$DB "$@" -w /app $IMG python3 /t.py 2>&1 | grep -E "OK|SEEDED|CANDIDATE|RACE|Error|error" | tail -2 | sed 's/^/    /'; }
q(){ docker exec -i rh2pg psql -U autospare -d autospare_pii -tAq -v ON_ERROR_STOP=1 "$@"; }
ver(){ echo "    version=$(q -c 'SELECT version_num FROM alembic_version' 2>&1) new_tables=$(q -c "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('agent_authorizations','supplier_outreach','procurement_orders','procurement_events')") new_cols=$(q -c "SELECT count(*) FROM information_schema.columns WHERE table_name='email_agent_messages' AND column_name IN ('source_folder','supplier_routed_at','supplier_route')")"; }
seed_email(){ q -c "INSERT INTO email_agent_messages (gmail_message_id, gmail_thread_id, status, sender_email) VALUES ('e1','thr-1','processed','sales@s.example'),('e2','t2','processed','x@y.example')"; }
fp(){ q -F' ' < $R/fingerprint.sql; }
docker network rm $NET >/dev/null 2>&1; docker network create --internal $NET >/dev/null

echo "=== R1 FAILURE DURING MIGRATION (0042 errors half-way)"; fresh; alem $OLD upgrade head >/dev/null; seed_email
rm -rf $R/broken && cp -r $CAND $R/broken && sed -i 's|    "CREATE INDEX IF NOT EXISTS ix_procurement_orders_order|    "SELECT 1/0",\n    "CREATE INDEX IF NOT EXISTS ix_procurement_orders_order|' $R/broken/alembic_pii/versions/0042_supplier_lifecycle.py
alem $R/broken upgrade head; ver
echo "  recovery = restore deployed code (no downgrade needed); deployed code on this schema:"; py $OLD $S/rehearsal/oldcode.py
echo "  candidate code meanwhile (if the container serves before recovery):"; py $CAND $S/rehearsal/gates.py -e STAGE=old
alem $OLD upgrade head >/dev/null; echo "    deployed 'upgrade head' (container start) -> exit=$?"; ver

echo "=== R2 MIGRATION OK, ACTIVATION/HEALTH FAILS -> FULL ROLLBACK WITH BACKUP"; fresh; alem $OLD upgrade head >/dev/null; seed_email
alem $CAND upgrade head; py $CAND $R/seed.py; fp > $R/fp_before.txt; sed 's/^/    before: /' $R/fp_before.txt
rm -rf $R/bk1; bash $B/ops/backup_0042_data.sh $R/bk1 | tail -2 | sed 's/^/    /'; echo "    backup exit=${PIPESTATUS[0]}"
echo "  overwrite guard:"; bash $B/ops/backup_0042_data.sh $R/bk1 | sed 's/^/    /'; echo "    exit=${PIPESTATUS[0]}"
bash $B/ops/verify_unchanged_0042.sh $R/bk1 | sed 's/^/    /'; echo "    verify exit=${PIPESTATUS[0]}"
alem $CAND downgrade 0041_email_agent_owner_flow; ver
echo "  deployed code after rollback:"; py $OLD $S/rehearsal/oldcode.py; alem $OLD upgrade head >/dev/null; echo "    deployed 'upgrade head' (container start) -> exit=$?"
echo "  later re-deploy + restore:"; alem $CAND upgrade head >/dev/null; bash $B/ops/restore_0042_data.sh $R/bk1 | sed 's/^/    /'; echo "    restore exit=${PIPESTATUS[0]}"
fp > $R/fp_after.txt; if diff -q $R/fp_before.txt $R/fp_after.txt >/dev/null; then echo "    CONTENT FINGERPRINT IDENTICAL (all 4 tables + email columns + sequence)"; else echo "    FINGERPRINT DIFFERS:"; diff $R/fp_before.txt $R/fp_after.txt | sed 's/^/    /'; fi
echo "  constraints still enforced after restore:"; q -c "UPDATE procurement_orders SET po_authorization_id=NULL WHERE state='PAYMENT_AUTHORIZED'" 2>&1 | grep -o 'violates check constraint "[a-z_]*"' | sed 's/^/    /'
q -c "INSERT INTO procurement_events (procurement_id, to_state, actor) VALUES (gen_random_uuid(), 'X', 't')" 2>&1 | grep -o 'violates foreign key constraint "[a-z_]*"' | sed 's/^/    /'
echo "    next event id continues: $(q -c "INSERT INTO procurement_events (procurement_id, to_state, actor) SELECT id,'PROBE','t' FROM procurement_orders LIMIT 1 RETURNING id") (max before restore: $(grep events_seq $R/fp_before.txt | cut -d' ' -f2))"
echo "  candidate code works on the restored data:"; py $CAND $S/rehearsal/gates.py -e STAGE=new

echo "=== R3 A WRITE LANDS AFTER THE BACKUP, BEFORE THE DOWNGRADE"; q -c "INSERT INTO procurement_orders (order_id,supplier_id,state,terms_hash,idempotency_key) VALUES ('late','s','AWAITING_PO_AUTHORIZATION','h','late-1')"
bash $B/ops/verify_unchanged_0042.sh $R/bk1 | sed 's/^/    /'; echo "    verify exit=${PIPESTATUS[0]}  -> STOP, take a new backup"
rm -rf $R/bk2; bash $B/ops/backup_0042_data.sh $R/bk2 | tail -1 | sed 's/^/    /'; bash $B/ops/verify_unchanged_0042.sh $R/bk2 | sed 's/^/    /'

echo "=== R4 DOWNGRADE BLOCKED (a live transaction holds a lock)"; ( q -c "BEGIN; SELECT count(*) FROM procurement_orders; SELECT pg_sleep(40); COMMIT;" >/dev/null 2>&1 & ); sleep 2
alem $CAND downgrade 0041_email_agent_owner_flow; ver; echo "    rows intact: $(q -c 'SELECT count(*) FROM procurement_orders')"
echo "  recovery A: retry when the lock is gone"; sleep 40; alem $CAND downgrade 0041_email_agent_owner_flow; ver
echo "  recovery B (downgrade impossible): deployed code + KEEP the 0042 migration file"; alem $CAND upgrade head >/dev/null; bash $B/ops/restore_0042_data.sh $R/bk2 | tail -1 | sed 's/^/    /'
rm -rf $R/oldplus && cp -r $OLD $R/oldplus && cp $CAND/alembic_pii/versions/0042_supplier_lifecycle.py $R/oldplus/alembic_pii/versions/
alem $R/oldplus upgrade head; ver; py $R/oldplus $S/rehearsal/oldcode.py
echo "    (without the file the deployed start fails:)"; alem $OLD upgrade head >/dev/null; echo "    exit=$?"

echo "=== R5 BACKUP RESTORE INCOMPLETE / CORRUPT"; alem $CAND downgrade 0041_email_agent_owner_flow >/dev/null; alem $CAND upgrade head >/dev/null
rm -rf $R/bkc && cp -r $R/bk2 $R/bkc && chmod -R u+w $R/bkc && head -n 2 $R/bk2/procurement_orders.csv > $R/bkc/procurement_orders.csv
bash $B/ops/restore_0042_data.sh $R/bkc | sed 's/^/    /'; echo "    (a) truncated file -> exit=${PIPESTATUS[0]}"
(cd $R/bkc && sha256sum *.csv *.sql MANIFEST.json > SHA256SUMS)
bash $B/ops/restore_0042_data.sh $R/bkc 2>&1 | grep -E "STOP|OK|FAILED|ERROR" | sed 's/^/    /'; echo "    (b) truncated file + re-forged checksums -> exit=${PIPESTATUS[0]}"
echo "    rows after failed restores: $(q -c 'SELECT (SELECT count(*) FROM procurement_orders)+(SELECT count(*) FROM agent_authorizations)')  (must be 0 = nothing half-restored)"
bash $B/ops/restore_0042_data.sh $R/bk2 | tail -1 | sed 's/^/    /'

echo "=== R6 CODE RESTORATION FAILS HALF-WAY"; rm -rf $R/code && mkdir -p $R/code/live && cp -r $CAND/. $R/code/live/
(cd $OLD && find . -type f -not -path '*/__pycache__/*' -print0 | sort -z | xargs -0 sha256sum) > $R/code/deployed.sha256
grep -c . $R/code/deployed.sha256 | sed 's/^/    deployed files hashed: /'
(cd $OLD && tar cf - agents/owner_console.py email_agent/store.py) | tar xf - -C $R/code/live   # restore stopped after 2 files
(cd $R/code/live && sha256sum -c --quiet $R/code/deployed.sha256 2>/dev/null | wc -l | sed 's/^/    files still NOT deployed version: /'); echo "    -> STOP: tree is mixed; do not start the container"
(cd $OLD && tar cf - .) | tar xf - -C $R/code/live; (cd $R/code/live && find . -type f -not -path '*/__pycache__/*' | sort) | comm -23 - <(cd $OLD && find . -type f -not -path '*/__pycache__/*' | sort) | while IFS= read -r f; do rm -f "$R/code/live/$f"; done
(cd $R/code/live && sha256sum -c --quiet $R/code/deployed.sha256 && echo "    re-run complete: tree == deployed ($(grep -c . $R/code/deployed.sha256) files)")
docker rm -f rh2pg >/dev/null; docker network rm $NET >/dev/null; rm -rf $R/broken $R/oldplus $R/code
