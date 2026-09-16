#!/usr/bin/env bash
# Weekly public export of the corpus — `ADR-0013` points 3 to 6.
#
# This is not the backup. `backup.sh` dumps every table, `client_id` and IP
# addresses included, to a private bucket. This selects only what is public —
# hashes, vectors, pipeline identities, counts, claims — and writes it where
# anyone can fetch it, under CC0. The two must never share a bucket.
#
# What it produces, in one dated prefix:
#   embeddings-<pipeline slug>.csv.gz   one per pipeline_version; columns
#                                       fingerprint_hash, named, pipeline_version,
#                                       contributor_count, created, embedding
#                                       — hash first, vector last, which is the
#                                       shape scripts/build_map.py already reads
#   claims.csv.gz                       fingerprint_hash, recording_mbid, claim_count
#   manifest.json                       date, licence, per-file row counts
#
# Where it goes (`ADR-0013` point 6, as it could be built with PutObject only):
#   exports/YYYY-MM-DD/   every run; the bucket's lifecycle expires these after 35 days
#   monthly/YYYY-MM/      the first run of each month; never expires
#   latest/               overwritten each run; /export/latest.json redirects here
# The host holds no s3:DeleteObject, exactly as for the backup, so a compromised
# instance can add exports but cannot erase them. Retention is S3's job.
#
# Install (on the instance, as the user that owns the compose project):
#   sudo cp deploy/clapback-export.service deploy/clapback-export.timer /etc/systemd/system/
#   sudo systemctl enable --now clapback-export.timer
# After a takedown (`ADR-0013` point 6): sudo systemctl start clapback-export.service
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

: "${EXPORT_S3_BUCKET:?EXPORT_S3_BUCKET is unset — see deploy/env.example}"
PUBLIC_URL="${EXPORT_PUBLIC_URL:?EXPORT_PUBLIC_URL is unset — see deploy/env.example}"
PUBLIC_URL="${PUBLIC_URL%/}"
STAMP="$(date -u +%Y-%m-%d)"
MONTH="$(date -u +%Y-%m)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
# The manifest is also kept beside the compose project, mounted read-only into
# the application, so /export can show the date without an outbound request.
LOCAL_DIR="exports"
mkdir -p "$LOCAL_DIR"

psql() {
	# `< /dev/null`: this runs inside a `while read` loop over the pipeline
	# list, and `docker compose exec -T` would otherwise consume the rest of
	# that list as its own stdin — which exported exactly one pipeline, the
	# first time this was run against a database with two.
	docker compose -f docker-compose.aws.yml exec -T postgres psql -U cache -At -q cache "$@" < /dev/null
}

# **Nothing below may name client_id, ip_stats, banned_ips or submission_agreement.**
# tests/test_export_script.py reads this file and fails if one appears.
# `named` is whether any client has claimed a recording id for the row; the
# claims themselves are in claims.csv.gz, counted rather than attributed.

# One file per pipeline, so a downloader takes only the identity they can compare with.
PIPELINES="$(psql -c "SELECT DISTINCT pipeline_version FROM embeddings ORDER BY 1")"
[ -n "$PIPELINES" ] || { echo "no embeddings to export" >&2; exit 1; }

