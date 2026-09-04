# Launching the commons

The steps `ADR-0003` leaves to an operator. Everything here is reversible except
the last one, and that one is reversible too — just noisily.

The launch is deliberately partial: **reads served publicly, writes restricted**,
which `ADR-0003` point 7 permits and whose opposite it forbids. Contribution keeps
working over the private network throughout.

## 0. Before anything

- The domain is `clapback.seethroughlab.com`. DNS for `seethroughlab.com` is at
  Porkbun, not Route 53.
- **The backup bucket is this project's own, and must be created.** `ADR-0003`
  point 9 assumed one existed because Familiar backs up to S3 — but that bucket
  is a personal music library (`artwork/`, `audio/`, `videos/`), and a public
  server holding credentials for it would trade a recomputable corpus for
  something irreplaceable. See step 6.
- Provisioning needs AWS credentials that can use Lightsail. The `s3_user`
  identity cannot — it is scoped to S3 and `lightsail:*` is denied.

## 1. Provision

```bash
PROFILE=admin BUNDLE=<id from the table it prints> ./deploy/provision.sh
```

`PROFILE` is not optional in practice: the default credentials on this machine are
`s3_user`, which cannot use Lightsail at all. The script checks that first and says
so rather than failing four steps later.

**Take `small_3_0` at $12, not `small_ipv6_3_0` at $10.** Same CPU, RAM and disk;
the two dollars buy a public IPv4 address. `ADR-0003` point 9's table records $10
and warned its figures were unverified — that row is the IPv6-only bundle, and a
commons strangers are meant to query cannot be unreachable to IPv4-only clients.
The script refuses an IPv6-only bundle unless `ALLOW_IPV6_ONLY=1`.

It creates the instance, attaches a static IP, and opens 22, 80 and 443 — and
nothing else. There is no port for Postgres because the compose file publishes
none (`ADR-0005` point 12), and opening one would quietly undo that.

## 2. DNS

At Porkbun, an `A` record: `clapback` → the static IP the script printed.

**Wait for it to resolve before step 4.** Caddy obtains its certificate over
HTTP-01, which needs the name pointing at the instance, and Let's Encrypt
rate-limits repeated failures.

```bash
dig +short clapback.seethroughlab.com     # should return the static IP
```

## 3. The instance

The instance uses Lightsail's default key pair, which you may never have
downloaded. Fetch it rather than hunting for it:

```bash
aws lightsail download-default-key-pair --profile admin --region us-east-1 \
	--query 'privateKeyBase64' --output text > ~/.ssh/clapback-lightsail.pem
chmod 600 ~/.ssh/clapback-lightsail.pem
```

**Point `apt` away from the regional mirror before anything else.** Ubuntu 24.04 on
Lightsail ships with `us-east-1.ec2.archive.ubuntu.com`, which on 2026-09-04
returned **HTTP 503** for every package file while the general mirrors were
healthy — measured side by side from the instance:

| mirror | result |
|---|---|
| `us-east-1.ec2.archive.ubuntu.com` | 503, 107 bytes, 20 s |
| `archive.ubuntu.com` | **200, 1.8 MB at 28 MB/s, 0.06 s** |
| `security.ubuntu.com` | 200, 1.8 MB at 16 MB/s |

The failure does not look like a broken mirror. `apt` retries, prints `Ign:` lines
and keeps going, so it presents as a machine that is merely slow — it burned
fifteen minutes here before anyone measured a mirror directly.

The instance also advertises a public IPv6 address it cannot use for egress
(`curl -6` times out), so forcing IPv4 saves a per-connection timeout on top.
Neither is the instance being broken: after both, `apt-get update` takes **10 s**
and Docker installs in **15 s**.

```bash
ssh -i ~/.ssh/clapback-lightsail.pem ubuntu@clapback.seethroughlab.com
sudo sed -i 's|us-east-1.ec2.archive.ubuntu.com|archive.ubuntu.com|g' \
	/etc/apt/sources.list.d/ubuntu.sources
printf 'Acquire::ForceIPv4 "true";\n' | sudo tee /etc/apt/apt.conf.d/99force-ipv4
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 awscli git
sudo usermod -aG docker ubuntu && exec su -l ubuntu
git clone https://github.com/seethroughlab/clapback.git && cd clapback/packages/server
cp deploy/env.example .env && ${EDITOR:-nano} .env
```

`.env` needs `DOMAIN`, `TLS_EMAIL`, `POSTGRES_PASSWORD`, `CACHE_ADMIN_PASSWORD`
and `BACKUP_S3_BUCKET`. The compose file refuses to start without the first four.

## 4. Bring it up

```bash
docker compose -f docker-compose.aws.yml up -d
docker compose -f docker-compose.aws.yml exec api uv run alembic upgrade head
curl https://clapback.seethroughlab.com/health          # TLS should already work
curl -X POST https://clapback.seethroughlab.com/v1/embeddings   # should be 403 by design
```

That last one is the check that matters. A `403` with a body explaining the
corpus is not yet open to anonymous clients means point 7 is satisfied; a `422`
or a `500` means Caddy is not in front of the app and **the launch is not safe**.

## 5. Move the corpus

488 MB, and `ADR-0003` point 5 already established this is minutes of downtime
for a service whose clients treat unavailability as a cache miss.

On the NAS:

```bash
docker compose -f docker-compose.omv.yml exec -T postgres \
	pg_dump -U cache --clean --if-exists cache | gzip -9 > clapback-migration.sql.gz
```

Then, on the instance:

