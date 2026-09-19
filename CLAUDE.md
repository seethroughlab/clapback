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
- **Client** (`packages/client/`): `clapback-client`, the contract a tool follows to take part —
  canonical fingerprint hashing, lookup-before-contribute, `client_id`, backoff. **No dependency
  beyond the standard library**, so a tool with its own embedder can contribute without ONNX
  Runtime. The CLI imports it; it is the published surface for plug-ins (`ADR-0011`). It must be on
  PyPI before any `clapback-cli` release that depends on it.
- **beets plugin** (`packages/beets-clapback/`): `beets-clapback`, `ADR-0011` point 5's first
  integration — `absubmit` reborn. Ships only `beetsplug/clapback.py` into a namespace package it
  shares with every other beets plugin; **never add a `beetsplug/__init__.py`**. Releases after
  both `clapback-client` and `clapback-embed`.
- **Picard plugin** (`packages/picard-clapback/`): `ADR-0011` point 5's second integration, for
  Picard 2.6–2.13. Not on PyPI — a zip attached to a `picard-v*` GitHub release, because a Picard
  plugin is a file installed from Options → Plugins. It **carries a verbatim copy of
  `clapback_client`** (`scripts/sync_client.py`; a test fails when the copy drifts) because a
  bundled Picard cannot install packages. Lookup-only mode is the default and must keep working
  with no embedder present. Tests run against real Picard from PyPI, headless.
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
and what deferred item 5's tool will need a home beside. **`ADR-0005` and `ADR-0006` are built** (the latter fully deployed 2026-09-08); `ADR-0007` and
`ADR-0008` are decided and outstanding, and each says so in its `Implementation:` block.

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

**`ADR-0010` was accepted 2026-09-10 and is fully done as of 2026-09-13.** The corpus key must be a
function of the audio: `fingerprint_hash` was SHA256 of whatever string a client stored, and
Familiar stored the same AcoustID fingerprint in two encodings — 14,284 hex-escaped, 11,364 raw —
both live keys in the corpus. **No server migration could fix it**: the server holds a one-way
digest and never sees a fingerprint, so only a client could re-key, and the server's schema and key
were never touched. Both clients now hash canonically (Familiar's `ADR-0114`), Familiar re-contributed
14,192 rows under canonical keys, and the 14,246 stranded old keys were deleted through the admin
API. Every count was predicted before it was measured; the Implementation block has them.

**`ADR-0011` was accepted 2026-09-13**, and is the first record about who the commons is *for*. It says the commons is the product and `clapback-cli` is the reference client, not the
acquisition channel: the tools people already run — beets and Picard first, both of which shipped and
then lost exactly this fetch-and-submit pair when AcousticBrainz died — are where a second
contributor comes from, via a stdlib-only `clapback-client` package. On acceptance it partially
supersedes `ADR-0009`'s account of how a contributor arrives; `ADR-0009` points 2 to 9 stand. Its
execution order is the client package, then the beets plugin, then the recording-id key, then Picard,
then outreach.

**`ADR-0012` was accepted 2026-09-13.** It answers `ADR-0001` deferred item 4, the
recording-id key: a recording id is a *claim* per client in a new `recording_claims` table, not a
column on the row, because the server cannot verify it and `ADR-0008` says the corpus counts
assertions rather than trusting one. The identifier is the MusicBrainz **recording** MBID — beets'
`mb_trackid` is one despite its name; Familiar's `musicbrainz_track_id` is unconfirmed and covers
6.8% of its library, unchanged since August. Ids will come from the plug-ins, and from a
Familiar backfill through AcoustID once the claims endpoint exists to receive it.

**`ADR-0013` was accepted 2026-09-15, the day it was proposed, and points 1–6 were built, released and deployed the same day; only point 7's import script is owed.** It says the corpus is public *data*, not just
a public endpoint: the rows are CC0 (as MusicBrainz's and AcousticBrainz's were), contribution is
stated as a CC0 dedication where the switch is, a weekly export that is not the backup goes to a
public bucket with `client_id` and every address stripped, and self-hosting from an export is the
one sanctioned direct-database write. Prompted by the first prospective second contributor asking,
in KalinkaPlayer#128, whether the data would outlive the box. Its point 8 has the execution order;
the licence sentence goes first.

**`ADR-0014`, `ADR-0015`, `ADR-0016` and `ADR-0018` were accepted 2026-09-16 and built the same
day, and deployed with `clapback-client` 0.3.0 on PyPI by ~01:30 UTC; `ADR-0017` was rejected
(below).** Five records from the first prospective second
contributor's six questions (KalinkaPlayer#128), in three groups. *Pipelines are legible:*
`ADR-0014` — an identity string's five tokens, and `GET /v1/pipelines`; its Implementation block
records that `artifact1` in the reference string is the ONNX export version, not the windowing
rule the Decision says it is. *Library scale:* `ADR-0015` batch lookup (100 keys, **hashes or
recording MBIDs mixed** by `ADR-0019` point 3, the limit counted per key) and `ADR-0016` batch
contribute (same per-row code, non-atomic, `client_id` required, and `ADR-0004` point 9's
per-client quota built first — 50,000 rows a day). *What a tool brings and gets back:*
`ADR-0017` — `fingerprint_pcm()`, conditional on a measurement; `ADR-0018` — the recording claims
served as "which recording is this?" with the honest line, its by-hash route serving `ADR-0019`'s
cross-key join. **A premise fell while building `ADR-0015`:** `slowapi` keys rate-limit windows
on the request *path*, so `GET /v1/embeddings/{hash}` is limited per hash, a library scan never
hits it, and "33 minutes for 10,000 tracks" was wrong — round trips bind, at 72 ms median to the
commons. Recorded in `ADR-0015` and `ADR-0004`.

**`ADR-0017` was rejected on 2026-09-16 by its own measurement, and the measurement found
something bigger** — recorded in `ADR-0010`'s Implementation block: the AcoustID fingerprint
*string* is not reproducible across fingerprinting paths. `fpcalc` and pyacoustid's library path
agree on 24 of 56 FLACs (10 of 24 CD-quality ones); `fpcalc` 1.5.1 and 1.6.1 on 37 of 56. One bit
is a new key, so a second client on another path lands on a different row more often than not,
and no agreement is recorded. **`ADR-0019` was accepted 2026-09-16 to fix it, and its points 1 and 3 are built**: the hash stays the row key,
but agreement, independence and confirmation are counted per *recording* — across keys — through
`ADR-0012`'s claims; tools that hold an MBID look up by it first; similarity collapses rows
sharing a recording; AcoustID track ids are admitted as a second claim type. Pinning `fpcalc`,
keying on the AcoustID id, and server-side fuzzy matching are each rejected with reasons.
**Points 1, 2 and 3 are built and deployed (2026-09-16)** — the documentation,
`Corpus.lookup(recording_mbid=)` in the client, and the cross-key agreement: migration `014`, the
comparison in `_contribute_one`, and `recording_confirmations` / `recording_contradictions` on every
read that names a recording, backed by the suite's first database test (server CI now runs
pgvector). **Point 4 too** (same day): `/v1/similar` folds named rows sharing a recording into the
nearest and reports `collapsed` — and deploying it exposed that HNSW had capped every `limit` above
40 at 40 since the index was built (`hnsw.ef_search`, now set per query). **And point 6** (same
day, migration `015`): the AcoustID track id is a second claim type, `claim_type` is part of the
claims key, the join runs on either id, and `clapback-client` 0.4.0 carries it. **Every point of
`ADR-0019` but 5 is deployed**; point 5 waits on `ADR-0007`. The three plug-in releases that carry
the ids and the batch calls went out the same day.

