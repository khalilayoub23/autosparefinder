#!/bin/bash
# Script: scripts/start_backend.sh
# Purpose: Deterministic backend start (FIXES_TRACKER #121). The API is started ONLY after both database migrations
#          have finished successfully. The old compose command ran the migrations in the background next to uvicorn
#          (`A && B && container_start.sh & uvicorn`): the API always served first, a failed migration was silent and
#          container_start.sh was skipped.
# Process:
#   1. catalog migration, then PII migration - each in the foreground, each bounded by MIGRATION_TIMEOUT_S, each
#      retried at most MIGRATION_RETRIES times MIGRATION_RETRY_DELAY_S apart ONLY when it failed (a lock timeout
#      usually clears; every migration here is transactional, so a failed attempt changes nothing).
#   2. success  -> container_start.sh in the background (waits for /api/health itself), then `exec uvicorn`, so
#                  uvicorn is PID 1 exactly as before (it receives SIGTERM directly and reaps children).
#   3. failure  -> uvicorn is NEVER started. A FATAL line names the failed step; /app/state/startup_status.json
#                  records it. STARTUP_ON_MIGRATION_FAILURE=hold (default) keeps the container alive but NOT serving
#                  (the healthcheck fails -> "unhealthy"), so `restart: unless-stopped` cannot turn a broken
#                  migration into a restart loop that retries the migration every few seconds; =exit exits with the
#                  migration's exit code instead (for an orchestrator that should restart / page).
#   Signals: SIGTERM/SIGINT during a migration is forwarded to alembic (its transaction rolls back), during the hold
#            it ends the container at once; after `exec` uvicorn handles them itself.
# Data Imported/Modified: none directly (alembic applies migrations); /app/state/startup_status.json.
# Last Updated: 2026-10-28
set -u
cd /app || exit 1

MIGRATION_TIMEOUT_S="${MIGRATION_TIMEOUT_S:-900}"
MIGRATION_RETRIES="${MIGRATION_RETRIES:-3}"
MIGRATION_RETRY_DELAY_S="${MIGRATION_RETRY_DELAY_S:-20}"
ON_FAILURE="${STARTUP_ON_MIGRATION_FAILURE:-hold}"
STATUS_FILE="${STARTUP_STATUS_FILE:-/app/state/startup_status.json}"
# extra arguments go to uvicorn unchanged (compose passes "--workers ${API_WORKERS:-1}", as the old command did)
if [ "$#" -gt 0 ]; then UVICORN_ARGS=("$@"); else UVICORN_ARGS=(--workers 1); fi
UVICORN_CMD=(uvicorn BACKEND_API_ROUTES:app --host 0.0.0.0 --port 8000 "${UVICORN_ARGS[@]}")

log() { echo "[start_backend] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2; }

status() {   # status <state> <step> <exit_code> <detail>
    mkdir -p "$(dirname "$STATUS_FILE")" 2>/dev/null
    printf '{"state":"%s","step":"%s","exit_code":%s,"detail":"%s","at":"%s","pid":%s}\n' \
        "$1" "$2" "$3" "$4" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" > "$STATUS_FILE.tmp" 2>/dev/null \
        && mv -f "$STATUS_FILE.tmp" "$STATUS_FILE" 2>/dev/null
}

CHILD=0
on_signal() {
    log "received $1 during start-up (child pid ${CHILD})"
    if [ "$CHILD" -gt 0 ] && kill -0 "$CHILD" 2>/dev/null; then
        kill -TERM "$CHILD" 2>/dev/null
        wait "$CHILD" 2>/dev/null
    fi
    status "stopped" "signal" 143 "stopped by $1 before the API started"
    exit 143
}
trap 'on_signal SIGTERM' TERM
trap 'on_signal SIGINT' INT

case "$MIGRATION_TIMEOUT_S$MIGRATION_RETRIES$MIGRATION_RETRY_DELAY_S" in
    *[!0-9]*) log "FATAL: MIGRATION_TIMEOUT_S / MIGRATION_RETRIES / MIGRATION_RETRY_DELAY_S must be integers"; exit 2 ;;
esac
[ "$MIGRATION_RETRIES" -ge 1 ] || MIGRATION_RETRIES=1

migrate() {   # migrate <label> <ini>  -> 0 on success, else the last exit code
    local label=$1 ini=$2 attempt=1 rc=0
    while :; do
        log "migration ${label}: attempt ${attempt}/${MIGRATION_RETRIES} (alembic -c ${ini} upgrade head, timeout ${MIGRATION_TIMEOUT_S}s)"
        timeout --signal=TERM --kill-after=30 "$MIGRATION_TIMEOUT_S" alembic -c "$ini" upgrade head &
        CHILD=$!
        wait "$CHILD"; rc=$?
        CHILD=0
        if [ "$rc" -eq 0 ]; then
            log "migration ${label}: OK"
            return 0
        fi
        [ "$rc" -eq 124 ] && log "migration ${label}: TIMED OUT after ${MIGRATION_TIMEOUT_S}s (killed; the transaction rolled back)" \
                          || log "migration ${label}: FAILED with exit ${rc}"
        if [ "$attempt" -ge "$MIGRATION_RETRIES" ]; then
            return "$rc"
        fi
        attempt=$((attempt + 1))
        sleep "$MIGRATION_RETRY_DELAY_S" & CHILD=$!; wait "$CHILD"; CHILD=0
    done
}

status "migrating" "start" 0 "running migrations before the API"
for step in "catalog:alembic.ini" "pii:alembic_pii.ini"; do
    label=${step%%:*}; ini=${step#*:}
    migrate "$label" "$ini"; rc=$?
    if [ "$rc" -ne 0 ]; then
        status "migration_failed" "$label" "$rc" "alembic -c ${ini} upgrade head failed; API NOT started"
        log "FATAL: ${label} migration failed (exit ${rc}) after ${MIGRATION_RETRIES} attempt(s) - the API was NOT started."
        log "FATAL: action: read the alembic error above (a lock timeout: find the blocker with pg_stat_activity);"
        log "FATAL: fix it, then 'docker restart autospare_backend'. Runbook: candidates/followups-119-122-2026-10-28/DEPLOY.md"
        if [ "$ON_FAILURE" = "exit" ]; then
            exit "$rc"
        fi
        log "holding without serving (STARTUP_ON_MIGRATION_FAILURE=hold): healthcheck fails, no restart loop"
        while :; do sleep 3600 & CHILD=$!; wait "$CHILD"; CHILD=0; done
    fi
done

status "starting_api" "migrations" 0 "migrations OK; starting uvicorn"
log "migrations complete - starting container_start.sh and the API"
/app/scripts/container_start.sh &
trap - TERM INT
exec "${UVICORN_CMD[@]}"
