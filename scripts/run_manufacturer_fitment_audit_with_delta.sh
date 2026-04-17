#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/fitment_audit"
STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
CURRENT_LOG="${LOG_DIR}/manufacturer_gap_audit_${STAMP}.log"
LATEST_LINK="${LOG_DIR}/latest.log"
DELTA_LOG="${LOG_DIR}/delta_${STAMP}.diff"

mkdir -p "${LOG_DIR}"

"${ROOT_DIR}/scripts/manufacturer_fitment_gap_audit.sh" > "${CURRENT_LOG}"

PREV_LOG=""
if [[ -f "${LATEST_LINK}" ]]; then
  PREV_LOG="$(readlink -f "${LATEST_LINK}" || true)"
fi

ln -sfn "${CURRENT_LOG}" "${LATEST_LINK}"

echo "audit_log=${CURRENT_LOG}"
if [[ -n "${PREV_LOG}" && -f "${PREV_LOG}" && "${PREV_LOG}" != "${CURRENT_LOG}" ]]; then
  PREV_NORM="$(mktemp)"
  CURR_NORM="$(mktemp)"
  trap 'rm -f "${PREV_NORM}" "${CURR_NORM}"' EXIT

  sed '/^generated_at:/d' "${PREV_LOG}" > "${PREV_NORM}"
  sed '/^generated_at:/d' "${CURRENT_LOG}" > "${CURR_NORM}"

  if diff -u "${PREV_NORM}" "${CURR_NORM}" > "${DELTA_LOG}"; then
    rm -f "${DELTA_LOG}"
    echo "delta=none"
  else
    echo "delta_log=${DELTA_LOG}"
  fi
else
  echo "delta=none (first run)"
fi
