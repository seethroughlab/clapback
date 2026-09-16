# ADR-0015: A Library Is Looked Up in Batches

Status: accepted

Date: 2026-09-15

Implementation:
- **Accepted 2026-09-16**, with one amendment from a record accepted in between: `ADR-0019`
  point 3 says the batch endpoint accepts a mix of fingerprint hashes and recording MBIDs, and
  a tool that holds an id looks up by it first. Point 1 is built that way — each entry in the
  request names which kind of key it is, and each entry in the answer says which key it
  answered — rather than as hashes-only with ids added later. Nothing is built.
- **The Context's premise is wrong, measured 2026-09-16.** "A 10,000-track library takes 33
  minutes to look up" was arithmetic on the configured `300/minute`, not a measurement, and the
  limit does not bind a library scan: `slowapi`'s default `key_style="url"` keys each window on
  the request *path*, so `GET /v1/embeddings/{hash}` has one window per hash. Against a local
  server, 320 lookups of distinct hashes from one address drew no 429; 320 of one hash drew the
  first at the 301st. What binds a scan is the round trip: sequential single lookups to the
  deployed commons measured **72 ms median, 83 ms p90 (n=60, misses)** — 12 minutes for 10,000
  tracks, an hour for 50,000. The decision stands on its own Alternatives paragraph, which said
  the round trip was the shape and the limit was not the bottleneck; the headline number was
  wrong by a factor of three and is corrected here, not in the Context.
- **Built 2026-09-16.** **Deployed 2026-09-16 ~01:30 UTC** (box at `215cd15`, migration `013` applied, counts unchanged at 25,886 / 23,198 claims) and `clapback-client` 0.3.0 on PyPI the same hour, verified cold. Points 1–3 as amended: `POST
  /v1/embeddings/lookup` takes up to 100 `{fingerprint_hash}` or `{recording_mbid}` keys,
  answers in order with the key echoed, a hash by its own row and an id by the row most clients
  have claimed under it; `vectors: false` drops the floats. Measured locally, 100 held keys:
  **671 KB with vectors, 40 KB without** — the record's "under 20 KB" was an estimate and the
  key echo plus per-row metadata is twice it. Point 3 is `app/limiter.py`'s `charge()`: the
  handler spends `len(keys)` of the lookup limit against the batch route's own per-address
  window after parsing (a 422 costs nothing); the fourth batch of a hundred in a minute is a
  429 with `Retry-After`. Point 4: `Corpus.lookup_many(keys, pipeline_version=None,
  vectors=True)` types keys by shape (64 hex or a UUID), chunks at 100, honours `Retry-After`
  (capped at 120 s) before the fixed delays, and refuses a short answer as an error. Client
  0.3.0. `tests/test_batch_lookup.py` on the server, `TestLookingUpALibrary` in the client.
- Owed: the plug-ins' whole-library passes moving to `lookup_many` (Follow-up; batched with
  their `ADR-0019` point 3 id lookups, one release each), and the post-deploy load measurement
  the Tradeoff asks for.

Extends [ADR-0009](ADR-0009-the-tool-is-useful-before-the-corpus-is.md) point 6 and
[ADR-0011](ADR-0011-the-commons-is-what-other-tools-plug-into.md) point 2. One of the five records
proposed together on 2026-09-15; the set and its order are in `ADR-0014` point 6.

## Context

