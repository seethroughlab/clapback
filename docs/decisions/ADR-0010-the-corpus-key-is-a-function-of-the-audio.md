# ADR-0010: The Corpus Key Is a Function of the Audio

Status: accepted

Date: 2026-09-10

Implementation:
- **Accepted 2026-09-10**, the same day it was proposed and the same day the defect was found.
  Nothing is built.
- The order point 6 sets is the order to work in: the rule first, in every client that hashes; then
  Familiar's re-contribution of the rows it keyed on the escaped form; then deletion of the keys
  that leaves stranded. `ADR-0009` point 6 is unblocked by the first of those alone.
- Nothing here is a server change. The server's request schema, storage and key are already correct
  and stay untouched — which is the unusual property of this record and the reason point 4 exists.
- **Points 1 and 2 are built in the CLI** (2026-09-10), which is the first client to follow the rule
  and the only one that gets it for free: it fingerprints audio and hashes the result, so the value
  never passes through storage and there is no encoding to undo. `packages/cli/src/clapback_cli/
  fingerprint.py` carries `canonical()` anyway, for fingerprints that *have* been through something,
  with the decode deliberately narrow — `\x`, even length, and a decode to printable ASCII, none of
  which a base64 fingerprint can satisfy. `packages/cli/tests/test_fingerprint.py` pins the two
  encodings to one hash and pins that hash against `hashlib` directly, so a change to the canonical
  form fails a test rather than silently re-keying the corpus.
- **Familiar adopted the rule 2026-09-11** (its `ADR-0114`, `#300`). Its `hash_fingerprint`
  canonicalises the same narrow way, and its tests reimplement this project's CLI rule independently
  and assert the two agree — because the property is that two implementations of one written rule
  produce one key, not that they share an import. Both clients now key on the audio.
- **Point 4's re-contribution ran 2026-09-11**, 84 minutes, `EXIT=0`: 26,431 considered, 11,415
  already present (the rows stored raw, whose keys never moved), **14,192 contributed** under
  canonical keys, 824 with no fingerprint, 0 refused, 0 errors. A re-send of vectors Familiar already
  held, not a recompute — no embedding changed. `--declare-pipeline` turned out to be mandatory
  rather than optional: since `ADR-0006` phase 4 the server requires `pipeline_version`, and the
  backfill declares none by default, so without the flag every POST is a 422.
- **Point 5's deletion ran 2026-09-13**: 14,260 old keys derived from Familiar's escaped rows (14,284
  rows; 24 share a fingerprint), 14,246 found present, **14,246 removed** — 5 in a trial, 14,241 in
  the run — 0 failures, `EXIT=0`, through `DELETE /admin/corpus/recordings/{hash}` and never
  through `psql`. Proved safe before running rather than after: 0 recordings would be orphaned
  (every old row had a canonical row waiting), 0 old keys collided with any new key, and 0 rows in
  `features`, `analysis_details` or `submission_agreement` were keyed on them, so the takedown
  endpoint's wider reach touched nothing else. A `pg_dump` (286 MB, 17:29 UTC) preceded it.
- **The corpus after this record: 25,515 rows, one pipeline, one contributor, zero rows under a key
  no client can reproduce.** It went 25,558 → 39,761 → 25,515 across the week, and the net change of
  −43 is the duplicate collapse the split had been hiding — recordings this library holds in both
  encodings are now one row. Every count in that sequence was predicted before it was measured.
- **Two things the operation taught that the design did not.** The admin cookie is set
  `secure=True`, which is right for a login form on a public host and is exactly what makes the
  cookie unusable over `http://localhost` — the only route to the admin surface, since Caddy 404s
  `/admin` from outside. Python's cookie jar silently dropped it and every DELETE returned 401 with
  a valid session; the fix is to send the cookie as a header. And a 14,000-request loop must run
  detached: the first attempt hung on the SSH hop before reaching the server, which looked identical
  to a slow deletion. Both are recorded in the script that ran it.