FILES_JSON=""
TOTAL=0
while IFS= read -r PIPELINE; do
	[ -n "$PIPELINE" ] || continue
	SLUG="$(printf '%s' "$PIPELINE" | tr -c 'A-Za-z0-9.\n' '_')"
	FILE="embeddings-${SLUG}.csv.gz"
	# The literal is passed as a dollar-quoted string so an identity containing a
	# quote cannot break out of it.
	psql -c "COPY (
	    SELECT e.fingerprint_hash,
	           EXISTS (SELECT 1 FROM recording_claims c
	                    WHERE c.fingerprint_hash = e.fingerprint_hash) AS named,
	           e.pipeline_version,
	           e.contributor_count,
	           e.created_at::date AS created,
	           e.embedding::text AS embedding
	    FROM embeddings e
	    WHERE e.pipeline_version = \$q\$${PIPELINE}\$q\$
	    ORDER BY e.fingerprint_hash
	) TO STDOUT WITH (FORMAT csv, HEADER)" | gzip -9 > "$OUT/$FILE"
	ROWS=$(( $(gunzip -c "$OUT/$FILE" | wc -l) - 1 ))
	TOTAL=$(( TOTAL + ROWS ))
	FILES_JSON="${FILES_JSON}${FILES_JSON:+,}
    {\"pipeline_version\": $(printf '%s' "$PIPELINE" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'), \"file\": \"$FILE\", \"rows\": $ROWS}"
done <<< "$PIPELINES"

# `claims.csv.gz` keeps the columns schema_version 1 promised — MusicBrainz
# recording claims only. `ADR-0019` point 6's second kind, the AcoustID track
# id, goes in its own file rather than a column that would change the first.
psql -c "COPY (
    SELECT fingerprint_hash, recording_id AS recording_mbid, COUNT(*) AS claim_count
    FROM recording_claims
    WHERE claim_type = 'musicbrainz_recording'
    GROUP BY fingerprint_hash, recording_id
    ORDER BY fingerprint_hash, recording_id
) TO STDOUT WITH (FORMAT csv, HEADER)" | gzip -9 > "$OUT/claims.csv.gz"
CLAIM_ROWS=$(( $(gunzip -c "$OUT/claims.csv.gz" | wc -l) - 1 ))

psql -c "COPY (
    SELECT fingerprint_hash, recording_id AS acoustid_track_id, COUNT(*) AS claim_count
    FROM recording_claims
    WHERE claim_type = 'acoustid_track'
    GROUP BY fingerprint_hash, recording_id
    ORDER BY fingerprint_hash, recording_id
) TO STDOUT WITH (FORMAT csv, HEADER)" | gzip -9 > "$OUT/acoustid_claims.csv.gz"
ACOUSTID_ROWS=$(( $(gunzip -c "$OUT/acoustid_claims.csv.gz" | wc -l) - 1 ))

cat > "$OUT/manifest.json" <<EOF
{
  "schema_version": 1,
  "generated": "$NOW",
  "licence": "CC0-1.0",
  "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
  "decision": "https://github.com/seethroughlab/clapback/blob/main/docs/decisions/ADR-0013-the-corpus-is-public-data-not-just-a-public-endpoint.md",
  "source": "https://clapback.seethroughlab.com",
  "url": "${PUBLIC_URL}/exports/${STAMP}/",
  "embeddings": [${FILES_JSON}
  ],
  "embeddings_total": $TOTAL,
  "claims": {"file": "claims.csv.gz", "rows": $CLAIM_ROWS},
  "acoustid_claims": {"file": "acoustid_claims.csv.gz", "rows": $ACOUSTID_ROWS}
}
EOF
python3 -m json.tool "$OUT/manifest.json" > /dev/null   # refuse to publish a manifest that does not parse

# An export with no rows over a good one is the failure mode that looks like success.
if [ "$TOTAL" -lt 1000 ]; then
	echo "refusing to upload: $TOTAL embedding rows is too few to be the corpus" >&2
	exit 1
fi

aws s3 cp --recursive --no-progress "$OUT" "s3://${EXPORT_S3_BUCKET}/exports/${STAMP}/"
# First export this month keeps a copy nothing expires.
if ! aws s3 ls "s3://${EXPORT_S3_BUCKET}/monthly/${MONTH}/" > /dev/null 2>&1; then
	aws s3 cp --recursive --no-progress "$OUT" "s3://${EXPORT_S3_BUCKET}/monthly/${MONTH}/"
fi
aws s3 cp --recursive --no-progress "$OUT" "s3://${EXPORT_S3_BUCKET}/latest/"
cp "$OUT/manifest.json" "$LOCAL_DIR/manifest.json"

echo "exported ${TOTAL} embeddings and ${CLAIM_ROWS} claims to s3://${EXPORT_S3_BUCKET}/exports/${STAMP}/"