Every client this project has written — the CLI, the beets plugin, the Picard plugin — follows the
contract's rule "look up before contributing", one `GET /v1/embeddings/{fingerprint_hash}` per
track. The lookup rate limit is 300 per minute per address (`packages/server/app/config.py`,
`lookup_rate_limit`), so the first run over a library is bounded by arithmetic, measured
2026-09-15: **a 10,000-track library takes 33 minutes to look up**, and a 50,000-track one
nearly three hours, before a single vector has been computed or contributed. The beets plugin's
first run over a 99-track library (`ADR-0011`'s Implementation block) was fine; the plugin's
first run over a real library is where a maintainer decides whether the commons is usable, and
it is thirty-three minutes of HTTP round trips answering, for a young corpus, mostly "no".

This was designed for the case the CLI has — a tool that embeds as it goes, where a lookup per
track is a small fraction of the model run it might save. It is the wrong shape for a tool that
already holds a fingerprint for every track and wants to know, before doing anything else, which
of them the commons has. KalinkaPlayer holds fingerprints and MBIDs per track already; its
question is a set question.

### What a lookup returns, as of 2026-09-15

`EmbeddingResponse` (`packages/server/app/api/routes.py`): the hash, the 512-float vector, the
pipeline identity, `contributor_count`, and — since `ADR-0012` — the recording MBID with the
most claims and how many. The vector is 6 KB as JSON; a batch of a hundred is 600 KB, which is
the same bytes as a hundred single lookups minus ninety-nine sets of headers and round trips.

## Decision

1. **`POST /v1/embeddings/lookup` takes up to 100 fingerprint hashes and an optional
   `pipeline_version`, and returns one entry per hash: the row if held, `null` if not.** Order is
   preserved so a client can zip the answer with its request. The body of a held entry is exactly
   `EmbeddingResponse` — the same fields, the same meaning, so nothing a client learned from the
   single endpoint changes.

2. **A `vectors: false` option returns everything but the embedding.** A tool asking "which of
   these do you hold, and what are they called?" — the Kalinka question, and the Picard
   lookup-only case — does not need 6 KB per row to learn a hash and an MBID. With vectors
   omitted, a batch of 100 is under 20 KB.

3. **The rate limit counts hashes, not requests.** A batch of 100 spends 100 of the 300 per
   minute, so the limit's meaning — how fast one address may read the corpus — does not change;
   what changes is that reaching it no longer costs a hundred round trips. A batch larger than
   100 is a `422`, not a partial answer.

4. **The client gains `Corpus.lookup_many(hashes, pipeline_version=None, vectors=True)`**,
   which chunks any iterable into batches of 100, backs off on `429` as `contribute` does, and
   yields `(hash, row_or_None)` pairs. `Corpus.lookup` stays as it is; the plug-ins move to
   `lookup_many` for their whole-library passes in their next releases.

5. **The single-hash `GET` stays, unchanged and undeprecated.** It is what Familiar calls, what
   the browse pages call, and what a tool embedding one track at a time should call. This record
   adds a shape; it does not replace one.

6. **Not decided here:** batching writes, which is `ADR-0016` and carries every guarantee the
   write path has; and a "since" cursor for a client that wants to learn what the corpus gained
   since its last pass, which is the incremental-sync question `ADR-0013` deferred.

## Alternatives Considered

- **Raise the lookup rate limit.** Rejected. 300 per minute is not the bottleneck; a round trip
  per track is. A limit of 3,000 with one request per track is still 10,000 requests, and it
  admits ten times the read load from a hostile address for no benefit to an honest one.
- **Publish a Bloom filter of held hashes.** Considered seriously — a client could answer "held
  or not" locally from a small download. Rejected for now because the answer a tool wants is not
  a bit but a row (the MBID at least), because a filter is stale between exports, and because
  `ADR-0013`'s export already gives a tool that wants the whole set the whole set. Worth
  revisiting if the corpus reaches a size where the export is too large to be the answer.
- **Let the export be the batch lookup.** Rejected as the *only* path: a weekly 64 MB file is the
  right answer for a mirror and the wrong one for a tool that wants today's answer for its
  library at install time. It is the right answer for a tool that would otherwise ask 50,000
  times, and the client's documentation should say so.
- **`GET` with a comma-separated list.** Rejected on URL length: a hundred 64-character hashes is
  6.5 KB, past what some proxies pass. `POST` with a JSON body is the shape `/v1/similar` already
  has for a non-mutating query.

## Consequences

- **Positive** — a library of any size is looked up in a number of requests a maintainer can
  reason about: 100 for 10,000 tracks, in under a minute.
- **Positive** — the lookup-only mode `ADR-0011` made the Picard default becomes a whole-library
  operation rather than a per-file one.
- **Tradeoff** — a second way to ask the same question; the client hides it, the API documents
  both, and the single endpoint's tests stay as the definition of what a row is.
- **Tradeoff** — a 600 KB response on a $12 instance is a different load profile from 6 KB ones;
  point 3 keeps the per-minute bytes the same, but the per-request peak is a hundred times
  larger. Measure it after it ships; `ADR-0003` point 11's RAM budget is the ceiling.
- **Follow-up** — `beets-clapback` and the Picard plugin adopt `lookup_many` for their
  whole-library passes, each a release.
