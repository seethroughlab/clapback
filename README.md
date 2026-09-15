# clapback

A public commons of **CLAP audio embeddings**, and the reference implementation that
produces them.

An embedding is a 512-dimensional vector describing what a recording *sounds like*.
Computing one costs seconds of CPU and a 600 MB model; comparing two is a dot product.
So it is worth computing once and sharing — provided everybody computes the same thing.

That proviso is the whole design. `clapback-embed` exists so there is exactly one
implementation: if two contributors disagree about a recording, the disagreement is
about the audio, not about whose code ran.

The commons is public at **https://clapback.seethroughlab.com**. Reads need no key and no
account. Contribution is open, opt-in in every client, and sends a vector and a one-way
hash — never audio, never a filename.

## Take part

The commons is the product. It is meant to be reached from the tools people already run,
and the way in is a package with no dependency beyond the standard library
([`ADR-0011`](docs/decisions/ADR-0011-the-commons-is-what-other-tools-plug-into.md)).

| If you… | Install | What it is |
|---|---|---|
| use **beets** | `pip install beets-clapback` | `absubmit` reborn: look up, else embed; contribute if you say so; `beet clapback-similar`. [README](packages/beets-clapback/) |
| use **Picard** | the zip from a [`picard-v*` release](https://github.com/seethroughlab/clapback/releases?q=picard-v) | Right-click: look up, name, contribute; *what sounds like this*. Works with no embedder installed, and says so. [README](packages/picard-clapback/) |
| write a **tool** | `pip install clapback-client` | The contract — canonical hashing, lookup-before-contribute, `client_id`, backoff — as code. Stdlib only, so a tool with its own embedder needs no ONNX Runtime. [README](packages/client/) |
| want a **command line** | `pip install clapback-cli` | The reference client: index a directory, search it by description, find duplicates, contribute. [README](packages/cli/) |
| need the **embedder** | `pip install clapback-embed` | The reference pipeline. ONNX Runtime, no `torch`. [README](packages/embed/) |

A tool that already computes CLAP vectors can contribute under its own pipeline
identity; they sit beside the reference's rather than being compared with it.

## The reference pipeline

```bash
pip install clapback-embed
```

```python
from clapback_embed import embed_file, embed_text

vector = embed_file("track.flac")                  # 512 floats, unit length
query  = embed_text("dreamy ambient with piano")   # same space
```

No `torch`, no `transformers` — it runs on ONNX Runtime, and optionally on a GPU.
Everything that could vary is pinned and versioned: the mel front-end, the windowing
rule, the pooling, the checkpoint and the precision. `PIPELINE_VERSION` is the identity
of all of it together, and it travels with every contribution.

Measured, not asserted:

| | |
|---|---|
| Same audio, two architectures (arm64 vs x86_64) | agree to **6.6e-11** |
| CPU vs CUDA | **6.6e-14** |
| What `pgvector`'s float4 storage costs | 6.0e-08 |
| Two different rips of one recording | 3e-04 – 3e-03 |

So the noise floor of the corpus is set by how vectors are *stored*, not by whose
machine computed them. See [`packages/embed/`](packages/embed/) for the details and
[`packages/embed/scripts/compare_vectors.py`](packages/embed/scripts/compare_vectors.py)
for the cross-machine check.

## Where this actually stands

Early, and worth being plain about. As of 2026-09-15:

- **25,886 embeddings, all from a single contributor.** Every row declares the pipeline
  that produced it and is keyed on a hash any client can reproduce from the audio —
  migration `011` removed the 47,486 rows that could not say what produced them, and
  [`ADR-0010`](docs/decisions/ADR-0010-the-corpus-key-is-a-function-of-the-audio.md)
  collapsed a key that had been stored in two encodings. So this is one library with a
  public API in front of it, and the honest description of the mechanism is that it is
  in place ahead of the evidence. **A second contributor is the single most valuable
  thing that could happen to this project** — four accepted decisions are waiting on one.
- **23,196 of those rows name their recording — 89.6%.** A row is keyed on a one-way
  hash, so similarity search used to return hashes nobody could resolve.
  [`ADR-0012`](docs/decisions/ADR-0012-a-contribution-can-name-its-recording.md) lets a
  client *claim* a MusicBrainz recording id for a row; a neighbour with a claim is a
  recording you can look up, and one without is still a hash, shown as one. The coverage
  came from one contributor resolving its library through AcoustID over thirty hours
  (2026-09-14/15); beets and Picard send the id with every contribution from now on.
- The `features` endpoints below still work and still serve the rows they hold.
  [`ADR-0001`](docs/decisions/ADR-0001-clapback-is-a-public-clap-embedding-commons.md)
  decided the commons carries **embeddings**, not the bpm/key/valence estimates that
  killed AcousticBrainz — so those endpoints are legacy, not direction.

[Familiar](https://github.com/seethroughlab/familiar) is the first client and, so far,
the only contributor. It is not the owner: the point of the packages above is that
anything can contribute.

## Privacy

- A recording is identified by the SHA256 of its AcoustID fingerprint — one-way, and
  computed by the client. The corpus never receives a title, an artist, a filename or a
  path, and cannot recover one.
- A client *may* attach a MusicBrainz recording id. That tells the operator which
  recording you hold, which is why every client sends it under the same opt-in switch
  as the vector and says so.
- `client_id` is a random UUID minted on the first contribution, derived from nothing
  about you or your machine. Delete it and you are a new contributor.
- Contribution is off until you turn it on, in every client.

## API

Full reference at [`/api`](https://clapback.seethroughlab.com/api); schema at
[`/docs`](https://clapback.seethroughlab.com/docs).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/embeddings/{hash}` | GET | The vector for a recording you hold, its pipeline, its confirmations, and its recording id if anyone has claimed one |
| `/v1/embeddings` | POST | Contribute a vector — optionally with a `recording_mbid` |
| `/v1/similar` | POST | Nearest recordings to a vector, across every library the corpus holds |
| `/v1/recordings/claims` | POST | Name a row you already contributed, without re-sending the vector |
| `/v1/recordings/{mbid}` | GET | What does this recording sound like — without holding it |
| `/v1/features/{hash}`, `/v1/features` | GET, POST | Legacy |
| `/`, `/browse/{hash}`, `/map` | GET | Public browse pages |
| `/health`, `/health/db` | GET | Liveness, database connectivity |

### Embeddings

#### GET `/v1/embeddings/{fingerprint_hash}`

Query params, all optional:
- `pipeline_version` (string): **what you should ask by.** Half the corpus key, so it
  selects exactly one row and it is the only parameter that says the answer is
  comparable with vectors you computed yourself. **Escape the `+` as `%2B`** — in a
  query string `+` means a space, so an unescaped identity matches nothing and 404s
  as though the recording were missing.
- `analysis_version` (int): the contributing client's own counter. A filter on
  metadata, not on identity.
- `clap_model_version` (string): the checkpoint. Also a filter, and it does not
  establish comparability — windowing or pooling can move every vector without
  changing it.

Ask by the last two alone and you can be handed a perfectly valid vector from a
pipeline you cannot use. If more than one row matches, the most-confirmed is
returned and the response says which pipeline it came from.

Response 200:
```json
{
  "fingerprint_hash": "abc123...",
  "embedding": [0.1, 0.2, ...],
  "analysis_version": 8,
  "clap_model_version": "laion/clap-htsat-unfused:v1",
  "pipeline_version": "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32",
  "contributor_count": 3,
  "recording_mbid": "1c6da765-da50-476b-a000-61e7cf45ded8"
}
```

`recording_mbid` is null when nobody has claimed one.

#### POST `/v1/embeddings`

Request body:
```json
{
  "fingerprint_hash": "abc123...",
  "embedding": [0.1, 0.2, ...],
  "analysis_version": 5,
  "clap_model_version": "laion/clap-htsat-unfused:v1",
  "client_id": "any-opaque-string-your-install-keeps",
  "pipeline_version": "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32",
  "recording_mbid": "1c6da765-da50-476b-a000-61e7cf45ded8"
}
```

**`pipeline_version` is required; `client_id` is not.** The contrast is deliberate
([`ADR-0006`](docs/decisions/ADR-0006-the-pipeline-identity-is-the-corpus-key.md)
point 4): an unattributed submission is still evidence, whereas an unidentified
pipeline is a vector that cannot be compared with anything, including itself later —
so there is no sensible key to store it under. Without a `client_id` a submission is
accepted and stored but can never count toward independent agreement
([`ADR-0004`](docs/decisions/ADR-0004-contributors-are-identified-but-not-accounts.md)
point 3).

`pipeline_version` is `clapback_embed.PIPELINE_VERSION`, composed from every component
that can move a vector. It is **half the corpus key**, so two rows are comparable
exactly when they share one. It is asserted rather than proven: the server believes
what you send, which catches the forgotten bump and the stale build and is not a
defence against a contributor who lies.

The same recording contributed from two pipelines is two rows, not a disagreement —
which is the point. A confirmation only happens between vectors that claim the same
provenance.

`recording_mbid` is optional and needs a `client_id`, because it is recorded as a
claim by that client. **Look up before you contribute**: a repeat submission is recorded
as agreement, and one install agreeing with itself would corrupt the one measurement
the commons exists to make.

### Recordings

A recording id is a *claim*, per client, that a hash is a particular MusicBrainz
**recording** — never verified against MusicBrainz, and counted rather than trusted
([`ADR-0012`](docs/decisions/ADR-0012-a-contribution-can-name-its-recording.md)). A
row's recording is whichever id the most distinct clients assert.

#### POST `/v1/recordings/claims`

```json
{"fingerprint_hash": "abc123...", "recording_mbid": "1c6da765-...", "client_id": "..."}
```

Attaches an id to a row that already exists without touching `contributor_count`. Use
this to name a library you already contributed; never re-send the vector to do it.
Idempotent. `404` if the corpus does not hold the hash.

#### GET `/v1/recordings/{recording_mbid}`

Every row claimed under that recording, one per pipeline, each with its vector,
`contributor_count`, and how many clients stand behind the claim. The way to ask what a
recording sounds like without holding it.

### Similarity

#### POST `/v1/similar`

```json
{"embedding": [ ...512 floats... ], "limit": 20,
 "pipeline_version": "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"}
```

Nearest recordings by cosine similarity, HNSW-indexed, about 3 ms across the corpus.
Each neighbour carries its `pipeline_version` — vectors from two pipelines are not
comparable however close they look, so filter by yours — and a `recording_mbid` with a
`recording_claims` count when anyone has named it. A neighbour nobody has named is a
bare hash, shown as one rather than hidden. The vector can come from `embed_file` or
`embed_text`: the space is joint, so a description is a query too.

### Features (legacy)

`ADR-0001` point 4 decided the commons stores embeddings and not features. These
endpoints still work and the existing rows are still served, but they are not where
this is going: bpm, key and valence are *claims about the world* that consensus cannot
verify, which is precisely what MetaBrainz identified when AcousticBrainz stopped taking
submissions. Familiar keeps its own private feature cache instead.

`GET /v1/features/{fingerprint_hash}?analysis_version=5` returns the stored feature
dict and its `contributor_count`; `POST /v1/features` takes `fingerprint_hash`,
`analysis_version` and a `features` object.

### Rate limits

300 lookups and 30 writes per minute, per address. A claim is a write. There is no key
to obtain and no plan to buy; if you need more, the corpus is a few hundred megabytes
and open source.

## Development

The repository is a `uv` workspace of peers
([`ADR-0005`](docs/decisions/ADR-0005-the-repository-is-a-workspace-of-peers.md)):
`packages/embed`, `packages/client`, `packages/cli` and `packages/beets-clapback` are
published to PyPI, `packages/picard-clapback` to a GitHub release as a zip; `packages/server`
is the commons. The root builds nothing.

```bash
# Install everything, from the root
uv sync

# The server
cd packages/server
CACHE_DATABASE_URL="postgresql+asyncpg://cache:cache@localhost:5433/cache" \
  uv run uvicorn app.main:app --reload
uv run pytest
docker compose up          # includes PostgreSQL, published on 5433

# A package
cd packages/embed
uv pip install -e '.[dev]'
pytest                     # add -m artifacts once the encoders are exported
```

Each package has its own CI (`.github/workflows/<name>-ci.yml`) and publishes to PyPI
from a tag with its own prefix (`embed-v*`, `client-v*`, `cli-v*`, `beets-v*`, `picard-v*`). The
embedder's conformance job checks the ONNX front-end against `transformers`, which is
the drift guard for the whole corpus — two implementations disagreeing looks exactly
like two contributors disagreeing, and nothing distinguishes them after the fact.

Architectural changes go through the records in [`docs/decisions/`](docs/decisions/).
Read the relevant ones before changing anything they govern.

## Deployment

[`ADR-0003`](docs/decisions/ADR-0003-the-commons-runs-on-one-small-server.md) chose one
small AWS instance running both Postgres and the application, sized by index RAM rather
than corpus size, with the upgrade path written down. That is what runs: TLS via Caddy,
nightly `pg_dump` to S3, a disk alert that delivers to a phone. `docker-compose.aws.yml`
is the configuration and [`deploy/RUNBOOK.md`](packages/server/deploy/RUNBOOK.md) is
how it was brought up and how it is operated.

```bash
cd packages/server
cp deploy/env.example .env      # DOMAIN is required — Caddy needs it for a certificate
docker compose -f docker-compose.aws.yml up -d
docker compose -f docker-compose.aws.yml exec api uv run alembic upgrade head
sudo cp deploy/clapback-backup.{service,timer} /etc/systemd/system/
sudo systemctl enable --now clapback-backup.timer
```

Backups are part of shipping rather than a follow-up, per `ADR-0003` point 6: the corpus
is contributed data nobody here can rebuild.

**The API is the only way in.** Every guarantee the corpus makes — confirmability,
revocation, the row ceiling, agreement recording — is code on the write path, so a
direct database connection is a second write path with none of them. The deployed
compose file exposes no database port.

### Locally, or on a NAS

```bash
cd packages/server
docker compose up -d                              # development, publishes Postgres on 5433
docker compose -f docker-compose.omv.yml up -d    # the NAS deployment this ran on first
docker compose exec api uv run alembic upgrade head
curl http://localhost:8000/health
```

## Configuration

Environment variables (prefix: `CACHE_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection URL |
| `CACHE_DB_DISABLE_SSL` | `false` | For a self-hosted Postgres with no TLS |
| `CACHE_LOOKUP_RATE_LIMIT` | `300/minute` | Reads, per address |
| `CACHE_CONTRIBUTE_RATE_LIMIT` | `30/minute` | Writes, per address — contributions and claims |
| `CACHE_MAX_EMBEDDINGS` | `500000` | Row ceiling, checked on write (`ADR-0004` point 9); 0 disables |
| `CACHE_ADMIN_PASSWORD` | — | The admin dashboard; unset disables it |
| `CACHE_DEBUG` | `false` | Debug logging |

## Architecture

- **Layout**: a `uv` workspace — four published packages and the server. Neither the
  root nor any package is the whole.
- **API**: FastAPI + uvicorn, SQLAlchemy 2.0 async, Alembic migrations.
- **Database**: PostgreSQL with pgvector; `(fingerprint_hash, pipeline_version)` is the
  key, HNSW over the vectors, and recording ids in a claims table beside them.
- **Hosting**: one AWS Lightsail instance, per `ADR-0003`. No auto-deploy; the runbook
  is the deploy.
