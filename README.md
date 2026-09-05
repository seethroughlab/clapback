# clapback

A public commons of **CLAP audio embeddings**, and the reference implementation that
produces them.

An embedding is a 512-dimensional vector describing what a recording *sounds like*.
Computing one costs seconds of CPU and a 600 MB model; comparing two is a dot product.
So it is worth computing once and sharing — provided everybody computes the same thing.

That proviso is the whole design. `clapback-embed` exists so there is exactly one
implementation: if two contributors disagree about a recording, the disagreement is
about the audio, not about whose code ran.

**Deployment:** self-hosted. The instance backing Familiar runs on the same machine
as it, reached over a shared Docker network. There is no public endpoint at present —
`familiar-cache.fly.dev` was retired when the service moved off Fly, and the DNS name
no longer resolves.

## The package

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
rule, the pooling, the checkpoint and the precision.

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

Early, and worth being plain about:

- **21,890 embeddings**, contributed by **9 addresses**, of which one accounts for
  **99.85%**. It is not yet a commons; it is one library and a handful of visitors.
- The corpus is keyed on the SHA256 of an AcoustID fingerprint, so it can answer
  "here is the embedding for a track you have" and *not* "what does this record I do
  not own sound like". Fixing that needs a recording id as a second key — decided
  in Familiar's `ADR-0102`, not yet built here.
- The `features` endpoints below still work and still hold 77,770 rows.
  [`ADR-0001`](docs/decisions/ADR-0001-clapback-is-a-public-clap-embedding-commons.md)
  decided the commons carries **embeddings**, not the bpm/key/valence estimates that
  killed AcousticBrainz — so those endpoints are legacy, not direction.

