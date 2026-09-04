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
BUNDLE=<id from the table it prints> ./deploy/provision.sh
```

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

```bash
ssh ubuntu@clapback.seethroughlab.com
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

First, a bucket of its own and a key that can reach nothing else:

```bash
aws s3 mb s3://clapback-backup --region us-east-1
aws s3api put-public-access-block --bucket clapback-backup \
	--public-access-block-configuration \
	BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Then an IAM user whose policy names only that bucket. This is the part worth not
skipping: the instance is public, and a key that reaches further than this is a
key an attacker inherits.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::clapback-backup/postgres/*" },
    { "Effect": "Allow", "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::clapback-backup" }
  ]
}
```

No `s3:DeleteObject`: a compromised host should not be able to erase the backups
it has been writing. Expiry is the bucket's job, not the instance's — a lifecycle
rule that deletes after 90 days keeps the cost at cents without handing the
delete verb to a public server.

```bash
sudo cp deploy/clapback-backup.service deploy/clapback-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now clapback-backup.timer
./deploy/backup.sh                        # run once by hand; do not wait for 04:12 to find out
aws s3 ls s3://clapback-backup/postgres/
```

**Then test a restore somewhere disposable.** A backup nobody has restored is a
hypothesis.

## 7. Point Familiar at it

Familiar's `community_cache_url` still defaults to `familiar-cache.fly.dev`,
which is NXDOMAIN (`ADR-0005` records this). Set it to
`https://clapback.seethroughlab.com` for reads. Contribution continues over the
private network, because the public host refuses writes.

## What is still not done after all of this

- **`ADR-0004` point 9's disk alert.** "A full disk is an outage; 80% of one is a
  Tuesday afternoon." The row ceiling bounds growth; nothing yet watches the disk.
- **Anonymous writes**, which stay refused until `ADR-0004` is built.
- **Similarity search** (`ADR-0002`), which needs `ADR-0001` deferred item 4's
  recording id before it returns anything a stranger can use.
