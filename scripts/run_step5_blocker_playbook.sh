#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/phase_c1_step5"
STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
OUT_FILE="${LOG_DIR}/blocker_playbook_${STAMP}.json"
LATEST_FILE="${LOG_DIR}/blocker_playbook_latest.json"
TMP_FILE="${OUT_FILE}.tmp"

BRAND_LIMIT="${1:-${EXTERNAL_PASS_BRAND_LIMIT:-2}}"
PARTS_PER_BRAND="${2:-${EXTERNAL_PASS_PARTS_PER_BRAND:-3}}"
FORCED_BRANDS="${EXTERNAL_PASS_BRANDS:-}"

mkdir -p "${LOG_DIR}"

EXTRA_ENV_ARGS=()
_add_env_if_set() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "${value}" ]]; then
    EXTRA_ENV_ARGS+=( -e "${key}=${value}" )
  fi
}

_add_env_if_set EXTERNAL_FITMENT_PROVIDER_URLS
_add_env_if_set EXTERNAL_ENABLE_NHTSA
_add_env_if_set NHTSA_API_BASE
_add_env_if_set EXTERNAL_ENABLE_EBAY
_add_env_if_set EBAY_BROWSE_API_BASE
_add_env_if_set EBAY_MARKETPLACE_ID
_add_env_if_set EBAY_BEARER_TOKEN
_add_env_if_set EXTERNAL_ENABLE_ROCKAUTO
_add_env_if_set ROCKAUTO_CROSSREF_ENDPOINT_TEMPLATE
_add_env_if_set ROCKAUTO_ITEMS_PATH
_add_env_if_set EXTERNAL_ENABLE_OEM_EPC
_add_env_if_set OEM_EPC_ENDPOINT_TEMPLATES
_add_env_if_set OEM_EPC_ITEMS_PATH

cd "${ROOT_DIR}"
docker compose exec -T \
  -e EXTERNAL_PASS_BRAND_LIMIT="${BRAND_LIMIT}" \
  -e EXTERNAL_PASS_PARTS_PER_BRAND="${PARTS_PER_BRAND}" \
  -e EXTERNAL_PASS_BRANDS="${FORCED_BRANDS}" \
  "${EXTRA_ENV_ARGS[@]}" \
  backend \
  python run_step5_blocker_playbook.py \
    --brand-limit "${BRAND_LIMIT}" \
    --parts-per-brand "${PARTS_PER_BRAND}" \
    --brands "${FORCED_BRANDS}" \
  > "${TMP_FILE}"

mv "${TMP_FILE}" "${OUT_FILE}"

ln -sfn "${OUT_FILE}" "${LATEST_FILE}"

echo "blocker_playbook_report=${OUT_FILE}"
grep -E '"status"|"blocked_reason"|"all_status_codes"' "${OUT_FILE}" || true