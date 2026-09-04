# ADR-0003: The Commons Runs on One Small Server

Status: accepted

Date: 2026-09-03

Extends [ADR-0002](ADR-0002-the-corpus-answers-similarity-queries.md), which decided the corpus
answers similarity queries and deliberately left the host open.

Implementation:
- Accepted 2026-09-03. The service still runs where the Context found it, on the machine hosting its
  only client, which is what `ADR-0002` point 5 decided it must stop doing.
- **The deployment configuration reached `main` on 2026-09-04**, satisfying point 8 and `ADR-0002`
  point 7. `packages/server/docker-compose.aws.yml` is the instance this record chose: Postgres 17
  with pgvector, the application, and Caddy terminating TLS in front of both. Nothing is provisioned
  yet — this is the configuration, not the deployment.
- **Point 7's write restriction is topological rather than a second authentication scheme.** Caddy
  refuses writes on the public host with a body saying the corpus is not closed but not yet open,
  and 404s the admin surface; Familiar keeps contributing over the private network by reaching the
  application directly. When [`ADR-0004`](ADR-0004-contributors-are-identified-but-not-accounts.md)
  lands, one `respond` line is deleted and nothing else changes. Neither Postgres nor the
  application publishes a port, so there is no way to reach `8000` and bypass either TLS or the
  restriction.
- **Point 6's backups are written and not yet installed.** `deploy/backup.sh` dumps to plain gzipped
  SQL — so a restore needs only `psql`, not a matching `pg_restore` — and uploads to the bucket
  point 9 says already exists, with a systemd timer beside it. It refuses to upload a dump under
  1 MB, because a truncated backup that looks like success is worse than a failed one.
- **`ADR-0004` point 9's row ceiling is implemented**, that point's one bound needing nothing from
  identity, defaulting to point 11's stated 500,000. It is checked only where a submission would
  create a new row, so a confirmation of an existing vector is never refused.
- Still owed before this is done: an instance, a domain (`ADR-0001` deferred item 6), the `pg_dump`
  and restore of the 488 MB currently on the NAS, and point 9's disk alert, which is not built.

## Context

`ADR-0002` set five requirements and named no vendor: approximate nearest-neighbour search, a public
endpoint, off the house, near-zero cost, and the deployment configuration on `main`. This picks the
host, and one further constraint decides it.

### The ambition is millions of vectors; the horizon is hundreds of thousands

Both numbers matter and they do different work. Millions is what the commons is *for*, and it is the
figure that eliminates most of the hosting field — so it belongs in the record rather than in
someone's head. A few hundred thousand is what the next year plausibly looks like, and it is what
the first box should be bought for.

Sizing for the ambition would mean paying for years of headroom before it is used. Sizing for the
horizon without writing down what comes after is how a project ends up migrating under pressure.
Point 5 is the second half of point 4 and should not be separated from it.

Measured on the live corpus 2026-09-03: **3,196 bytes per row**, across 21,924 rows. Most of that is
TOAST — the table itself is 3.8 MB and its btree indexes 5.3 MB, against 67 MB total, because a
512-dimensional `vector` does not sit inline.

| corpus | table + TOAST | HNSW index | total |
|---|---|---|---|
| 21,924 — today | 62 MB | **57 MB** | 124 MB |
| 150,000 | ~415 MB | ~390 MB | **~830 MB** |
| 300,000 | ~830 MB | ~780 MB | **~1.7 GB** |
| 1,000,000 | ~2.8 GB | ~2.6 GB | **~5.6 GB** |
| 10,000,000 | ~28 GB | ~26 GB | **~56 GB** |

**These are measured, not estimated.** An HNSW index (`m=16, ef_construction=64`) was built on the
live corpus on 2026-09-03: **57 MB across 21,924 rows — 2,724 bytes per row**, in 19 seconds. Table
and TOAST measure 2,830 bytes per row, so the corpus costs about **5.5 KB per vector all-in**.