- **The key is a function of the audio *and of the fingerprinting path*, and this record did
  not know that until 2026-09-16.** The premise here is that two clients holding the same audio
  compute the same key. Measured on 56 FLACs (`packages/embed/scripts/measure_fingerprint.py`,
  results beside it; the measurement `ADR-0017` was written to make): the official `fpcalc`
  binary and pyacoustid's library path — the two paths this project's own clients use, Familiar
  and Picard on the first, beets-on-a-Mac on the second — produce **the same fingerprint string
  on 24 of 56 files, and on 10 of the 24 sixteen-bit CD-quality ones.** The rest differ by 1 to
  17 bits of ~30,000, because `fpcalc` resamples to 11,025 Hz in ffmpeg's swresample before
  chromaprint runs and libchromaprint given native-rate audio resamples internally. A second,
  smaller mechanism: two decoders feeding one front-end agree exactly on 16-bit sources and
  disagree on 9 of 32 twenty-four-bit ones, from how each rounds 24 bits to 16. AcoustID never
  cared, because AcoustID matches fingerprints fuzzily; this corpus hashes the string, so a bit
  is a row. **The 99-of-99 figure above was one machine and one path** — Core Audio through
  pyacoustid both times — and measured stability, not reproducibility. Nor is `fpcalc` a path
  one can pin: the official 1.6.0 and 1.6.1 builds (both ffmpeg 8) agree on 56 of 56, but 1.5.1
  (ffmpeg 4, 2021) agrees with 1.6.1 on **37 of 56**, every disagreement a 16-bit 44.1 kHz file
  — the resampler moved between ffmpeg releases, and Picard bundles one `fpcalc` while every
  Linux distribution ships another (`measure_fingerprint.versions-2026-09-16.csv`).
- **What this does and does not break, as of 2026-09-16.** Every row in the corpus came from one
  client on one path, so nothing in it is wrong. What cannot be relied on is the thing three
  accepted records are built on: that a *second* client with the same file lands on the same
  row. Across the two paths, on CD audio, that happens less than half the time; the other half,
  the same recording becomes two rows with `contributor_count` 1 each, `ADR-0008` records no
  agreement, `ADR-0004` counts no independence, and `ADR-0007`'s attestation would pass both.
  Within one path it is fine — every beets install on macOS agrees with every other, every
  Picard with every Picard. The decision that fixes this is not made here; it is proposed as
  `ADR-0019`, the same day, with the options measured rather than assumed. Until it is decided,
  the client's documentation says which path it uses and that another path may not confirm it.
  The 2026-09-15 reply in KalinkaPlayer#128 said "two rips hash differently, by construction" and
  was right for the wrong reason: two *decoders* of one rip do too.

Extends [ADR-0006](ADR-0006-the-pipeline-identity-is-the-corpus-key.md), which made
`(fingerprint_hash, pipeline_version)` the key and fixed the half of it that describes the pipeline.
This fixes the other half. It blocks [ADR-0009](ADR-0009-the-tool-is-useful-before-the-corpus-is.md)
point 6, which cannot be built correctly until it is decided.

## Context

`ADR-0006` established that two rows share a key exactly when they are comparable, and spent a
migration and 47,486 deleted rows making the `pipeline_version` half of that true. The
`fingerprint_hash` half was assumed to be true already. It is not.

### `fingerprint_hash` is SHA256 of whatever string the client happened to store

The server never sees a fingerprint. It receives a 64-character hex digest
(`packages/server/app/api/routes.py:114`) and stores it. Every property the key has is therefore a
property of the client's hashing, and Familiar's hashing
(`backend/app/services/community_cache.py:212`) takes whatever it is handed:

```python
if isinstance(acoustid_fingerprint, bytes):
    return hashlib.sha256(acoustid_fingerprint).hexdigest()
return hashlib.sha256(acoustid_fingerprint.encode()).hexdigest()
```

That is correct code given a canonical input. It was never given one.

### Familiar stores the same fingerprint in two encodings, measured 2026-09-10

`track_analysis.acoustid` is a `text` column holding 25,648 non-null values in two shapes:

| form | rows | example |
|---|---|---|
| hex-escaped | **14,284** (56%) | `\x41514144744a4c79...` |
| raw base64 | **11,364** (44%) | `AQADtJESbVkUhYL8...` |

These are not two kinds of fingerprint. `\x41514144` is hex-of-ASCII for `AQAD`: decode the escaped
form and the base64 string comes back unchanged. The most likely history is a `bytea` → `text`
column migration that rendered the existing values in Postgres's hex output format, after which new
writes — which come from a subprocess returning a decoded `str`
(`backend/app/services/analysis.py:750`) — landed raw.

