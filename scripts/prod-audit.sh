#!/usr/bin/env bash
# Production health and disk audit for mailing-agent.
# Exit non-zero on hard failures so CI/deploy gates stay red.
# Usage (on server, from repo root):
#   ./scripts/prod-audit.sh
# Optional:
#   PUBLIC_BASE_URL=https://offer.parresh.ru DISK_WARN_PERCENT=85 ./scripts/prod-audit.sh
#   MAILING_AGENT_IMAGE=ghcr.io/parallel-solutions/mailing-agent:<sha> ./scripts/prod-audit.sh
#   EXPECTED_IMAGE_ID=sha256:... ./scripts/prod-audit.sh

set -euo pipefail

PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://offer.parresh.ru}"
APP_LOCAL_BASE_URL="${APP_LOCAL_BASE_URL:-http://127.0.0.1:9806}"
DISK_WARN_PERCENT="${DISK_WARN_PERCENT:-85}"
MAILING_AGENT_IMAGE="${MAILING_AGENT_IMAGE:-}"
ONLYOFFICE_IMAGE="${ONLYOFFICE_IMAGE:-ghcr.io/parallel-solutions/mailing-agent:onlyoffice-9.4.0.1}"
EXPECTED_IMAGE_ID="${EXPECTED_IMAGE_ID:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE=(
  docker compose
  --env-file .env.docker
  --profile onlyoffice
  -f docker-compose.yml
  -f docker-compose.prod.yml
)
AUDIT_FAILED=0

section() {
  echo ""
  echo "=== $1 ==="
}

fail() {
  echo "ERROR: $*" >&2
  AUDIT_FAILED=1
}

warn() {
  echo "WARNING: $*" >&2
}

get_head_revision() {
  python3 - <<'PY'
import re
from pathlib import Path

versions = Path("migrations/versions")
revisions = {}
for path in versions.glob("*.py"):
    text = path.read_text(encoding="utf-8")
    rev_match = re.search(r'^revision\s*=\s*"([^"]+)"', text, re.M)
    if not rev_match:
        continue
    rev = rev_match.group(1)
    down_match = re.search(r'^down_revision\s*=\s*(.+)$', text, re.M)
    down = None
    if down_match:
        raw = down_match.group(1).strip()
        if raw.startswith('"'):
            down = raw.strip('"')
    revisions[rev] = down

referenced = {down for down in revisions.values() if down}
for rev in revisions:
    if rev not in referenced:
        print(rev)
        raise SystemExit(0)
if revisions:
    print(sorted(revisions)[-1])
PY
}

ids_match() {
  local a="$1" b="$2"
  [[ -n "$a" && -n "$b" && ( "$a" == "$b"* || "$b" == "$a"* ) ]]
}

section "Disk"
if command -v df >/dev/null 2>&1; then
  df_line="$(df -h / | tail -n 1)"
  echo "$df_line"
  if [[ "$df_line" =~ ([0-9]+)% ]]; then
    used="${BASH_REMATCH[1]}"
    if (( used >= DISK_WARN_PERCENT )); then
      warn "Disk usage ${used}% exceeds warning threshold ${DISK_WARN_PERCENT}%."
    fi
  fi
else
  warn "df not available — run on Linux prod host."
fi

section "Docker disk"
docker system df

section "Compose services"
"${COMPOSE[@]}" ps

section "Deployed image summary"
for svc in app worker; do
  cname="mailing-agent-${svc}-1"
  if ! docker inspect "$cname" >/dev/null 2>&1; then
    fail "container $cname not found"
    continue
  fi
  img="$(docker inspect "$cname" --format '{{.Config.Image}}' 2>/dev/null || true)"
  img_id="$(docker inspect "$cname" --format '{{.Image}}' 2>/dev/null || true)"
  started="$(docker inspect "$cname" --format '{{.State.StartedAt}}' 2>/dev/null || true)"
  restart_count="$(docker inspect "$cname" --format '{{.RestartCount}}' 2>/dev/null || true)"
  status="$(docker inspect "$cname" --format '{{.State.Status}}' 2>/dev/null || true)"
  health="$(docker inspect "$cname" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || true)"
  echo "$svc status=$status health=$health image=$img id=$img_id started=$started RestartCount=${restart_count:-0}"

  if [[ "$status" != "running" ]]; then
    fail "$svc is not running (status=$status)"
  fi
  if [[ "$health" != "healthy" ]]; then
    fail "$svc is not healthy (health=$health)"
  fi
  if [[ -n "$MAILING_AGENT_IMAGE" && "$img" != "$MAILING_AGENT_IMAGE" ]]; then
    # Compose may store the tag without digest; also accept if Config.Image is the ID.
    if [[ "$img" != "$MAILING_AGENT_IMAGE"* ]]; then
      fail "$svc Config.Image='$img' does not match MAILING_AGENT_IMAGE='$MAILING_AGENT_IMAGE'"
    fi
  fi
  if [[ -n "$EXPECTED_IMAGE_ID" ]] && ! ids_match "$EXPECTED_IMAGE_ID" "$img_id"; then
    fail "$svc Image ID '$img_id' does not match EXPECTED_IMAGE_ID='$EXPECTED_IMAGE_ID'"
  fi
  if [[ -n "$restart_count" ]] && (( restart_count > 5 )); then
    fail "$svc RestartCount=$restart_count (>5) — investigate crash loop"
  fi