An earlier draft of this ADR estimated the index at ~2.2 KB per row from how pgvector stores
elements. The real figure is 24% higher, which is the sort of gap that decides an instance size —
hence the insistence on measuring rather than extrapolating from the shape of the data structure.

Two things follow for sizing. **The index is roughly the same size as the data**, so budget double
the obvious number. And **RAM, not disk, is what runs out**: at 300,000 vectors the index alone is
around 780 MB, which wants to sit in page cache alongside Postgres itself.

### What the budget rules out

Under $10 a month, with a corpus heading for gigabytes:

- **Managed Postgres free tiers cap at around 0.5 GB.** They fit today's 67 MB and nothing beyond
  roughly 200,000 vectors. Designing onto one means a forced migration at exactly the moment the
  project starts working.
- **Managed Postgres paid tiers start above the budget** — Neon's first paid tier and Supabase Pro
  are both well past $10, before storage.

So the budget and the ambition together mean running Postgres rather than renting it — even though
the horizon alone would fit a free tier for a while, which is exactly the trap: it fits until the
project starts working. That is a
conclusion, not a preference, and it is worth stating plainly because "just use managed Postgres" is
otherwise the obvious answer.

### What the current server is and is not ready for

It has never been publicly reachable, so this was audited rather than assumed — and an earlier draft
of this ADR was wrong about it in both directions.

**Already sound.** The admin surface **fails closed**: an unset `CACHE_ADMIN_PASSWORD` returns 503
rather than granting access. The password comparison is timing-safe (`secrets.compare_digest`), and
the session cookie is `httponly`, `secure` and `samesite=lax`. `allow_origins=["*"]` looks alarming
and is not: `allow_credentials` is unset and therefore false, so no cross-origin page can ride the
admin cookie, and the data behind it is opaque hashes and vectors. Rate limiting (`slowapi`) is
applied per-route on both lookups and contributions, and an `IPBanMiddleware` tracks and blocks.

**Actually outstanding.** Two things, and neither is a configuration flag:

- **Contribution is unauthenticated.** Anyone who can reach the endpoint can POST an embedding.
  Behind a private network that is a non-issue; on a public one it is the whole of `ADR-0001`'s
  deferred item 3 — identity, revocation and deletion — arriving at once. Rate limits bound the
  volume, not the intent.
- **There is no TLS.** A bare instance serves plain HTTP; a public endpoint needs a certificate and
  a terminator in front of the application.

## Decision

1. **One small VPS runs both Postgres and the application.** Not a managed database, not two hosts.
   At this size the database is a few gigabytes and the app is an idle Python process; splitting
   them doubles the bill and the number of things that can be down.

2. **Postgres with `pgvector`, self-managed.** Already the assumed mechanism under `ADR-0002` point
   2, and the version in use (0.8.2 on Postgres 17.10) supports HNSW. Self-managing it is the price
   of the budget, and at this scale the operational surface is a nightly dump rather than a job.

3. **RAM is the sizing constraint, not disk.** HNSW wants its index in page cache; when it does not
   fit, recall holds but latency degrades sharply. Disk is cheap on any VPS and the index is not.
   Size the box by expected index size, not by corpus size.

4. **Size for the next few hundred thousand vectors, not for the ambition.** That is roughly ten
   times the present corpus and a realistic horizon; a box sized for ten million would be paid for
   years before it was used. The smallest useful VPS — two cores and four gigabytes is the common
   shape — holds a few hundred thousand vectors and their index with room to spare.

5. **The upgrade path is written down now, because the point of sizing small is knowing what happens
   next.** In order, each step taken only when the previous one is uncomfortable:

   | corpus | move | cost of making it |
   |---|---|---|
   | to ~300k | nothing — the initial box | — |
   | ~300k–1M | resize the VPS for more RAM | a reboot |
   | 1M–5M | `halfvec` to halve the index, **or** IVFFlat instead of HNSW | a reindex; `halfvec` changes stored precision and is therefore a corpus decision under `ADR-0002` point 3 |
   | beyond | reconsider managed or dedicated vector hosting | a dump and restore |

   **What makes this path safe is that migration stays cheap.** A corpus of a few gigabytes is a
   `pg_dump` and a restore — minutes of downtime for a service whose clients treat unavailability as
   a cache miss. There is no point at which the data becomes too large to move; there is only a
   point at which one box stops being the cheapest way to serve it.

