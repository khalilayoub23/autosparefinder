#!/bin/bash
# Run BEFORE any docker restart / rebuild
# Captures running subprocess state and saves to persistent volume + host /tmp
# The container_start.sh reads from /app/state/worker_state.json on next boot

set -e
STATE_FILE="/tmp/restart_state.json"
echo "=== PRE-RESTART CAPTURE ===" >&2

# 1. Capture job_registry running jobs
JOBS=$(docker exec autospare_postgres_catalog psql -U autospare -d autospare -t -c "
SELECT json_agg(json_build_object('job_id', job_id, 'status', status, 'age_mins', EXTRACT(EPOCH FROM (NOW()-last_heartbeat_at))/60))
FROM job_registry WHERE status='running';" 2>/dev/null | tr -d '[:space:]')

# 2. Capture running MANUAL importer subprocesses (container_start.sh / post_restart.sh
#    relaunch whatever is recorded here after the restart).
#    Done by scripts/restart_workers.py INSIDE the container (FIXES_TRACKER #60): it reads
#    each process's real argv from /proc, so it records the EXACT arguments (the old
#    `ps aux | grep` text match kept only the script path and one process per script) and
#    it writes /app/state/worker_state.json itself.
#    NOT captured (#57): oem_parts_online_scraper, oempartsonline_importer and the
#    catalog_scraper CLI — brand-discovery work is owned by night_pipeline.Controller.
WORKERS_STATE=$(docker exec autospare_backend python3 /app/scripts/restart_workers.py capture 2>/dev/null || echo '{"workers": []}')
WORKERS_JSON=$(printf '%s' "$WORKERS_STATE" | python3 -c "import sys, json; print(json.dumps((json.load(sys.stdin) or {}).get('workers', [])))" 2>/dev/null || echo "[]")

# Write raw captures to temp files to avoid shell quoting issues with JSON
JOBS_TMP="/tmp/jobs_raw.json"
WORKERS_TMP="/tmp/workers_raw.json"
printf '%s' "${JOBS:-null}" > "$JOBS_TMP"
printf '%s' "${WORKERS_JSON:-[]}" > "$WORKERS_TMP"

# 3. Save combined state to host /tmp (for manual post_restart.sh)
python3 - << PYEOF
import json
from datetime import datetime, timezone

try:
    jobs_raw = open('/tmp/jobs_raw.json').read().strip()
    jobs = json.loads(jobs_raw) if jobs_raw and jobs_raw != 'null' else None
except Exception:
    jobs = None

try:
    workers_raw = open('/tmp/workers_raw.json').read().strip()
    workers = json.loads(workers_raw) if workers_raw else []
    if not isinstance(workers, list):
        workers = []
except Exception:
    workers = []

state = {
    'jobs': jobs,
    'workers': workers,
    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}

import sys
STATE_FILE = '/tmp/restart_state.json'
with open(STATE_FILE, 'w') as f:
    json.dump(state, f, indent=2)
print(json.dumps(state, indent=2))

print(f'[pre_restart] {len(workers)} worker(s) recorded with exact arguments in /app/state/worker_state.json')
PYEOF
echo "" >&2
echo "State saved to $STATE_FILE" >&2

# 4.6 Close out in-flight job_registry rows so a restart does not orphan them.
# run_all_tasks / run_brand_discovery run as asyncio tasks INSIDE uvicorn (not as
# the subprocesses SIGTERM'd below), so without this their 'running' rows survive
# the restart and get reaped 2h later as 'failed' → false "Worker failed" owner
# alert. Mark them 'superseded' (terminal, non-alerting). heartbeat can't revert
# it (its WHERE status='running' no longer matches). The app's shutdown handler
# does this too — belt & suspenders so the pre-restart layer is self-sufficient.
echo "Closing in-flight jobs (job_registry running → superseded)..." >&2
docker exec autospare_postgres_catalog psql -U autospare -d autospare -t -c "
UPDATE job_registry SET status='superseded', completed_at=NOW(),
    error_message='Superseded: pre-restart graceful shutdown'
WHERE status='running';" 2>/dev/null | tr -d '[:space:]' | { read n; echo "  marked superseded" >&2; } || true

# 5. Gracefully stop active importers (let them finish the current DB batch / part).
#    scripts/restart_workers.py matches processes by their real argv. The old loop ran
#    `pgrep -f <name>` inside a `bash -c` whose own command line contained every name, so
#    it matched and SIGTERM'd itself on the first name and signalled nothing else (#60).
echo "Sending SIGTERM to active importers..." >&2
docker exec autospare_backend python3 /app/scripts/restart_workers.py stop >&2 || \
    echo "[pre_restart] WARNING: stop step could not run (container may already be stopped)" >&2

echo "=== PRE-RESTART COMPLETE ===" >&2
