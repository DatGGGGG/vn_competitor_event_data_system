#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

COMPOSE_FILE="${REPO_ROOT}/deploy/docker/docker-compose.yml"
VM_ENV_FILE="${REPO_ROOT}/deploy/docker/vm.env"
PIPELINE_ENV_FILE="${REPO_ROOT}/deploy/docker/pipeline.env"

DB_PATH="${DB_PATH:-/app/data/warehouse.db}"
CONFIG_PATH="${CONFIG_PATH:-/app/examples/config.json}"
SOCIALDATA_SYNC_LOOKBACK_DAYS="${SOCIALDATA_SYNC_LOOKBACK_DAYS:-10}"
SENSORTOWER_SYNC_LOOKBACK_DAYS="${SENSORTOWER_SYNC_LOOKBACK_DAYS:-3}"
PIPELINE_RESTART_API="${PIPELINE_RESTART_API:-1}"
PIPELINE_UNIFIED_MONTHS="${PIPELINE_UNIFIED_MONTHS:-}"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  echo "[$(timestamp)] $*"
}

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    log "missing required file: ${path}"
    exit 1
  fi
}

compose() {
  docker compose --env-file "${VM_ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

run_job() {
  log "job_start: $*"
  compose --profile ops run --rm --no-deps job "$@"
  log "job_done: $*"
}

resolve_months() {
  if [[ -n "${PIPELINE_UNIFIED_MONTHS}" ]]; then
    IFS=',' read -r -a requested_months <<< "${PIPELINE_UNIFIED_MONTHS}"
    printf '%s\n' "${requested_months[@]}" | sed '/^[[:space:]]*$/d' | awk '{$1=$1; print}'
    return
  fi

  local current_month
  local previous_month
  current_month="$(date -u +%Y-%m)"
  previous_month="$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m)"
  if [[ "${previous_month}" == "${current_month}" ]]; then
    printf '%s\n' "${current_month}"
    return
  fi
  printf '%s\n%s\n' "${previous_month}" "${current_month}"
}

require_file "${VM_ENV_FILE}"
require_file "${PIPELINE_ENV_FILE}"

log "vm_pipeline_started repo_root=${REPO_ROOT}"
log "pipeline_config db_path=${DB_PATH} config_path=${CONFIG_PATH} socialdata_lookback_days=${SOCIALDATA_SYNC_LOOKBACK_DAYS} sensortower_lookback_days=${SENSORTOWER_SYNC_LOOKBACK_DAYS}"

run_job vn-event-dw sync-socialdata-posts \
  --db "${DB_PATH}" \
  --config "${CONFIG_PATH}" \
  --lookback-days "${SOCIALDATA_SYNC_LOOKBACK_DAYS}"

run_job vn-event-dw sync-sensortower-raw \
  --config "${CONFIG_PATH}" \
  --lookback-days "${SENSORTOWER_SYNC_LOOKBACK_DAYS}"

run_job vn-event-dw load-sensortower-raw \
  --db "${DB_PATH}"

while IFS= read -r month; do
  [[ -n "${month}" ]] || continue
  run_job vn-event-dw build-unified-events-llm \
    --db "${DB_PATH}" \
    --month "${month}"
done < <(resolve_months)

if [[ "${PIPELINE_RESTART_API}" == "1" ]]; then
  log "api_restart_started"
  compose restart api
  log "api_restart_completed"
fi

log "vm_pipeline_completed"
