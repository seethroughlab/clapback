# ADR-0018: The Corpus Answers "Which Recording Is This?"

Status: proposed

Date: 2026-09-15

Extends [ADR-0012](ADR-0012-a-contribution-can-name-its-recording.md) points 1, 5 and 8, and
[ADR-0015](ADR-0015-a-library-is-looked-up-in-batches.md). One of the five records proposed
together on 2026-09-15; the set and its order are in `ADR-0014` point 6.

## Context

`ADR-0001` said the corpus carries embeddings, not the metadata estimates that killed
AcousticBrainz, and `ADR-0012` added exactly one piece of metadata as a *claim*: a MusicBrainz
recording id, per client, counted and never verified. Its purpose was stated narrowly — so that
a similarity result is a title a person can read rather than a hash nobody can resolve. As of
2026-09-15, 23,196 of 25,886 rows (89.6%) carry one, and every read that returns a hash returns
the best-supported id beside it.

That is a second thing the corpus can answer, and it was not designed for it: **given a
fingerprint, what recording is this?** A tool holding a fingerprint and no MBID gets one back
from any lookup, for free, at 300 per minute, with a count of how many independent installs
agreed. KalinkaPlayer#128 named MusicBrainz enrichment as a bottleneck — one request per second
and frequent 503s — and did not know the lookup already carried an answer, because nothing says
so. Neither did this project until it re-read the response shape to answer him.

**Honesty about what this is and is not.** AcoustID answers the same question from the same
fingerprint, by *fuzzy* match against a database of millions, maintained for that purpose, and
its answers are curated. This corpus answers by *exact hash* match against rows contributors
happened to hold, and its answers are counted assertions (`ADR-0008`) that one install could
have got wrong. It is a shortcut that works when the corpus holds the recording and is silent
otherwise — never a replacement for AcoustID, and this record must not let a tool treat it as
one. What it has that AcoustID does not: no key, no per-second limit, and the answer comes with
the vector.

### What a lookup returns today, measured 2026-09-15

`EmbeddingResponse` carries `recording_mbid` — the id with the most distinct clients behind it,
ties broken on the id text so the choice is total (`_recordings_for` in
`packages/server/app/api/routes.py`) — and `recording_claims`, that count. With one contributor
the count is always 1. The response also carries 6 KB of vector the asking tool may not want.

## Decision

1. **The recording claims are served as an answer, and the API says what kind of answer they
   are.** `/api` and the client README gain a section "which recording is this?" that states,
   in this order: it works only for recordings the corpus holds; the id is the one most
   independent installs asserted, and `recording_claims` is how many; it is not verified and
   not AcoustID; and a tool that gets a count of 1 has been told that one install said so.

2. **`recording_claims` counts distinct `client_id`s, and a read can ask for all of them.**
   `GET /v1/recordings/by-hash/{fingerprint_hash}` returns every claimed id for the row with
   its distinct-client count, most-supported first, and no vector. A tool that wants the
   dissent — two ids, one with 3 installs and one with 1 — sees it here; the lookup's single
   `recording_mbid` stays the summary. This is `ADR-0012` point 5 completed in the direction it
   pointed.

3. **`ADR-0015`'s batch lookup with `vectors: false` is the whole-library form of this
   question**, and the section in point 1 says so: a hundred hashes in, a hundred
   `(mbid, claims)` out, under 20 KB.

4. **A claim from the corpus is never re-contributed as a claim.** A tool that learns an id here
   and sends it back as its own claim would count itself as independent confirmation of what
   it copied, which is the manufactured agreement `ADR-0004` point 4 and `ADR-0008` exist to
   exclude. `Corpus.claim`'s docstring and the section in point 1 say: claim only what you
   established yourself — from your tags, from AcoustID, from Picard — never what a lookup
   told you. The server cannot enforce this; the contract says it.

5. **Not decided here:** any fuzzy or partial match, any resolution of an id to a title on the
   server (`ADR-0012` point 3 stands, and its rejected alternative "resolve ids server-side"
   stays rejected — the browser resolves names), and any ranking of
   *releases* under a recording (`ADR-0012` point 10).

## Alternatives Considered

- **Say nothing; it already works.** Rejected: an answer nobody knows exists is not an answer, and
  the first tool author to need it did not find it. The cost of documenting is a section; the
  cost of not documenting was a paragraph in a thread that nearly missed it.
- **Verify claims against MusicBrainz or AcoustID server-side before serving them.** Rejected,
  as `ADR-0012` point 3 rejected server-side derivation: the server would then be an AcoustID
  client with a rate limit and an opinion, and the corpus would be asserting rather than
  counting. Verification is the reader's job, with the count as the evidence.
- **Serve only claims with two or more independent installs.** Rejected for now: with one
  contributor that serves nothing, and a count of 1 labelled as a count of 1 is more useful
  than silence. Revisit when `ADR-0008`'s agreement data exists and a threshold can be measured
  rather than chosen.
- **Position it as an AcoustID alternative for tools without a key.** Rejected in the Context.
  An exact-hash lookup over one contributor's library is not a fingerprint service, and saying
  it is would earn the project the reputation it does not want.

## Consequences

- **Positive** — for recordings the corpus holds, a tool gets an id with a confidence count and
  no rate limit worth mentioning, which is a concrete answer to the one bottleneck KalinkaPlayer
  named that this project can do anything about.
- **Positive** — dissent between claims becomes visible, which is `ADR-0008`'s "serve
  agreement, not a verdict" applied to ids.
- **Tradeoff** — a tool that ignores point 4 pollutes agreement counts, and the server cannot
  tell. The contract is the only defence, and `ADR-0007`'s attestation, when built, does not
  cover this either. Written down so the risk is known rather than discovered.
- **Tradeoff** — the more useful this is, the more the corpus is asked for metadata, which is
  the road `ADR-0001` said not to walk. The line is: one identifier, from contributors, counted.
  Anything past that is a new record.
- **Follow-up** — the section in point 1 belongs in the "for plug-in authors" FAQ `ADR-0014`
  names.
