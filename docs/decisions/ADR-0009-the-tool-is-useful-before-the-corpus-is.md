# ADR-0009: The Tool Is Useful Before the Corpus Is

Status: accepted

Date: 2026-09-04

Answers [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) deferred item 5 and fills
the directory [ADR-0005](ADR-0005-the-repository-is-a-workspace-of-peers.md) point 8 reserved without
deciding anything about it.

Implementation:
- Accepted 2026-09-04. **Nothing is built**, and `packages/cli/` does not exist — `ADR-0005` point 8
  reserved the name and deliberately created no directory, which is still the honest state.
- The four records waiting on this are unchanged: `ADR-0004` point 4 cannot count independence,
  `ADR-0007` cannot reach a quorum of two, `ADR-0008` reports zero confirmations, and `ADR-0002`
  justifies an endpoint nobody outside this project yet has reason to query. Point 3's search now
  exists and answers in 3 ms; what it lacks is a second party to ask it anything.
- Two of this record's premises were confirmed by building other things in the meantime. The
  brute-force measurement in the Context holds — the corpus is now HNSW-indexed for the *server's*
  similarity endpoint, and that index exists because 22,000 vectors live in Postgres rather than on
  a laptop. And point 5's chromaprint caution proved right for a reason the record did not
  anticipate: the fingerprint reaches storage hex-escaped rather than raw, and a tool that decoded
  it the obvious way would key every contribution to a hash nobody looks up.

## Context

`ADR-0001` point 8 is the whole brief:

> **The tool must be worth running with the corpus empty.** A donation client with no local value has
> no first contributor, and this project has measured proof that passive accumulation does not
> happen. What the tool does locally — search your own library by description, find duplicates across
> formats and masters — is the draw; contribution is a byproduct of it.

Everything below is an attempt to take that seriously rather than to build a submission client with a
feature bolted on.

### Nothing else in the project can produce a second contributor

Four accepted records are waiting on one that does not exist:

| record | what it cannot do with one contributor |
|---|---|
| `ADR-0004` point 4 | count independence — there is one client to count |
| `ADR-0007` points 10–11 | confirm a pipeline — K=2 and there is one attester |
| `ADR-0008` | report a confirmation — every row would read zero |
| `ADR-0002` | justify a public endpoint nobody has reason to query |

The corpus is 21,890 embeddings from 9 addresses, one of which is 99.85%. That is not a commons, and
no amount of further design makes it one. This record is the only queued work whose output is a
person who was not already here.

### What already exists, and what is missing

`clapback-embed 0.1.0` is published and does the expensive half: `embed_file` turns audio into a
vector, `embed_text` turns "dreamy ambient with piano" into a vector in the same space. That is the
part that took a pinned front-end, a windowing rule and a conformance test against `transformers`.

What is missing is unglamorous: somewhere to keep the vectors, a way to walk a directory, and two
queries. Nothing about it is research.

### Search does not need an index, and that is measured

The obvious assumption is that a library search needs an ANN index, which would mean `faiss` or
`hnswlib` — a wheel-compatibility problem in a tool meant to install cleanly. Measured 2026-09-04, a
plain `numpy` matrix-vector product over unit vectors:

| library | store | one query |
|---|---|---|
| 10,000 tracks | 20.5 MB | **0.2 ms** |
| 26,000 tracks | 53.2 MB | **0.3 ms** |
| 100,000 tracks | 204.8 MB | 1.4 ms |
| 500,000 tracks | 1.0 GB | 5.5 ms |

26,000 is the size of the library this project's first client actually analysed. A personal music
collection does not reach the row where this becomes interesting, and `ADR-0003` already reasoned
that an HNSW index matters at hundreds of thousands of *contributed* vectors — a server problem, not
a laptop one.

### Near-duplicate detection has a measured threshold, and did not always

Familiar's `ADR-0104` measured what happens to two rips of one recording under the two pipelines it
was choosing between. A 1.2-second difference in lead-in — well inside what two CD rips differ by —
moves a **middle-ten-seconds** embedding to **0.950**, which that record notes is "the same distance
as a genuinely different track". Under the whole-track chunked mean this project settled on, the same
pair sits at **0.9972 – 0.9995**.

So the capability `ADR-0001` point 8 named is only available because of a pipeline decision taken for
another reason. Duplicates across formats and masters are separable from different music by roughly
two orders of magnitude — but only with `pool1`, and the number belongs to the pipeline rather than
to the tool.

### The corpus key is the one thing that needs a native binary

The corpus is keyed on the SHA256 of an AcoustID fingerprint, which means contributing requires
`chromaprint`. That is a C library shipped as a platform binary, and it misbehaves: Familiar runs it
in an isolated subprocess specifically "to prevent C-level assertion failures (e.g. channel count
mismatches) from killing the analysis worker"
(`backend/app/services/analysis.py:700` in that repository).

Requiring it to *use* the tool would put a native dependency in front of the local value that is
supposed to be the draw. It is needed only to talk to the corpus.

## Decision

1. **The tool is a local index over the user's own library, and a client to the commons second.**
   `packages/cli/`, installed as `clapback`. It builds and maintains a store of vectors for audio
   files the user already has, and answers questions about them. Contribution is something it can
   also do.

2. **Two capabilities at launch, both named by `ADR-0001` point 8.** Search a library by description,
   using `embed_text` against the stored vectors; and find near-duplicates across formats and
   masters, using stored vectors against each other. Both are queries over data the tool already
   holds — neither needs the corpus, the network, or a fingerprint.

