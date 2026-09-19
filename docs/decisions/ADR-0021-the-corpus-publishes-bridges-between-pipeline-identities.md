# ADR-0021: The Corpus Publishes Bridges Between Pipeline Identities

Status: proposed

Date: 2026-09-19

Extends [ADR-0006](ADR-0006-the-pipeline-identity-is-the-corpus-key.md),
[ADR-0013](ADR-0013-the-corpus-is-public-data-not-just-a-public-endpoint.md) and
[ADR-0019](ADR-0019-agreement-is-counted-per-recording-not-per-key.md); answers the half of
[ADR-0002](ADR-0002-the-corpus-answers-similarity-queries.md)'s open "reference identity" question
that a measurement can answer. Prompted by
[madenvel/KalinkaPlayer#128](https://github.com/madenvel/KalinkaPlayer/issues/128)'s third comment,
2026-09-16: *"Could old and new versions coexist while libraries are updated gradually?"* and
*"whether mixing the vectors actually works."*

## Context

`ADR-0006` made the pipeline identity half of the corpus key, and `ADR-0014` said any tool may
contribute under its own. Both are right, and together they have a consequence nobody has yet
paid for because the corpus holds one identity: **two identities cannot see each other.** A
similarity query under identity A searches A's rows; a lookup names one identity; a row under B
is invisible to both. Three situations make that cost real, and the second contributor's
comment names two of them.

1. **A new identity starts with an empty corpus.** When Kalinka's first install contributes, its
   users' similarity queries return only Kalinka rows — a handful — while the 25,886 reference
   rows sit beside them, unreachable. Every future identity begins the same way. `ADR-0009`'s
   "useful before the corpus is" was about the tool; this is about the corpus being useless *to*
   a new population for exactly as long as it takes that population to build its own.
2. **A model upgrade splits a library in two.** A tool that moves to a new checkpoint mints a new
   identity (`ADR-0006`); while its libraries re-index, each is two spaces, and discovery across
   them is impossible. The client README's "When your model changes" (2026-09-19) says so
   plainly; it does not yet say what would fix it.
3. **`ADR-0002` deferred "which identity is the reference"** to a time when there were two. The
   question assumes identities are incommensurable, so one must be crowned and the rest asked to
   compute it too. If they are *not* incommensurable, the question changes shape.

### What is measured, 2026-09-19

`packages/embed/scripts/measure_crosspipe.py`, results beside it: 493 tracks from one library,
one per album, FLAC and MP3 mixed, embedded under the corpus's identity (`laion/clap-htsat-unfused`,
whole-track windows) and Kalinka's (`music_audioset_epoch_15_esc_90.14`, three 10-s fragments),
plus each checkpoint under the other's windowing to separate the two effects. The music checkpoint
ran through `laion_clap`'s PyTorch implementation, not Kalinka's ONNX export.

- **The spaces are orthogonal.** Same-track cosine across the two checkpoints is −0.009; a random
  different track across them is −0.006. In one index holding both, none of a query's top 10 are
  from the other space and its own other-space vector ranks a median 769th of 985 — below random.
  Mixing vectors from two identities in one index is not approximately right; it is wrong.
- **The spaces agree about structure.** Top-10 neighbour overlap between them is 0.34 and the
  Spearman correlation of each track's full ranking is 0.72 — against 0.53 and 0.94 for a
  windowing change *within* one checkpoint.
- **A linear map fitted on paired tracks crosses them.** Ridge least squares (λ = 0.03), five-fold
  cross-validated, held-out queries against the full gallery, corpus → Kalinka: R@1 0.86, R@10
  0.99, same-track cosine median 0.876 (min 0.34), top-10 overlap 0.56 — *more* than the
  windowing change alone preserves (0.53). Kalinka → corpus: 0.85 / 0.98 / 0.947 / 0.58.
  Checkpoint alone, whole-track windowing on both sides: 0.96 / 1.00 / 0.92 / 0.64. Orthogonal
  Procrustes — a pure rotation, no parameter — is within a few points everywhere and needs fewer
  pairs. R@1 rose from 0.76 at 200 tracks to 0.86 at 493; more pairs help.

The reading: a checkpoint swap is mostly a re-coordinatisation, and a linear map undoes most of
it; a windowing change discards audio the model never saw, and nothing recovers that. The
caveats: one library, a 493-track gallery (R@10 is easier at 493 than at 25,886; the overlap
figure is the portable one), the music checkpoint through a different implementation, and a
worst held-out track at 0.34 that has not been examined.

### What the corpus uniquely has

