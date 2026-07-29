#!/usr/bin/env bash
# Restore a production backup into disposable Docker volumes and verify DB/S3 integrity.

set -Eeuo pipefail

MANIFEST_FILE="${1:-}"
[[ -n "$MANIFEST_FILE" ]] || {
  echo "Usage: $0 /path/to/backup-*.manifest" >&2
  exit 2
}
[[ -f "$MANIFEST_FILE" ]] || {
  echo "ERROR: manifest not found: $MANIFEST_FILE" >&2
  exit 1
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

manifest_value() {
  local key="$1"
  local value
  value="$(grep -F -m1 "$key=" "$MANIFEST_FILE" | cut -d= -f2-)"
  [[ -n "$value" ]] || fail "manifest key is missing: $key"
  printf '%s' "$value"
}

BACKUP_DIR="$(cd "$(dirname "$MANIFEST_FILE")" && pwd)"
DB_BACKUP_NAME="$(manifest_value database_backup)"
DB_SHA256="$(manifest_value database_sha256)"
DATABASE_REVISION="$(manifest_value alembic_revision)"
MINIO_BACKUP_NAME="$(manifest_value minio_backup)"
MINIO_SHA256="$(manifest_value minio_sha256)"
MINIO_IMAGE_ID="$(manifest_value minio_image_id)"

[[ "$(basename "$DB_BACKUP_NAME")" == "$DB_BACKUP_NAME" ]] \
  || fail "unsafe database backup name in manifest"
[[ "$(basename "$MINIO_BACKUP_NAME")" == "$MINIO_BACKUP_NAME" ]] \
  || fail "unsafe MinIO backup name in manifest"

DB_BACKUP_FILE="$BACKUP_DIR/$DB_BACKUP_NAME"
MINIO_BACKUP_FILE="$BACKUP_DIR/$MINIO_BACKUP_NAME"
[[ -f "$DB_BACKUP_FILE" ]] || fail "database backup not found: $DB_BACKUP_FILE"
[[ -f "$MINIO_BACKUP_FILE" ]] || fail "MinIO backup not found: $MINIO_BACKUP_FILE"
echo "$DB_SHA256 *$DB_BACKUP_FILE" | sha256sum --check --status \
  || fail "database backup checksum mismatch"
echo "$MINIO_SHA256 *$MINIO_BACKUP_FILE" | sha256sum --check --status \
  || fail "MinIO backup checksum mismatch"

stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
pg_container="mailing-restore-pg-$stamp"
minio_container="mailing-restore-minio-$stamp"
pg_volume="mailing-restore-pg-$stamp"
minio_volume="mailing-restore-minio-$stamp"

cleanup() {
  local status=$?
  docker rm -f "$pg_container" "$minio_container" >/dev/null 2>&1 || true
  docker volume rm "$pg_volume" "$minio_volume" >/dev/null 2>&1 || true
  return "$status"
}
trap cleanup EXIT

docker image inspect postgres:16 >/dev/null \
  || fail "postgres:16 image is unavailable"
docker image inspect "$MINIO_IMAGE_ID" >/dev/null \
  || fail "exact MinIO image $MINIO_IMAGE_ID is unavailable"
docker volume create "$pg_volume" >/dev/null
docker volume create "$minio_volume" >/dev/null

docker run -d --name "$pg_container" \
  -e POSTGRES_USER=mailing \
  -e POSTGRES_PASSWORD=mailing \
  -e POSTGRES_DB=postgres \
  -v "$pg_volume:/var/lib/postgresql/data" \
  postgres:16 >/dev/null

for _ in {1..60}; do
  if docker exec "$pg_container" pg_isready -U mailing -d postgres >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$pg_container" pg_isready -U mailing -d postgres >/dev/null \
  || fail "temporary PostgreSQL did not become ready"
docker exec "$pg_container" createdb -U mailing mailing_restore
docker exec -i "$pg_container" pg_restore \
  -U mailing -d mailing_restore --exit-on-error < "$DB_BACKUP_FILE"

restored_revision="$(docker exec "$pg_container" \
  psql -U mailing -d mailing_restore -t -A \
  -c 'SELECT version_num FROM alembic_version;' | tr -d '[:space:]')"
[[ "$restored_revision" == "$DATABASE_REVISION" ]] \
  || fail "restored revision mismatch: expected $DATABASE_REVISION, got $restored_revision"

row_counts="$(sed -n '/^row_counts_begin$/,/^row_counts_end$/p' "$MANIFEST_FILE" \
  | sed '1d;$d')"
while IFS=, read -r table_name expected_count; do
  [[ -n "$table_name" ]] || continue
  [[ "$table_name" =~ ^[a-z_]+$ ]] || fail "unsafe table name in manifest: $table_name"
  table_exists="$(docker exec "$pg_container" \
    psql -U mailing -d mailing_restore -t -A \
    -c "SELECT to_regclass('public.$table_name') IS NOT NULL;" | tr -d '[:space:]')"
  if [[ "$expected_count" == "missing" ]]; then
    [[ "$table_exists" == "f" ]] \
      || fail "table $table_name unexpectedly exists after restore"
    continue
  fi
  [[ "$table_exists" == "t" ]] || fail "table $table_name is missing after restore"
  actual_count="$(docker exec "$pg_container" \
    psql -U mailing -d mailing_restore -t -A \
    -c "SELECT count(*) FROM $table_name;" | tr -d '[:space:]')"
  [[ "$actual_count" == "$expected_count" ]] \
    || fail "$table_name row count mismatch: expected $expected_count, got $actual_count"
done <<< "$row_counts"

docker run --rm \
  -v "$minio_volume:/target" \
  -v "$BACKUP_DIR:/backup:ro" \
  postgres:16 \
  tar -C /target -xf "/backup/$MINIO_BACKUP_NAME"
docker run -d --name "$minio_container" \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  -v "$minio_volume:/data" \
  "$MINIO_IMAGE_ID" server /data >/dev/null

for _ in {1..60}; do
  if docker exec "$minio_container" mc ready local >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$minio_container" mc ready local >/dev/null \
  || fail "restored MinIO did not become ready"

echo "Backup restore verification passed: $MANIFEST_FILE"
echo "PostgreSQL revision: $restored_revision"
echo "MinIO image ID: $MINIO_IMAGE_ID"
