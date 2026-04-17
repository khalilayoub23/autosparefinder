#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCHEDULE="${1:-15 2 * * *}"
CRON_CMD="cd ${ROOT_DIR} && ./scripts/run_manufacturer_fitment_audit_with_delta.sh >> ${ROOT_DIR}/logs/fitment_audit/cron.log 2>&1"
CRON_TAG="# autospare-manufacturer-fitment-audit"

mkdir -p "${ROOT_DIR}/logs/fitment_audit"

TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT

crontab -l 2>/dev/null | grep -v "${CRON_TAG}" > "${TMP_FILE}" || true
printf "%s %s %s\n" "${SCHEDULE}" "${CRON_CMD}" "${CRON_TAG}" >> "${TMP_FILE}"
crontab "${TMP_FILE}"

echo "Installed cron entry: ${SCHEDULE}"
echo "Command: ${CRON_CMD}"
