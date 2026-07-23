#!/usr/bin/env bash
# Production deploy for mailing-agent.
# Usage (on server, from repo root):
#   ./scripts/deploy.sh
#   ./scripts/deploy.sh --post-deploy-stats
#   ./scripts/deploy.sh --ref release/companies-campaign-wizard-2026-07-22
#   PUBLIC_BASE_URL=https://offer.parresh.ru ./scripts/deploy.sh

set -euo pipefail

PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://offer.parresh.ru}"
HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-300}"
POST_DEPLOY_STATS=0
GIT_REF=""

usage() {
  sed -n '2,8p' "$0"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --post-deploy-stats)
      POST_DEPLOY_STATS=1
      shift
      ;;
    --ref)
      GIT_REF="${2:-}"
      [[ -n "$GIT_REF" ]] || usage
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $1" >&2
    exit 1
  fi
}

require_cmd docker
require_cmd git
require_cmd curl

if [[ ! -f .env.docker ]]; then
  echo "ERROR: .env.docker not found. Copy .env.docker.example and configure secrets first." >&2
  exit 1
fi

echo "=== Git update ==="
git fetch --all --prune
if [[ -n "$GIT_REF" ]]; then
  git checkout "$GIT_REF"
fi
git pull --ff-only

echo "=== Build and restart app + worker ==="
"${COMPOSE[@]}" up -d --build app worker

wait_for_url() {
  local url="$1"
  local label="$2"
  local deadline=$((SECONDS + HEALTH_TIMEOUT_SEC))
  until curl -sf "$url" >/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "ERROR: timed out waiting for $label at $url" >&2
      "${COMPOSE[@]}" ps
      exit 1
    fi
    sleep 5
  done
  echo "$label OK: $url"
}

echo "=== Health checks ==="
wait_for_url "$PUBLIC_BASE_URL/health" "health"
wait_for_url "$PUBLIC_BASE_URL/ready" "ready"

echo "=== Production audit ==="
PUBLIC_BASE_URL="$PUBLIC_BASE_URL" ./scripts/prod-audit.sh

if (( POST_DEPLOY_STATS )); then
  echo "=== Post-deploy statistics recovery ==="
  ./scripts/post-deploy-stats.sh
fi

echo ""
echo "Deploy complete."
