# ADR-0019: Agreement Is Counted per Recording, Not per Key

Status: accepted

Date: 2026-09-16

Implementation:
- **Accepted 2026-09-16**, the day it was proposed, on the measurement in `ADR-0010`'s
  Implementation block. Nothing is built. Point 8's order: the documentation line (point 1),
  then lookup by recording in the client (point 3), then the cross-key agreement (point 2, with
  its migration and the two-keys-one-recording test), then collapsed similarity (point 4), then
  the AcoustID claim type (point 6); point 5 waits on `ADR-0007`. `ADR-0015` and `ADR-0018`,
  still proposed, each gain a point if accepted: batch lookup takes ids, and the by-hash claims
  route serves the join. **Points 1, 2, 3, 4 and 6 were all built and deployed on 2026-09-16;
  only point 5 remains, with `ADR-0007`.**
- **`ADR-0014`, `ADR-0015`, `ADR-0016` and `ADR-0018` were accepted 2026-09-16**, `ADR-0015`
  and `ADR-0018` with the amendments above written into their Implementation blocks.
- **Point 1 is done** (2026-09-16, `e6124da`): the client README's first obligation and `/api`'s
  "Get an embedding" section state the measured figures (24 of 56, 10 of 24 CD-quality, 37 of
  56 across `fpcalc` generations) and that a miss by hash does not mean the corpus lacks the
  recording. `/api`'s description of `GET /v1/recordings/{mbid}` no longer says "one per
  pipeline" — two rows under one pipeline there are one file keyed twice.
- **Point 2 is built and deployed** (2026-09-16 ~01:45 UTC; box at `346f57c`, migration `014` applied, the one existing agreement backfilled, counts unchanged; every live read serves 0 / 0, which with one contributor is the truth). Migration `014_agreement_other_hash` adds
  `submission_agreement.other_hash` — the stored row a similarity was measured against, backfilled
  with the row's own hash for every existing agreement, which is what they were — and an index on
  `(other_hash, pipeline_version)`. `_contribute_one` gained `_record_cross_key_agreements`: a
  contribution that names its recording is compared with every row under its pipeline that any
  client has claimed under that id, other than the row under its own key, and each comparison
  is an agreement naming the other row; it runs in the created branch as well as the confirmed
  one, because the created branch is the second-client-on-another-path case. The served figures
  are `recording_confirmations` and `recording_contradictions`, computed at read time as
  `ADR-0008` point 6 requires (`_recording_agreements_for`), on every response that carries a
  `recording_mbid` — the single lookup, neighbours, the recording route, and the batch lookup.
  A confirmation is a distinct `client_id` inside `ADR-0008`'s band against any of the
  recording's rows under the pipeline; a contradiction is one outside it; a client is never
  counted against a row it contributed, and a submission without an id is not counted at all.
  The test point 8 demands is `tests/test_cross_key_agreement.py`, the suite's first against a
  real database: one recording, two keys, two clients, one confirmation — served by hash, by
  id, in a batch, and as a neighbour — plus the contradiction, the self-agreement, the
  unattributed submission, the batch path, and the column's meaning. Server CI gained a
  pgvector service for it; without `CACHE_TEST_DATABASE_URL` the six tests skip.
- **A consequence the record did not spell out.** `ADR-0008`'s Context said two rips of one
  recording "cannot reach a shared key" and so land in different rows rather than disagreeing in
  one. Through the recording id they now can: a second install's different rip, claimed under the
  same MBID, lands 3e-04 to 3e-03 outside the band and is served as a contradiction. That is
  `ADR-0008` point 4 working as written — disagreement served, not hidden — and it means a
  contradiction count is not by itself evidence of a wrong pipeline. The band is unchanged; the
  figure is labelled for what it is on `/api`.
- **Point 4 is built and deployed** (2026-09-16 ~02:10 UTC; box at `f801ce3`, no migration; live `limit: 100` answers 100 neighbours and `collapsed: 0`). `/v1/similar` ranks as before, then
  `_collapse_by_recording` over-fetches — a window of twice `limit`, doubling to a ceiling of
  1,000 — resolves each row's recording through the claims, and keeps the first row seen per
  (recording, pipeline), which is the nearest, until `limit` survive or the corpus runs out.
  Unnamed rows are never folded. The response gains `collapsed`, the number of rows folded away;
  `searched` still counts what was ranked. No schema change. Four more tests in
  `tests/test_cross_key_agreement.py`: two keys one neighbour, the nearer rip is the one kept,
  unnamed rows untouched, and the window widening past eight rows of one recording. With one
  contributor every live search collapses nothing, and `collapsed` says 0.
