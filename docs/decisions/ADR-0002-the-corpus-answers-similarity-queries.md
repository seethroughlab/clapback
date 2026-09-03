# ADR-0002: The Corpus Answers Similarity Queries

Status: accepted

Date: 2026-09-03

Extends [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md), which decided what this
project is and deferred where it runs.

Implementation:
- Accepted 2026-09-03. Nothing in the Decision is built yet.
- **No similarity endpoint and no vector index exist.** The corpus still has zero distance
  operators; the three indexes on `embeddings` are the primary key and two timestamps, and the only
  queries against the table are the exact-key lookups the Context describes. Point 1 is the
  capability this project exists to provide and remains entirely owed.
- Point 4's dependency is unchanged. The recording-id key is `ADR-0001`'s deferred item 4 and has
  not been taken, so the endpoint can still be built but not usefully consumed.
- Point 7 is half done, and in the wrong direction. `docker-compose.omv.yml` was merged to `main`,
  so the running configuration is no longer on an unmerged branch — but it is the NAS deployment
  that point 5 rules out and [`ADR-0003`](ADR-0003-the-commons-runs-on-one-small-server.md) then
  replaced.

## Context

`ADR-0001` calls this a public commons. It currently has no public endpoint at all.

### What is actually deployed, measured 2026-09-03

| | |
|---|---|
| Embeddings | **21,924** rows, **67 MB** |
| Features | 77,770 rows, 68 MB |
| Whole database | 488 MB |
| Postgres | 17.10 |
| pgvector | 0.8.2 |

The service runs on the same machine as its only real client, which reaches it at
`http://familiar-cache:8000` over a shared Docker network. Three things about that are worth
recording because none of them are obvious:

- **The Fly deployment no longer exists.** `familiar-cache.fly.dev` is NXDOMAIN and `flyctl apps
  list` shows no such app. `ADR-0001` cites "Fly scale-to-zero plus Neon" as the reason running cost
  is near zero; that arrangement was already gone when it was written.
- **`fly-deploy.yml` had been failing on every push to `main`** since the app was destroyed, and
  nobody noticed. Removed alongside this.
- **The running deployment is not in the main line.** The container is started from
  `/home/jeff/familiar-cache/docker-compose.omv.yml`, a file that exists only on the unmerged
  `self-host-omv` branch. What is deployed and what the repository describes are different things.

### pgvector is currently doing nothing

Worth establishing before deciding anything, because it looked like a hard constraint on hosting and
is not:

- **Zero** distance operators anywhere in the codebase — no `<=>`, no `cosine_distance`, nothing.
- Exactly two queries touch `embeddings`, both exact-key lookups on
  `(fingerprint_hash, analysis_version, clap_model_version)`.
- **No vector index exists.** The three indexes on the table are all btree: the primary key and two
  timestamps.
- `_cosine_similarity`, which measures agreement between submissions, is a pure Python function in
  `app/api/routes.py`. Even that does not use the database.

`pgvector` appears once, as `Vector(512)` in the model. Today the corpus is a key-value store that
happens to be spelled in Postgres, and 67 MB of it would fit anywhere.

### The question that changes the answer

Whether to keep pgvector is not a storage question, it is a product one: **should the corpus answer
"what else sounds like this?"**

Without it, the commons serves one purpose — an installation that already holds a track can skip
analysing it. That is real but small, and it makes the corpus useless to anyone who has not already
done the work. With it, the corpus can tell you about music you do not have, which is the only thing
that makes contributing worth a stranger's time.

## Decision

1. **The corpus answers similarity queries.** Given a vector, it returns the nearest recordings in
   the corpus. This is the capability the commons exists to provide; exact-key lookup is a cache,
   and a cache is not worth a public endpoint.

2. **Therefore approximate nearest-neighbour search is a requirement of the host**, not an
   implementation detail. `pgvector` with an HNSW index is the assumed mechanism because it is
   already present, already versioned (0.8.2), and keeps one system rather than two. Any host must
   support it or an equivalent.

3. **The stored precision is part of the contract.** `pgvector`'s `vector` type is float4, which is
   what makes a byte-identical resubmission score 0.99999994 rather than 1.0. Every agreement
   threshold in `ADR-0001` derives from that 6.0e-08 floor. A storage change that alters precision —
   `halfvec`, float8, raw bytes — moves the floor and invalidates the thresholds, so it is a corpus
   decision rather than an optimisation.

