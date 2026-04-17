#!/usr/bin/env bash
set -euo pipefail

# Guardrail: detect cold-search latency regressions for manual full-context fitment flow.
# Usage:
#   ./scripts/fitment_search_latency_guard.sh
# Optional env:
#   RESTART_BACKEND=1
#   COLD_MAX_S=2.5
#   WARM_MAX_S=0.20
#   VEHICLE_MANUFACTURER=Chevrolet
#   VEHICLE_MODEL=CAMARO
#   VEHICLE_SUBMODEL=6.2L
#   VEHICLE_YEAR=2021
#   CATEGORY=engine_performance
#   PER_TYPE=3

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

RESTART_BACKEND="${RESTART_BACKEND:-1}"
COLD_MAX_S="${COLD_MAX_S:-2.5}"
WARM_MAX_S="${WARM_MAX_S:-0.20}"

HOST_HEADER="${HOST_HEADER:-autosparefinder.co.il}"
FORWARDED_PROTO="${FORWARDED_PROTO:-https}"
SEARCH_URL="${SEARCH_URL:-http://127.0.0.1/api/v1/parts/search}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1/api/v1/system/health}"

VEHICLE_MANUFACTURER="${VEHICLE_MANUFACTURER:-Chevrolet}"
VEHICLE_MODEL="${VEHICLE_MODEL:-CAMARO}"
VEHICLE_SUBMODEL="${VEHICLE_SUBMODEL:-6.2L}"
VEHICLE_YEAR="${VEHICLE_YEAR:-2021}"
CATEGORY="${CATEGORY:-engine_performance}"
PER_TYPE="${PER_TYPE:-3}"

if [[ "$RESTART_BACKEND" == "1" ]]; then
  echo "[guard] restarting backend to force cold cache"
  docker compose restart backend >/dev/null
fi

echo "[guard] waiting for backend health"
for i in $(seq 1 45); do
  code=$(curl -s -o /tmp/fitment_guard_health.out -w "%{http_code}" \
    -H "Host: $HOST_HEADER" \
    -H "X-Forwarded-Proto: $FORWARDED_PROTO" \
    "$HEALTH_URL" || true)
  if [[ "$code" == "200" ]]; then
    break
  fi
  if [[ "$i" == "45" ]]; then
    echo "[guard] backend did not become healthy (code=$code)"
    cat /tmp/fitment_guard_health.out || true
    exit 1
  fi
  sleep 2
done

run_search() {
  local out_file="$1"
  curl -s -o "$out_file" -w "%{time_total}" -G \
    -H "Host: $HOST_HEADER" \
    -H "X-Forwarded-Proto: $FORWARDED_PROTO" \
    --data-urlencode "query=" \
    --data-urlencode "vehicle_manufacturer=$VEHICLE_MANUFACTURER" \
    --data-urlencode "vehicle_model=$VEHICLE_MODEL" \
    --data-urlencode "vehicle_submodel=$VEHICLE_SUBMODEL" \
    --data-urlencode "vehicle_year=$VEHICLE_YEAR" \
    --data-urlencode "category=$CATEGORY" \
    --data-urlencode "per_type=$PER_TYPE" \
    "$SEARCH_URL"
}

echo "[guard] measuring cold request"
cold_time="$(run_search /tmp/fitment_guard_cold.json)"
echo "[guard] measuring warm request"
warm_time="$(run_search /tmp/fitment_guard_warm.json)"

echo "[guard] cold=${cold_time}s warm=${warm_time}s"

python3 - <<PY
cold = float("$cold_time")
warm = float("$warm_time")
cold_max = float("$COLD_MAX_S")
warm_max = float("$WARM_MAX_S")

if cold > cold_max:
    raise SystemExit(f"[guard] FAIL: cold latency {cold:.3f}s exceeded max {cold_max:.3f}s")
if warm > warm_max:
    raise SystemExit(f"[guard] FAIL: warm latency {warm:.3f}s exceeded max {warm_max:.3f}s")
print(f"[guard] PASS: cold={cold:.3f}s (<= {cold_max:.3f}) warm={warm:.3f}s (<= {warm_max:.3f})")
PY
