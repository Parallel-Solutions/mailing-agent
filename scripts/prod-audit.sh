#!/usr/bin/env bash
# Production health and disk audit for mailing-agent.
# Usage (on server, from repo root):
#   ./scripts/prod-audit.sh
# Optional:
#   PUBLIC_BASE_URL=https://offer.parresh.ru DISK_WARN_PERCENT=85 ./scripts/prod-audit.sh

set -euo pipefail

PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://offer.parresh.ru}"
DISK_WARN_PERCENT="${DISK_WARN_PERCENT:-85}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

section() {
  echo ""
  echo "=== $1 ==="
}

get_head_revision() {
  python3 - <<'PY'
from pathlib import Path

versions = Path("migrations/versions")
revisions = {}
for path in versions.glob("*.py"):
    text = path.read_text(encoding="utf-8")
    rev = down = None
    for line in text.splitlines():
        if line.strip().startswith("revision = "):
            rev = line.split('"')[1]
        elif line.strip().startswith("down_revision = "):
            down = line.split('"')[1]
    if rev:
        revisions[rev] = down

referenced = {down for down in revisions.values() if down}
for rev in revisions:
    if rev not in referenced:
        print(rev)
        raise SystemExit
if revisions:
    print(sorted(revisions)[-1])
PY
}

section "Disk"
if command -v df >/dev/null 2>&1; then
  df_line="$(df -h / | tail -n 1)"
  echo "$df_line"
  if [[ "$df_line" =~ ([0-9]+)% ]]; then
    used="${BASH_REMATCH[1]}"
    if (( used >= DISK_WARN_PERCENT )); then
      echo "WARNING: Disk usage ${used}% exceeds warning threshold ${DISK_WARN_PERCENT}%." >&2
    fi
  fi
else
  echo "WARNING: df not available — run on Linux prod host." >&2
fi

section "Docker disk"
docker system df

section "Compose services"
"${COMPOSE[@]}" ps

section "Health"
curl -sf "$PUBLIC_BASE_URL/health" || echo "WARNING: Public /health failed." >&2
curl -sf "$PUBLIC_BASE_URL/ready" || echo "WARNING: Public /ready failed." >&2

section "App stability"
restart_count="$(docker inspect mailing-agent-app-1 --format '{{.RestartCount}}' 2>/dev/null || true)"
if [[ -n "$restart_count" ]]; then
  echo "app RestartCount=$restart_count"
  if (( restart_count > 5 )); then
    echo "WARNING: app has restarted more than 5 times — investigate crash loop." >&2
  fi
fi

section "Alembic"
db_rev="$("${COMPOSE[@]}" exec -T postgres psql -U mailing -d mailing -t -A -c "SELECT version_num FROM alembic_version;" 2>/dev/null || true)"
head_rev="$(get_head_revision || true)"
echo "database alembic_version=${db_rev:-unknown}"
echo "repo head revision=${head_rev:-unknown}"
if [[ -n "$db_rev" && -n "$head_rev" && "$db_rev" != "$head_rev" ]]; then
  echo "WARNING: Alembic stamp differs from repo head — check migration drift before deploy." >&2
fi

section "Data directories"
for path in ./tmp ./logs ./storage; do
  if [[ -d "$path" ]]; then
    size_mb="$(du -sm "$path" 2>/dev/null | awk '{print $1}')"
    echo "${path}: ${size_mb:-0} MB"
  fi
done

section "Docker volumes"
for vol in mailing-agent_pgdata mailing-agent_minio-data mailing-agent_redis-data mailing-agent_chroma-data; do
  mountpoint="$(docker volume inspect "$vol" --format '{{.Mountpoint}}' 2>/dev/null || true)"
  if [[ -n "$mountpoint" ]]; then
    echo "$vol -> $mountpoint"
    if [[ "$vol" == *test* ]]; then
      echo "WARNING: Postgres may be on a test volume ($vol)." >&2
    fi
  fi
done

section "PUBLIC_BASE_URL"
for svc in app worker; do
  val="$("${COMPOSE[@]}" exec -T "$svc" printenv PUBLIC_BASE_URL 2>/dev/null || true)"
  echo "$svc PUBLIC_BASE_URL=$val"
done

section "Optional profiles (should be stopped on prod unless needed)"
for name in mailing-agent-onlyoffice-1 mailing-agent-gotenberg-2-1; do
  state="$(docker inspect "$name" --format '{{.State.Status}}' 2>/dev/null || true)"
  if [[ "$state" == "running" ]]; then
    echo "WARNING: $name is running — consider stopping to save RAM/disk." >&2
  fi
done

echo ""
echo "Audit complete."