### Both encodings are already keys in the live corpus

Probed 2026-09-10: 400 random Familiar tracks, hashed exactly as stored, looked up in the deployed
corpus.

| form | sampled | found in corpus |
|---|---|---|
| hex-escaped | 212 | **211** |
| raw base64 | 188 | **188** |

So this is not a latent hazard in one client. It is the state of the commons: of the 25,558 rows
`ADR-0006` phase 4 left behind, a majority are keyed on a string that describes how Familiar's
database once stored a value, and the rest on the value itself.

### The premise this contradicts

Familiar's backfill script states the rule plainly
(`backend/scripts/backfill_community_cache.py:15`): *"The fingerprint is hashed as stored, not
decoded"*, backed by a real measurement — hashing the stored string matched 232 of 500 tracks in the
corpus, hashing the decoded bytes matched 1. **That measurement was right and the conclusion drawn
from it is what cements the problem.** Hashing as stored is the only way to match a corpus that was
built by hashing as stored; it also guarantees the corpus can never be matched by a client that did
not share Familiar's storage history. The 232-of-500 figure does not isolate the split — a miss
there can equally mean the track was never contributed — so it is consistent with this finding
rather than evidence of it. What it does show is that the question "which string do we hash" had
already been noticed, answered locally, and never asked at the level of the corpus.

### Why this blocks a second contributor rather than merely annoying one

`ADR-0009` point 6's tool computes a fingerprint from audio with chromaprint and gets the base64
string. Hashing that matches the ~44% of recordings Familiar stored raw. For the other ~56% it
computes a key that exists nowhere, and contributes a **new row** for a recording the corpus already
holds.

The corpus would then contain two rows per affected recording, from two clients, that can never
confirm each other and will never contradict each other either — they are simply invisible to one
another. `ADR-0008` reports independent confirmations and contradictions; here it would report
neither, for reasons indistinguishable after the fact from two people who own different recordings.
**The first genuine second contributor would generate corrupt evidence of exactly the kind the
corpus exists to produce honestly.** That is not a cost worth paying for an earlier launch.

## Decision

1. **The canonical fingerprint is what chromaprint returns, and nothing else.** It is the base64
   ASCII byte string produced by `acoustid.fingerprint_file` (equivalently, `fpcalc`'s
   `FINGERPRINT=` value). `fingerprint_hash` is SHA256 of exactly those bytes, hex-digested.

2. **A client hashes what it computed, not what it stored.** Any transformation a client's storage
   layer applies — hex escaping, base64-of-base64, JSON quoting, a trailing newline — must be undone
   before hashing. Concretely, a stored value beginning with `\x` whose hex decodes to ASCII is a
   re-encoding and is decoded first. The test is not "what is in my column" but "what did
   chromaprint hand me".

3. **The canonical form is defined by the upstream tool, deliberately.** It is not a format this
   project invents, because every client already has it before it has anywhere to put it, and a
   definition that requires reading this ADR to implement is one that clients will get wrong
   silently. `ADR-0006` chose a composed identity string for `pipeline_version` because nothing
   upstream described a pipeline; something upstream does describe a fingerprint.

4. **Rows keyed on a non-canonical hash are re-contributed, not relabelled.** This is `ADR-0006`
   point 5's rule and it applies for a stronger reason here: the server cannot verify a re-key
   claim, and because SHA256 is one-way and the server never receives a fingerprint, **it cannot
   compute one either.** No server-side migration can fix this. Only a client holding the
   fingerprints can, which makes this the first corpus-wide correction that is not a migration.

5. **The stale keys are deleted through the existing admin delete path**
   (`DELETE /admin/corpus/recordings/{fingerprint_hash}`, `packages/server/app/api/admin.py:235`;
   the router carries no `/v1` prefix, `packages/server/app/api/admin.py:27`)
   rather than left to rot. A client can enumerate exactly which keys it orphaned, because it still
   holds the fingerprints that produced them. Left in place they are unconfirmable rows that no
   correct client will ever look up — the same category as the 47,486 that `ADR-0006` removed, and
   they should not be allowed to accumulate a second time under a different cause.

