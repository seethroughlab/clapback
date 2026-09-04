# Clapback

A public commons of CLAP audio embeddings, and the reference implementation that produces them. An
embedding is a 512-dimensional vector describing what a recording sounds like: expensive to compute,
trivial to compare, and therefore worth computing once and sharing — provided everybody computes the
same thing.

## Architecture

- **Server** (`app/`): FastAPI + PostgreSQL (pgvector) + SQLAlchemy 2.0 async, Alembic migrations.
  Keeps its own dependencies; adding audio libraries to the repository must not add them to the
  deployed image.
- **Embedder** (`packages/embed/`): `clapback-embed`, the reference implementation. ONNX Runtime
  only — no `torch`, no `transformers` at runtime. Published for others to depend on.
- **Corpus**: embeddings keyed on the SHA256 of an AcoustID fingerprint, stored as `Vector(512)`.
- **Deployment**: self-hosted. See `ADR-0003` for where it is going.

`PIPELINE_VERSION` in `packages/embed/src/clapback_embed/__init__.py` is the identity of the whole
pipeline — front-end, windowing, pooling, checkpoint, precision. Two vectors are comparable only if
it matches. It is not the checkpoint: a change to windowing or pooling moves every vector while
`laion/clap-htsat-unfused` stays fixed.

## Architecture Decisions (ADRs)

**Architectural changes are made through ADRs in `docs/decisions/`. Read the relevant ones before
changing anything they govern, and propose a new one before making a decision they don't cover.**

An ADR is warranted when a change sets a direction rather than implements one: a change to the
pipeline identity or the stored precision, a new key or endpoint the corpus is built around, moving
responsibility between the package and the server, a new external dependency or protocol, where and
how the commons is hosted, or reversing something an existing ADR decided. Ordinary feature work,
bug fixes, and refactors inside an established direction do not need one — they just need to respect
the ADRs already in force.

### Convention

- Filename `ADR-NNNN-kebab-case-title.md`; heading `# ADR-NNNN: Title Case`.
- `Status:` (`proposed` → `accepted`; also `superseded by ADR-NNNN` / `rejected`) and `Date:` lines.
  Supersession can be partial, and is recorded that way: `accepted — points 1 and 5 superseded by
  [ADR-NNNN](...)`. This is what numbering the Decision points buys.
- Optional `Implementation:` block, added as work lands, recording what shipped and what turned out
  differently — an accepted ADR stays a living record, not a snapshot.
- Optional `Extends [ADR-NNNN](ADR-NNNN-slug.md)` links after the header.
- Sections in order: `## Context`, `## Decision` (numbered points once non-trivial),
  `## Alternatives Considered`, `## Consequences` (bulleted, tagged **Positive** / **Tradeoff** /
  **Follow-up**).
- The directory holds only ADRs — no README, no template file.

### Rules

1. **One decision per ADR**, decomposed so each can be planned, approved, and executed on its own.
   Propose the set together; note the execution order, which often differs from the numbering.
2. **New ADRs start `Status: proposed`** and flip to `accepted` only when that specific ADR is
   approved. Never write one straight to `accepted`.
3. **`## Alternatives Considered` must contain real rejected options with real reasons.** Strawmen
   make the record worthless.
4. **Verify every metric, file path, and line number cited** against the repo at write time. ADRs
   are read months later as fact.
5. **Date every measurement**, in the sub-heading that carries it — "What is actually deployed,
   measured 2026-09-03". A figure without a date is indistinguishable from a figure that is now
   wrong, and this corpus is one where the numbers are the argument.
6. **Record contradicted premises in `## Context`.** If investigation disproved the original
   rationale, say so, so nobody re-derives it. `ADR-0002` found that the Fly deployment `ADR-0001`
   relied on had already been destroyed, and says so.
7. **Never edit an accepted ADR's Decision to reflect a change of mind** — supersede it with a new
   ADR and update the old one's `Status:`.
