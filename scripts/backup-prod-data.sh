#!/usr/bin/env bash
# Create and verify PostgreSQL and MinIO backups before production migrations.
#
# Application writers must already be stopped. PostgreSQL uses pg_dump's
# consistent snapshot. MinIO is stopped briefly while its named volume is
# archived, so the object-store backup cannot contain partially written files.

set -Eeuo pipefail

BACKUP_DIR="${PROD_BACKUP_DIR:-/var/backups/mailing-agent}"
BACKUP_KEEP_COUNT="${PROD_BACKUP_KEEP_COUNT:-30}"
MINIO_BACKUP_KEEP_COUNT="${PROD_MINIO_BACKUP_KEEP_COUNT:-3}"
DATABASE_NAME="${PROD_DATABASE_NAME:-mailing}"
POSTGRES_CONTAINER="${PROD_POSTGRES_CONTAINER:-mailing-agent-postgres-1}"
MINIO_CONTAINER="${PROD_MINIO_CONTAINER:-mailing-agent-minio-1}"
POSTGRES_VOLUME="${PROD_POSTGRES_VOLUME:-mailing-agent_pgdata}"
MINIO_VOLUME="${PROD_MINIO_VOLUME:-mailing-agent_minio-data}"
BACKUP_HELPER_IMAGE="${PROD_BACKUP_HELPER_IMAGE:-postgres:16}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ "$DATABASE_NAME" == "mailing" ]] \
  || fail "production backup requires database 'mailing', got '$DATABASE_NAME'"
[[ "$BACKUP_KEEP_COUNT" =~ ^[0-9]+$ ]] && (( BACKUP_KEEP_COUNT >= 3 )) \
  || fail "PROD_BACKUP_KEEP_COUNT must be an integer >= 3"
[[ "$MINIO_BACKUP_KEEP_COUNT" =~ ^[0-9]+$ ]] && (( MINIO_BACKUP_KEEP_COUNT >= 2 )) \
  || fail "PROD_MINIO_BACKUP_KEEP_COUNT must be an integer >= 2"

container_volume_at() {
  local container="$1"
  local destination="$2"
  docker inspect "$container" --format \
    "{{range .Mounts}}{{if eq .Destination \"$destination\"}}{{.Name}}{{end}}{{end}}" \
    2>/dev/null
}

pg_volume="$(container_volume_at "$POSTGRES_CONTAINER" "/var/lib/postgresql/data")"
minio_volume="$(container_volume_at "$MINIO_CONTAINER" "/data")"
[[ "$pg_volume" == "$POSTGRES_VOLUME" ]] \
  || fail "Postgres volume mismatch: expected '$POSTGRES_VOLUME', got '${pg_volume:-missing}'"
[[ "$minio_volume" == "$MINIO_VOLUME" ]] \
  || fail "MinIO volume mismatch: expected '$MINIO_VOLUME', got '${minio_volume:-missing}'"
[[ "$pg_volume" != *test* && "$minio_volume" != *test* ]] \
  || fail "Refusing to back up test volumes as production"

for writer in mailing-agent-app-1 mailing-agent-worker-1; do
  writer_running="$(docker inspect "$writer" --format '{{.State.Running}}' 2>/dev/null || true)"
  [[ "$writer_running" != "true" ]] \
    || fail "$writer is still running; stop application writers before backup"
done

install -d -m 0700 "$BACKUP_DIR"
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
db_tmp_file="$BACKUP_DIR/.mailing-$stamp.dump.tmp"
db_backup_file="$BACKUP_DIR/mailing-$stamp.dump"
minio_tmp_file="$BACKUP_DIR/.minio-$stamp.tar.tmp"
minio_backup_file="$BACKUP_DIR/minio-$stamp.tar"
manifest_file="$BACKUP_DIR/backup-$stamp.manifest"
minio_was_running="$(docker inspect "$MINIO_CONTAINER" --format '{{.State.Running}}')"

cleanup() {
  local status=$?
  rm -f -- "$db_tmp_file" "$minio_tmp_file"
  if [[ "$minio_was_running" == "true" ]]; then
    docker start "$MINIO_CONTAINER" >/dev/null 2>&1 || true
  fi
  return "$status"
}
trap cleanup EXIT

docker exec "$POSTGRES_CONTAINER" \
  pg_dump -U mailing -d "$DATABASE_NAME" -Fc > "$db_tmp_file"
[[ -s "$db_tmp_file" ]] || fail "pg_dump produced an empty file"
docker exec -i "$POSTGRES_CONTAINER" pg_restore --list < "$db_tmp_file" >/dev/null