**`ADR-0020` was proposed, accepted and built 2026-09-19 (points 1–7 and 9); migration `016` is
not yet deployed and `clapback-client` 0.5.0 is not yet released.** The prospective second
contributor (KalinkaPlayer#128, third comment) holds vectors and MusicBrainz recording ids for tracks
it never fingerprinted and asked to contribute them as they are. A contribution with an MBID and no
fingerprint is keyed on the recording — `SHA256("musicbrainz_recording:" + mbid)` in the existing key
column, a `key_type` column saying so on every read and in the export, a self-claim so `ADR-0019`'s
join and collapse apply with no new read code — and a client holding a fingerprint keys on it,
always. It qualifies `ADR-0010`'s title without superseding any point of it. The self-claim needed
no new code: `_record_claims` already wrote one for every id a request named. The same
thread's cross-checkpoint measurement (`packages/embed/scripts/measure_crosspipe.py`, results dated
2026-09-19 beside it) found the two checkpoints orthogonal and a fitted linear bridge crossing them
at R@10 0.99 / top-10 overlap 0.56 — a published per-identity-pair bridge is a live question for a
new record.

**`ADR-0021` was proposed 2026-09-19, not accepted.** The corpus fits a linear bridge between every
pair of identities sharing ≥ 200 recordings (Procrustes below 1,000 pairs, ridge above), publishes
each as a 1 MB matrix in the weekly export with its held-out metrics and never a verdict, lists them
at `GET /v1/bridges`, and — second, after one export cycle — lets `/v1/similar` take `across=true`
with every translated neighbour labelled. Translated vectors are never stored. It answers
`ADR-0002`'s reference question by not needing one. Nothing can be built until a second identity
shares recordings with the first: zero cross-identity pairs exist.

`ADR-0008` closes `ADR-0001` deferred item 2 — the part the cross-machine measurement did not answer,
being where the agreement threshold sits and what the corpus does with it.

**What is still owed, in execution order:**

| ADR-0001 item | Decision | Why here |
|---|---|---|
| 4 | The recording-id key | `ADR-0002` point 4 makes it a prerequisite: similarity search over a hash-keyed corpus returns hashes nobody can resolve |
| 6 | The rename, and what the domain serves | Cheap, and last on purpose — nothing above depends on it |

### What is actually running, as of 2026-09-16

