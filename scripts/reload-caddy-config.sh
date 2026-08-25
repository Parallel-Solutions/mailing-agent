#!/usr/bin/env bash
# Install, validate and hot-reload the Caddy config used by production.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADDY_CONTAINER="${CADDY_CONTAINER:-mailing-agent-caddy}"
SOURCE_CONFIG="$REPO_ROOT/deploy/Caddyfile"
TARGET_CONFIG="$REPO_ROOT/Caddyfile"

if [[ ! -f "$SOURCE_CONFIG" ]]; then
  echo "ERROR: Caddy source config not found: $SOURCE_CONFIG" >&2
  exit 1
fi

if [[ "$(docker inspect "$CADDY_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)" != "running" ]]; then
  echo "ERROR: Caddy container is not running: $CADDY_CONTAINER" >&2
  exit 1
fi

mounted_config="$(docker inspect "$CADDY_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
if [[ "$mounted_config" != "$TARGET_CONFIG" ]]; then
  echo "ERROR: $CADDY_CONTAINER mounts '${mounted_config:-nothing}' at /etc/caddy/Caddyfile; expected '$TARGET_CONFIG'." >&2
  exit 1
fi

backup_config="$(mktemp "${TMPDIR:-/tmp}/mailing-agent-caddyfile.XXXXXX")"
target_existed=0
if [[ -f "$TARGET_CONFIG" ]]; then
  cp "$TARGET_CONFIG" "$backup_config"
  target_existed=1
fi

restore_previous_config() {
  if (( target_existed )); then
    cp "$backup_config" "$TARGET_CONFIG"
  else
    unlink "$TARGET_CONFIG" 2>/dev/null || true
  fi
}

cleanup() {
  unlink "$backup_config" 2>/dev/null || true
}
trap cleanup EXIT

install -m 0644 "$SOURCE_CONFIG" "$TARGET_CONFIG"
if ! docker exec "$CADDY_CONTAINER" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
  restore_previous_config
  echo "ERROR: new Caddy config is invalid; previous file restored." >&2
  exit 1
fi

if ! docker exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
  restore_previous_config
  docker exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
  echo "ERROR: Caddy reload failed; previous config restored and reload attempted." >&2
  exit 1
fi

echo "Caddy config reloaded; access log: /data/access/offer-access.json"
