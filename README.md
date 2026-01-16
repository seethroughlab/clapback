# Familiar Cache

Community embedding cache server for the [Familiar](https://github.com/seethroughlab/familiar) music player.

Stores and retrieves CLAP embeddings keyed by AcoustID fingerprint hashes, allowing users to share pre-computed embeddings and reduce analysis time from 10-20s to ~1s per track.

## Quick Start

```bash
# Start with Docker Compose
docker compose up -d

# Run migrations
docker compose exec api uv run alembic upgrade head

# Test health
curl http://localhost:8000/health
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Basic liveness check |
| `/health/db` | GET | Database connectivity check |
| `/v1/embeddings/{hash}` | GET | Lookup embedding by fingerprint hash |
| `/v1/embeddings` | POST | Contribute an embedding |

### GET `/v1/embeddings/{fingerprint_hash}`

Query params:
- `analysis_version` (int): Analysis pipeline version
- `clap_model_version` (string): CLAP model identifier

Response 200:
```json
{
  "fingerprint_hash": "abc123...",
  "embedding": [0.1, 0.2, ...],
  "analysis_version": 1,
  "clap_model_version": "laion/clap-htsat-unfused:v1",
  "contributor_count": 3
}
```

### POST `/v1/embeddings`

Request body:
```json
{
  "fingerprint_hash": "abc123...",
  "embedding": [0.1, 0.2, ...],
  "analysis_version": 1,
  "clap_model_version": "laion/clap-htsat-unfused:v1"
}
```

Response 201 (created) or 200 (already exists, contributor count incremented).

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

### openmediavault

```bash
rsync -av ~/Developer/familiar-cache/ root@openmediavault:/opt/familiar-cache/
ssh root@openmediavault "cd /opt/familiar-cache && docker compose -f docker-compose.prod.yml up -d"
```

## Configuration

Environment variables (prefix: `CACHE_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_DATABASE_URL` | `postgresql+asyncpg://cache:cache@localhost:5432/cache` | PostgreSQL connection URL |
| `CACHE_LOOKUP_RATE_LIMIT` | `100/minute` | Rate limit for lookups |
| `CACHE_CONTRIBUTE_RATE_LIMIT` | `10/minute` | Rate limit for contributions |
| `CACHE_DEBUG` | `false` | Enable debug logging |
