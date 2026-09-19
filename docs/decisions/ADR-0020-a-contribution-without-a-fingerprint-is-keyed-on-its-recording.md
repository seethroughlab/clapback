# ADR-0020: A Contribution Without a Fingerprint Is Keyed on Its Recording

Status: accepted

Date: 2026-09-19

Implementation:
- **Accepted 2026-09-19**, the day it was proposed, before the reply that points KalinkaPlayer#128
  at it went out — so the maintainer reads a decision, not a deferral. Nothing is built. The order
  is point 10's: migration `016` and the request schema first, with the two-client database test.
- The cross-checkpoint measurement this record's last Follow-up refers to finished the same day
  (`packages/embed/scripts/measure_crosspipe.py`, results beside it): the two checkpoints are
  orthogonal spaces, and a linear map fitted on ~400 paired tracks searches across them at R@10
  0.99 with a top-10 overlap of 0.56 — more than a windowing change within one checkpoint keeps.
  A published bridge between identities is therefore a live question, and a separate record.

Extends [ADR-0012](ADR-0012-a-contribution-can-name-its-recording.md) and
[ADR-0019](ADR-0019-agreement-is-counted-per-recording-not-per-key.md); qualifies the title of
[ADR-0010](ADR-0010-the-corpus-key-is-a-function-of-the-audio.md) without reversing any point
of it. Prompted by the first prospective second contributor's third comment in
[madenvel/KalinkaPlayer#128](https://github.com/madenvel/KalinkaPlayer/issues/128), 2026-09-16.

## Context

A row in the corpus exists only under a fingerprint hash. `fingerprint_hash` is the first half
of the primary key (`packages/server/app/db/models.py:44`), the contribution request requires
exactly 64 hex characters of it (`packages/server/app/api/routes.py:376`), and a recording id
is a *claim* attached to a row (`ADR-0012` point 1) that cannot stand without one. A tool that
holds a vector and a MusicBrainz recording id for a track it never fingerprinted has nothing
the corpus will take.

### The ask, 2026-09-16

KalinkaPlayer's maintainer asked twice. On 2026-09-15, as questions 1 and 2: is there a key when
neither a fingerprint nor an MBID is available, and can a contribution be made under an MBID
alone? This project answered on 2026-09-16: not today, "I'd rather not add an MBID-only key
type, but I'm open to talking about it once there's a second population to talk with." On
2026-09-16 the same maintainer measured `fpcalc` on a Raspberry Pi (0.8–1 s per track),
withdrew the performance objection, and asked again in narrower terms: *"vectors plus recording
MBIDs and model details would let Kalinka contribute already-enriched tracks without another
processing pass."*

That narrowing matters. The request is no longer for a key from nothing; it is for the corpus
to accept a vector whose recording is named by the identifier the corpus already counts
agreement by. Kalinka's libraries hold, for most tracks, a CLAP vector, a MusicBrainz recording
id, and the pipeline that produced the vector; fingerprinting in Kalinka is optional and gated on
an AcoustID application key, so a fingerprint exists for some tracks and not others. Contributing
the backlog under the current rule means decoding every track again to produce a key — on a Pi,
for a library of tens of thousands — for the sole purpose of satisfying the corpus. The second
population is asking to arrive with what it has, and the rule says no.

### Why the rule was drawn where it is

`ADR-0010` says the key is a function of the audio, and the reasoning is sound: two contributors
who processed the same bytes should land on the same row without coordinating, so that a
disagreement between them is about the audio and not about tagging. An MBID is not derived
from the audio. It is a claim about what the audio *is* — from a tagger, from AcoustID's
service, from a human — and two libraries tagged with one MBID may hold different masters, a
radio edit, a mislabelled live take, or the wrong song. Under a fingerprint key those are
different rows whose claims contradict; under a recording key they would be one row whose
vectors fight.

### What has changed since, and this is the premise the record rests on

Three things, each already accepted.

1. **`ADR-0019` moved the unit of agreement to the recording.** Since 2026-09-16, confirmations
   and contradictions are counted per recording *across keys* (its point 2, built:
   `_record_cross_key_agreements`, `packages/server/app/api/routes.py:178`), the recording is
   the first lookup for a tool that holds an id (point 3), and similarity collapses rows that
   share one (point 4). The corpus already treats "these rows claim the same recording" as the
   meaningful join, and already has to cope with a wrong claim.
2. **`ADR-0008` says the corpus serves agreement, not a verdict.** No row was ever declared
   true because its key was a fingerprint; a row is one assertion and the corpus reports how
   many independent installs agree with it. A recording-keyed row is one more assertion, of a
   visibly different kind.
3. **The fingerprint key is not the clean audio-derived identity `ADR-0010` described.** Its
   own Implementation block records the 2026-09-16 measurement: two fingerprinting paths key the
   same file identically on 24 of 56 FLACs, two `fpcalc` generations on 37 of 56. Within one
   path the key is exact; across paths the *recording claim* is what joins the rows. The purity
   argument is not lost, but it is already qualified, and the thing that rescued it is the
   identifier this record proposes to key on.

### What is actually in the corpus, measured 2026-09-19

`GET /v1/pipelines`: one identity, `laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32`,
25,886 rows, 23,196 named (89.6%). Every named row's claim is under one `client_id`. There is
no second population yet; this record exists because one is asking what it may bring.

## Decision

1. **A contribution that carries a MusicBrainz recording id and no fingerprint is accepted, and
   keyed on the recording.** The row's key is `(SHA256("musicbrainz_recording:" + mbid),
   pipeline_version)` — a 64-hex digest in the existing column, derived by the server from the
   id, reproducible by any client, and cryptographically disjoint from every fingerprint hash.
   The primary key does not change; every index, read path, quota, ceiling, revocation and
   deletion that works on `fingerprint_hash` works unchanged on it.

2. **The row says which kind of key it has.** A new `key_type` column on `embeddings`,
   `NOT NULL`, values `fingerprint` and `musicbrainz_recording`, default `fingerprint` so
   migration `016` touches no existing row. Every read that serves a row — lookup, batch
   lookup, `/v1/similar`, `GET /v1/recordings/{mbid}`, `/v1/pipelines`' counts, and
   `ADR-0013`'s export — carries it. A reader who wants only audio-keyed evidence can filter;
   nothing is hidden behind an aggregate.

3. **A recording-keyed row claims its own recording, and that is how it joins everything else.**
   On creation the server writes a `recording_claims` row for it — `(derived_hash,
   musicbrainz_recording, mbid, client_id)` — exactly as it would for a fingerprint-keyed
   contribution that named the same id. From there `ADR-0019` does the rest with no new code:
   point 2 compares its vector with every fingerprint-keyed row claimed under the same recording
   and pipeline and counts the agreement; point 4 collapses it with them in similarity results;
   `GET /v1/recordings/{mbid}` returns it beside them. A second install contributing the same
   recording under the same pipeline lands on the same row and is a confirmation, with the
   agreement recorded, as today.

4. **Only the MusicBrainz recording id keys a row this way.** Not the AcoustID track id, though
   `ADR-0019` point 6 admits it as a claim: a client holding an AcoustID id obtained it by
   fingerprinting, so it has a fingerprint and keys on that. Not a release, work or track id.
   Not a file checksum — see Alternatives. The recording key exists for one case, a vector whose
   audio was never fingerprinted, and the id it uses is the one the corpus already counts by.

5. **A client that has a fingerprint keys on it, always.** The recording key is not an
   alternative for a client that finds fingerprinting inconvenient; it is for a vector that has
   no fingerprint and will not get one. The server cannot enforce this (it never sees a
   fingerprint, `ADR-0010` point 7) and the client library enforces it by shape:
   `Corpus.contribute()` keeps requiring `fingerprint_hash`, and a separate, documented
   `Corpus.contribute_recording(recording_mbid=, embedding=, pipeline_version=)` is the only way
   to make a recording-keyed row. `contribute_many` accepts rows of either shape and says which
   it made.

6. **`client_id` is required for a recording-keyed contribution**, as it already is for any
   contribution that names a recording (`packages/server/app/api/routes.py:641`): the row *is*
   a claim, and a claim with nobody behind it can be neither revoked nor counted.

7. **The wire form is: `fingerprint_hash` becomes optional on `POST /v1/embeddings` and in a
   batch row; a request with neither `fingerprint_hash` nor `recording_mbid` is refused with a
   422 that says so.** A request with both is a fingerprint-keyed contribution with a claim,
   exactly as today. The server derives the key; a client never sends the digest of point 1
   itself, so there is one implementation of it and it is the server's, which — unlike a
   fingerprint hash — the server can verify.

8. **What this does not decide.** A key for a track with neither a fingerprint nor an id
   (Kalinka's original question 1) — there is none, and this record does not invent one.
   Whether a recording-keyed row is ever *promoted* to a fingerprint key when the same client
   later fingerprints the file — it is not; `ADR-0006` point 5 and `ADR-0010` point 4 say rows
   are re-contributed, never relabelled, and a later fingerprint-keyed contribution of the same
   recording simply joins it through point 3. How `ADR-0007`'s attestation treats a
   recording-keyed row — attestation is per pipeline and per fingerprinting *path* (`ADR-0019`
   point 5); a recording-keyed row has a pipeline and no path, and the record that builds
   attestation must say what that means.

9. **On acceptance, `ADR-0010`'s Implementation block gains a dated line** saying its title
   holds for fingerprint-keyed rows, that rows declare which they are, and pointing here.
   No point of `ADR-0010` is superseded: its seven points are about how a fingerprint is
   canonicalised and hashed, and every one of them still governs every fingerprint-keyed row.

10. **Execution order:** migration `016` and the request schema (points 1, 2, 6, 7) with a
    database test that contributes one recording under a fingerprint key from one client and a
    recording key from another and asserts the confirmation is counted (point 3) → `key_type`
    on every read and in `export.sh` (point 2) → `clapback-client`'s `contribute_recording`
    and the batch shape (point 5), with the README's "what leaves the machine" paragraph
    extended → the Implementation block here carries the first count of recording-keyed rows,
    which will be Kalinka's backlog or nothing.

## Alternatives Considered

- **Keep refusing, and wait for the backlog to be fingerprinted.** The rule as it stands. Rejected
  because the cost falls entirely on the arriving population and buys the corpus a property it
  no longer has in the form the rule assumed: `ADR-0019` already joins rows by the recording,
  and the measurement in `ADR-0010`'s Implementation block already shows fingerprint keys
  splitting across paths on more than half of files. Refusing a vector because its key is a
  claim, while counting agreement by that same claim, is a rule defending a line the corpus has
  already crossed — and it costs the one thing four accepted records are waiting on, a second
  contributor's rows.

- **Make the recording id the key for everyone.** Rejected for the reasons `ADR-0019` rejected
  the AcoustID id as the key: it needs a tagger or a network service at contribution time, an
  offline tool cannot key a row, and a recording nobody has entered in MusicBrainz — exactly
  the obscure material a personal library holds — has no id. The fingerprint stays the key for
  every contribution that can produce one (point 5).

- **Put the MBID itself in the key column.** A 36-character UUID in `fingerprint_hash`. Rejected:
  every surface validates 64 hex characters, the export and the admin delete path address rows
  by that digest, and a mixed-format column is a namespace held together by string length. A
  derived digest under a stated prefix keeps every contract and is as reproducible.

- **A separate table for recording-keyed vectors.** Rejected: `/v1/similar` runs on one HNSW
  index over one table, and quota, ceiling, revocation, deletion, agreement and export would
  each need a second implementation. `key_type` on the row is the same information at the cost
  of one column.

- **Accept the vector under a placeholder key and re-key it when a fingerprint arrives.**
  Rejected: that is relabelling, which `ADR-0006` point 5 and `ADR-0010` point 4 forbid for a
  reason that applies here in full — the server cannot verify the re-key claim. A recording
  later fingerprinted is contributed again under its fingerprint and the two rows join through
  the claim (point 8).

- **Key on a checksum of the audio bytes or the file.** The "lightweight key" of Kalinka's
  question 1. Rejected: a file checksum is a function of the encoding, the container and the
  tags, so no two rips, re-encodes or re-tags share one, and a key nothing else can ever land on
  is a private row in a public corpus. A checksum of decoded PCM is the same under a different
  cost — `ADR-0017`'s measurement found even two decoders of one file disagree on 24-bit sources.
  What a fingerprint adds over a checksum is exactly the tolerance a shared key needs, and the
  recording id is the only other identifier two libraries arrive at independently.

## Consequences

- **Positive:** the second population can contribute what it already holds. Kalinka's backlog
  arrives as vectors, ids and identity strings without a Pi re-decoding its library; the same
  path serves any tool that tags through MusicBrainz and never fingerprinted — beets libraries
  without the `chroma` plugin, Familiar's tracks with an id and no fingerprint (its 2026-09-11
  backfill found 824 without one; how many of those hold an id is not measured).
- **Positive:** nothing on the read side is new. A recording-keyed row is found by
  `GET /v1/recordings/{mbid}` and the batch lookup exactly as a named fingerprint row is, agrees
  and contradicts through `ADR-0019` point 2, and collapses under point 4 — built, tested and
  deployed already. The change is one column, one derived key and one optional field.
- **Positive:** the key is server-verifiable. For the first time the server can compute a row's
  key from the request and check it, which no fingerprint key allows (`ADR-0010` point 4).
- **Tradeoff:** a recording-keyed row can never be confirmed by an audio-derived key, only agreed
  with by other claims. It is a claim all the way down, and `key_type` exists so that a reader
  can see it. A reader who counts confirmations without looking at `key_type` is counting
  tagging agreement alongside audio agreement, and the served field is the only guard.
- **Tradeoff:** a mistag becomes a vector *at* the recording's key rather than a contradicting
  claim beside a correctly keyed vector. Per-recording counting still reports it as one voice
  among the rows that name that recording, and a fingerprint-keyed row from a second client
  outvotes it the same way a wrong claim is outvoted today — but the failure is more direct and
  the record should not pretend otherwise.
- **Tradeoff:** `ADR-0010`'s title is now a statement about fingerprint-keyed rows, which will be
  most rows but not all. Point 9 records that where the older record's readers will find it.
- **Follow-up:** `ADR-0007`'s attestation must decide what a recording-keyed row attests to
  (point 8). **Follow-up:** the landing page and `/v1/pipelines` should break counts down by
  `key_type` once a second value exists, alongside the by-identity breakdown `ADR-0014` owes.
  **Follow-up:** the measured cross-checkpoint results being gathered for KalinkaPlayer#128 will
  say whether a recording-keyed row under one identity can ever inform a search under another;
  that is a separate decision if the numbers support one.