A bridge is fitted from tracks embedded under both identities. A single tool holds those only if
it runs both models. The corpus holds them the moment two identities overlap on a recording —
through `ADR-0019`'s claims join (and `ADR-0020`'s recording-keyed rows), which is the same join
that counts agreement. The paired data for every bridge is a by-product of contribution, it grows
without anyone doing anything, and it is composed of what *different* contributors computed for
the *same* recordings. That is a thing only a commons can make, and the first artefact the corpus
could publish that no single contributor could have produced.

### What is actually in the corpus, measured 2026-09-19

One identity (`GET /v1/pipelines`): 25,886 rows, 23,196 named. **Zero cross-identity pairs.**
Nothing here can be fitted until a second identity contributes rows that share recordings with
the first, which is why this record is proposed now and built later.

## Decision

1. **The corpus fits a linear bridge between every pair of identities that share enough
   recordings, and publishes it as data.** A bridge from identity A to identity B is a 512×512
   float32 matrix W such that `normalise(v_A · W)` is searched against B's rows. It is fitted from
   the rows of A and B that name the same recording (`ADR-0019`'s join: MusicBrainz recording id or
   AcoustID track id; `ADR-0020`'s recording-keyed rows included), one pair per recording, the
   nearest rows when a recording has several under one identity (`ADR-0019` point 4's rule).

2. **The fit is orthogonal Procrustes below 1,000 pairs and ridge least squares above, and the
   record does not pretend the threshold is measured.** Procrustes is a rotation: fewer effective
   parameters, no hyper-parameter, within a few points of ridge at 493 pairs and better behaved
   with fewer. Ridge's λ is chosen by the same five-fold cross-validation that produces the
   served metrics. The threshold is a starting rule to be replaced by the first measurement on
   real pairs, and the Implementation block will say what replaced it.

3. **A bridge is published only with its held-out metrics, and never with a verdict.** Each
   bridge carries `pairs`, `fitted_at`, `method`, and five-fold held-out `recall_at_1`,
   `recall_at_10`, `same_track_cosine_median` and `top10_overlap` — the four figures in the
   measurement above, computed the same way. `ADR-0008`'s rule applies: the corpus serves the
   number and no threshold declares a bridge good. The one gate is existence — **no bridge is
   fitted under 200 pairs**, because a 512-dimensional map from fewer is a fit to noise, and
   publishing it with honest metrics would still invite use.

4. **Bridges ship in the weekly export, beside the rows.** `ADR-0013`'s export gains
   `bridges.json` (the metrics for every published bridge) and one `bridge-{A}-{B}.npy` per
   bridge — 1 MB each, CC0 like everything else in the bucket. The fit runs in the export job,
   weekly, and refits every bridge from the pairs that exist that week; a bridge's `fitted_at`
   and `pairs` say how current it is. **A tool that needs a bridge downloads it and translates
   locally** — a half-migrated library searches its old half through the matrix on the machine,
   with no round trip and no new endpoint. That is the primary interface, and it is the one
   Kalinka's upgrade case needs.

5. **`GET /v1/bridges` lists what is published**, with the metrics of point 3 and the export
   URL of each matrix, cached like `/v1/pipelines`. It exists so a tool can ask "is there a
   bridge from mine to the big one, and how good is it?" without parsing the export manifest.

6. **`/v1/similar` learns `across=true`, second, and labels what it returns.** With the flag,
   the query is translated through every published bridge from its identity and searched in each
   target identity too; each translated neighbour carries `translated_from`, the bridge's
   `top10_overlap`, and the same `recording_mbid` / `recording_claims` fields as a native one.
   Without the flag nothing changes. The default stays `false` because a translated ranking is a
   translation, not a measurement, and a caller should ask for it knowingly. This point is built
   after points 1–5 have run for at least one export cycle on real pairs, not before.

7. **A translated vector is never stored.** The bridge is applied at query time or on the
   client; the corpus holds only what a pipeline computed, under the identity that computed it.
   `ADR-0006` point 5 forbids relabelling, and a translated vector stored under B is a relabelled
   A vector with a loss attached.

8. **What this does to `ADR-0002`'s question.** "Which identity is the reference" was a question
   about which population every tool must join. With bridges, a tool joins its own and can
   *reach* every other, at a stated loss. The corpus therefore designates no reference identity;
   what it publishes instead is `GET /v1/pipelines`' row counts — which identity is largest is a
   fact, not a decision — and the bridges between them with their metrics. If a future record
   wants a reference, it will be because the loss turned out to matter, and the metrics will say.

9. **Not decided here.** Non-linear or learned bridges — no evidence yet that they are needed,
   and a linear one already preserves more than a windowing change does. Bridges between
   identities that differ only in precision (`+fp32` / `+int8` of one pipeline) — those spaces are
   not orthogonal, a bridge between them is nearly the identity, and whether the convention should
   treat them as one population is `ADR-0014` point 5's question, not this record's. Whether a
   bridge is fitted per contributor pair as well as per identity pair, to detect a contributor
   whose vectors do not fit the population — that is `ADR-0007`'s territory and is noted there
   as a possibility this record creates.

10. **Execution order:** nothing until the corpus holds a second identity with ≥ 200 shared
    recordings — expected to be Kalinka's under `ADR-0020`. Then: the fit in the export job
    (points 1–4), with the 493-track measurement as its test fixture (a bridge fitted from those
    pairs must reproduce the metrics above to within rounding); `GET /v1/bridges` (point 5); a
    client helper `Corpus.bridge(from, to)` that downloads the matrix and returns a callable;
    the README's "When your model changes" updated to say the bridge exists; then, after one
    export cycle, `across=true` (point 6). The Implementation block carries the first real
    bridge's metrics — fitted from two contributors' rows rather than one library run twice —
    which is the number this record is actually waiting for.

## Alternatives Considered

- **Crown a reference identity and ask every tool to compute it too.** `ADR-0002`'s implicit
  plan. Rejected: it asks a tool to run a second model — on a Raspberry Pi, for Kalinka — which
  is precisely the cost the commons exists to remove, and it makes the reference a permanent
  choice the corpus can never move without every contributor recomputing. A bridge costs 1 MB and
  a matrix multiply, and moves when the data does.

- **Leave bridges to the tools.** Any tool may fit its own from tracks it holds under both
  identities — nothing here forbids it, and the export makes it easy. Rejected as the *only*
  path: a tool holds pairs only if it runs both pipelines, which is the previous alternative
  under another name, and the corpus's pairs come from many libraries where a tool's come from
  one. The measurement above is one library; whether a bridge holds across libraries is exactly
  what the corpus can measure and a tool cannot.

- **Store translated vectors so that one identity's rows appear under another.** Rejected by
  `ADR-0006` point 5 and point 7 above: it is relabelling with loss, and after the fact nothing
  distinguishes a translated row from a computed one. The corpus's one claim is that a row is what
  a pipeline produced; a translation stored as a row breaks it.

- **A learned non-linear map.** Rejected for now: no evidence it is needed (a linear map
  preserves 0.56 of the top-10 where a windowing change within one model preserves 0.53), it
  needs more pairs than the corpus will have for some time, and it cannot ship as a 1 MB file a
  tool applies with one line. Left open in point 9 for the day the linear metrics plateau.

- **Do nothing; each identity is its own corpus.** The status quo, and the cheapest. Rejected:
  it makes the existing 25,886 rows worthless to every new population until that population has
  rebuilt them, which is the cold start that has kept the corpus at one contributor, and it leaves
  the upgrade question — the second contributor's stated *bigger* concern — answered with
  "coexistence, but no search across."

## Consequences

- **Positive:** a new identity can search the whole corpus from its first row, at a stated loss.
  The cold start that every new population would otherwise face is reduced to fitting a matrix
  once 200 recordings overlap.
- **Positive:** the upgrade path is complete. Old rows stay (`ADR-0006`), the re-index is paid once
  per population (README, 2026-09-19), and the half-migrated library can be searched across
  through a bridge it downloads. Kalinka's condition for a PR — a practical migration path — is
  met in writing.
- **Positive:** the corpus publishes something no contributor could make alone, fitted from
  different contributors' vectors for the same recordings. It is the first artefact of the commons
  *as* a commons, and it is CC0 data in the same bucket as the rows.
- **Positive:** `ADR-0002`'s reference question is answered by not needing one. Which identity is
  largest is served as a count; which to join is the tool's choice; every other is reachable.
- **Tradeoff:** a translated ranking is lossy, and a client that treats it as native will draw
  conclusions the numbers do not support. Point 3's metrics and point 6's labels are the only
  guard; the record cannot stop a caller ignoring them.
- **Tradeoff:** bridges are fitted from whatever recordings two populations happen to share,
  which is not a random sample of music. A bridge fitted mostly on one genre may translate another
  badly, and the served metrics — held out from the same pairs — will not show it. The first
  real bridge should be checked against the 493-track measurement for exactly this.
- **Tradeoff:** bridges multiply with identities — k identities is up to k(k−1) matrices. Each
  is 1 MB and seconds to fit, and only pairs over 200 shared recordings exist, so the cost is
  bounded by overlap rather than by k; but the export grows, and the manifest must stay legible.
- **Follow-up:** `ADR-0007` gains a possible instrument — a contributor whose rows do not fit the
  bridge between its identity and another is a contributor worth looking at — noted there when
  this is accepted. **Follow-up:** `ADR-0014` point 5's precision question becomes concrete the
  first time a `+fp32` / `+int8` pair of one pipeline would qualify for a bridge that is nearly
  the identity. **Follow-up:** the README's "When your model changes" says the bridge is not
  decided; it changes when this is accepted and again when the first one is published.
