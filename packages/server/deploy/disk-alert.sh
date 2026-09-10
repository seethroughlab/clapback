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

# Remember operator overrides before `.env` gets a chance to clobber them.
# Sourcing under `set -a` makes the file win over the environment, which is the
# wrong way round for a script whose whole value is in branches you cannot reach
# on a box that is at 14%. Testing it used to mean editing the deployed `.env`.
_pre_percent="${DISK_ALERT_PERCENT-}"
_pre_state="${DISK_ALERT_STATE-}"
_pre_ntfy="${DISK_ALERT_NTFY_URL-}"
_pre_email="${DISK_ALERT_EMAIL-}"

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

[ -n "$_pre_percent" ] && DISK_ALERT_PERCENT="$_pre_percent"
[ -n "$_pre_state" ] && DISK_ALERT_STATE="$_pre_state"
[ -n "$_pre_ntfy" ] && DISK_ALERT_NTFY_URL="$_pre_ntfy"
[ -n "$_pre_email" ] && DISK_ALERT_EMAIL="$_pre_email"

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
MESSAGE="clapback: disk at ${USED}% (${AVAIL} free) on $(hostname), threshold ${THRESHOLD}%.
Postgres holds the corpus on this volume. What usually grows: docker images
(docker system prune), the local backup dump in /tmp, and journald.
  df -h /
  docker system df
  du -sh /var/lib/docker /tmp 2>/dev/null"

echo "$MESSAGE" >&2
logger -t clapback-disk-alert "disk at ${USED}% (${AVAIL} free), threshold ${THRESHOLD}%"

# **Deliver before latching.** The latch records "this crossing was announced",
# so writing it before the send turns a failed delivery into an alert nobody
# receives and nothing retries — the precise failure this script exists to avoid,
# reproduced one layer up. Sending first means a network blip costs fifteen
# minutes rather than the outage.
ATTEMPTED=0
DELIVERED=0

if [ -n "${DISK_ALERT_NTFY_URL:-}" ]; then
	ATTEMPTED=1
	if curl -fsS --max-time 10 \
		-H "Title: clapback disk ${USED}%" \
		-H "Priority: high" \
		-H "Tags: warning,floppy_disk" \
		-d "$MESSAGE" "$DISK_ALERT_NTFY_URL" >/dev/null 2>&1; then
		DELIVERED=1
	else
		logger -t clapback-disk-alert "ntfy delivery failed for ${DISK_ALERT_NTFY_URL}"
	fi
fi

# Mail if the box can. A missing MTA is not a failure worth exiting non-zero for:
# the journal entry above is the durable record.
if command -v mail >/dev/null && [ -n "${DISK_ALERT_EMAIL:-}" ]; then
	ATTEMPTED=1
	if echo "$MESSAGE" | mail -s "clapback: disk ${USED}% on $(hostname)" "$DISK_ALERT_EMAIL"; then
		DELIVERED=1
	else
		logger -t clapback-disk-alert "mail delivery failed for ${DISK_ALERT_EMAIL}"
	fi
fi

# No channel configured is journal-only mode, which is a deliberate choice rather
# than a fault: latch and exit clean. A channel that was tried and failed is a
# fault, so leave the latch off and fail the unit — an undeliverable alert should
# be visible as a broken thing, not as silence.
if [ "$ATTEMPTED" = 1 ] && [ "$DELIVERED" = 0 ]; then
	echo "clapback: alert could not be delivered on any configured channel" >&2
	exit 1
fi

echo "$USED" > "$STATE"