database_revision="$(docker exec "$POSTGRES_CONTAINER" \
  psql -U mailing -d "$DATABASE_NAME" -t -A \
  -c 'SELECT version_num FROM alembic_version;' | tr -d '[:space:]')"
row_counts="$(docker exec "$POSTGRES_CONTAINER" \
  psql -U mailing -d "$DATABASE_NAME" -t -A -F, -c \
  "SELECT 'users',count(*) FROM users
   UNION ALL SELECT 'campaigns',count(*) FROM campaigns
   UNION ALL SELECT 'campaign_recipients',count(*) FROM campaign_recipients
   UNION ALL SELECT 'mail_templates',count(*) FROM mail_templates
   UNION ALL SELECT 'template_versions',count(*) FROM template_versions
   UNION ALL SELECT 'smtp_mailboxes',count(*) FROM smtp_mailboxes;")"

minio_size_kb="$(docker run --rm \
  -v "$MINIO_VOLUME:/source:ro" \
  "$BACKUP_HELPER_IMAGE" \
  sh -c "du -sk /source | cut -f1")"
available_kb="$(df -Pk "$BACKUP_DIR" | awk 'NR == 2 {print $4}')"
[[ "$minio_size_kb" =~ ^[0-9]+$ && "$available_kb" =~ ^[0-9]+$ ]] \
  || fail "could not determine MinIO size or backup free space"
(( available_kb > minio_size_kb + 1048576 )) \
  || fail "not enough free space for MinIO snapshot plus 1 GiB safety margin"

if [[ "$minio_was_running" == "true" ]]; then
  docker stop --time 60 "$MINIO_CONTAINER" >/dev/null
fi
docker run --rm \
  -v "$MINIO_VOLUME:/source:ro" \
  -v "$BACKUP_DIR:/backup" \
  "$BACKUP_HELPER_IMAGE" \
  tar -C /source -cf "/backup/$(basename "$minio_tmp_file")" .
[[ -s "$minio_tmp_file" ]] || fail "MinIO archive is empty"
docker run --rm \
  -v "$BACKUP_DIR:/backup:ro" \
  "$BACKUP_HELPER_IMAGE" \
  tar -tf "/backup/$(basename "$minio_tmp_file")" >/dev/null
if [[ "$minio_was_running" == "true" ]]; then
  docker start "$MINIO_CONTAINER" >/dev/null
fi

mv "$db_tmp_file" "$db_backup_file"
mv "$minio_tmp_file" "$minio_backup_file"
db_checksum="$(sha256sum "$db_backup_file" | awk '{print $1}')"
minio_checksum="$(sha256sum "$minio_backup_file" | awk '{print $1}')"
{
  echo "created_at_utc=$stamp"
  echo "database=$DATABASE_NAME"
  echo "alembic_revision=$database_revision"
  echo "database_backup=$(basename "$db_backup_file")"
  echo "database_sha256=$db_checksum"
  echo "postgres_volume=$pg_volume"
  echo "minio_backup=$(basename "$minio_backup_file")"
  echo "minio_sha256=$minio_checksum"
  echo "minio_volume=$minio_volume"
  echo "minio_source_size_kb=$minio_size_kb"
  echo "row_counts_begin"
  echo "$row_counts"
  echo "row_counts_end"
} > "$manifest_file"
chmod 0600 "$db_backup_file" "$minio_backup_file" "$manifest_file"

mapfile -t existing_backups < <(
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'mailing-*.dump' \
    -printf '%T@ %p\n' | sort -nr | awk '{print $2}'
)
for (( index=BACKUP_KEEP_COUNT; index<${#existing_backups[@]}; index++ )); do
  old_backup="${existing_backups[$index]}"
  [[ "$old_backup" == "$BACKUP_DIR"/mailing-*.dump ]] \
    || fail "unsafe retention target: $old_backup"
  rm -f -- "$old_backup"
done

mapfile -t existing_minio_backups < <(
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'minio-*.tar' \
    -printf '%T@ %p\n' | sort -nr | awk '{print $2}'
)
for (( index=MINIO_BACKUP_KEEP_COUNT; index<${#existing_minio_backups[@]}; index++ )); do
  old_backup="${existing_minio_backups[$index]}"
  [[ "$old_backup" == "$BACKUP_DIR"/minio-*.tar ]] \
    || fail "unsafe MinIO retention target: $old_backup"
  rm -f -- "$old_backup"
done

echo "Production database backup verified: $db_backup_file"
echo "Database SHA256: $db_checksum"
echo "Production MinIO backup verified: $minio_backup_file"
echo "MinIO SHA256: $minio_checksum"
echo "Manifest: $manifest_file"