3. **Brute force, and no approximate-nearest-neighbour dependency.** The table above is the
   justification: a personal library is three orders of magnitude below where an index earns its
   packaging cost. If a user ever has a library where it does not, that is a good problem and a later
   record.

4. **The store is a local file the user owns, and nothing leaves the machine by default.** The tool
   works offline, end to end, with the corpus unreachable and contribution off. `ADR-0001` point 11
   made Familiar's contribution opt-in and off by default; the same applies here and for the same
   reason — a commons that acquires contributors by default acquires them without consent.

5. **Fingerprinting is optional and required only to talk to the corpus.** Without `chromaprint` the
   tool indexes, searches and de-duplicates exactly as well; it simply cannot look up or contribute,
   and says so plainly rather than failing. The local half must never depend on a native binary that
   is known to crash on malformed channel counts.

6. **When it does contribute, it sends what the records require.** A `client_id` per `ADR-0004`, and
   a `pipeline_version` per `ADR-0006` once that lands. A new client with no legacy has no excuse for
   contributing unattributably.

7. **It depends on `clapback-embed` from PyPI and reimplements nothing.** The whole argument for one
   implementation (`ADR-0001` point 3) is undone by a tool that quietly does its own windowing. It
   pins the same way `ADR-0005` point 4 requires of any client: a change that moves
   `PIPELINE_VERSION` is a deliberate upgrade, not a rebuild.

8. **The duplicate threshold is a default derived from measurement, and is adjustable.** The
   0.9972 – 0.9995 band above is what two rips of one recording look like under `pool1`. The tool
   ships a default inside it and lets the user move it, because "duplicate" is partly a judgement —
   a remaster is a different master and sometimes a different recording.

9. **What it is not.** Not a player, not a tagger, not a library manager, not a downloader. Familiar
   is all of those and this is not a second one. The tool does the two things a CLAP embedding makes
   uniquely easy and stops.

## Alternatives Considered

- **Build the local features into Familiar instead.** Much less work: Familiar already has a library,
  a database, embeddings for 26,000 tracks and a UI to put results in. Rejected because it produces
  no second contributor, which is the entire point. A capability that exists only inside one music
  player is available to the people already contributing and to nobody else, and `ADR-0001` point 1
  is explicit that Familiar is the first client and not the owner.

- **Ship an ANN index anyway** — `hnswlib` or `faiss` — so the tool scales without a later migration.
  Rejected on the measurement: it buys nothing below 500,000 tracks and costs a compiled dependency
  in a tool whose adoption argument is that it installs cleanly. `ADR-0003` reached the same
  conclusion for the server at a much larger scale and rejected a dedicated vector database for the
  same reason.

- **Make contribution the point and the local features the extra.** The honest description of what a
  commons wants, and it is what the project would build if `ADR-0001` point 8 had not been written.
  Rejected because it has already been tested: contribution has been available to Familiar users and
  the corpus has 9 addresses. Passive accumulation does not happen, and this record exists because
  that was measured rather than assumed.

- **Require `chromaprint` and key everything on fingerprints from the start.** Simpler — one code
  path, and every indexed track is corpus-ready. Rejected because it puts a crash-prone native binary
  in front of the first thing a new user does. The tool's job is to be worth running before any of
  this matters.

- **A GUI, or a web interface.** Better for browsing results, and search-by-description is a visual
  experience more than a textual one. Rejected as scope rather than as direction: it is a second
  product with a second set of platform problems, and the CLI is what proves anybody wants the
  capability at all.

- **Defer until the corpus is worth querying.** The order the project has taken with everything else.
  Rejected because the dependency runs the other way and `ADR-0002` already found this: nobody can
  contribute to something they cannot reach, and nobody reaches for a corpus that has nothing in it.
  The tool is the only end of the loop that can be started from a standing stop.

## Consequences

- **Positive** — the project acquires the thing four accepted records are blocked on. A second
  contributor makes `ADR-0008` able to report a number, `ADR-0007` able to confirm a pipeline, and
  `ADR-0004` point 4's independence count able to mean something.
- **Positive** — the local capabilities need no corpus, no server and no network, so the tool is
  useful on the day `ADR-0003`'s host does not exist yet, which is today.
- **Positive** — it is the first thing built on `clapback-embed` as a published package rather than
  as a subdirectory, which tests `ADR-0005`'s claim that anything can depend on it.
- **Tradeoff** — **this is the first user-facing product this project has had**, and it comes with
  everything that implies: installation on three platforms, an interface people have opinions about,
  and support questions. `ADR-0001` names attention as the scarce resource and this spends it.
- **Tradeoff** — the encoders are 614 MB and are not vendored, so a first run is a large download
  before anything works. That is inherited from `ADR-0005` and is the tool's problem to present well,
  not to solve.
- **Tradeoff** — point 5's split means two support stories: what works without `chromaprint` and what
  needs it. Worth it, and worth documenting rather than discovering.
- **Follow-up** — how the store is shaped and where it lives. Deliberately not decided here: point 3
  rules out an index dependency, and everything else is an implementation detail that should be
  chosen while writing it rather than in advance.
- **Follow-up** — whether the tool should read a library it did not index, such as Familiar's
  database. Tempting, and a coupling that `ADR-0005` point 12's boundary reasoning should be applied
  to before anyone does it.
- **Follow-up** — packaging and distribution. `pip install clapback` is the obvious answer and
  `pipx`/`uv tool` the obvious refinement, but a tool with a 614 MB model download may want something
  else, and that is a decision rather than a detail.
