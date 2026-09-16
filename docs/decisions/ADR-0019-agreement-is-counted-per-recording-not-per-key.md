# ADR-0019: Agreement Is Counted per Recording, Not per Key

Status: proposed

Date: 2026-09-16

Extends [ADR-0010](ADR-0010-the-corpus-key-is-a-function-of-the-audio.md),
[ADR-0008](ADR-0008-the-corpus-serves-agreement-not-a-verdict.md) and
[ADR-0012](ADR-0012-a-contribution-can-name-its-recording.md) point 5. Proposed the day the
measurement in `ADR-0010`'s Implementation block was made, and because of it.

## Context

`ADR-0010` made the corpus key the SHA256 of the AcoustID fingerprint string so that a second
client holding the same audio lands on the same row and can confirm it. `ADR-0004` counts
independence on that row, `ADR-0008` records agreement on it, and `ADR-0007` would attest a
pipeline by it. All four assume the string is a function of the audio.

**It is a function of the audio and of the fingerprinting path, measured 2026-09-16** on 56
FLACs (`packages/embed/scripts/measure_fingerprint.py`; the entry in `ADR-0010` has the full
table). The two paths this project's own clients use — the `fpcalc` binary, which Familiar and
Picard run, and pyacoustid's library path, which beets runs — produce the same string on 24 of
56 files, and on 10 of the 24 sixteen-bit CD-quality ones. Two official `fpcalc` builds from
different ffmpeg generations agree on 37 of 56. The strings differ by 1 to 17 bits of ~30,000,
which AcoustID's fuzzy matcher was built to absorb and a SHA256 cannot. So, as things stand:

- Within one path, the key works: every beets-on-macOS install agrees with every other.
- Across paths, on CD audio, **a second client lands on the same row less than half the time.**
  The other half, one recording is two rows, each with `contributor_count` 1; no agreement is
  recorded; no independence is counted; a `lookup` by hash from the second client misses.

Nothing in the corpus today is wrong — every row came from one client on one path. What is
wrong is the promise, and the promise is what the outreach is making to a second contributor.

### What already exists to build on, as of 2026-09-16

`ADR-0012` gave every row a *recording claim*: a MusicBrainz recording id, per client, counted.
23,196 of 25,886 rows (89.6%) carry one, from Familiar's AcoustID backfill (`ADR-0115` there),
and both plug-ins send one for every file they can. `GET /v1/recordings/{mbid}` already returns
every row claimed under an id, per pipeline — "what does recording X sound like" — which is a
lookup that does not depend on the key at all. `SubmissionAgreement`
(`packages/server/app/db/models.py`) already records, per submission, a similarity against an
existing vector, with the submitter's `client_id` and the pipeline. The pieces that survive the
measurement are the ones keyed on the recording; the pieces that do not are the ones keyed on
the string.

## Decision

1. **The hash stays the row key, and its documentation stops promising what it cannot do.** The
   SHA256 of the fingerprint remains how a row is addressed and deduplicated — it is offline,
   one-way, and exact within a path, and nothing better exists that is all three. The client
   README and `/api` state the measured fact: two paths, or two `fpcalc` generations, may key one
   file differently, and a miss on lookup does not mean the corpus lacks the recording.

2. **Agreement, independence and confirmation are counted per recording, across keys.** When a
   contribution arrives with a `recording_mbid`, the server compares its vector not only with
   the row under its own key but with every row under the same `pipeline_version` that any
   client has claimed under the same recording id. Each comparison is a `SubmissionAgreement`
   as today, with a new column naming the *other* row's hash. `contributor_count` on a row keeps
   meaning "submissions to this key"; a new, served figure — `recording_confirmations` — means
   "independent installs whose vector for this recording, under this pipeline, agreed to
   `ADR-0008`'s band", counted by distinct `client_id` across all the recording's rows.

3. **The recording is the first lookup for a tool that holds an id, and the batch lookup takes
   ids.** `Corpus.lookup` gains `recording_mbid=` as an alternative to the hash, backed by
   `GET /v1/recordings/{mbid}`; `ADR-0015`'s batch endpoint accepts a mix of hashes and ids.
   The contract's rule becomes: look up by recording if you have one, by hash otherwise, and
   contribute under your hash either way. A Picard or beets install that holds an id never
   misses a recording the corpus holds because of a resampler.

4. **Similarity results collapse rows that share a recording.** `/v1/similar` returns one
   neighbour per claimed recording under the requested pipeline — the nearest of its rows — so
   a recording contributed by two installs on two paths appears once, not as two adjacent
   near-identical results. Rows with no claim stay as they are.

