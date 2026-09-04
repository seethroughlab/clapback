#!/usr/bin/env bash
# Nightly pg_dump to object storage — `ADR-0003` point 6.
#
# "Backups are a nightly pg_dump to object storage, and are part of shipping
# this. Not a follow-up. The corpus is contributed data that cannot be
# regenerated without the contributors' audio — nobody here can rebuild it."
#
# Install (on the instance, as the user that owns the compose project):
#   sudo cp deploy/clapback-backup.service deploy/clapback-backup.timer /etc/systemd/system/
#   sudo systemctl enable --now clapback-backup.timer
#   systemctl list-timers clapback-backup   # confirm the next run
#
# Restore is the other half and is the reason the dump is plain SQL rather than a
# custom-format archive: it needs no matching pg_restore version, only psql.
#   gunzip -c clapback-YYYY-MM-DD.sql.gz | docker compose -f docker-compose.aws.yml exec -T postgres psql -U cache cache
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET is unset — see deploy/env.example}"
PREFIX="${BACKUP_S3_PREFIX:-clapback/postgres}"
STAMP="$(date -u +%Y-%m-%d)"
NAME="clapback-${STAMP}.sql.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --clean --if-exists so a restore over a live database replaces it rather than
# colliding. Piped straight to gzip: a few gigabytes compressed costs cents to
# store, and the instance has 60 GB it should not be filling with dumps.
docker compose -f docker-compose.aws.yml exec -T postgres \
	pg_dump -U cache --clean --if-exists cache | gzip -9 > "$TMP/$NAME"

# An empty or truncated dump uploaded over a good one is worse than a failed
# backup, because it looks like success. 1 MB is far below any plausible dump of
# this corpus and far above an error message.
SIZE=$(wc -c < "$TMP/$NAME")
if [ "$SIZE" -lt 1000000 ]; then
	echo "refusing to upload: dump is ${SIZE} bytes, which is too small to be real" >&2
	exit 1
fi

aws s3 cp "$TMP/$NAME" "s3://${BACKUP_S3_BUCKET}/${PREFIX}/${NAME}"
echo "uploaded ${NAME} (${SIZE} bytes) to s3://${BACKUP_S3_BUCKET}/${PREFIX}/"