8. **Titles assert.** "The Corpus Answers Similarity Queries", not "Similarity search". A reader
   should learn the decision from the filename.

### Current set and execution order

`ADR-0001` is framing: it says the project is a public commons rather than a Familiar feature, that
the corpus carries embeddings and not the estimates that killed AcousticBrainz, and that the tool
must be worth running with the corpus empty. It decides no schema and no endpoint, and defers six
decisions in an order that differs from their numbering.

`ADR-0002`–`ADR-0004` answer three of them:

| # | ADR | Answers |
|---|---|---|
| 0002 | The corpus answers similarity queries | Makes ANN a hosting requirement rather than an implementation detail, and pins stored precision as a corpus decision |
| 0003 | The commons runs on one small server | One AWS Lightsail box for app and Postgres, sized by index RAM, with the upgrade path written down |
| 0004 | Contributors are identified, but not accounts | `ADR-0001`'s deferred item 3: self-issued client identifiers, revocation, deletion, and write bounds |

`ADR-0005`–`ADR-0008` were accepted 2026-09-04. `ADR-0005` answers no deferred item but blocks the
one that matters most: it makes the embedder a published peer of the server rather than a
subdirectory of it, which is what `ADR-0001` point 3's "published for others to depend on" requires
and what deferred item 5's tool will need a home beside. **Only `ADR-0005` is built** — the other
three are decided and outstanding, and each says so in its `Implementation:` block.

| # | ADR | Answers |
|---|---|---|
| 0005 | The repository is a workspace of peers | The restructure, publishing `clapback-embed` to PyPI, and the rule separating package version from pipeline identity |
| 0006 | The pipeline identity is the corpus key | `ADR-0005`'s follow-up: `(fingerprint_hash, pipeline_version)` replaces a key made of a checkpoint and a client's counter, and the 21,890 legacy rows are recomputed rather than relabelled |
| 0007 | A pipeline proves itself on a reference signal | `ADR-0001` point 4's attestation: a client demonstrates its pipeline on a signal defined arithmetically, and the expected vector is agreed by quorum rather than configured. **Decided, deliberately unbuilt** — see its points 10 and 11 |
| 0008 | The corpus serves agreement, not a verdict | `ADR-0001` point 9's confidence and deferred item 2's remainder: independent confirmations, contradictions and the worst similarity are reported; no threshold declares a vector trustworthy |
| 0009 | The tool is useful before the corpus is | `ADR-0001` deferred item 5: a local library index that searches by description and finds near-duplicates, with contribution as a byproduct — the only queued work whose output is a contributor who was not already here |

`ADR-0009` answers deferred item 5, which nothing else can: four accepted records —
`ADR-0004` point 4, `ADR-0007`, `ADR-0008` and `ADR-0002` — are each waiting on a second contributor,
and the tool is the only queued work that produces one.

`ADR-0008` closes `ADR-0001` deferred item 2 — the part the cross-machine measurement did not answer,
being where the agreement threshold sits and what the corpus does with it.

**What is still owed, in execution order:**

| ADR-0001 item | Decision | Why here |
|---|---|---|
| 4 | The recording-id key | `ADR-0002` point 4 makes it a prerequisite: similarity search over a hash-keyed corpus returns hashes nobody can resolve |
| 6 | The rename, and what the domain serves | Cheap, and last on purpose — nothing above depends on it |

### What is actually running, as of 2026-09-04

The commons is public at **https://clapback.seethroughlab.com** — one AWS instance, TLS via Caddy,
nightly `pg_dump` to `s3://clapback-backup`, and a corpus being replaced in place by a backfill from
Familiar. All three members of `ADR-0001` point 3 now exist: `clapback-embed` on PyPI, the server
deployed, and the tool in `packages/cli`.

Shipped since the records were written: `ADR-0002`'s similarity endpoint (HNSW, ~3 ms),
`ADR-0003`'s deployment and backups, `ADR-0004` point 7's delete path and point 9's row ceiling,
`ADR-0005`'s restructure and PyPI release, and `ADR-0009`'s tool.

