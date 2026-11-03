#!/bin/bash
# Isolated startup-order repro. S1 success, S2 migration failure (lock held > lock_timeout), S3 recovery by restart.
set -u; cd "$(dirname "$0")"; export APP_TREE=$1; C="docker compose -p suo -f compose.yml"
PSQL="docker exec suopg psql -U autospare -tAq"
log(){ echo "[$(date -u +%H:%M:%S.%3N)] $*"; }
$C down -v >/dev/null 2>&1; rm -rf out; mkdir -p out; chmod 777 out
$C up -d pg >/dev/null 2>&1; for i in $(seq 30); do $PSQL -d autospare -c 'select 1' >/dev/null 2>&1 && break; sleep 1; done
$PSQL -d autospare -c 'CREATE DATABASE autospare_pii'
ONE="$C run -T --rm --no-deps --entrypoint '' backend"
eval $ONE alembic -c alembic.ini stamp head >/dev/null 2>&1; log "catalog stamp head exit=$?"
eval $ONE alembic -c alembic_pii.ini upgrade 0041_email_agent_owner_flow >/dev/null 2>&1; log "pii -> 0041 exit=$?"
rev(){ $PSQL -d autospare_pii -c 'select version_num from alembic_version'; }
log "S2 start: PII=$(rev); holding ACCESS SHARE lock on email_agent_messages for 40 s"
( $PSQL -d autospare_pii -c "BEGIN; LOCK TABLE email_agent_messages IN ACCESS SHARE MODE; SELECT pg_sleep(40); COMMIT;" >/dev/null 2>&1 ) &
sleep 2
T0=$(date +%s.%N); $C up -d --no-deps backend >/dev/null 2>&1; log "service started"
sleep 25
log "S2 after 25s: PII=$(rev) container=$(docker inspect -f '{{.State.Status}} restarts={{.RestartCount}}' suobackend)"
docker exec suobackend python3 -c "import urllib.request;print('HTTP',urllib.request.urlopen('http://localhost:8000/probe').status)"
log "S2 app_started: $(cat out/app_started 2>/dev/null) (T0=$T0)"; log "S2 served: $(cat out/served 2>/dev/null)"
log "S2 container_start ran: $(cat out/hook 2>/dev/null | wc -l)"
docker logs suobackend 2>&1 | grep -E 'canceling statement|lock timeout|Error|Running upgrade' | head -5 | sed 's/^/   S2 log: /'
wait; rm -f out/*
log "S3 recovery: lock released, docker restart"
docker restart suobackend >/dev/null; sleep 20
log "S3: PII=$(rev) container=$(docker inspect -f '{{.State.Status}} restarts={{.RestartCount}}' suobackend)"
docker exec suobackend python3 -c "import urllib.request;print('HTTP',urllib.request.urlopen('http://localhost:8000/probe').status)"
log "S3 app_started: $(cat out/app_started)"; log "S3 served: $(cat out/served)"; log "S3 container_start ran: $(cat out/hook | wc -l) at $(cut -d' ' -f1 out/hook)"
docker logs suobackend 2>&1 | grep -E 'Running upgrade' | sed 's/^/   S3 log: /'
log "S1-equivalent ordering check: app_started vs hook (hook runs only AFTER both migrations; app does not wait)"
$C down -v >/dev/null 2>&1; log done