6. **Backups are a nightly `pg_dump` to object storage, and are part of shipping this.** Not a
   follow-up. The corpus is contributed data that cannot be regenerated without the contributors'
   audio — nobody here can rebuild it. A few gigabytes compressed costs cents to store.

7. **TLS terminates in front of the application, and unauthenticated writes are a launch blocker.**
   The audit above found the admin surface and CORS already sound, so neither holds this up. What
   does: a bare instance has no certificate, and a public endpoint that accepts anonymous
   contributions is `ADR-0001`'s deferred item 3 becoming due. Serving reads publicly while writes
   stay restricted is an acceptable first step; serving anonymous writes publicly is not, and
   deciding how identity works is a prerequisite rather than a follow-up.

8. **The deployment configuration is merged to `main`**, per `ADR-0002` point 7. The instance
   running today starts from a compose file on an unmerged branch, which is how the repository came
   to describe a Fly deployment that had been destroyed.

9. **The host is AWS.** Compared on RAM per unit cost — because point 3 makes RAM the binding
   constraint — AWS is not the best ratio available. It is chosen anyway, for reasons that are not
   about the ratio:

   - **Operational familiarity.** The people running this already know AWS. A cheaper host nobody
     has used is a worse host at 2am, and `ADR-0001` names resources — including attention — as
     MetaBrainz's first cause of failure.
   - **The scaling path is well-trodden.** Instance types change with a stop and start; EBS volumes
     grow online. Point 5's upgrade steps are ordinary operations rather than research.
   - **It is fully driveable from the CLI**, which keeps the deployment reproducible and scriptable
     rather than a sequence of console clicks nobody can repeat.
   - There is already a footprint: Familiar backs up to an S3 bucket in `us-east-1`, so the backup
     destination in point 6 exists and is paid for.

   Figures below are approximate and were not verified against current pricing pages. **Check before
   buying** — the ordering is the durable part.

   | vendor | ~$/month | vCPU | RAM | note |
   |---|---|---|---|---|
   | Hetzner CX22 | ~4.50 | 2 | 4 GB | best ratio found; unfamiliar |
   | Netcup | ~5 | 4 | 8 GB | best ratio anywhere; EU only |
   | **AWS Lightsail** | **10** | 2 | **2 GB** | 60 GB SSD, 3 TB transfer, all-in |
   | AWS EC2 `t4g.small` | ~12–15 | 2 | 2 GB | plus EBS and egress; cheaper with a savings plan |
   | DigitalOcean / Vultr / Linode | 12 | 1 | 2 GB | |
   | AWS RDS | 15+ | — | — | outside the budget |

10. **Within AWS, start on Lightsail rather than EC2.** At this budget Lightsail's fixed price
    includes the instance, 60 GB of storage and 3 TB of transfer, where the equivalent EC2 instance
    bills storage and egress separately and lands above ten dollars before a savings plan. EC2
    becomes the better choice at the first upgrade step, when growing storage online and changing
    instance types matters more than the bundled price — and moving between them is the same
    `pg_dump` and restore as any other step in point 5.

11. **This decision costs headroom, and the cost is recorded rather than discovered.** Two gigabytes
    against the four the cheapest option offered halves the runway before point 5's first upgrade.
    On the measured figures that is comfortable to roughly **300,000 vectors** — where the index is
    around 780 MB and still fits in page cache beside Postgres — and tight beyond 500,000. That is a
    resize, not a migration, and a fair price for operating something familiar. It is written down so
    that hitting it reads as expected rather than as a surprise.

    **Egress is metered on AWS and was not on the alternatives.** Lightsail's 3 TB allowance is far
    beyond anything this corpus will serve — a 512-float vector is about two kilobytes — but it is a
    variable that did not previously exist, and a public commons becoming popular is exactly when a
    metered bill surprises someone. Worth an alarm, not worth worrying about.

