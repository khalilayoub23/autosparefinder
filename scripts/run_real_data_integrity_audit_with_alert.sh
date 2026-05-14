#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/real_data_audit"
STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
CURRENT_LOG="${LOG_DIR}/real_data_integrity_audit_${STAMP}.log"
LATEST_LINK="${LOG_DIR}/latest.log"
ALERT_LOG="${LOG_DIR}/alerts.log"

mkdir -p "${LOG_DIR}"

set +e
"${ROOT_DIR}/scripts/real_data_integrity_audit.sh" > "${CURRENT_LOG}" 2>&1
AUDIT_EXIT=$?
set -e

ln -sfn "${CURRENT_LOG}" "${LATEST_LINK}"

echo "audit_log=${CURRENT_LOG}"

if [[ "${AUDIT_EXIT}" -eq 0 ]]; then
  echo "status=pass"
  exit 0
fi

VIOLATION_COUNT="$(awk -F': ' '/^violation_count:/ {print $2}' "${CURRENT_LOG}" | tail -1)"
if [[ -z "${VIOLATION_COUNT}" ]]; then
  VIOLATION_COUNT="unknown"
fi

ALERT_MSG="$(date -u +"%Y-%m-%dT%H:%M:%SZ") [REAL_DATA_AUDIT] ALERT violation_count=${VIOLATION_COUNT} log=${CURRENT_LOG}"
echo "${ALERT_MSG}" | tee -a "${ALERT_LOG}"
logger -t autospare-real-data-audit "${ALERT_MSG}" || true

if [[ "${AUDIT_EXIT}" -eq 2 ]]; then
  exit 2
fi

exit "${AUDIT_EXIT}"
