#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${REPO_DIR:-}" ]]; then
  echo "REPO_DIR is required" >&2
  exit 1
fi

if [[ -z "${VENV_DIR:-}" ]]; then
  echo "VENV_DIR is required" >&2
  exit 1
fi

if [[ -z "${DB_PATH:-}" ]]; then
  echo "DB_PATH is required" >&2
  exit 1
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"

cd "${REPO_DIR}"
source "${VENV_DIR}/bin/activate"

exec python -m vn_event_dw.cli serve-api-ngrok \
  --db "${DB_PATH}" \
  --host "${HOST}" \
  --port "${PORT}"
