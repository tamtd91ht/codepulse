#!/bin/sh
# Daily PostgreSQL backup (docs/specs/13): custom-format dump, kept for BACKUP_KEEP_DAYS.
# Restore: pg_restore --clean --if-exists -d "$DATABASE_URL" /backups/codepulse-<date>.dump
# Connects with the PG* variables, or with DATABASE_URL (SQLAlchemy form) when it is set.
set -eu
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DB_URL=$(printf '%s' "${DATABASE_URL:-}" | sed 's/+psycopg//')
while true; do
  file="/backups/codepulse-$(date -u +%Y%m%dT%H%M%SZ).dump"
  if pg_dump ${DB_URL:+"--dbname=$DB_URL"} --format=custom --file="$file.partial" && mv "$file.partial" "$file"; then
    echo "backup ok: $file"
  else
    echo "backup FAILED" >&2
    rm -f "$file.partial"
  fi
  find /backups -name 'codepulse-*.dump' -mtime +"$KEEP_DAYS" -delete
  sleep 86400
done
