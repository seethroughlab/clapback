# Familiar Cache

Community cache server for the [Familiar](https://github.com/seethroughlab/familiar) music player.

Stores and retrieves **CLAP embeddings** and **audio features** keyed by AcoustID fingerprint hashes. Allows users to share pre-computed analysis data, reducing analysis time from 10-30s to ~100ms per track.

**Live:** https://familiar-cache.fly.dev

## Privacy

- Only SHA256 hashes of audio fingerprints are stored (one-way, anonymous)
- No filenames, metadata, or personal information is transmitted
- Contribution is opt-in via Familiar's Admin settings

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
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
  "clap_model_version": "laion/clap-htsat-unfused:v1"
}
```

### Features

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

```bash
# Install dependencies
uv sync

# Run locally (requires PostgreSQL with pgvector)
CACHE_DATABASE_URL="postgresql+asyncpg://cache:cache@localhost:5432/cache" \
  uv run uvicorn app.main:app --reload

# Run with Docker (includes PostgreSQL)
docker compose up
```

## Deployment

### Fly.io (Production)

The cache is deployed on Fly.io with a Neon PostgreSQL database.

```bash
# Deploy (auto-deploys on push to main via GitHub Actions)
fly deploy

# Run migrations
fly ssh console -C "bash -c 'cd /app && uv run alembic upgrade head'"

# Check status
curl https://familiar-cache.fly.dev/health/db
```

### Self-hosted

```bash
# Docker Compose
docker compose up -d
docker compose exec api uv run alembic upgrade head

# Test
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

- **API**: FastAPI + uvicorn
- **Database**: PostgreSQL with pgvector extension
- **Hosting**: Fly.io (app) + Neon (database)
- **CI/CD**: GitHub Actions auto-deploy on push to main
