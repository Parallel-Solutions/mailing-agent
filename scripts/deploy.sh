#!/usr/bin/env bash
# Production deploy for mailing-agent.
# Usage (on server, from repo root):
#   ./scripts/deploy.sh --pull
#   ./scripts/deploy.sh --pull --post-deploy-stats
#   ./scripts/deploy.sh --pull --skip-git-update
#   ./scripts/deploy.sh --no-build
#   ./scripts/deploy.sh --ref release/companies-campaign-wizard-2026-07-22
#   PUBLIC_BASE_URL=https://offer.parresh.ru ./scripts/deploy.sh --pull
#
# Preferred path (CI / normal ops): pull immutable image from GHCR.
#   MAILING_AGENT_IMAGE=ghcr.io/parallel-solutions/mailing-agent:<sha> ./scripts/deploy.sh --pull
#
# Emergency only (registry unavailable): rebuild on the server.
#   ./scripts/deploy.sh
# On Docker Hub rate-limit, rebuild fails hard — use --pull / GHCR instead.

set -euo pipefail

PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://offer.parresh.ru}"
MAILING_AGENT_IMAGE="${MAILING_AGENT_IMAGE:-ghcr.io/parallel-solutions/mailing-agent:latest}"
HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-300}"
APP_LOCAL_BASE_URL="${APP_LOCAL_BASE_URL:-http://127.0.0.1:9806}"
POST_DEPLOY_STATS=0
NO_BUILD=0
PULL_IMAGE=0
SKIP_GIT_UPDATE=0
GIT_REF=""

usage() {
  sed -n '2,15p' "$0"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --post-deploy-stats)
      POST_DEPLOY_STATS=1
      shift
      ;;
    --no-build)
      NO_BUILD=1
      shift
      ;;
    --pull)
      PULL_IMAGE=1
      NO_BUILD=1
      shift
      ;;
    --skip-git-update)
      SKIP_GIT_UPDATE=1
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

if (( SKIP_GIT_UPDATE )); then
  echo "=== Git update skipped (caller pinned the checkout) ==="
else
  echo "=== Git update ==="
  git fetch --all --prune
  if [[ -n "$GIT_REF" ]]; then
    git checkout "$GIT_REF"
  fi
  git pull --ff-only
fi

image_id() {
  docker image inspect "$1" --format '{{.Id}}' 2>/dev/null
}

verify_running_image() {
  local expected_id="$1"
  local svc image_id config_image
  for svc in app worker; do
    image_id="$(docker inspect "mailing-agent-${svc}-1" --format '{{.Image}}' 2>/dev/null || true)"
    config_image="$(docker inspect "mailing-agent-${svc}-1" --format '{{.Config.Image}}' 2>/dev/null || true)"
    if [[ -z "$image_id" ]]; then
      echo "ERROR: could not resolve image for service $svc (is the container up?)" >&2
      "${COMPOSE[@]}" ps
      exit 1
    fi
    # Compare short or full IDs
    if [[ "$expected_id" != "$image_id"* && "$image_id" != "$expected_id"* ]]; then
      echo "ERROR: $svc is not running expected image." >&2
      echo "  expected: $expected_id" >&2
      echo "  actual:   $image_id ($config_image)" >&2
      echo "  MAILING_AGENT_IMAGE=$MAILING_AGENT_IMAGE" >&2
      exit 1
    fi
    echo "$svc image OK: $image_id ($config_image)"
  done
}

