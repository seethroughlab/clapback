# ADR-0016: A Library Is Contributed in Batches, With Every Guarantee Kept

Status: accepted

Date: 2026-09-15

Implementation:
- **Accepted 2026-09-16** as written, last in the set's order on purpose. Nothing is built.
  Point 7's per-client quota — `ADR-0004` point 9's third bound, owed since 2026-09-04 — is
  built before the endpoint is public. The single-endpoint refactor into a shared per-row
  function must pass the existing tests unchanged before the batch handler exists. `ADR-0019`
  point 2's cross-key agreement, when built, lands inside that shared function, so a batch
  row gets it without this record changing.

Extends [ADR-0004](ADR-0004-contributors-are-identified-but-not-accounts.md) points 4 and 9,
[ADR-0008](ADR-0008-the-corpus-serves-agreement-not-a-verdict.md) and
[ADR-0015](ADR-0015-a-library-is-looked-up-in-batches.md). One of the five records proposed
together on 2026-09-15; the set and its order are in `ADR-0014` point 6. **Last in that order,
on purpose.**

## Context

`ADR-0015` makes the read side of a first run take a minute. The write side is worse and the
arithmetic is the same kind: the contribution rate limit is 30 per minute per address
(`packages/server/app/config.py`, `contribute_rate_limit`), one `POST /v1/embeddings` per track.
Measured 2026-09-15: **a 10,000-track library takes five and a half hours to contribute**, at one
row per two seconds, from a client that has already computed every vector. On a Raspberry Pi
that has just spent a day embedding a library, that is another night of the machine talking to
one server, and it is the number a maintainer sees when they turn contribution on.

Thirty per minute was chosen when the only contributor was Familiar on a private network, sending
as it analysed, where two seconds a track was slower than the model. It is a write-abuse bound
dressed as a rate, and `ADR-0004` point 9 already says the bounds that matter are totals — the
row ceiling, the disk, per-client quotas — not the rate.

### What the write path guarantees per row, as of 2026-09-15

`contribute_embedding` in `packages/server/app/api/routes.py` does, for one row: select the
existing row under `(fingerprint_hash, pipeline_version)`; if held, compute cosine against the
stored vector, record a `SubmissionAgreement` with the submitter's `client_id`, increment
`contributor_count`, and answer `confirmed`; if not held, check the row ceiling
(`max_embeddings`), insert, answer `created`; then the claim, if one was sent (`ADR-0012`). Every
one of these is what `ADR-0008` counts, what `ADR-0004` point 4 counts by client, and what
`ADR-0004` point 9's ceiling refuses past. **None of it is per request.** A batch endpoint that
did any of it per batch — one agreement for a hundred rows, one ceiling check before a hundred
inserts — would be a second write path with fewer guarantees, which is the thing `ADR-0005` point
12 exists to forbid.

The per-client quota `ADR-0004` point 9 names third is not built; as of this date nothing bounds
what one `client_id` may write except the ceiling and the rate.

## Decision

1. **`POST /v1/embeddings/batch` takes up to 100 contributions and answers with one result per
   contribution, in order.** Each entry is exactly an `EmbeddingRequest` — hash, vector,
   `pipeline_version`, `client_id`, optional `recording_mbid` — and each result is exactly a
   `ContributeResponse` (`created`, `confirmed`, or an error with its detail). The batch is a
   container; the row is the unit.

2. **Every per-row guarantee runs per row, in the same code.** The batch handler calls the same
   function the single endpoint calls, once per entry, in one transaction per entry. Agreement is
   recorded per row with the submitter's `client_id`; `contributor_count` moves per row; the
   ceiling is checked per row, so a batch that crosses it is accepted up to the line and refused
   past it, row by row, with the refusal in the result. No new write code touches the tables.

3. **The rate limit counts rows, not requests, and is raised to what a client can honestly
   send.** The limit becomes 600 rows per minute per address — a 10,000-track library in
   seventeen minutes — and a batch of 100 spends 100 of it. Why 600 and not unlimited: the
   ceiling is a total, the disk alert is a total, but a burst of writes is what fills a page cache
   and a WAL on a $12 instance, and ten rows a second is the rate the single endpoint was
   measured comfortable at times twenty, which is an estimate. **The figure is a starting point,
   to be measured against the instance after the first real batch contributor, and this record
   says so.**

