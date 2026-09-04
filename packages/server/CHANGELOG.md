# Changelog

All notable changes to Familiar Cache will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0-alpha.1] - 2026-02-20

First alpha release of Familiar Cache — a community cache server for sharing pre-computed audio analysis data.

### Added

- **Core API v1** — Embeddings, features, and analysis-detail endpoints (GET/POST)
- **CLAP embedding storage** — pgvector (512-dim vectors), keyed by AcoustID fingerprint hash
- **Audio features caching** — Flexible JSONB schema with backfill on contribution
- **Analysis detail caching** — Full structured analysis data (separate from features due to size)
- **Admin dashboard** — Session auth, IP stats, ban/flag management
- **IP ban middleware** — Async stats tracking with per-IP flag/ban controls
- **Per-IP rate limiting** — 300 lookups/min, 30 contributions/min
- **Health check endpoints** — `/health` and `/health/db`
- **Fly.io deployment** — GitHub Actions auto-deploy workflow
- **Docker Compose** — Local development environment
- **Alembic database migrations** — Schema versioning for PostgreSQL + pgvector

### Technical

- **Backend**: FastAPI + uvicorn async
- **Database**: PostgreSQL + pgvector + SQLAlchemy 2.0 async
- **Deployment**: Fly.io with Neon PostgreSQL

[Unreleased]: https://github.com/seethroughlab/familiar-cache/compare/v0.1.0-alpha.1...HEAD
[0.1.0-alpha.1]: https://github.com/seethroughlab/familiar-cache/releases/tag/v0.1.0-alpha.1
