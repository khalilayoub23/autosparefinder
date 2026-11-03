#!/bin/bash
# Isolated acceptance tests for #121 (deterministic start). Usage: run.sh <backend tree with scripts/start_backend.sh>
set -u; cd "$(dirname "$0")"; export APP_TREE=$1
C="docker compose -p s121 -f compose.yml"; PSQL="docker exec s121pg psql -U autospare -tAq"
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
ok(){ if eval "$2"; then echo "PASS $1"; else echo "FAIL $1   (condition: $2)"; FAILS=$((FAILS+1)); fi; }
FAILS=0
rev(){ $PSQL -d autospare_pii -c 'select version_num from alembic_version' 2>/dev/null; }
st(){ docker inspect -f '{{.State.Status}}' s121backend 2>/dev/null; }
health(){ docker inspect -f '{{.State.Health.Status}}' s121backend 2>/dev/null; }
restarts(){ docker inspect -f '{{.RestartCount}}' s121backend 2>/dev/null; }
pid1(){ docker exec s121backend cat /proc/1/cmdline 2>/dev/null | tr '\0' ' '; }
http(){ docker exec s121backend python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/health',timeout=2).status)" 2>/dev/null || echo none; }
lock(){ ( $PSQL -d autospare_pii -c "BEGIN; LOCK TABLE email_agent_messages IN ACCESS SHARE MODE; SELECT pg_sleep($1); COMMIT;" >/dev/null 2>&1 ) & }
reset(){
  $C down -v >/dev/null 2>&1; rm -rf out; mkdir -p out; chmod 777 out
  $C up -d pg >/dev/null 2>&1; for i in $(seq 30); do $PSQL -d autospare -c 'select 1' >/dev/null 2>&1 && break; sleep 1; done
  $PSQL -d autospare -c 'CREATE DATABASE autospare_pii'
  ONE="$C run -T --rm --no-deps --entrypoint '' backend"
  eval $ONE alembic -c alembic.ini stamp head >/dev/null 2>&1
  eval $ONE alembic -c alembic_pii.ini upgrade 0041_email_agent_owner_flow >/dev/null 2>&1
}
start(){ $C up -d --no-deps backend >/dev/null 2>&1; }

log "T1 successful migration completes BEFORE uvicorn starts"
reset; T0=$(date +%s); start
for i in $(seq 60); do [ "$(http)" = 200 ] && break; sleep 1; done
ok T1a '[ "$(rev)" = 0042_supplier_lifecycle ]'
ok T1b 'grep -q "rev=0042_supplier_lifecycle" out/app_started'          # the app's FIRST look already saw 0042
ok T1c '[ "$(http)" = 200 ]'
ok T1d '[ "$(wc -l < out/hook 2>/dev/null)" = 1 ]'                       # container_start.sh ran exactly once
ok T1e 'pid1 | grep -q "uvicorn BACKEND_API_ROUTES:app"'                 # uvicorn is PID 1 (exec), as in production
ok T1f 'docker logs s121backend 2>&1 | grep -c "Running upgrade 0041_email_agent_owner_flow -> 0042" | grep -qx 1'
sleep 8; ok T1g '[ "$(health)" = healthy ]'
log "T7 normal stop of a running API: uvicorn shuts down gracefully"
S0=$(date +%s); docker stop -t 20 s121backend >/dev/null; S1=$(date +%s)
ok T7a '[ "$(docker inspect -f {{.State.ExitCode}} s121backend)" = 0 ]'
ok T7b '[ $((S1-S0)) -lt 10 ]'
ok T7c 'docker logs s121backend 2>&1 | grep -q "Finished server process"'

log "T2 migration fails on every attempt (lock held > 3 x 15 s lock_timeout): uvicorn never starts, no restart loop"
reset; lock 120; sleep 2; start
sleep 70
ok T2a '[ "$(rev)" = 0041_email_agent_owner_flow ]'                      # the failed 0042 rolled back
ok T2b '[ ! -e out/app_started ]'                                        # the app module was never imported
ok T2c '[ "$(http)" = none ]'                                            # nothing listens on :8000
ok T2d '[ "$(health)" = unhealthy ]'
ok T2e '[ "$(st)" = running ] && [ "$(restarts)" = 0 ]'                  # unless-stopped kept, no restart loop
ok T2f '[ ! -e out/hook ]'                                               # container_start.sh not run
ok T2g 'grep -q "\"state\":\"migration_failed\",\"step\":\"pii\"" out/startup_status.json'
ok T2h 'docker logs s121backend 2>&1 | grep -q "FATAL: pii migration failed (exit 1) after 3 attempt"'
ok T2i '[ "$(docker logs s121backend 2>&1 | grep -c "migration pii: attempt")" = 3 ]'   # bounded retries, then stop
log "T6 docker stop while holding: exits promptly"
S0=$(date +%s); docker stop -t 20 s121backend >/dev/null; S1=$(date +%s)
ok T6a '[ $((S1-S0)) -lt 5 ] && [ "$(docker inspect -f {{.State.ExitCode}} s121backend)" = 143 ]'
log "T10 recovery: blocker gone, 'docker restart' -> migration applies, API starts"
wait; docker start s121backend >/dev/null; for i in $(seq 60); do [ "$(http)" = 200 ] && break; sleep 1; done
ok T10a '[ "$(rev)" = 0042_supplier_lifecycle ] && [ "$(http)" = 200 ] && grep -q "rev=0042" out/app_started'

log "T3 transient lock (released after 26 s): attempt 1 times out on the lock, attempt 2 succeeds"
reset; lock 26; sleep 2; start
for i in $(seq 90); do [ "$(http)" = 200 ] && break; sleep 1; done
ok T3a '[ "$(rev)" = 0042_supplier_lifecycle ] && grep -q "rev=0042" out/app_started'
ok T3b 'docker logs s121backend 2>&1 | grep -q "migration pii: FAILED with exit 1" && docker logs s121backend 2>&1 | grep -q "migration pii: attempt 2/3"'
ok T3c '[ "$(restarts)" = 0 ]'
wait

log "T4 migration hangs past MIGRATION_TIMEOUT_S=5 (lock wait 15 s): killed deterministically, API not started"
reset; lock 60; sleep 2; MTIMEOUT=5 RETRIES=1 start
sleep 15
ok T4a 'docker logs s121backend 2>&1 | grep -q "migration pii: TIMED OUT after 5s"'
ok T4b '[ "$(rev)" = 0041_email_agent_owner_flow ] && [ ! -e out/app_started ] && [ "$(http)" = none ]'
sleep 20; ok T4c '[ "$($PSQL -d autospare_pii -c "select count(*) from pg_stat_activity where query ilike '"'"'%source_folder%'"'"' and pid<>pg_backend_pid()")" = 0 ]'   # the orphaned server statement ended (lock_timeout)
docker stop -t 5 s121backend >/dev/null; wait

log "T5 docker stop DURING a migration: SIGTERM forwarded, exits promptly, database unchanged"
reset; lock 60; sleep 2; start; sleep 5
S0=$(date +%s); docker stop -t 20 s121backend >/dev/null; S1=$(date +%s)
ok T5a '[ $((S1-S0)) -lt 8 ] && [ "$(docker inspect -f {{.State.ExitCode}} s121backend)" = 143 ]'
ok T5b '[ "$(rev)" = 0041_email_agent_owner_flow ] && [ ! -e out/app_started ]'
ok T5c 'docker logs s121backend 2>&1 | grep -q "received SIGTERM during start-up"'
wait

log "T8 STARTUP_ON_MIGRATION_FAILURE=exit (restart: no): exits non-zero instead of holding"
reset; lock 60; sleep 2; ON_FAIL=exit RETRIES=1 RESTART=no start; sleep 25
ok T8a '[ "$(st)" = exited ] && [ "$(docker inspect -f {{.State.ExitCode}} s121backend)" = 1 ] && [ ! -e out/app_started ]'
wait

log "T9 runbook one-off form is unaffected (its command replaces the start script)"
reset; out9=$($C run -T --rm --no-deps --entrypoint '' backend alembic -c alembic_pii.ini current 2>/dev/null | tail -1)
ok T9a '[ "$out9" = "0041_email_agent_owner_flow" ] && [ ! -e out/startup_status.json ]'

$C down -v >/dev/null 2>&1
log "DONE: $FAILS failed"; exit $FAILS
