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

# 2. Capture ALL running Python worker subprocesses inside the container
PROCS_RAW=$(docker exec autospare_backend ps aux 2>/dev/null | \
  grep -E "freesbe_importer|category_backfill|oempartsonline_importer|car_parts_ie_import|oem_parts_online_scraper|ebay_brand_importer|kgm_ssangyong|saab_parts|gm_playwright|catalog_scraper|kick_run_all|run_todo" | \
  grep -v grep || true)

WORKERS_JSON=$(echo "$PROCS_RAW" | python3 -c "
import sys, json, re
workers = []
seen = set()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    parts = line.split(None, 10)
    if len(parts) < 11:
        continue
    full_cmd = parts[10] if len(parts) > 10 else ''

    # Extract the script path
    m = re.search(r'python3\s+(/\S+\.py)', full_cmd)
    if not m:
        continue
    script = m.group(1)
    if script in seen:
        continue
    seen.add(script)

    # Derive a friendly name from the script basename
    name = script.split('/')[-1].replace('.py', '')
    workers.append({'name': name, 'cmd': script, 'full_cmd': full_cmd})

print(json.dumps(workers))
" 2>/dev/null || echo "[]")

# Write raw captures to temp files to avoid shell quoting issues with JSON
JOBS_TMP="/tmp/jobs_raw.json"
WORKERS_TMP="/tmp/workers_raw.json"
printf '%s' "${JOBS:-null}" > "$JOBS_TMP"
printf '%s' "${WORKERS_JSON:-[]}" > "$WORKERS_TMP"

# 3. Save combined state to host /tmp (for manual post_restart.sh)
VOLUME_STATE_FILE="/tmp/worker_state_volume.json"
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

# 4. Save worker state to persistent volume file (will be docker cp'd in a moment)
vol_state = {'workers': workers, 'timestamp': state['timestamp']}
open('/tmp/worker_state_volume.json', 'w').write(json.dumps(vol_state, indent=2))
print(f'[pre_restart] Saved {len(workers)} workers to /tmp/worker_state_volume.json')
PYEOF
echo "" >&2
echo "State saved to $STATE_FILE" >&2

docker exec autospare_backend mkdir -p /app/state 2>/dev/null || true
docker cp "$VOLUME_STATE_FILE" autospare_backend:/app/state/worker_state.json 2>/dev/null && \
    echo "[pre_restart] Worker state copied to container volume" || \
    echo "[pre_restart] WARNING: could not copy to container (may already be stopped)"

# 5. Gracefully stop active importers (let them finish current DB batch)
echo "Sending SIGTERM to active importers..." >&2
docker exec autospare_backend bash -c "
for proc in freesbe_importer category_backfill oempartsonline_importer car_parts_ie_import oem_parts_online_scraper ebay_brand_importer; do
  pids=\$(pgrep -f \$proc 2>/dev/null)
  if [ -n \"\$pids\" ]; then
    echo \"  Stopping \$proc (PIDs: \$pids)\"
    kill -TERM \$pids 2>/dev/null || true
  fi
done
sleep 3
" 2>/dev/null || true

echo "=== PRE-RESTART COMPLETE ===" >&2
