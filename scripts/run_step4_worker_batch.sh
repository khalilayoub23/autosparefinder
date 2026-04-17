#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL_RAW="${1:-batch_manual_$(date -u +"%Y%m%dT%H%M%SZ")}" 
LABEL="${LABEL_RAW// /_}"
LOG_DIR="${ROOT_DIR}/logs/phase_c1_step4_${LABEL}"

mkdir -p "${LOG_DIR}"

cd "${ROOT_DIR}"
./scripts/fitment_kpi_report.sh > "${LOG_DIR}/kpi_before.log"

docker compose exec -T backend python run_step4_worker_cycle.py --label "${LABEL}" > "${LOG_DIR}/worker_pass_report.json"

./scripts/fitment_kpi_report.sh > "${LOG_DIR}/kpi_after.log"
(diff -u "${LOG_DIR}/kpi_before.log" "${LOG_DIR}/kpi_after.log" > "${LOG_DIR}/kpi_delta.diff" || true)

echo "step4_batch_label=${LABEL}"
echo "worker_report=${LOG_DIR}/worker_pass_report.json"
grep -E '"task"|"status"|"updated_parts"|"inserted"' "${LOG_DIR}/worker_pass_report.json" || true