The commons is public at **https://clapback.seethroughlab.com** — one AWS instance, TLS via Caddy,
nightly `pg_dump` to `s3://clapback-backup`, and a corpus of **25,886 rows, every one of which
declares the pipeline that produced it and is keyed on a hash any client can reproduce from the
audio — and 23,196 of which (89.6%) name their MusicBrainz recording**, up from 6.8% on
2026-09-14, after Familiar's `ADR-0115` backfill. Four packages are on PyPI (`clapback-embed` 0.1.0,
`clapback-client` 0.4.0, `clapback-cli` 0.2.0, `beets-clapback` 0.3.0) and the Picard plugin ships as a zip on a
`picard-v*` release (`picard-v0.2.0`). The bare name `clapback` on PyPI belongs to an unrelated 2018 package; the
CLI is `clapback-cli`.

The corpus went 25,558 → 39,761 → 25,515 across 2026-09-08 to 09-13. The middle number is
`ADR-0010`'s re-contribution; the net −43 is the duplicate collapse the encoding split had hidden.

**`ADR-0006` is fully deployed as of 2026-09-08.** The key is `(fingerprint_hash,
pipeline_version)`, `pipeline_version` is `NOT NULL`, and a contribution that does not declare one
is refused with a `422`. Migration `011` removed the 47,486 rows that could not say what produced
them. The corpus is therefore smaller than it has ever been and is the first version of it where
the README's premise — that a disagreement is about the audio rather than about whose code ran —
is actually true.

Shipped since the records were written: `ADR-0002`'s similarity endpoint (HNSW, ~3 ms),
`ADR-0003`'s deployment and backups, `ADR-0004` point 7's delete path and point 9's row ceiling,
`ADR-0005`'s restructure and PyPI release, **all four phases of `ADR-0006`** and point 7's guard,
and `ADR-0009`'s tool — including **point 6, contribution** (2026-09-10), which makes the tool the
first client to key on the audio rather than on what it stored.

**`ADR-0004` point 9's disk alert is built and now actually delivers** (2026-09-10). Worth knowing
why that is two claims: the timer had been enabled and exiting 0 every fifteen minutes since
2026-09-04 with no delivery channel configured, so it was disk *detection* and a journald line on a
box nobody reads. A webhook (`DISK_ALERT_NTFY_URL`) now carries it, delivery happens before the
latch is written, and a failed send fails the unit instead of latching silently. `deploy/RUNBOOK.md`
section 9 has the commands that exercise all three branches without waiting for a full disk — **a
green timer is evidence the check ran, not evidence anyone would hear it.**

**Still unbuilt:**

- **`ADR-0007`**, deliberately, until a second contributor exists.
- **`ADR-0008`**, partly — confirmations and contradictions are served per recording since
  2026-09-16; the worst similarity and point 7 are not.
- **`ADR-0013` point 7** — the import script.

**The commons box is at `6f3dbe5` and migration `015` as of 2026-09-16 ~02:55 UTC; the site
wording in `95fd2e0` is not yet deployed.** All three plug-ins were released 2026-09-16 on
`clapback-client` 0.4.0: they look up by recording id, then AcoustID id, then hash; send and
claim both ids; and use the batch calls (Picard contributes as it scans, on the single call).

**`ADR-0011` point 5's outreach went out 2026-09-15** — beets#7032 and picard-plugins#436 (the
listings), dj-track-similarity#2 and KalinkaPlayer#128 (proposals, PR offered). Its
Implementation block has the links and the one thing the code survey changed: both proposals
ask for contribution *under their own pipeline identity*, not a checkpoint swap. A yes on
either proposal commits this project to a ~50-line PR in that codebase. **beets#7032 was
closed the same day**: too new to list, and beets' `AI_POLICY.md` forbids agent-drafted PRs,
which it was. The Implementation block records what that corrected — **messages to
maintainers are written by Jeff, AI use is disclosed where a project asks, and a project's AI
policy is read before anything is sent.**

**The bottleneck is a second contributor, not a decision, and not a build.** Everything queued
that this project can do alone is done; the next step is somebody else replying.

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
key asserting they are comparable. `ADR-0006` made `PIPELINE_VERSION` the key itself so
the case cannot arise, and **all four phases are deployed as of 2026-09-08**: the server stores the
declared `pipeline_version`, keys on it, reports it, and refuses a contribution without one;
Familiar reads it from the installed embedder rather than from a constant, so its copy cannot drift
from the embedder that actually ran.

**What this does and does not fix.** Two vectors can no longer collide under a key that asserts
they are comparable — that case is now structurally impossible rather than merely unlikely. But the
server still believes what it is told (point 8): a client that declares a pipeline it did not run
is accepted, and `ADR-0007`'s attestation is what would catch that, deliberately unbuilt until a
second contributor exists. `EMBEDDING_VERSION` also still exists in Familiar as its own counter for
driving re-analysis; it is no longer a claim about comparability, so it no longer has to move in
lockstep with `PIPELINE_VERSION` — but moving `PIPELINE_VERSION` still means Familiar must
recompute, because its old vectors will no longer match the key it now sends.

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
