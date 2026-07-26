#!/usr/bin/env bash
# Post-deploy statistics recovery: backfill sent_mail_log, sync providers, verify counts.
# Usage (on server, from repo root):
#   ./scripts/post-deploy-stats.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
PY=("${COMPOSE[@]}" exec -T -e PYTHONPATH=/app -w /app app .venv/bin/python)

section() {
  echo ""
  echo "=== $1 ==="
}

section "Backfill dry-run"
"${PY[@]}" scripts/migrate/backfill_campaign_sent_mail_log.py --dry-run

section "Backfill apply"
"${PY[@]}" scripts/migrate/backfill_campaign_sent_mail_log.py

section "Provider sync"
"${PY[@]}" scripts/migrate/sync_provider_delivery_events.py || true

section "Import JSONL merge"
"${PY[@]}" scripts/migrate/import_sent_mail_stats.py --merge || true

section "Verify migration"
"${PY[@]}" scripts/migrate/verify_migration.py || true

section "Campaign table"
docker compose -f docker-compose.yml exec -T postgres psql -U mailing -d mailing -c \
  "SELECT c.id, c.job_id, c.name, c.sent_count, (SELECT count(*) FROM job_events je WHERE je.job_id = c.job_id AND je.stream = 'sent_mail_log') AS log_count FROM campaigns c WHERE c.sent_count > 0 ORDER BY c.sent_count DESC LIMIT 15;"

section "sent_mail_log total"
docker compose -f docker-compose.yml exec -T postgres psql -U mailing -d mailing -t -A -c \
  "SELECT count(*) FROM job_events WHERE stream='sent_mail_log';"

echo ""
echo "Post-deploy stats recovery complete."
