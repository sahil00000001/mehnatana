#!/bin/sh
# Nightly backup: the Mehnatana database schema + the private media folder.
# Keeps 30 days locally; copy the folder off the server as well (see README_RUN.md).
#
# Run from the project folder (where .env and docker-compose.yml are), e.g. from cron:
#   30 2 * * *  cd /opt/mehnatana && ./scripts/backup.sh >> /var/log/mehnatana-backup.log 2>&1
set -eu

BACKUP_DIR=${BACKUP_DIR:-/var/backups/mehnatana}
KEEP_DAYS=${KEEP_DAYS:-30}
STAMP=$(date +%Y%m%d-%H%M)

value() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-; }
DATABASE_URL=$(value DATABASE_URL)
SCHEMA=$(value POSTGRES_SCHEMA)
SCHEMA=${SCHEMA:-mehnatana}

if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL is empty in .env — nothing to dump." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

# 1. Database: only our schema, so other tables in the Supabase project are not touched.
#    postgres:17 has a pg_dump new enough for Supabase's server.
docker run --rm postgres:17 pg_dump "$DATABASE_URL" --schema="$SCHEMA" --no-owner --format=custom \
  > "$BACKUP_DIR/db-$STAMP.dump"

# 2. Uploaded files and logos.
docker compose run --rm --no-deps -v "$BACKUP_DIR":/backup --entrypoint "" web \
  tar czf "/backup/media-$STAMP.tar.gz" -C /app media

# 3. Keep the last KEEP_DAYS days.
find "$BACKUP_DIR" -name 'db-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name 'media-*.tar.gz' -mtime +"$KEEP_DAYS" -delete

# 4. Off-server copy (uncomment and configure one), e.g. with rclone:
# rclone copy "$BACKUP_DIR" remote:mehnatana-backups

echo "Backup done: $BACKUP_DIR/db-$STAMP.dump and media-$STAMP.tar.gz"
