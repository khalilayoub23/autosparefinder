#!/bin/bash
# ISOLATED REHEARSAL 3 - the EXACT production command form through an isolated compose project.
S=/tmp/claude-0/-opt-autosparefinder/2b46ba28-2c33-4d4f-b9b3-7c878aed1e70/scratchpad; R3=$S/rehearsal3; B=/opt/autosparefinder/candidates/supplier-lifecycle-2026-10-27
export PII_CONTAINER=rh3pg; DC="docker compose -p rh3 -f $R3/compose.yml"
crun(){ tree=$1; shift; APP_TREE=$tree $DC run -T --rm --no-deps --entrypoint "" "$@" > $R3/crun.log 2>&1; rc=$?; grep -E "Running|ERROR|FAILED|STARTUP_HOOK_RAN|\(head\)|^0041|^0042|LockNotAvailable|Can't" $R3/crun.log | sed 's/^/    /' | cut -c1-140 | tail -3; echo "    exit=$rc"; return $rc; }
q(){ docker exec -i rh3pg psql -U autospare -d autospare_pii -tAq -v ON_ERROR_STOP=1 "$@"; }
ver(){ echo "    version=$(q -c 'SELECT version_num FROM alembic_version' 2>&1) new_tables=$(q -c "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('agent_authorizations','supplier_outreach','procurement_orders','procurement_events')")"; }
fp(){ q -F' ' < $S/rehearsal2/fingerprint.sql; }
APP_TREE=$S/base3/backend $DC down -v --remove-orphans >/dev/null 2>&1
APP_TREE=$S/base3/backend $DC up -d pg >/dev/null 2>&1; for i in $(seq 1 30); do docker exec rh3pg pg_isready -U autospare -q 2>/dev/null && sleep 1 && break; sleep 1; done
NET=$(docker inspect rh3pg --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')

echo "=== X1 exact compose form: deploy chain + full rollback with backup + restore"
echo "  deployed tree, upgrade head:"; crun $S/base3/backend backend alembic -c alembic_pii.ini upgrade head; ver
q -c "INSERT INTO email_agent_messages (gmail_message_id, gmail_thread_id, status, sender_email) VALUES ('e1','thr-1','processed','sales@s.example')"
echo "  candidate tree, upgrade head:"; crun $S/cand3/backend backend alembic -c alembic_pii.ini upgrade head; ver
docker run --rm --network $NET -v $S/cand3/backend:/app:ro -v $S/rehearsal2/seed.py:/t.py:ro -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e RUNTIME_IDENTITY_DIR=/tmp/ri -e DATABASE_PII_URL=postgresql+asyncpg://autospare@pg:5432/autospare_pii -e DATABASE_URL=postgresql+asyncpg://autospare@pg:5432/autospare_pii -w /app autosparefinder-backend:latest python3 /t.py 2>&1 | grep -E "SEEDED|Error" | sed 's/^/    /'
fp > $R3/fp_before.txt; sed 's/^/    before: /' $R3/fp_before.txt
rm -rf $R3/bk; bash $B/ops/backup_0042_data.sh $R3/bk | tail -1 | sed 's/^/    /'; bash $B/ops/verify_unchanged_0042.sh $R3/bk | sed 's/^/    /'
echo "  candidate tree, downgrade (EXACT runbook form):"; crun $S/cand3/backend backend alembic -c alembic_pii.ini downgrade 0041_email_agent_owner_flow; ver
echo "  startup hook ran in any one-off? $(grep -c STARTUP_HOOK_RAN $R3/crun.log)"
echo "=== X2 repeated recovery execution"
echo "  downgrade again (already 0041):"; crun $S/cand3/backend backend alembic -c alembic_pii.ini downgrade 0041_email_agent_owner_flow; ver
echo "  deployed tree, current:"; crun $S/base3/backend backend alembic -c alembic_pii.ini current
echo "  re-deploy upgrade:"; crun $S/cand3/backend backend alembic -c alembic_pii.ini upgrade head >/dev/null; ver
bash $B/ops/restore_0042_data.sh $R3/bk | tail -1 | sed 's/^/    /'; echo "    restore #1 exit=${PIPESTATUS[0]}"
fp > $R3/fp_after.txt; diff -q $R3/fp_before.txt $R3/fp_after.txt >/dev/null && echo "    CONTENT FINGERPRINT IDENTICAL" || { echo "    DIFF:"; diff $R3/fp_before.txt $R3/fp_after.txt | sed 's/^/    /'; }
bash $B/ops/restore_0042_data.sh $R3/bk | tail -1 | sed 's/^/    /'; echo "    restore #2 exit=${PIPESTATUS[0]} (must refuse: tables not empty)"
fp | diff -q - $R3/fp_before.txt >/dev/null && echo "    data unchanged by the refused repeat"
bash $B/ops/backup_0042_data.sh $R3/bk | sed 's/^/    /'; echo "    backup into the same dir exit=${PIPESTATUS[0]} (must refuse)"
echo "=== X3 migration killed part-way (process dies while the upgrade waits on a lock)"
crun $S/cand3/backend backend alembic -c alembic_pii.ini downgrade 0041_email_agent_owner_flow >/dev/null; ver
( q -c "BEGIN; SELECT count(*) FROM email_agent_messages; SELECT pg_sleep(30); COMMIT;" >/dev/null 2>&1 & ); sleep 2
( APP_TREE=$S/cand3/backend $DC run -T --rm --no-deps --name rh3_mig --entrypoint "" backend alembic -c alembic_pii.ini upgrade head > $R3/killed.log 2>&1 & ); sleep 6
docker kill rh3_mig >/dev/null 2>&1 && echo "    killed the migration container mid-transaction"; sleep 3; ver
echo "    waiting migrations left in pg_stat_activity: $(q -c "SELECT count(*) FROM pg_stat_activity WHERE query ILIKE '%supplier_outreach%' OR query ILIKE '%agent_authorizations%'")"
sleep 25; echo "  after the lock clears, a fresh upgrade:"; crun $S/cand3/backend backend alembic -c alembic_pii.ini upgrade head; ver
APP_TREE=$S/base3/backend $DC down -v --remove-orphans >/dev/null 2>&1; echo "isolated project removed"