```bash
gunzip -c clapback-migration.sql.gz \
	| docker compose -f docker-compose.aws.yml exec -T postgres psql -U cache cache
docker compose -f docker-compose.aws.yml exec api uv run alembic current   # expect 007
```

Verify the corpus arrived intact before pointing anything at it:

```bash
docker compose -f docker-compose.aws.yml exec -T postgres \
	psql -U cache -c 'select count(*) from embeddings; select count(*) from features;'
```

Expect roughly **21,890** and **77,770** — the figures `ADR-0002` measured. A
count materially below either means the restore was partial, and the fix is to
restore again rather than to start serving.

## 6. Backups, before anyone depends on the data

`ADR-0003` point 6 makes this part of shipping, not a follow-up.

**`s3://clapback-backup` already exists and is configured.** Created and verified
2026-09-04, in `us-east-1` alongside the instance:

| | |
|---|---|
| public access | fully blocked, all four settings |
| encryption at rest | AES256, bucket keys on |
| versioning | enabled |
| lifecycle | dumps expire after 90 days, noncurrent versions after 30, incomplete multipart aborted after 7 |

What remains is an IAM user for the instance, which needs IAM permissions the
`s3_user` credentials do not have. `deploy/iam-backup-policy.json` is the policy:

```bash
aws iam create-user --user-name clapback-backup-writer
aws iam put-user-policy --user-name clapback-backup-writer \
	--policy-name clapback-backup-write \
	--policy-document file://deploy/iam-backup-policy.json
aws iam create-access-key --user-name clapback-backup-writer
```

Put the key in `.env` as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

This is the part worth not skipping. The instance is public, and a key on it is a
key an attacker inherits — so the policy grants `PutObject` on one prefix and
`ListBucket` on that prefix, and nothing else.

**No `s3:DeleteObject`, and versioning is on.** Together those mean a compromised
host cannot erase or overwrite what it has already written: it can add objects,
and every previous version survives. Expiry belongs to the lifecycle rule, which
runs on S3's side where the instance cannot reach it.

```bash
sudo cp deploy/clapback-backup.service deploy/clapback-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now clapback-backup.timer
./deploy/backup.sh                        # run once by hand; do not wait for 04:12 to find out
aws s3 ls s3://clapback-backup/postgres/
```

**Then test a restore somewhere disposable.** A backup nobody has restored is a
hypothesis.

## 6b. Reaching the admin dashboard

Caddy 404s `/admin` on the public host, and the application binds to `127.0.0.1`
on the instance. So administration is a tunnel, and needs SSH — which is the
point: anyone who can reach it already owns the box.

```bash
ssh -i ~/.ssh/clapback-lightsail.pem -L 8000:127.0.0.1:8000 ubuntu@clapback.seethroughlab.com
# then, locally: http://127.0.0.1:8000/admin
```

`CACHE_ADMIN_PASSWORD` is not recovered from anywhere — it is an environment
variable compared with `secrets.compare_digest`, stored in no database, so a new
deployment chooses a new one. `openssl rand -base64 24`.

## 7. Point Familiar at it

Set `community_cache_url` to `https://clapback.seethroughlab.com`. **Reads work
the moment DNS resolves.**

**Contribution stays closed, for everyone, until the delete path exists.**
`ADR-0004` point 7 is not a preference:

> A delete path exists before the endpoint is public. Non-optional. A public
> corpus needs takedown for legal requests and retraction for poisoned
> recordings, and there is no `DELETE` route anywhere today.

There still is not one. So the launch is read-only, and the thing that opens
writes is building that route plus point 9's disk alert — not finding a private
channel for one contributor.

**A private write path would be the wrong answer even though it is available.**
Putting the instance on a Tailnet would let Familiar keep contributing tomorrow,
and would make the commons's only write path a network that one person can join.
That is not a restricted commons, it is a single-contributor corpus with a public
mirror — and a permanent one, because `ADR-0007`'s attestation and `ADR-0008`'s
confirmations both need a second party that the architecture would now forbid.
The pause is the honest state: the corpus stops growing for as long as it takes
to build a `DELETE` route, and then grows from anyone.

## If something hangs

**Measure the remote end before diagnosing the local one.** Everything slow here
turned out to be a mirror returning 503, and two wrong theories were chased first
— an IPv6 egress failure (real, but only worth a timeout per connection) and an
MTU blackhole (not real: a 9001-byte jumbo ping failing against a 1500-byte
internet path is normal, not a fault).

The test that settles it in one command is comparing hosts, not tuning the
instance:

```bash
for h in us-east-1.ec2.archive.ubuntu.com archive.ubuntu.com security.ubuntu.com; do
  printf '%-38s ' "$h"
  curl -4 -sL -o /dev/null -w '%{http_code} %{size_download}B %{speed_download}B/s\n' \
    "http://$h/ubuntu/dists/noble/main/binary-amd64/Packages.gz"
done
```

A healthy instance pulls 1.8 MB at tens of MB/s. If one host is slow and another
is fast, the instance is fine.

Public clients are unaffected — DNS has an `A` record and no `AAAA`, so nothing
reaching this host is asked to use IPv6.

**Do not `pkill -f apt-get` over SSH.** The pattern matches the remote command
string carrying it, so the shell kills itself and everything after it silently
does not run.

## What is still not done after all of this

- **`ADR-0004` point 9's disk alert.** "A full disk is an outage; 80% of one is a
  Tuesday afternoon." The row ceiling bounds growth; nothing yet watches the disk.
- **Anonymous writes**, which stay refused until `ADR-0004` is built.
- **Similarity search** (`ADR-0002`), which needs `ADR-0001` deferred item 4's
  recording id before it returns anything a stranger can use.
