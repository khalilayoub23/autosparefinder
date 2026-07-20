#!/bin/bash
# Runs inside the container after uvicorn has started (launched in background).
# Waits for the API to be healthy, then resumes any workers that were active before restart.
# Triggered automatically by the docker-compose command on every container start.

STATE_DIR="/app/state"
LOG_DIR="/app/state/logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"

echo "[container_start] Waiting for API to be healthy..." >&2
for i in $(seq 1 30); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" 2>/dev/null; then
        echo "[container_start] API ready after ${i}x2s" >&2
        break
    fi
    sleep 2
done

# Clear any stale Redis lock left over from before the crash
python3 - << 'PYEOF'
import asyncio, sys, os
sys.path.insert(0, '/app')
async def clear_lock():
    try:
        from BACKEND_AUTH_SECURITY import get_redis
        redis = await get_redis()
        if redis:
            key = 'autospare:lock:db_update_agent'
            deleted = await redis.delete(key)
            if deleted:
                print(f'[container_start] Cleared stale Redis lock: {key}')
            await redis.aclose()
    except Exception as e:
        print(f'[container_start] Redis lock clear failed (non-fatal): {e}')
asyncio.run(clear_lock())
PYEOF

# Mark any stale running job_registry entries as dead
python3 - << 'PYEOF'
import asyncio, asyncpg, os
DB = os.environ.get('DATABASE_URL', '').replace('postgresql+asyncpg://', 'postgresql://')
async def cleanup_jobs():
    try:
        conn = await asyncpg.connect(DB)
        result = await conn.execute("""
            UPDATE job_registry SET status='dead', completed_at=NOW(),
                error_message='Killed: container restarted before job finished'
            WHERE status='running'
        """)
        if 'UPDATE' in result and result != 'UPDATE 0':
            print(f'[container_start] Stale jobs killed: {result}')
        await conn.close()
    except Exception as e:
        print(f'[container_start] Job cleanup failed (non-fatal): {e}')
asyncio.run(cleanup_jobs())
PYEOF

# Resume workers based on state file
STATE_FILE="$STATE_DIR/worker_state.json"
if [ -f "$STATE_FILE" ]; then
    echo "[container_start] Found worker state file — resuming workers..." >&2
    python3 - << 'PYEOF'
import json, subprocess, os, time
from pathlib import Path

STATE_FILE = '/app/state/worker_state.json'
LOG_DIR = '/app/state/logs'

try:
    state = json.loads(Path(STATE_FILE).read_text())
except Exception as e:
    print(f'[container_start] Could not read state file: {e}')
    exit(0)

workers = state.get('workers', [])
print(f'[container_start] Resuming {len(workers)} worker(s)...')

for w in workers:
    cmd = w.get('cmd', '')
    name = w.get('name', 'unknown')
    if not cmd:
        continue
    log = f'{LOG_DIR}/{name}_{int(time.time())}.log'
    try:
        subprocess.Popen(
            ['bash', '-c', f'PYTHONUNBUFFERED=1 python3 {cmd} >> {log} 2>&1'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f'[container_start] Resumed: {name} -> {cmd}')
        time.sleep(2)  # stagger starts to avoid memory spike
    except Exception as e:
        print(f'[container_start] Failed to resume {name}: {e}')
PYEOF
else
    echo "[container_start] No worker state file — fresh start" >&2
fi

echo "[container_start] Startup complete" >&2
