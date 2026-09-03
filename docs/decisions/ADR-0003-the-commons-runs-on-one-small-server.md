# ADR-0003: The Commons Runs on One Small Server

Status: proposed

Date: 2026-09-03

Extends [ADR-0002](ADR-0002-the-corpus-answers-similarity-queries.md), which decided the corpus
answers similarity queries and deliberately left the host open.

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

| corpus | table (measured rate) | HNSW index (**estimated**) | total |
|---|---|---|---|
| 21,924 — today | 67 MB | — | 67 MB |
| 1,000,000 | ~3.2 GB | ~2.2 GB | **~5.4 GB** |
| 10,000,000 | ~32 GB | ~22 GB | **~54 GB** |

The row nearest the decision is the one not in this table: **a few hundred thousand vectors is
roughly 1 GB of rows and perhaps 700 MB of index** — comfortably inside the smallest VPS worth
renting, which is the point.

**The index column is an estimate and should be measured before anything is sized on it.** No vector
index exists yet — `ADR-0002` established that pgvector is currently only a column type — so the
figure comes from pgvector storing the full vector plus its neighbour links per element, not from
observation. Building an HNSW index on the current 21,924 rows and multiplying is an afternoon's
work and would replace a guess with a number.

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

### What the current server is not ready for

It has never been publicly reachable. Two things in it were fine on a private network and are not on
a public one:

- `allow_origins=["*"]` in `app/main.py`
- `admin_password` defaults to an empty string, set from `CACHE_ADMIN_PASSWORD`

Rate limiting exists (`slowapi`), which is a start. `ADR-0001` deferred identity, revocation and
deletion as item 3; a public endpoint is what makes that item due rather than pending.

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

7. **The public surface is hardened before it is public.** `allow_origins=["*"]` and an
   empty-by-default admin password are private-network settings. This does not decide the identity
   and moderation design — that is `ADR-0001`'s deferred item 3 — but the endpoint does not go up
   with an unauthenticated admin surface on it.

8. **The deployment configuration is merged to `main`**, per `ADR-0002` point 7. The instance
   running today starts from a compose file on an unmerged branch, which is how the repository came
   to describe a Fly deployment that had been destroyed.

9. **Hetzner is the recommended vendor, and the choice is reversible.** Compared on RAM per unit
   cost, because point 3 makes RAM the binding constraint. Figures are approximate and were not
   re-verified against current pricing pages; **check before buying**, since the ordering matters
   more than the numbers.

   | vendor | ~$/month | vCPU | RAM | note |
   |---|---|---|---|---|
   | **Hetzner CX22** | ~4.50 | 2 | **4 GB** | 40 GB SSD, 20 TB traffic, EU and US regions |
   | Netcup | ~5 | 4 | **8 GB** | best ratio found; EU only |
   | AWS Lightsail | 10 | 2 | 2 GB | 60 GB, 3 TB transfer |
   | DigitalOcean / Vultr / Linode | 12 | 1 | 2 GB | better documentation, worse ratio |
   | AWS EC2 `t4g.small` | ~12 + EBS + egress | 2 | 2 GB | before storage and transfer |
   | AWS RDS | 15+ | — | — | outside the budget entirely |

   Hetzner offers roughly four times the RAM per unit cost of the nearest AWS option, which under
   point 5 is directly the difference between the first box lasting to a few hundred thousand
   vectors or half that.

10. **AWS was considered seriously and rejected on ratio, not reflex.** There is an existing
    footprint — Familiar already backs up to an S3 bucket in `us-east-1` — so consolidating billing
    and credentials would have real value. Three things outweighed it. Lightsail gives half the RAM
    for twice the price. Egress is metered above a free allowance where Hetzner includes 20 TB,
    which introduces a variable bill exactly when a public commons becomes popular — the failure
    `ADR-0001` records MetaBrainz naming first. And the thing AWS is genuinely good at here, RDS, is
    priced out of the budget; what remains is running Postgres on a VM oneself, which is the same
    work at several times the cost.

    If the budget ever reaches roughly thirty dollars, managed Postgres becomes a real argument and
    this point should be reopened rather than inherited.

## Alternatives Considered

- **Managed Postgres (Neon, Supabase, Railway).** Backups, upgrades and availability handled;
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
- **Follow-up** — measure a real HNSW index on the current corpus. Every sizing number above rests on
  an estimate, and the measurement is cheap.
- **Follow-up** — the region, and account setup. Point 9 names a vendor; Hetzner asks some new
  accounts for identity verification, which has taken a day in the past, so it is worth starting
  before it is needed. US regions exist, so this is not an argument for EU latency.
- **Follow-up** — `ADR-0001`'s deferred item 3, identity and revocation, becomes due when the
  endpoint goes public rather than when the corpus grows.
- **Follow-up** — whether the 77,770 legacy feature rows travel to the new host at all. A migration
  is the natural moment to decide, per `ADR-0002`.
