#!/usr/bin/env bash
# `ADR-0004` point 9's second bound: "Disk alerting before Postgres dies, not
# after. A full disk is an outage; 80% of one is a Tuesday afternoon."
#
# The row ceiling bounds how many embeddings the corpus accepts. It does not
# bound anything else on the volume — Docker images, logs, the backup dump that
# is written locally before it is uploaded, or a Postgres WAL that grows because
# something is holding a replication slot. So the ceiling is not this, and this
# is not the ceiling.
#
# Deliberately not a monitoring stack. `ADR-0003` chose one small box on the
# argument that resources — including attention — are what killed the project it
# is modelled on. A cron job that mails on a threshold is the version of this
# that still exists in a year.
#
# Install:
#   sudo cp deploy/clapback-disk-alert.{service,timer} /etc/systemd/system/
#   sudo systemctl enable --now clapback-disk-alert.timer
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

THRESHOLD="${DISK_ALERT_PERCENT:-80}"
STATE="${DISK_ALERT_STATE:-/tmp/clapback-disk-alert.state}"

# POSIX `df -P` rather than GNU `--output=`: the instance is Linux, but a script
# that cannot run on the machine it is written on cannot be tested on it either,
# and this one has three branches worth exercising before it matters.
USED=$(df -P / | tail -1 | awk '{print $5}' | tr -dc '0-9')
AVAIL=$(df -Ph / | tail -1 | awk '{print $4}')

if [ "$USED" -lt "$THRESHOLD" ]; then
	# Clear the latch, so recovering and filling again alerts a second time. An
	# alert that only ever fires once is one nobody trusts.
	rm -f "$STATE"
	exit 0
fi

# Latched: fire on the crossing, not every fifteen minutes for a week. An alert
# that repeats until someone silences it teaches people to silence it.
if [ -f "$STATE" ] && [ "$(cat "$STATE")" = "$USED" ]; then
	exit 0
fi
echo "$USED" > "$STATE"

MESSAGE="clapback: disk at ${USED}% (${AVAIL} free) on $(hostname), threshold ${THRESHOLD}%.
Postgres holds the corpus on this volume. What usually grows: docker images
(docker system prune), the local backup dump in /tmp, and journald.
  df -h /
  docker system df
  du -sh /var/lib/docker /tmp 2>/dev/null"

echo "$MESSAGE" >&2
logger -t clapback-disk-alert "disk at ${USED}% (${AVAIL} free), threshold ${THRESHOLD}%"

# Mail if the box can. A missing MTA is not a failure worth exiting non-zero for:
# the journal entry above is the durable record, and systemd surfaces a failed
# unit, which is one more thing to look at rather than one more thing that works.
if command -v mail >/dev/null && [ -n "${DISK_ALERT_EMAIL:-}" ]; then
	echo "$MESSAGE" | mail -s "clapback: disk ${USED}% on $(hostname)" "$DISK_ALERT_EMAIL" || true
fi
