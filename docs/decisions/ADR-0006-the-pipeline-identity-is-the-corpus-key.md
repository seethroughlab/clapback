# ADR-0006: The Pipeline Identity Is the Corpus Key

Status: accepted

Date: 2026-09-03

Answers the follow-up [ADR-0005](ADR-0005-the-repository-is-a-workspace-of-peers.md) left open, and
enforces [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) point 4's "pinned
pipeline" at the only place it can be enforced: the key. **On acceptance this supersedes `ADR-0001`
point 10**, which kept the existing embeddings and marked them unconfirmed; point 5 below recomputes
them instead.

Implementation:
- Accepted 2026-09-04. **Point 6 phase 1 is built** (2026-09-05); phases 2 to 4 are outstanding.
- Phase 1: `pipeline_version` is accepted on `POST /v1/embeddings`, stored on the row
  (`packages/server/app/db/models.py`, migration `010_embeddings_pipeline_version`), returned by
  the lookup and by `/v1/similar`, and offered there as a filter. The key is unchanged, the column
  is nullable, and nothing is rejected — so no client in the field notices.
- **Point 7's guard ships with phase 1 rather than with phase 4**, because it is needed most while
  the key still excludes the pipeline: two incomparable vectors can land on one row today, and
  their cosine similarity would be a real number measuring version drift inside the table that
  exists to measure contributor drift. `contribute_embedding` records an agreement only when the
  submitted and stored pipelines are equal, which includes both being absent — the legacy case,
  unchanged.
- **No stored row is relabelled**, not even a null one from a submission that declares a pipeline.
  Point 5 says the existing rows are recomputed; writing a pipeline onto a vector nobody can vouch
  for would assert exactly the provenance phase 4 is built to trust.
- **Deployed 2026-09-05**, migration `010` applied to the live corpus. Measured immediately after:
  47,486 rows, 0 of them declaring a pipeline. That is the honest state rather than a gap — nothing
  recorded the pipeline for the 21,890 legacy rows, and the v7 rows were contributed by a client
  with no field to declare it in. Phase 2 is what starts populating it. `/v1/similar` accordingly
  answers a `pipeline_version` filter with `{"neighbours": [], "searched": 0}`, which is the
  correct answer and looks like a broken endpoint; the API page says so in as many words.
- Point 6 phase 4 needed `ADR-0004` point 7's delete path, **which now exists** — that
  prerequisite is met, and phase 4 is blocked only on phases 2 and 3 landing in Familiar.
- Point 5 supersedes `ADR-0001` point 10, whose `Status:` line now records it.

## Context

The README states the premise the whole project rests on:

> if two contributors disagree about a recording, the disagreement is about the audio, not about
> whose code ran.

That sentence is currently false, and nothing in the corpus would reveal it.

### The same fact is maintained in two repositories, by hand

| | |
|---|---|
| `PIPELINE_VERSION` (`packages/embed/src/clapback_embed/__init__.py:65`) | `laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32` |
| composed from | `CHECKPOINT` (`packages/embed/src/clapback_embed/artifacts.py:48`), `FRONTEND_VERSION` (`packages/embed/src/clapback_embed/mel.py:29`), `ARTIFACT_VERSION` (`packages/embed/src/clapback_embed/artifacts.py:51`), `POOLING_VERSION` (`packages/embed/src/clapback_embed/__init__.py:63`), precision |
| `EMBEDDING_VERSION` (Familiar, `backend/app/config.py:122`) | `7` |
| `CLAP_MODEL_VERSION` (Familiar, `backend/app/services/community_cache.py:34`) | `laion/clap-htsat-unfused:v1` |
| what Familiar sends | `analysis_version=7`, `clap_model_version="laion/clap-htsat-unfused:v1"` |

Both sides are individually correct and well documented. This package says two vectors are comparable
only if `PIPELINE_VERSION` matches. Familiar's `ADR-0104` point 6 says `EMBEDDING_VERSION` "carries
the guarantee, and any change to windowing, pooling, mel parameters or truncation must bump it even
when the checkpoint is untouched."

