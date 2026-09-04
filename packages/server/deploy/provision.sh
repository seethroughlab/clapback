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

# **This needs credentials that can use Lightsail, which the S3-scoped ones
# cannot.** On the machine this was written on the default profile is `s3_user`,
# and every call below fails with AccessDenied under it — the failure is clear
# but arrives four commands in, after the bundle table has already printed. So
# the check happens first and names the profile it is using.
PROFILE="${PROFILE:-${AWS_PROFILE:-}}"
AWS=(aws)
[ -n "$PROFILE" ] && AWS+=(--profile "$PROFILE")

command -v aws >/dev/null || { echo "aws cli not found" >&2; exit 1; }

IDENTITY=$("${AWS[@]}" sts get-caller-identity --query 'Arn' --output text 2>/dev/null) || {
	echo "no usable AWS credentials${PROFILE:+ for profile '$PROFILE'}" >&2
	exit 1
}
echo "Using ${IDENTITY}"

# Probed rather than assumed, because the useful error is this one and not the
# one four steps later. `get-regions` is the cheapest Lightsail read there is.
if ! "${AWS[@]}" lightsail get-regions --region "$REGION" >/dev/null 2>&1; then
	cat >&2 <<MSG
${IDENTITY} cannot use Lightsail.

Set PROFILE to one that can, e.g.

    PROFILE=admin BUNDLE=<id> $0

Listing the bundles below needs the same permission, so nothing further will work
until this is right.
MSG
	exit 1
fi

# **The bundle is chosen by RAM, not by price rank.** `ADR-0003` point 3 makes
# RAM the binding constraint because HNSW wants its index in page cache, and
# point 11 already recorded that 2 GB is the accepted headroom cost. Printed for
# confirmation rather than assumed, because that record's own price table says
# "Figures below are approximate... Check before buying."
echo "Bundles with at least 2 GB, cheapest first:"
"${AWS[@]}" lightsail get-bundles --region "$REGION" \
	--query 'sort_by(bundles[?ramSizeInGb>=`2`&&supportedPlatforms[0]==`LINUX_UNIX`],&price)[:4].[bundleId,ramSizeInGb,cpuCount,diskSizeInGb,price]' \
	--output table

# **The cheapest 2 GB bundle is IPv6-only, and that is a trap for this project.**
# `small_ipv6_3_0` is $10 and `small_3_0` is $12 for identical CPU, RAM and disk;
# the difference is a public IPv4 address. A commons meant for strangers to query
# cannot be reachable only over IPv6 — a large share of clients still have no IPv6
# path at all, and they would see a name that does not resolve rather than an
# error they could act on. `ADR-0003` point 9's table records $10 for Lightsail
# and says the figures were not verified; $12 is the one that serves the internet.
case "$BUNDLE" in
	*ipv6*)
		cat >&2 <<MSG
${BUNDLE} is an IPv6-only bundle. This host is meant to serve a public API to
clients it does not control, and IPv4-only clients — still a large share — could
not reach it at all.

If that is genuinely what you want, set ALLOW_IPV6_ONLY=1. Otherwise use the
dual-stack bundle of the same size, which the table above prices two dollars
higher.
MSG
		[ -n "${ALLOW_IPV6_ONLY:-}" ] || exit 1
		;;
esac

BUNDLE="${BUNDLE:-}"
if [ -z "$BUNDLE" ]; then
	echo
	echo "Set BUNDLE to one of the bundleIds above and re-run, e.g. PROFILE=$PROFILE BUNDLE=small_3_0 $0" >&2
	exit 1
fi

echo "Creating instance ${NAME} (${BUNDLE}) in ${ZONE}..."
"${AWS[@]}" lightsail create-instances --region "$REGION" \
	--instance-names "$NAME" \
	--availability-zone "$ZONE" \
	--blueprint-id "$BLUEPRINT" \
	--bundle-id "$BUNDLE" >/dev/null

echo "Waiting for it to run..."
until [ "$("${AWS[@]}" lightsail get-instance-state --region "$REGION" --instance-name "$NAME" --query 'state.name' --output text)" = "running" ]; do
	sleep 5
done

# A static IP, because the DNS record points at it and a reboot must not move it.
"${AWS[@]}" lightsail allocate-static-ip --region "$REGION" --static-ip-name "${NAME}-ip" >/dev/null 2>&1 || true
"${AWS[@]}" lightsail attach-static-ip --region "$REGION" --static-ip-name "${NAME}-ip" --instance-name "$NAME" >/dev/null

# Exactly two ports plus SSH. `ADR-0005` point 12 keeps the database off the
# network entirely and the compose file publishes no port for it, so there is
# nothing here to open for Postgres — and opening 5432 would quietly undo that.
"${AWS[@]}" lightsail put-instance-public-ports --region "$REGION" --instance-name "$NAME" --port-infos \
	'fromPort=22,toPort=22,protocol=TCP' \
	'fromPort=80,toPort=80,protocol=TCP' \
	'fromPort=443,toPort=443,protocol=TCP' >/dev/null

IP=$("${AWS[@]}" lightsail get-static-ip --region "$REGION" --static-ip-name "${NAME}-ip" --query 'staticIp.ipAddress' --output text)
cat <<OUT

Instance ${NAME} is running at ${IP}.

Next, and in this order:
  1. At Porkbun, add an A record:  clapback  ->  ${IP}
     Wait for it to resolve before step 4 — Caddy cannot obtain a certificate
     until the name points here, and Let's Encrypt rate-limits failures.
  2. ssh -i <key> ubuntu@${IP}
  3. Follow deploy/RUNBOOK.md
OUT
