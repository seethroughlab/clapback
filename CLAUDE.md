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

**What is still owed, in execution order:**

| ADR-0001 item | Decision | Why here |
|---|---|---|
| 5 | The tool's local features | `ADR-0001` point 8's draw, and the only thing that produces a second contributor |
| 4 | The recording-id key | `ADR-0002` point 4 makes it a prerequisite: similarity search over a hash-keyed corpus returns hashes nobody can resolve |
| 6 | The rename, and what the domain serves | Cheap, and last on purpose — nothing above depends on it |

**Two launch blockers are decided but unbuilt**, both from `ADR-0003` point 7: a public endpoint
needs TLS in front of it, and it must not accept anonymous writes. `ADR-0004` decides how the second
is answered; none of it is implemented beyond `client_id` being accepted and stored.

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