6. **The execution order is: rule, then re-contribution, then deletion — and new clients wait only
   for the rule.** `ADR-0009` point 6 can be built as soon as point 1 is settled, because a tool
   that hashes canonically is correct from its first contribution regardless of how much of the
   legacy corpus has been corrected. Blocking the tool on a backfill would invert the priority:
   the backfill improves a corpus of one contributor, the tool is what produces a second.

7. **The server does not validate canonicality, and cannot.** As in `ADR-0006` point 8 it believes
   what it is told; a hash is a hash. This is a client-side rule enforced by clients, and
   [ADR-0007](ADR-0007-a-pipeline-proves-itself-on-a-reference-signal.md)'s attestation is the only
   mechanism that would eventually catch a client that gets it wrong. Recorded here so that the
   absence of enforcement is a known property rather than an assumed one.

## Alternatives Considered

- **Canonicalise going forward and leave existing rows.** No re-contribution, no deletions, no
  coordination with Familiar. Rejected because it makes the split permanent rather than historical:
  ~14,000 recordings would sit under keys no correct client can produce, so every future contributor
  re-contributes them from scratch and the corpus carries two rows for each indefinitely. That is
  the cheapest option today and the one that costs most per contributor thereafter, which is the
  wrong way round for a commons whose whole problem is the second contributor.

- **Adopt the hex-escaped form as canonical**, since it is the majority. Rejected because it is not
  a description of a fingerprint but a description of a Postgres column: it would require every
  client to hex-encode a value chromaprint already gave it, for no reason expressible in terms of
  audio, and would leave the 11,364 raw rows wrong instead. Majority is not the right tiebreak when
  one of the candidates is an accident.

- **Have the server normalise on write.** Impossible rather than merely unwise: the server receives
  a digest, not a fingerprint. Worth stating because it is the first place one looks, and because it
  is a direct consequence of the privacy property `hash_fingerprint` was written for — the corpus
  cannot recover what it deliberately never learned.

- **Store the fingerprint alongside the hash so the server can re-key later.** Rejected: it undoes
  the one-way hashing that lets someone contribute without publishing which recordings they own,
  which is a property `ADR-0001` treats as load-bearing for participation. Fixing a key by removing
  a privacy guarantee trades a bounded, correctable problem for a permanent one.

- **Fix it in Familiar's column and let the hashes follow.** Tempting, and it may be worth doing
  anyway for Familiar's own hygiene, but it does not address the corpus: the rows already contributed
  keep their old keys whatever Familiar's column says afterwards. It is a prerequisite for a clean
  re-contribution, not a substitute for one.

## Consequences

- **Positive** — the key becomes a property of the recording rather than of a client's schema
  history. Two people who own the same recording reach the same key by computing the same thing,
  which is what `ADR-0001`'s premise ("the disagreement is about the audio, not about whose code
  ran") requires of the key as much as of the pipeline.

- **Positive** — `ADR-0009` point 6 is unblocked by point 1 alone, so the work that produces a
  second contributor does not queue behind a backfill.

- **Positive** — it is caught before the second contributor rather than after. The same defect found
  six months from now is found as unexplained disagreement between two parties, and
  `ADR-0008`'s output would be the evidence that misleads.

- **Tradeoff** — ~14,284 of Familiar's tracks must be re-contributed and their old keys deleted. The
  vectors exist locally, so this is re-sending rather than recomputing, and the backfill script
  already does lookup-then-contribute; but it is another long paced run with the failure modes the
  last two had.

- **Tradeoff** — the corpus shrinks again, and visibly, for the second time in a month. The
  count is not the metric that matters and this record should not pretend the optics are free.

- **Tradeoff** — correctness here rests entirely on clients, with no server-side check, until
  `ADR-0007` exists. The rule is simple enough to state in one sentence, which is the only defence
  available.

- **Follow-up** — Familiar needs the same decision recorded on its side and the two must move
  together, exactly as `ADR-0006` phases 2 and 3 did. Its `hash_fingerprint` is the single place the
  rule belongs.

- **Follow-up** — Familiar's `track_analysis.acoustid` should be normalised to one encoding for its
  own sake, which makes the re-contribution a plain backfill rather than one carrying a decode rule.

- **Follow-up** — whether any *other* client state feeds the key this way is now a question worth
  asking once rather than discovering twice.