- **Deploying point 4 found a bug older than it** (2026-09-16). The first live check with
  `limit: 100` returned 39 neighbours and `collapsed: 0`. pgvector's HNSW returns at most
  `hnsw.ef_search` candidates — 40 by default — whatever the `LIMIT`: on the instance, a
  `LIMIT 200` ANN query answered 40 rows. So `/v1/similar` had silently truncated every `limit`
  above 40 since migration `009` built the index, and point 4's over-fetch window could never
  have widened past it. `_collapse_by_recording` now runs `SET LOCAL hnsw.ef_search` to its
  window before each fetch. The test that pins it had to switch off sequential scans and drop
  the pipeline filter to make sixty rows reach the index at all — with the filter, the planner
  takes the btree and sorts exactly, which is why the suite never saw it. Also noticed, not
  fixed: the instance carries two HNSW indexes on the same column (`ix_embeddings_hnsw_cosine`
  with `m=16, ef_construction=64`, and migration `009`'s `ix_embeddings_vector_cosine`), which
  is one index of RAM for nothing; `ADR-0003` point 11's budget should know.
- **Point 6 is built and deployed** (2026-09-16 ~02:55 UTC; box at `6f3dbe5`, migration `015` applied — 23,198 claims typed `musicbrainz_recording`, counts unchanged; both export queries run against the live schema; `clapback-client` 0.4.0 on PyPI, verified cold). Migration `015_claim_type`: `recording_claims`
  gains `claim_type` (`musicbrainz_recording` for every existing row, or `acoustid_track`) as
  part of the key, and its id column is renamed `recording_id` — both kinds are UUID text, and a
  column called `recording_mbid` holding something that is not one is the trap `ADR-0012`
  names. Contributions and claims accept `acoustid_track_id` beside or instead of
  `recording_mbid`; point 2's join runs on either id the submission carries; every read
  carries `acoustid_track_id` and `acoustid_claims` beside the MBID pair; the by-hash claims
  route lists both kinds with their `type`; `GET /v1/recordings/{id}?type=acoustid_track` and
  the batch key `{acoustid_track_id}` ask by it. A row is grouped for agreement and collapse
  under its MBID when it has one, else its AcoustID id — the two namespaces are never mixed.
  The export keeps `claims.csv.gz`'s columns (MusicBrainz claims only) and adds
  `acoustid_claims.csv.gz` with a manifest entry, so schema_version 1 readers are untouched.
  Client 0.4.0: `contribute(acoustid_track_id=)`, `claim(...)` taking either,
  `lookup(acoustid_track_id=)`, `recording(id, type=)`, and `("acoustid", id)` tuples in
  `lookup_many`. Five more database tests, the first of which is the point: a client holding
  only the AcoustID id, under a new key, confirms a row that has both.
- **Point 3 is built in the client** (2026-09-16, `clapback-client` 0.3.0, on PyPI the same day):
  `Corpus.lookup(recording_mbid=, pipeline_version=)` returns the most-claimed row under the id
  in the shape a hash lookup returns, `None` when nobody has claimed it, and refuses both keys
  or neither. It is `GET /v1/recordings/{mbid}` — no server change. The rule "by recording if
  you hold one, by hash otherwise, contribute under your hash either way" is in the docstring,
  the README and the module docstring, and a test pins the wording. The Picard copy is synced.
  **Owed:** the two plug-ins passing the id they already hold, each a release — beets'
  `mb_trackid` at `beetsplug/clapback.py`, Picard's `musicbrainz_recordingid` in `_core.py` —
  batched with their `ADR-0015` `lookup_many` adoption so each is one release, not two.

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
  2026-09-16: `fpcalc` 1.5.1 and 1.6.1 disagree on 19 of 24 CD-quality files (builds since the
  ffmpeg 5 era agree among themselves, 56 of 56 across swresample 6.1 and 7.1, so the break is a
  step rather than a drift). A pinned binary version is still not something a Picard user or a
  Debian package can be held to, and the 2021 build is in the wild.
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