done

section "Health (local)"
if ! curl -sf "$APP_LOCAL_BASE_URL/health" >/dev/null; then
  fail "Local /health failed at $APP_LOCAL_BASE_URL/health"
else
  echo "local health OK: $APP_LOCAL_BASE_URL/health"
fi
if ! curl -sf "$APP_LOCAL_BASE_URL/ready" >/dev/null; then
  fail "Local /ready failed at $APP_LOCAL_BASE_URL/ready"
else
  echo "local ready OK: $APP_LOCAL_BASE_URL/ready"
fi

section "Health (public)"
if ! curl -sf "$PUBLIC_BASE_URL/health" >/dev/null; then
  fail "Public /health failed at $PUBLIC_BASE_URL/health"
else
  echo "public health OK: $PUBLIC_BASE_URL/health"
fi
if ! curl -sf "$PUBLIC_BASE_URL/ready" >/dev/null; then
  fail "Public /ready failed at $PUBLIC_BASE_URL/ready"
else
  echo "public ready OK: $PUBLIC_BASE_URL/ready"
fi

section "OnlyOffice"
onlyoffice_container="mailing-agent-onlyoffice-1"
onlyoffice_status="$(docker inspect "$onlyoffice_container" --format '{{.State.Status}}' 2>/dev/null || true)"
onlyoffice_health="$(docker inspect "$onlyoffice_container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || true)"
onlyoffice_image="$(docker inspect "$onlyoffice_container" --format '{{.Config.Image}}' 2>/dev/null || true)"
echo "onlyoffice status=${onlyoffice_status:-missing} health=${onlyoffice_health:-unknown} image=${onlyoffice_image:-unknown}"
if [[ "$onlyoffice_status" != "running" ]]; then
  fail "OnlyOffice is not running (status=${onlyoffice_status:-missing})"
fi
if [[ "$onlyoffice_health" != "healthy" ]]; then
  fail "OnlyOffice is not healthy (health=${onlyoffice_health:-unknown})"
fi
if [[ "$onlyoffice_image" != "$ONLYOFFICE_IMAGE" ]]; then
  fail "OnlyOffice image '$onlyoffice_image' does not match '$ONLYOFFICE_IMAGE'"
fi
if ! "${COMPOSE[@]}" exec -T onlyoffice curl -fsS http://127.0.0.1/healthcheck >/dev/null; then
  fail "OnlyOffice internal healthcheck failed"
else
  echo "onlyoffice internal health OK"
fi
if ! curl -fsS "$PUBLIC_BASE_URL/onlyoffice/healthcheck" >/dev/null; then
  fail "OnlyOffice public healthcheck failed"
else
  echo "onlyoffice public health OK: $PUBLIC_BASE_URL/onlyoffice/healthcheck"
fi
if ! curl -fsS "$PUBLIC_BASE_URL/onlyoffice/web-apps/apps/api/documents/api.js" >/dev/null; then
  fail "OnlyOffice public API script is unavailable"
else
  echo "onlyoffice public API OK"
fi
onlyoffice_ports="$(docker port "$onlyoffice_container" 80/tcp 2>/dev/null || true)"
if [[ -n "$onlyoffice_ports" ]]; then
  fail "OnlyOffice port 80 must not be published directly on production: $onlyoffice_ports"
else
  echo "onlyoffice has no direct host port (Caddy HTTPS only)"
fi

onlyoffice_public_url="$("${COMPOSE[@]}" exec -T app printenv ONLYOFFICE_EDITOR_PUBLIC_URL 2>/dev/null || true)"
echo "app ONLYOFFICE_EDITOR_PUBLIC_URL=$onlyoffice_public_url"
if [[ "$onlyoffice_public_url" != "$PUBLIC_BASE_URL/onlyoffice" ]]; then
  fail "ONLYOFFICE_EDITOR_PUBLIC_URL='$onlyoffice_public_url' (expected $PUBLIC_BASE_URL/onlyoffice)"
fi