They are the same guarantee, expressed twice, in two repositories, with no link between them.
`PIPELINE_VERSION` appears exactly once in Familiar — `backend/scripts/smoke_test_clap.py:42`, where
it is printed. Nothing compares it to anything.

### The key is made of the wrong things, audited 2026-09-03

The key is `(fingerprint_hash, analysis_version, clap_model_version)` (`packages/server/app/db/models.py:29-31`), and
the server accepts any value for either version component (`packages/server/app/api/routes.py:57-58`):

```python
analysis_version: int = Field(..., ge=1)
clap_model_version: str = Field(..., min_length=1, max_length=100)
```

Neither component means what the key needs it to mean:

- **`clap_model_version` is the checkpoint, and the checkpoint is not the identity.** This is the
  error `ADR-0104` point 6 corrected on Familiar's side and the package's own module docstring warns
  about: pooling can change every vector while `laion/clap-htsat-unfused` stays fixed.
- **`analysis_version` is a client-owned counter that moves for unrelated reasons.** Its history
  (`backend/app/config.py:111-115`) records `v6: Matched features version at loudness addition` — a bump taken to
  stay in step with `FEATURES_VERSION`, not because the embedding pipeline changed. A key component
  that moves when the pipeline does not is not a comparability key; it is a client's changelog.

So the key over-fragments on one axis and under-detects on the other, and the one string that
actually expresses comparability is not in it.

### What happens today when a pipeline moves and a version does not

Suppose `POOLING_VERSION` goes to 2 here and `EMBEDDING_VERSION` stays 7 there — one forgotten line
in a different repository, which is the whole of what it takes.

1. The client computes vectors under the new pooling and submits them as `analysis_version=7`.
2. For a fingerprint the corpus already holds, the key collides. First-write-wins keeps the old
   vector, so **the corpus quietly serves the old pipeline's answer to a client running the new
   one**, and the client cannot tell.
3. `contribute_embedding` scores the submission against the stored vector and writes the result to
   `submission_agreement` (`packages/server/app/api/routes.py:174-185`).

Step 3 is what turns a bug into a corrupted instrument. That table exists to answer `ADR-0001`
point 9's question — *do two machines computing the same audio produce the same vector?* — and its
own docstring says a divergence at 0.05 would mean "a naive threshold would reject honest data and
the design needs a different shape". A version mismatch produces exactly that divergence, between two
honest clients, and records it as evidence about **contributors** when it is evidence about
**versions**. The corpus would show the symptom and blame the wrong cause.

Every threshold `ADR-0001` sets — the `identical` band at 0.999999, the 6.0e-08 storage floor, the
6.6e-11 cross-architecture agreement — assumes the compared vectors came from the same pipeline.
Nothing establishes that they did.

### The existing rows have no provenance, and cannot be given one

The corpus's 21,890 embeddings were computed by Familiar **before** `ADR-0105` moved it onto this
package, by a `torch`/`transformers` implementation that no longer exists in either repository. That
implementation's output matches the current one to 1.2e-07, inside the `identical` band — but that is
a *measured equivalence between implementations*, not a record of what produced any particular row.
Nobody observed which code computed row 12,000.

Relabelling them with today's `PIPELINE_VERSION` would assert exactly that unobserved fact, and would
put the corpus's least trustworthy data behind its strongest claim. `ADR-0001` point 10 took the
defensible position available at the time — keep them, mark them unconfirmed — but that leaves a
permanent unconfirmed tier inside a corpus whose whole argument is provenance.

**Recomputing them is affordable, and the mechanism already exists.** Familiar's `ADR-0104` point 7
records that re-analysis rides the existing sync phase rather than being scheduled, selecting on
`embedding_version < EMBEDDING_VERSION` — today at `backend/app/services/tasks/analysis_queue.py:119` and `:276`, the ADR's own
line reference having drifted. Moving every vector in the library by bumping that constant is not
hypothetical work: `ADR-0104` did it, from middle-ten-seconds to a whole-track mean, in September.

## Decision

1. **The pipeline identity is the key.** The embeddings key becomes
   `(fingerprint_hash, pipeline_version)`, where `pipeline_version` is `PIPELINE_VERSION` verbatim.
   Two vectors share a key if and only if they are comparable, which is the property the key existed
   to have and has never had.