4. **Similarity search is only useful once results are identifiable, and that is a dependency.**
   A query today returns neighbouring *fingerprint hashes*, which tell a caller nothing unless they
   already hold the audio to hash. Familiar's `ADR-0102` adds a MusicBrainz recording id as a second
   key; until that exists here, similarity search can be built but not usefully consumed. **Do not
   ship the endpoint and call the capability delivered.**

5. **The public endpoint must not depend on the house.** The commons cannot be reachable only while
   one domestic network and one machine are up — that machine is also the CI runner and the music
   server, and it has been rebooted twice this week. Where it moves is left open; that it moves off
   the house is decided.

6. **Running cost stays near zero, and that constrains scale rather than capability.** MetaBrainz
   named resources as their first cause of failure. 21,924 vectors is 45 MB of float4 and fits in
   the free tier of every managed Postgres worth using; an HNSW index over them is megabytes. The
   constraint bites at millions of recordings, and that is a problem worth having.

7. **The deployment configuration lives on `main`.** The running instance is started from a file on
   an unmerged branch, which is how the repository came to describe a Fly deployment that had been
   destroyed. Whatever host is chosen, its compose file is merged.

## Alternatives Considered

- **Keep the corpus a key-value store and drop pgvector.** Genuinely tempting once the evidence
  above is in hand: nothing uses it, 67 MB fits in SQLite, Turso, D1 or object storage, and the
  hosting field opens up enormously. Rejected because it optimises for what the corpus does today
  rather than what it is for. An installation that can only look up tracks it already owns has no
  reason to recommend the commons to anyone.

- **A dedicated vector database** (Qdrant, Weaviate, Milvus). Better ANN, richer filtering, and
  purpose-built. Rejected on `ADR-0001`'s resources argument: it is a second system to run, back up,
  upgrade and pay for, against a corpus that is currently 45 MB of vectors. `pgvector` is one system
  and already installed. Revisit if the corpus reaches a scale where Postgres genuinely struggles,
  which is millions of rows away.

- **An in-memory index (hnswlib/faiss) rebuilt from object storage.** Cheapest possible at this
  size, and very fast. Rejected because it adds an index lifecycle — build, persist, invalidate,
  reload — that has to be correct across restarts and deploys, for a corpus small enough that
  Postgres will not notice the work. Complexity now against a saving later.

- **Stay on the NAS and expose it with a tunnel.** Zero marginal cost, and the ingress work was
  already started on the `self-host-omv` branch. Rejected by point 5: a public commons whose
  availability tracks a home network and a machine doing three other jobs is not one people can
  build on. It also concentrates a second failure into a box whose downtime already stops music
  playing.

- **Defer the endpoint until there are contributors.** Defensible, and the corpus is one library
  today. Rejected because the causation runs the other way: nobody can contribute to something they
  cannot reach, and `ADR-0001` point 5 already decided the tool must be worth running before the
  corpus is worth querying. The endpoint is what lets that second half ever start.

## Consequences

- **Positive** — the corpus becomes worth querying by people who have contributed nothing, which is
  the only version of this that grows.
- **Positive** — settles that `pgvector` is a requirement rather than an accident, so the hosting
  shortlist is a real shortlist instead of an open field re-litigated each time.
- **Positive** — records that the Fly deployment is gone and the running config is off-main, both of
  which the repository previously misdescribed.
- **Tradeoff** — a similarity endpoint over an opaque-hash corpus returns opaque hashes. Point 4
  makes the dependency explicit, but until `ADR-0102`'s recording id exists here, the capability is
  built and idle.
- **Tradeoff** — ANN over a public endpoint is a different exposure from key lookup. Today a caller
  must already hold audio to ask anything; afterwards they can ask "what is near this vector" and
  enumerate the corpus by walking the space. That is the legibility `ADR-0102` flagged, arriving by
  another route and without the recording ids that made it a deliberate trade.
- **Tradeoff** — moving off the house means a bill, however small, and a second thing to keep alive.
  `ADR-0001`'s "near zero" becomes "small and monitored" rather than "free".
- **Follow-up** — the host itself. This ADR decides the requirements — ANN, public, off-house,
  cheap, config on `main` — and deliberately not the vendor.
- **Follow-up** — whether `halfvec` is worth it at scale. It halves storage and index size for a
  precision cost that point 3 says is a corpus decision; the arithmetic only becomes interesting
  well past a million rows.
- **Follow-up** — the 77,770 feature rows still served by legacy endpoints. `ADR-0001` point 7 said
  nothing existing migrates; a host move is the natural moment to decide whether they travel at all.
