#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCHEDULE="${1:-*/5 * * * *}"
CRON_TAG="# autospare-ingress-keep-warm"
LOG_FILE="${ROOT_DIR}/logs/ingress_keep_warm_cron.log"

mkdir -p "${ROOT_DIR}/logs"

# Keep the run light: no hierarchy, capped requests, spacing+jitter.
CRON_CMD="cd ${ROOT_DIR} && INCLUDE_HIERARCHY=0 MAX_REQUESTS=12 MIN_INTERVAL_S=2.4 JITTER_MAX_MS=400 ./scripts/ingress_keep_warm.sh >> ${LOG_FILE} 2>&1"

TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT

crontab -l 2>/dev/null | grep -v "${CRON_TAG}" > "${TMP_FILE}" || true
printf "%s %s %s\n" "${SCHEDULE}" "${CRON_CMD}" "${CRON_TAG}" >> "${TMP_FILE}"
crontab "${TMP_FILE}"

echo "Installed cron entry: ${SCHEDULE}"
echo "Command: ${CRON_CMD}"
