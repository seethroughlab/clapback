#!/usr/bin/env bash
# Provision the instance `ADR-0003` chose. Run once, from a machine with AWS
# credentials that can use Lightsail.
#
# `ADR-0003` point 9 chose AWS partly because it "is fully driveable from the
# CLI, which keeps the deployment reproducible and scriptable rather than a
# sequence of console clicks nobody can repeat". This is that script; if it
# stops matching reality, that reason has lapsed.
#
# It creates the instance, gives it a static IP, and opens exactly two ports.
# It does not install anything — see RUNBOOK.md for what happens next.
set -euo pipefail

REGION="${REGION:-us-east-1}"          # where the backup bucket already is
NAME="${NAME:-clapback}"
BLUEPRINT="${BLUEPRINT:-ubuntu_24_04}"
ZONE="${ZONE:-${REGION}a}"

command -v aws >/dev/null || { echo "aws cli not found" >&2; exit 1; }
aws sts get-caller-identity >/dev/null || { echo "no usable AWS credentials" >&2; exit 1; }

# **The bundle is chosen by RAM, not by price rank.** `ADR-0003` point 3 makes
# RAM the binding constraint because HNSW wants its index in page cache, and
# point 11 already recorded that 2 GB is the accepted headroom cost. Printed for
# confirmation rather than assumed, because that record's own price table says
# "Figures below are approximate... Check before buying."
echo "Bundles with at least 2 GB, cheapest first:"
aws lightsail get-bundles --region "$REGION" \
	--query 'sort_by(bundles[?ramSizeInGb>=`2`&&supportedPlatforms[0]==`LINUX_UNIX`],&price)[:4].[bundleId,ramSizeInGb,cpuCount,diskSizeInGb,price]' \
	--output table

BUNDLE="${BUNDLE:-}"
if [ -z "$BUNDLE" ]; then
	echo
	echo "Set BUNDLE to one of the bundleIds above and re-run, e.g. BUNDLE=small_3_0 $0" >&2
	exit 1
fi

echo "Creating instance ${NAME} (${BUNDLE}) in ${ZONE}..."
aws lightsail create-instances --region "$REGION" \
	--instance-names "$NAME" \
	--availability-zone "$ZONE" \
	--blueprint-id "$BLUEPRINT" \
	--bundle-id "$BUNDLE" >/dev/null

echo "Waiting for it to run..."
until [ "$(aws lightsail get-instance-state --region "$REGION" --instance-name "$NAME" --query 'state.name' --output text)" = "running" ]; do
	sleep 5
done

# A static IP, because the DNS record points at it and a reboot must not move it.
aws lightsail allocate-static-ip --region "$REGION" --static-ip-name "${NAME}-ip" >/dev/null 2>&1 || true
aws lightsail attach-static-ip --region "$REGION" --static-ip-name "${NAME}-ip" --instance-name "$NAME" >/dev/null

# Exactly two ports plus SSH. `ADR-0005` point 12 keeps the database off the
# network entirely and the compose file publishes no port for it, so there is
# nothing here to open for Postgres — and opening 5432 would quietly undo that.
aws lightsail put-instance-public-ports --region "$REGION" --instance-name "$NAME" --port-infos \
	'fromPort=22,toPort=22,protocol=TCP' \
	'fromPort=80,toPort=80,protocol=TCP' \
	'fromPort=443,toPort=443,protocol=TCP' >/dev/null

IP=$(aws lightsail get-static-ip --region "$REGION" --static-ip-name "${NAME}-ip" --query 'staticIp.ipAddress' --output text)
cat <<OUT

Instance ${NAME} is running at ${IP}.

Next, and in this order:
  1. At Porkbun, add an A record:  clapback  ->  ${IP}
     Wait for it to resolve before step 4 — Caddy cannot obtain a certificate
     until the name points here, and Let's Encrypt rate-limits failures.
  2. ssh -i <key> ubuntu@${IP}
  3. Follow deploy/RUNBOOK.md
OUT
