#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# 3:00 AM local server time by default.
SCHEDULE="${1:-0 3 * * *}"
CRON_TAG="# autospare-100-users-loadtest"
LOG_FILE="${ROOT_DIR}/logs/loadtest_100_users_cron.log"

mkdir -p "${ROOT_DIR}/logs/loadtests"

CRON_CMD="cd ${ROOT_DIR} && ./scripts/run_100_users_load_test.sh >> ${LOG_FILE} 2>&1"

TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT

crontab -l 2>/dev/null | grep -v "${CRON_TAG}" > "${TMP_FILE}" || true
printf "%s %s %s\n" "${SCHEDULE}" "${CRON_CMD}" "${CRON_TAG}" >> "${TMP_FILE}"
crontab "${TMP_FILE}"

echo "Installed cron entry: ${SCHEDULE}"
echo "Command: ${CRON_CMD}"
