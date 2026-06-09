#!/usr/bin/env bash
# Nightly job-hunter Postgres backup. Installed on the VPS host via cron:
#   0 3 * * * /opt/job-hunter/deploy/backup.sh >> /var/log/jobhunter-backup.log 2>&1
# Dumps are gzip-compressed SQL; 14 days retained. Restore with:
#   gunzip -c <file>.sql.gz | docker exec -i job-hunter-postgres-1 psql -U jobhunter job_hunter
set -euo pipefail

BACKUP_DIR=/root/backups/job-hunter
CONTAINER=job-hunter-postgres-1
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/job_hunter_${STAMP}.sql.gz"

docker exec "$CONTAINER" pg_dump -U jobhunter job_hunter | gzip > "$OUT"

# A dump that gzip can't read back is worse than no dump — fail loudly.
gunzip -t "$OUT"
echo "$(date -u '+%F %T') backup OK: $OUT ($(du -h "$OUT" | cut -f1))"

find "$BACKUP_DIR" -name 'job_hunter_*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
