#!/usr/bin/env bash
set -euo pipefail
nohup docker exec -i -w /app autospare_backend python3 - <<'PY' > /opt/autosparefinder/logs/oem_lookup.log 2>&1 &
import asyncio, json
from BACKEND_DATABASE_MODELS import async_session_factory
from ai_catalog_builder import lookup_oem_spec

async def main():
    total = 0
    for i in range(600):  # 600 × 500 = 300K parts
        async with async_session_factory() as db:
            r = await lookup_oem_spec(db, limit=500)
            total += r["oem_found"]
            print(f"Round {i+1}: found={r['oem_found']} total={total} pending={r['scanned']}", flush=True)
            if r["scanned"] == 0:
                print("Done!", flush=True)
                break

asyncio.run(main())
PY
pid=$!
echo "$pid" > /opt/autosparefinder/logs/oem_lookup.pid
echo "Started docker exec PID: $pid"
