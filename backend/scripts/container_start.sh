#!/bin/bash
# Runs inside the container after uvicorn has started (launched in background).
# Waits for the API to be healthy, then resumes any workers that were active before restart.
# Triggered automatically by the docker-compose command on every container start.

# Taken BEFORE uvicorn finishes starting (this script is launched in the background just ahead of it, see the
# compose command). Anything that "started" after this instant belongs to THIS container.
export CONTAINER_START_UTC="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

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

# Mark stale running job_registry entries as dead - ONLY rows that started before this container did.
# (2026-11-14: this used to be `WHERE status='running'`, which also killed a job the new container's night pipeline
# had started 3 seconds earlier -> "Killed: container restarted before job finished" + a false critical alert to the
# owner, while the job itself ran to completion.)
python3 - << 'PYEOF'
import asyncio, asyncpg, os
DB = os.environ.get('DATABASE_URL', '').replace('postgresql+asyncpg://', 'postgresql://')
async def cleanup_jobs():
    try:
        conn = await asyncpg.connect(DB)
        from datetime import datetime
        cstart = datetime.fromisoformat(os.environ['CONTAINER_START_UTC'])
        result = await conn.execute("""
            UPDATE job_registry SET status='dead', completed_at=NOW(),
                error_message='Killed: container restarted before job finished'
            WHERE status='running' AND started_at < $1
        """, cstart)
        if 'UPDATE' in result and result != 'UPDATE 0':
            print(f'[container_start] Stale jobs killed: {result}')
        await conn.close()
    except Exception as e:
        print(f'[container_start] Job cleanup failed (non-fatal): {e}')
asyncio.run(cleanup_jobs())
PYEOF

# Resume workers based on state file — scripts/restart_workers.py (FIXES_TRACKER #60).
# It replays each captured importer with its EXACT arguments, refuses (loudly) when the
# arguments or an input file are missing, never duplicates a running one, and consumes the
# state so post_restart.sh or a later start cannot launch it a second time.
if python3 /app/scripts/restart_workers.py resume >&2; then
    echo "[container_start] Resume step finished" >&2
else
    echo "[container_start] WARNING: at least one captured worker was NOT resumed (see lines above)" >&2
fi

echo "[container_start] Startup complete" >&2