2. **`clap_model_version` leaves the key.** The checkpoint is already the first component of the
   identity string, so storing it separately as a key lets two fields disagree about the same fact.
   It is retained as a recorded column for the existing rows and for reading history, and it is no
   longer part of what identifies a vector.

3. **`analysis_version` leaves the key and stays as metadata.** It is the contributing client's own
   counter, it is useful provenance, and it is not a statement about comparability — as its own
   history shows. Clients keep sending it; the corpus stops keying on it.

4. **A contribution without a `pipeline_version` is rejected once the key changes.** There is no
   sensible key for a vector that will not say what produced it. This is deliberately unlike
   `ADR-0004` point 3's treatment of `client_id`, and the difference is the point: an unattributed
   submission is still evidence, whereas an unidentified pipeline is a vector that cannot be compared
   with anything, including itself later.

5. **The existing 21,890 rows are recomputed, not relabelled, and this supersedes `ADR-0001`
   point 10.** Familiar bumps `EMBEDDING_VERSION`, its existing re-analysis path recomputes the
   library through `clapback-embed`, and the results are contributed under real declared provenance.
   The legacy rows are then deleted rather than carried. This costs CPU that has already been spent
   once before for a smaller reason, and it buys a corpus with no unconfirmed tier in it at all.

6. **The change is phased so the endpoint contract is never broken**, honouring `ADR-0005` point 10
   rather than waiving it. In order, each step landing before the next begins:

   | phase | change | who |
   |---|---|---|
   | 1 | Server accepts and stores `pipeline_version` as an optional column. Key unchanged; nothing rejected. | this repository |
   | 2 | Familiar sends `pipeline_version=clapback_embed.PIPELINE_VERSION` on every contribution. | Familiar |
   | 3 | Familiar bumps `EMBEDDING_VERSION`; re-analysis recomputes and re-contributes the library with provenance. | Familiar |
   | 4 | Server switches the key, drops the legacy rows, and begins rejecting undeclared contributions per point 4. | this repository |

   Phase 4 needs the delete path `ADR-0004` point 7 already made non-optional, so that ADR is a
   prerequisite of this one rather than a parallel track.

7. **A mismatched submission is never recorded as disagreement.** Whatever the key is at a given
   phase, a submission whose declared pipeline differs from the stored row's must not reach
   `submission_agreement`. Recording it would put version drift into the measurement that exists to
   detect contributor drift, which is the specific failure this record is written to prevent.

8. **The declaration is asserted, not proven, and must be described that way.** A client sends a
   string; the server believes it. This catches the accident — the forgotten bump, the stale build,
   the two-repository drift above — and catches nothing deliberate. `ADR-0004` point 10 made this
   distinction a rule for identity and it applies here unchanged.

9. **The reference-clip attestation of `ADR-0001` point 4 is how this becomes proof, and it upgrades
   the same field rather than replacing it.** A client that also submits its vector for a fixed
   reference signal lets the server check the declaration against arithmetic instead of trusting it.
   The key does not change when that arrives; only the confidence attached to it does. Deliberately
   not decided here — see the follow-up.

## Alternatives Considered

- **Keep the key and add `pipeline_version` as a binding the server enforces.** The incremental
  version of this record, and what it originally proposed: the first declaration for a key binds it,
  a contradicting one is rejected, existing rows are left unbound and unconfirmed per `ADR-0001`
  point 10. It needs no migration, no recomputation and no coordinated change in another repository,
  and it detects the drift that matters. Rejected once recomputing the corpus was on the table,
  because it leaves three fields describing one fact — a key that over-fragments, a checkpoint that
  under-detects, and a declaration bolted alongside to catch the difference — and a permanent
  unconfirmed tier that no later work can clear. It buys detection while leaving the thing being
  detected in place.

- **Relabel the existing rows with today's `PIPELINE_VERSION`.** Free, instant, and the vectors are
  almost certainly right — the implementations agree to 1.2e-07. Rejected because it asserts
  provenance nobody recorded, and it does so for the least scrutinised data in the corpus. A commons
  whose argument is "we know what produced this" cannot start by guessing.