prune_old_repo_images() {
  local keep_id="$1"
  echo "=== Prune unused mailing-agent images ==="
  # Remove dangling images first.
  docker image prune -f >/dev/null || true
  # Drop untagged / unused tags of this repository except the one we just deployed.
  # `docker images` of the repo; skip the keep_id and any still referenced by a container.
  local repo line img_id img_ref
  repo="${MAILING_AGENT_IMAGE%:*}"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    img_id="$(awk '{print $1}' <<<"$line")"
    img_ref="$(awk '{print $2}' <<<"$line")"
    if [[ -z "$img_id" ]]; then
      continue
    fi
    if [[ "$keep_id" == "$img_id"* || "$img_id" == "$keep_id"* ]]; then
      continue
    fi
    # Skip if any container (running or stopped) still references this image.
    if docker ps -a --filter "ancestor=$img_id" --format '{{.ID}}' | grep -q .; then
      echo "keep (in use): $img_ref ($img_id)"
      continue
    fi
    echo "removing unused: $img_ref ($img_id)"
    docker rmi "$img_id" >/dev/null 2>&1 || true
  done < <(docker images --format '{{.ID}} {{.Repository}}:{{.Tag}}' "$repo" 2>/dev/null || true)
}

echo "=== Build and restart app + worker ==="
EXPECTED_IMAGE_ID=""

# Ensure infra is running under the prod overlay (applies MinIO localhost binds
# when the overlay changes; does not force-recreate healthy postgres).
echo "=== Ensure prod infra ==="
MAILING_AGENT_IMAGE="$MAILING_AGENT_IMAGE" "${COMPOSE[@]}" up -d postgres minio redis gotenberg

if (( PULL_IMAGE )); then
  echo "Pulling image: $MAILING_AGENT_IMAGE"
  docker pull "$MAILING_AGENT_IMAGE"
  EXPECTED_IMAGE_ID="$(image_id "$MAILING_AGENT_IMAGE")"
  if [[ -z "$EXPECTED_IMAGE_ID" ]]; then
    echo "ERROR: pulled image has no local ID: $MAILING_AGENT_IMAGE" >&2
    exit 1
  fi
  echo "Pulled image ID: $EXPECTED_IMAGE_ID"
  export MAILING_AGENT_IMAGE
  MAILING_AGENT_IMAGE="$MAILING_AGENT_IMAGE" "${COMPOSE[@]}" up -d --no-build --force-recreate --pull never app worker
  verify_running_image "$EXPECTED_IMAGE_ID"
elif (( NO_BUILD )); then
  echo "Skipping image build (--no-build); restarting existing containers."
  "${COMPOSE[@]}" restart app worker
else
  echo "Emergency rebuild on server (prefer --pull from GHCR)."
  if ! MAILING_AGENT_IMAGE="$MAILING_AGENT_IMAGE" "${COMPOSE[@]}" up -d --build --pull never app worker; then
    echo "ERROR: rebuild failed (often Docker Hub rate limit on base images)." >&2
    echo "Use GHCR instead: MAILING_AGENT_IMAGE=ghcr.io/parallel-solutions/mailing-agent:<sha> ./scripts/deploy.sh --pull" >&2
    exit 1
  fi
  EXPECTED_IMAGE_ID="$(image_id "$MAILING_AGENT_IMAGE" || true)"
fi

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

echo "=== Health checks (local) ==="
wait_for_url "$APP_LOCAL_BASE_URL/health" "local health"
wait_for_url "$APP_LOCAL_BASE_URL/ready" "local ready"

echo "=== Health checks (public) ==="
wait_for_url "$PUBLIC_BASE_URL/health" "public health"
wait_for_url "$PUBLIC_BASE_URL/ready" "public ready"

if [[ -n "$EXPECTED_IMAGE_ID" ]]; then
  prune_old_repo_images "$EXPECTED_IMAGE_ID"
fi

echo "=== Production audit ==="
MAILING_AGENT_IMAGE="$MAILING_AGENT_IMAGE" \
  PUBLIC_BASE_URL="$PUBLIC_BASE_URL" \
  APP_LOCAL_BASE_URL="$APP_LOCAL_BASE_URL" \
  EXPECTED_IMAGE_ID="${EXPECTED_IMAGE_ID:-}" \
  ./scripts/prod-audit.sh

if (( POST_DEPLOY_STATS )); then
  echo "=== Post-deploy statistics recovery ==="
  ./scripts/post-deploy-stats.sh
fi

echo ""
echo "Deploy complete."