app_secret_length="$("${COMPOSE[@]}" exec -T app sh -lc 'printf "%s" "${#ONLYOFFICE_JWT_SECRET}"' 2>/dev/null || true)"
app_secret_hash="$("${COMPOSE[@]}" exec -T app sh -lc 'test -n "$ONLYOFFICE_JWT_SECRET" && printf "%s" "$ONLYOFFICE_JWT_SECRET" | sha256sum | cut -d " " -f 1' 2>/dev/null || true)"
onlyoffice_secret_hash="$("${COMPOSE[@]}" exec -T onlyoffice sh -lc 'test -n "$JWT_SECRET" && printf "%s" "$JWT_SECRET" | sha256sum | cut -d " " -f 1' 2>/dev/null || true)"
if [[ ! "$app_secret_length" =~ ^[0-9]+$ ]] || (( app_secret_length < 32 )); then
  fail "ONLYOFFICE_JWT_SECRET must contain at least 32 characters"
elif [[ -z "$app_secret_hash" || "$app_secret_hash" != "$onlyoffice_secret_hash" ]]; then
  fail "App and OnlyOffice JWT secrets do not match"
else
  echo "onlyoffice JWT secret is configured consistently (length=$app_secret_length)"
fi

section "Alembic"
db_rev="$("${COMPOSE[@]}" exec -T postgres psql -U mailing -d mailing -t -A -c "SELECT version_num FROM alembic_version;" 2>/dev/null || true)"
head_rev="$(get_head_revision || true)"
echo "database alembic_version=${db_rev:-unknown}"
echo "repo head revision=${head_rev:-unknown}"
if [[ -z "$db_rev" || -z "$head_rev" ]]; then
  fail "Could not resolve Alembic versions (db='${db_rev:-}' head='${head_rev:-}')"
elif [[ "$db_rev" != "$head_rev" ]]; then
  fail "Alembic stamp differs from repo head (db=$db_rev head=$head_rev)"
fi

section "Data directories"
for path in ./tmp ./logs ./storage; do
  if [[ -d "$path" ]]; then
    size_mb="$(du -sm "$path" 2>/dev/null | awk '{print $1}')"
    echo "${path}: ${size_mb:-0} MB"
  fi
done

section "Docker volumes"
pg_ok=0
for vol in mailing-agent_pgdata mailing-agent_minio-data mailing-agent_redis-data mailing-agent_chroma-data \
  mailing-agent_onlyoffice-data mailing-agent_onlyoffice-lib mailing-agent_onlyoffice-logs mailing-agent_onlyoffice-db; do
  mountpoint="$(docker volume inspect "$vol" --format '{{.Mountpoint}}' 2>/dev/null || true)"
  if [[ -n "$mountpoint" ]]; then
    echo "$vol -> $mountpoint"
    if [[ "$vol" == "mailing-agent_pgdata" ]]; then
      pg_ok=1
    fi
  fi
done
if (( pg_ok == 0 )); then
  fail "Expected volume mailing-agent_pgdata not found"
fi
# Detect postgres mounted on a test volume (wrong stack after unit tests).
pg_mounts="$(docker inspect mailing-agent-postgres-1 --format '{{range .Mounts}}{{.Name}} {{end}}' 2>/dev/null || true)"
echo "postgres mounts: ${pg_mounts:-unknown}"
if [[ "$pg_mounts" == *pgdata-test* || "$pg_mounts" == *mailing-agent-test* ]]; then
  fail "Postgres is on a test volume ($pg_mounts) — restore prod stack (pgdata)"
fi
if [[ -n "$pg_mounts" && "$pg_mounts" != *mailing-agent_pgdata* ]]; then
  fail "Postgres is not using mailing-agent_pgdata (mounts: $pg_mounts)"
fi

section "PUBLIC_BASE_URL"
for svc in app worker; do
  val="$("${COMPOSE[@]}" exec -T "$svc" printenv PUBLIC_BASE_URL 2>/dev/null || true)"
  echo "$svc PUBLIC_BASE_URL=$val"
  if [[ -z "$val" ]]; then
    fail "$svc PUBLIC_BASE_URL is empty"
  elif [[ "$val" != "https://offer.parresh.ru" ]]; then
    fail "$svc PUBLIC_BASE_URL='$val' (expected https://offer.parresh.ru)"
  fi
done

section "Forbidden / optional containers (must be stopped on prod)"
# Hardcoded known offenders + any container from e2e/test compose projects.
forbidden_patterns=(
  'mailing-agent-gotenberg-2'
  'mailpit'
  'playwright'
  'mailing-agent-e2e'
  'mailing-agent-test'
)
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  name="$(awk '{print $1}' <<<"$line")"
  project="$(awk '{print $2}' <<<"$line")"
  for pat in "${forbidden_patterns[@]}"; do
    if [[ "$name" == *"$pat"* || "$project" == *"$pat"* ]]; then
      fail "Forbidden container running: name=$name project=$project"
    fi
  done
done < <(docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' 2>/dev/null || true)

# Also flag any compose services outside the prod allowlist in this project.
section "Prod allowlist check"
# Expected long-lived: app worker postgres minio redis gotenberg onlyoffice (minio-init is one-shot).
allow_services='^(app|worker|postgres|minio|minio-init|redis|gotenberg|onlyoffice)$'
while IFS= read -r svc; do
  [[ -z "$svc" ]] && continue
  if [[ ! "$svc" =~ $allow_services ]]; then
    state="$(docker inspect "mailing-agent-${svc}-1" --format '{{.State.Status}}' 2>/dev/null || true)"
    if [[ "$state" == "running" ]]; then
      fail "Unexpected compose service running on prod: $svc"
    fi
  fi
done < <("${COMPOSE[@]}" ps --services 2>/dev/null || true)

echo ""
if (( AUDIT_FAILED )); then
  echo "Audit FAILED." >&2
  exit 1
fi
echo "Audit complete (OK)."
