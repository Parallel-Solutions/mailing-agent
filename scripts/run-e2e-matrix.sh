#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

required_vars=(
  RUN_REAL_E2E
  RUSENDER_API_KEY
  RUSENDER_SENDER_EMAIL
  RUSENDER_WEBHOOK_SECRET
  PUBLIC_BASE_URL
)

missing=()
for name in "${required_vars[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Missing required environment variables: ${missing[*]}" >&2
  echo "Copy .env.e2e.example to .env.docker and fill real RuSender credentials." >&2
  exit 1
fi

if [[ "${RUN_REAL_E2E}" != "1" ]]; then
  echo "Set RUN_REAL_E2E=1 to run the real send matrix." >&2
  exit 1
fi

echo "Clearing stale sender locks under tmp/storage/jobs ..."
find tmp/storage/jobs -path '*/state/.sender.run.lock' -delete 2>/dev/null || true

echo "Starting E2E send matrix inside app container ..."
docker compose exec \
  -e RUN_REAL_E2E=1 \
  -e E2E_BASE_URL="${E2E_BASE_URL:-http://localhost:9806}" \
  app .venv/bin/python -m tests.e2e.run_send_matrix