5. **`ADR-0007`'s attestation, when built, attests per path.** A reference signal fingerprinted
   through two paths is two keys; the record's design must expect that, and this point is the
   note that says so before it is built.

6. **A second claim type — the AcoustID track id — is admitted but not required.** AcoustID's
   id is what its own fuzzy matcher assigns to a cluster of near-identical fingerprints; it is
   the same across decoders, across `fpcalc` versions, and across lossy re-encodings of one
   rip. A client that has one (Picard always; beets' `chroma` stores it as `acoustid_id`;
   Familiar's `ADR-0115` backfill received one with every track it resolved, whether or not it
   kept it) sends it beside the MBID, and point 2's
   join runs on either id. It is not required because it needs AcoustID's service, an
   application key, and coverage — a recording AcoustID has never seen has no id — none of
   which `ADR-0010`'s offline key needs.

7. **Not decided here:** replacing the key with the AcoustID id (rejected below), tolerant
   matching on the server (rejected below), and what a row with no claim of either kind is
   worth — it is what it was, a vector one install computed, and this record does not lower
   that.

8. **Execution order:** point 1 (documentation, a day) → point 3's `recording_mbid=` lookup
   (the client already has the endpoint) → point 2's agreement (a migration adding the other
   row's hash, and the comparison loop) → point 4 → point 6 → point 5 when `ADR-0007` is built.
   Point 2 is the one that changes what the corpus says, and it ships with a test that
   contributes one recording under two different keys from two `client_id`s and asserts the
   confirmation is counted.

## Alternatives Considered

- **Pin the fingerprinting path: "the key is what `fpcalc` says".** Rejected by measurement,
  2026-09-16: `fpcalc` 1.5.1 and 1.6.1 disagree on 19 of 24 CD-quality files. A pinned binary
  version is not something a Picard user or a Debian package can be held to.
- **Make the AcoustID track id the key.** Considered seriously — it is the right identifier for
  "same audio, whoever decoded it". Rejected as the *key* because it needs the network and an
  application key at contribution time, which makes an offline tool unable to key a row, and
  because AcoustID has no id for a recording nobody has submitted to it — exactly the obscure
  material a personal library holds and a commons exists for. Admitted as a claim in point 6,
  which gets the join without the dependency.
- **Store the raw fingerprint and match tolerantly on the server** — an inverted index over the
  fingerprint's integers, as AcoustID's own server does, merging a submission into an existing
  row within a Hamming threshold. Rejected for now: it stores 3.8 KB of fingerprint per row
  where the corpus stores a 64-character digest, on the privacy line `ADR-0010` drew; it is a
  second index on a `$12` instance sized for one; and it re-implements the service AcoustID
  already runs. If point 2's join proves insufficient because claims are sparse, this is the
  fallback, and it should be measured against point 6 first.
- **Coarsen the key — hash a lossy reduction of the fingerprint that survives a few bit flips.**
  Rejected: the flips fall on arbitrary bits of arbitrary integers (1 to 17 of them), so no
  fixed mask survives them, and a locality-sensitive scheme is the previous alternative under
  another name.
- **Accept it and say nothing.** Rejected. The corpus is asking a second contributor to plug
  in on the strength of confirmation, and the measurement says confirmation across paths mostly
  does not happen. A record that knew that and kept the README as it was would be the thing
  `ADR-0001` said this project would not be.

## Consequences

- **Positive** — agreement becomes about the recording, which is what a person means by
  "two installs have this track", rather than about a string two resamplers disagree on.
- **Positive** — the different-rip case `ADR-0008` said the key could never join is joined by
  the same mechanism, for free: two rips of one recording with one MBID are compared.
- **Positive** — a tool with ids never misses on lookup for a reason it cannot see.
- **Tradeoff** — confirmation now depends on claims, so an unnamed row cannot be confirmed
  across paths. 10.4% of the corpus today; the plug-ins name everything they can, and point 6
  widens the join for tools that have AcoustID ids and no MBIDs.
- **Tradeoff** — point 2 costs a query per contribution over the recording's rows under the
  pipeline: a handful, indexed on `(recording_mbid)` in `recording_claims`. Cheap; measured
  after it ships.
- **Tradeoff** — a claim is an assertion (`ADR-0012`). Two installs that both mis-tag a file
  with the same wrong MBID will be counted as agreeing about it — which they are — about the
  wrong recording. `ADR-0008` point 4 serves disagreement rather than hiding it, and that is the
  defence here too.
- **Follow-up** — the reply owed in KalinkaPlayer#128 should say this plainly: the key is
  path-dependent, the recording is the join, and Kalinka's MBIDs are worth more to the corpus
  than this project understood when it wrote to them.
- **Follow-up** — `ADR-0007`'s design gains a constraint (point 5) before anything is built.