[Familiar](https://github.com/seethroughlab/familiar) is the first client and largest
contributor. It is not the owner: the point of the package is that anything can
contribute.

## Privacy

- Only SHA256 hashes of audio fingerprints are stored (one-way, anonymous)
- No filenames, metadata, or personal information is transmitted
- The public browse pages (`/`, `/browse/{hash}`) only show those hashes and the analysis data keyed off them
- Contribution is opt-in via Familiar's Admin settings

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Public browse landing page (stats + paginated table) |
| `/browse/{hash}` | GET | Public detail view for a fingerprint |
| `/health` | GET | Basic liveness check |
| `/health/db` | GET | Database connectivity check |
| `/v1/embeddings/{hash}` | GET | Lookup CLAP embedding |
| `/v1/embeddings` | POST | Contribute an embedding |
| `/v1/features/{hash}` | GET | Lookup audio features |
| `/v1/features` | POST | Contribute audio features |

### Embeddings

#### GET `/v1/embeddings/{fingerprint_hash}`

Query params:
- `analysis_version` (int): Analysis pipeline version
- `clap_model_version` (string): CLAP model identifier

Response 200:
```json
{
  "fingerprint_hash": "abc123...",
  "embedding": [0.1, 0.2, ...],
  "analysis_version": 5,
  "clap_model_version": "laion/clap-htsat-unfused:v1",
  "pipeline_version": null,
  "contributor_count": 3
}
```

#### POST `/v1/embeddings`

Request body:
```json
{
  "fingerprint_hash": "abc123...",
  "embedding": [0.1, 0.2, ...],
  "analysis_version": 5,
  "clap_model_version": "laion/clap-htsat-unfused:v1",
  "client_id": "any-opaque-string-your-install-keeps",
  "pipeline_version": "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"
}
```

`client_id` and `pipeline_version` are both optional and both worth sending. Without a
`client_id` a submission cannot count toward independent agreement
([`ADR-0004`](docs/decisions/ADR-0004-contributors-are-identified-but-not-accounts.md)
point 3). `pipeline_version` is `clapback_embed.PIPELINE_VERSION`, and it is the only
field that says whether your vector is comparable with anyone else's — the checkpoint
alone does not, since windowing or pooling can move every vector without changing it.
[`ADR-0006`](docs/decisions/ADR-0006-the-pipeline-identity-is-the-corpus-key.md) makes
it part of the key in a later phase, and it is optional until then. It is asserted
rather than proven: the server believes what you send, which catches the forgotten
bump and the stale build and is not a defence against a contributor who lies.

Every row in the corpus reports `"pipeline_version": null` today. Nothing recorded it
for the legacy rows, and no backfill would be honest — `ADR-0006` point 5 recomputes
them instead.

### Features (legacy)

`ADR-0001` point 4 decided the commons stores embeddings and not features. These
endpoints still work and the existing 77,770 rows are still served, but they are not
where this is going: bpm, key and valence are *claims about the world* that consensus
cannot verify, which is precisely what MetaBrainz identified when AcousticBrainz
stopped taking submissions. Familiar keeps its own private feature cache instead.

#### GET `/v1/features/{fingerprint_hash}`

Query params:
- `analysis_version` (int): Analysis pipeline version

Response 200:
```json
{
  "fingerprint_hash": "abc123...",
  "analysis_version": 5,
  "features": {
    "bpm": 120.5,
    "key": "C",
    "energy": 0.8,
    "danceability": 0.7,
    "valence": 0.6,
    "acousticness": 0.2,
    "instrumentalness": 0.9,
    "speechiness": 0.1,
    "liveness": 0.15,
    "loudness": -8.5
  },
  "contributor_count": 2
}
```

#### POST `/v1/features`

Request body:
```json
{
  "fingerprint_hash": "abc123...",
  "analysis_version": 5,
  "features": {
    "bpm": 120.5,
    "key": "C",
    "energy": 0.8
  }
}
```

Response: 201 (created) or 200 (confirmed, contributor count incremented).

## Development

The repository is a `uv` workspace of peer members (`ADR-0005`): `packages/embed` is the
published library, `packages/server` is the commons. The root builds nothing.

```bash
# Install everything, from the root
uv sync

# The server
cd packages/server
CACHE_DATABASE_URL="postgresql+asyncpg://cache:cache@localhost:5432/cache" \
  uv run uvicorn app.main:app --reload
uv run pytest
docker compose up          # includes PostgreSQL

# The library
cd packages/embed
uv pip install -e '.[dev]'
pytest                     # add -m artifacts once the encoders are exported
```

## Deployment

[`ADR-0003`](docs/decisions/ADR-0003-the-commons-runs-on-one-small-server.md) chose one
small AWS instance running both Postgres and the application, sized by index RAM rather
than corpus size, with the upgrade path written down. `docker-compose.aws.yml` is that
configuration; nothing is deployed to it yet.

The first public step is deliberately partial.
[`ADR-0003`](docs/decisions/ADR-0003-the-commons-runs-on-one-small-server.md) point 7
allows **reads served publicly while writes stay restricted**, and forbids the reverse —
so Caddy refuses writes at the edge and Familiar keeps contributing over the private
network. That restriction is topological rather than a new authentication scheme, and it
is one `respond` line to delete once
[`ADR-0004`](docs/decisions/ADR-0004-contributors-are-identified-but-not-accounts.md)
is built.

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

### Locally, or on a NAS

```bash
cd packages/server
docker compose up -d                              # development, publishes Postgres on 5433
docker compose -f docker-compose.omv.yml up -d    # the NAS deployment this ran on
docker compose exec api uv run alembic upgrade head
curl http://localhost:8000/health
```

## Configuration

Environment variables (prefix: `CACHE_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection URL |
| `CACHE_LOOKUP_RATE_LIMIT` | `100/minute` | Rate limit for lookups |
| `CACHE_CONTRIBUTE_RATE_LIMIT` | `10/minute` | Rate limit for contributions |
| `CACHE_DEBUG` | `false` | Enable debug logging |

Fly.io also reads `DATABASE_URL` and converts `postgres://` to `postgresql+asyncpg://` automatically.

## Architecture

- **Layout**: a `uv` workspace — `packages/embed` (the published library),
  `packages/server` (the commons). Neither is the repository root.
- **API**: FastAPI + uvicorn
- **Database**: PostgreSQL with pgvector extension
- **Hosting**: self-hosted today; [`ADR-0003`](docs/decisions/ADR-0003-the-commons-runs-on-one-small-server.md)
  chose one small AWS instance, and nothing is deployed there yet
- **CI**: `embed-ci.yml` lints, tests and checks the embedder against `transformers`;
  `server-ci.yml` lints and tests the server; `embed-release.yml` publishes on an
  `embed-v*` tag. There is no auto-deploy.
