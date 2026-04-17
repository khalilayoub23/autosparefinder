#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/phase_c1_step5"
STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
OUT_FILE="${LOG_DIR}/preflight_${STAMP}.json"
LATEST_FILE="${LOG_DIR}/preflight_latest.json"
TMP_FILE="${OUT_FILE}.tmp"

EXTERNAL_PASS_BRANDS_RAW="${EXTERNAL_PASS_BRANDS:-}"
BRAND_DEFAULT="${EXTERNAL_PASS_BRANDS_RAW%%,*}"
BRAND="${1:-${BRAND_DEFAULT}}"
PART_NUMBER="${2:-1233014L00@}"
if [[ -z "${BRAND}" ]]; then
  BRAND="Renault"
fi

mkdir -p "${LOG_DIR}"
cd "${ROOT_DIR}"

docker compose exec -T \
  backend \
  python run_step5_preflight.py \
    --brand "${BRAND}" \
    --part-number "${PART_NUMBER}" \
  > "${TMP_FILE}"

mv "${TMP_FILE}" "${OUT_FILE}"
ln -sfn "${OUT_FILE}" "${LATEST_FILE}"

echo "step5_preflight_report=${OUT_FILE}"
grep -E '"status"|"blocked_reason"|"fitment_attempts_executable"|"fitment_attempts_skipped"' "${OUT_FILE}" || true
