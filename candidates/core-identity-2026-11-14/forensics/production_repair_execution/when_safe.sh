#!/bin/bash
# usage: when_safe.sh DEADLINE_EPOCH -- command...   runs the command ONLY immediately after a fresh in-container SAFE preflight
DL=$1; shift; shift
while [ $(date +%s) -lt $DL ]; do
  out=$(docker exec autospare_backend python3 /app/scripts/restart_preflight.py 2>&1)
  if echo "$out" | grep -q "PREFLIGHT: SAFE"; then
    echo "PREFLIGHT SAFE at $(date -Is)"; echo "$out" | head -3
    exec "$@"
  fi
  sleep 4
done
echo "HOLD: deadline reached without SAFE at $(date -Is)"; echo "$out"; exit 9