- **Have Familiar derive `analysis_version` from `PIPELINE_VERSION`.** Removes the duplicated fact at
  its source and needs nothing from the server. Rejected on two counts. `EMBEDDING_VERSION` is an
  integer with four versions of pre-`clapback-embed` history that no function of `PIPELINE_VERSION`
  can reproduce, so it would need a hand-maintained mapping — the same duplicated fact wearing a hat.
  And it fixes exactly one client: `ADR-0001` point 1 says Familiar is the first client and not the
  owner, and a second would arrive with the identical hazard and no reason to know about it.

- **Validate against a registry of known-good versions on the server.** The server holds a list of
  pipelines it accepts and rejects anything else, catching a misconfigured client on its *first* bad
  submission rather than its second. Rejected because it makes the server the gatekeeper of which
  pipelines may exist, so every embedder release needs a coordinated server deploy before anyone can
  contribute — reintroducing across the API exactly the two-place lockstep this record removes.

- **Build reference-clip attestation now and skip the key change.** Strictly more truthful: it proves
  the pipeline rather than believing a string, and `ADR-0001` point 4 already committed to it.
  Rejected as sequencing, not direction. Attestation verifies a claim, and the claim has to be part
  of the record first — a corpus that cannot say which pipeline a row came from has nothing for an
  attestation to confirm. Point 1 is what attestation attests to.

- **Do nothing; the discipline has held so far.** True: one client, one careful maintainer, no
  incident. Rejected because the discipline has never been tested. `POOLING_VERSION` has been 1 for
  the life of the package, so the two-repository lockstep has not had to work even once, and the
  precedent runs the wrong way — `ADR-0104` moved every vector Familiar held by changing pooling
  alone, which is precisely the change that would break this, and it happened before the second
  variable existed to forget.

## Consequences

- **Positive** — the README's founding claim becomes structurally true rather than aspirational. Two
  vectors under one key are comparable by construction, so a disagreement in `submission_agreement`
  is about audio or about contributors, with the version case designed out instead of detected.
- **Positive** — the corpus ends the migration with **no unconfirmed tier**. Every row says what
  produced it, which is the precondition for `ADR-0001` point 4's attestation, point 9's confidence,
  and any quorum built on either.
- **Positive** — three fields describing one fact collapse into one. `clap_model_version` and
  `analysis_version` stop being able to contradict each other or the pipeline.
- **Tradeoff** — **this is a coordinated migration across two repositories, and the largest one so
  far.** Four phases, a schema change, a full re-analysis of Familiar's library, and a delete path
  that does not exist yet. Point 6 is what keeps it from breaking the client, and it is still four
  chances to get an order wrong.
- **Tradeoff** — recomputing 21,890 embeddings costs CPU on the contributor's machine for vectors
  that are, in all likelihood, already correct. The purchase is provenance, not accuracy, and that
  is worth being explicit about.
- **Tradeoff** — point 4 makes `pipeline_version` mandatory, which is a harder wall than anything
  `ADR-0004` put in front of contributing. Justified because an unidentified vector is unusable
  rather than merely unattributed, but it does mean a client that has not been updated stops being
  able to contribute at phase 4 rather than degrading gracefully.
- **Tradeoff** — point 8 means a client that lies is believed. The corpus gains a well-shaped key,
  not a guarantee, until attestation lands.
- **Follow-up** — reference-clip attestation: what the signal is, whether it is synthesised
  deterministically from a fixed seed rather than shipped as audio — which would also keep the
  decoder, which `PIPELINE_VERSION` does not cover, out of the check — where expected vectors live,
  and what a failure does.
- **Follow-up** — `ADR-0001` point 10's `Status:` line is updated to record the partial supersession
  when this is accepted.
- **Follow-up** — whether the same key change should reach `/v1/features` and `/v1/analysis-detail`.
  `ADR-0001` point 4 gives embeddings the full standard and point 6 gives tier two less, so probably
  not — but those endpoints are live for Familiar under `ADR-0005`, and "probably not" should be
  written down as a decision rather than left as an omission.
