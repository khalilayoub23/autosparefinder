#!/usr/bin/env bash
set -euo pipefail

BACKEND_CONTAINER="${1:-autospare_backend}"

echo "== Phase C1 Step 0 Release Gate =="
echo "timestamp_utc: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "backend_container: ${BACKEND_CONTAINER}"

echo

echo "[1/3] API health check"
docker exec "${BACKEND_CONTAINER}" sh -lc "python - <<'PY'
import time
import urllib.request

last_error = None
for _ in range(30):
    try:
        r = urllib.request.urlopen('http://localhost:8000/api/v1/system/health', timeout=5)
        print('health_status:', r.status)
        if r.status == 200:
            raise SystemExit(0)
        last_error = f'Unexpected status: {r.status}'
    except Exception as exc:
        last_error = str(exc)
        time.sleep(1)

print('health_check_error:', last_error)
raise SystemExit(1)
PY"

echo

echo "[2/3] Strict-fitment smoke tests"
docker exec "${BACKEND_CONTAINER}" sh -lc "cd /app && pytest -q tests/test_fitment_step0_release_gate.py"

echo

echo "[3/3] Existing fitment guardrails"
docker exec "${BACKEND_CONTAINER}" sh -lc "cd /app && pytest -q tests/test_fitment_pipeline_guardrails.py"

echo

echo "Gate result: PASS"