4. **A batch is not atomic, and says so.** A client that sends 100 rows and gets 97 `created`,
   2 `confirmed` and 1 ceiling refusal has contributed 99 rows. Atomicity would mean rolling back
   confirmations that were correct because a later row hit the ceiling, which is evidence thrown
   away; and it would make the batch a transaction the single endpoint is not, which is a
   guarantee difference in the other direction. Clients retry per row, on the row's result.

5. **`client_id` is required on the batch endpoint.** The single endpoint accepts a contribution
   without one for the reason `ADR-0004` point 3 gives — clients that predate it must keep
   working. No client predates this endpoint. A contribution that cannot be confirmed by anyone
   is admissible one at a time and not by the hundred.

6. **The client gains `Corpus.contribute_many(rows)`**, which chunks, sends, backs off on `429`,
   and yields each row's result. `contribute` stays. The reference CLI's `contribute` command and
   the beets plugin's whole-library pass move to it; the Picard plugin, which contributes as it
   scans, stays on the single call.

7. **The per-client quota `ADR-0004` point 9 owes is built before this endpoint is public, not
   after.** A batch endpoint without it is a faster way for one identifier to reach the ceiling
   alone. The quota is a rows-per-day figure per `client_id`, generous enough for any library and
   tight enough that the ceiling cannot be reached by one install in one day: 50,000 rows per
   `client_id` per rolling 24 hours, ten percent of the ceiling. Reached, it answers `429` with a
   `Retry-After`, per row, like the rate limit.

## Alternatives Considered

- **Raise the single endpoint's rate limit and stop.** Rejected: 5½ hours becomes 33 minutes at
  300 per minute, which is `ADR-0015`'s read figure and was judged unacceptable there for the
  same reason — a round trip per row is the shape, not the rate.
- **Accept a file — the export format in reverse.** Considered: a client uploads
  `embeddings-<identity>.csv.gz` and the server loads it. Rejected because a file is a batch
  without per-row results, per-row agreement, or a per-row ceiling — everything point 2 keeps —
  and because a 64 MB upload to a $12 instance is a denial-of-service shape. The export is for
  taking the corpus away; contribution is per row.
- **Make the batch atomic.** Rejected in point 4.
- **Per-batch agreement, for speed** — compare vectors in bulk, insert agreements in bulk.
  Rejected: bulk *insert* of per-row agreements is fine and is an implementation detail of point
  2; per-batch *semantics* is not. The distinction is whether every row still gets its own
  `SubmissionAgreement` with its own similarity, and it must.
- **Skip the quota until there is a second contributor to bound.** Rejected: the second
  contributor is what this endpoint is for, and it should arrive after the bound exists, not
  before. It is also the last unbuilt item in an accepted record from `2026-09-04`.

## Consequences

- **Positive** — a library is contributed in minutes, which is the number that turns a
  maintainer's "interested" into a switch turned on.
- **Positive** — the quota `ADR-0004` promised is built, and the corpus gains its first bound on
  what one identifier can do to it.
- **Tradeoff** — the single-endpoint code is refactored into a function both endpoints call,
  which is a change to the write path with no behaviour change; the existing tests are the
  contract that says so, and they must pass unchanged before the batch handler is written.
- **Tradeoff** — point 3's 600 is a guess with a rationale; the first batch contributor is the
  measurement. If the instance suffers, the number comes down and the record says so.
- **Tradeoff** — 100 rows × 6 KB is a 600 KB request body; the server's request size limit
  must admit it and refuse ten times it.
- **Follow-up** — the CLI and beets plugin adopt `contribute_many`; each a release.
- **Follow-up** — once a second contributor has used this, `ADR-0008`'s agreement reporting has
  its first real data, and that record's "how many agreements make a vector trustworthy" can be
  asked with something to answer it.