## Alternatives Considered

- **A cheaper VPS with a better RAM ratio (Hetzner, Netcup).** Roughly four times the RAM per unit
  cost, which under point 5 is directly twice the runway before the first upgrade. Rejected on point
  9's reasoning rather than on the numbers: the numbers favour it and the operational familiarity
  does not. Recorded because the tradeoff is real and someone should be able to see what was given
  up, not because the decision is soft.

- **AWS RDS, rather than Postgres on an instance.** The thing AWS is genuinely best at here —
  managed backups, upgrades and failover, which point 6 otherwise makes this project's job. Rejected
  purely on price: the smallest instance is above the whole budget before storage. This is the
  alternative most worth reopening if the budget ever reaches roughly thirty dollars a month.

- **Managed Postgres elsewhere (Neon, Supabase, Railway).** Backups, upgrades and availability handled;
  `ADR-0001` even cites Neon as part of why cost was near zero. Rejected on arithmetic rather than
  preference: free tiers cap around 0.5 GB, which is roughly 200,000 vectors, and the paid tiers
  start above the whole budget. Choosing one now means a migration precisely when the corpus starts
  to matter. Worth revisiting if the project ever justifies a real bill.

- **Two hosts: managed Postgres plus a scale-to-zero app.** The shape `ADR-0001` described, and
  operationally the least work. Rejected by the same storage arithmetic, plus it doubles the bill for
  an app that idles at zero and a database of a few gigabytes.

- **Keep it on the NAS and expose it through a tunnel.** Free, and the ingress work is already
  started on the `self-host-omv` branch. Rejected by `ADR-0002` point 5 and by an explicit
  preference for the data to live off that machine: it is the CI runner and the music server, it has
  rebooted twice this week, and a public commons whose availability tracks a home network is not one
  people can build on.

- **A dedicated vector database.** Better ANN and purpose-built. Rejected under `ADR-0002`'s
  reasoning and again here on cost: it is a second system to run against a corpus small enough that
  Postgres will not notice it, and the managed offerings are priced above the budget.

- **Object storage plus an in-memory index rebuilt at boot.** Genuinely the cheapest at any scale
  below tens of millions, since blob storage is nearly free. Rejected because it trades a bill for an
  index lifecycle — build, persist, invalidate, reload — that has to be right across restarts, and
  because a VPS that costs less than a coffee removes the problem entirely.

## Consequences

- **Positive** — one machine, one bill, comfortably under budget, and no free-tier policy change can
  strand the corpus.
- **Positive** — the corpus leaves the house, so the commons stops depending on a domestic network
  and a machine doing three other jobs.
- **Positive** — self-managed Postgres means `pgvector` version, index type and parameters are all
  choices rather than whatever a provider supports.
- **Tradeoff** — backups, upgrades and security patching become this project's job. Point 6 makes
  the backup part explicit because it is the one that matters and the one most easily deferred.
- **Tradeoff** — a single box is a single point of failure. Acceptable for a cache whose clients
  treat unavailability as a miss, and unacceptable later if anything comes to depend on it being up.
- **Tradeoff** — **one maintainer now operates a public service.** `ADR-0001` recorded MetaBrainz
  citing resources first, and this adds an ongoing obligation that did not exist when the thing was
  private.
- **Follow-up** — the region. `us-east-1` is where the existing backup bucket lives, which argues
  for putting the instance there and keeping backup traffic in-region and free.
- **Follow-up** — a billing alarm before the endpoint is public, given point 11's metered egress.
- **Follow-up** — `ADR-0001`'s deferred item 3, identity and revocation, becomes due when the
  endpoint goes public rather than when the corpus grows.
- **Follow-up** — whether the 77,770 legacy feature rows travel to the new host at all. A migration
  is the natural moment to decide, per `ADR-0002`.
