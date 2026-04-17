#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KEEP_RUNS="${STEP4_NIGHTLY_KEEP_RUNS:-14}"
STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
LABEL="batch_nightly_${STAMP}"

cd "${ROOT_DIR}"
./scripts/run_step4_worker_batch.sh "${LABEL}"

# Keep only latest N nightly folders.
mapfile -t NIGHTLY_DIRS < <(ls -1dt logs/phase_c1_step4_batch_nightly_* 2>/dev/null || true)
if [[ ${#NIGHTLY_DIRS[@]} -gt ${KEEP_RUNS} ]]; then
  for ((i=KEEP_RUNS; i<${#NIGHTLY_DIRS[@]}; i++)); do
    rm -rf "${NIGHTLY_DIRS[$i]}"
  done
fi

echo "nightly_batch_label=${LABEL}"