**Still unbuilt, and the first is the one with consequences:**

- **`ADR-0004` point 9's disk alert.** "A full disk is an outage; 80% of one is a Tuesday
  afternoon." The row ceiling bounds growth; nothing watches the disk.
- **`ADR-0006` entirely** — the pipeline identity as the corpus key, and the four-phase migration
  that makes the corpus coherent. The corpus still holds 21,890 middle-10s vectors from the pipeline
  `ADR-0104` rejected.
- **`ADR-0007`**, deliberately, until a second contributor exists.
- **`ADR-0008`** — the corpus cannot yet tell anyone how corroborated a vector is.
- **`ADR-0009` point 6** — the tool does not contribute yet; only its local half is built.

**The pattern worth keeping.** For most of this project's life the decisions ran far ahead of the
code, and the `Implementation:` block is the only thing that kept that legible. Write one the day
work starts, and correct the record when building disproves it — several premises here were wrong
and are marked so rather than quietly overtaken.

## Compatibility with Familiar

[Familiar](https://github.com/seethroughlab/familiar) is the only client. `ADR-0001` point 1 says it
does not own this project; it does constrain it. **Check both surfaces before changing either, and
verify against the sibling checkout rather than from memory** — the audit in `ADR-0005` is dated and
will go stale.

**The package.** Familiar imports `embed_file`, `embed_text`, `embed_audio` and `PIPELINE_VERSION`
from the top level, and reaches into two submodules by path: `clapback_embed.artifacts`
(`audio_session`, `providers`, `model_dir`) and `clapback_embed.mel` (`SAMPLE_RATE`). Its
`backend/tests/test_embedder_delegation.py` stubs those module paths by name, so **the module layout
is part of the contract**, not an implementation detail. `ADR-0005` point 11 widens `__all__` to
match.

**The server.** Familiar calls `/v1/embeddings`, `/v1/features` and `/v1/analysis-detail` — GET and
POST on each — plus `/health`. **The features and analysis-detail endpoints are legacy for the
corpus but live for the client.** `ADR-0001` point 7 decided no existing feature rows migrate; that
is a statement about what the corpus carries, and it does not license removing the endpoints.

**The API is the only way in.** Clients and tools — Familiar, the CLI of `ADR-0001` point 3,
anything else — reach the corpus over HTTP and never over a database connection. Every guarantee the
corpus makes (confirmability, revocation, quotas, the row ceiling, agreement recording) is code on
the write path, so a direct `psql` connection is a second write path with none of them. `ADR-0005`
point 12 has the reasoning. The development compose file publishes 5432 for local work; the deployed
one exposes no port.

**The version that lives in two places.** `PIPELINE_VERSION` here and `EMBEDDING_VERSION` in
Familiar's `backend/app/config.py` are the same fact — the identity of the embedding pipeline —
maintained separately by hand. Moving one without the other contributes incomparable vectors under a
key asserting they are comparable. `ADR-0006` (`proposed`) makes `PIPELINE_VERSION` the key itself so
the case cannot arise, and phases the change so the endpoint contract never breaks. Until it lands,
nothing detects the drift, and **any change that moves `PIPELINE_VERSION` requires a matching bump in
Familiar in the same breath.**

## Development

```bash
uv sync
CACHE_DATABASE_URL="postgresql+asyncpg://cache:cache@localhost:5432/cache" \
  uv run uvicorn app.main:app --reload
uv run pytest

cd packages/embed && uv pip install -e '.[dev]' && pytest
```

`packages/embed` has its own `pyproject.toml`, lock file, virtualenv and CI
(`.github/workflows/embed-ci.yml`). Its conformance job checks the ONNX front-end against
`transformers`, which is the drift guard for the whole corpus — two implementations disagreeing
looks exactly like two contributors disagreeing, and nothing distinguishes them after the fact.
