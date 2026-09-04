# ADR-0006: A Submission Declares the Pipeline That Produced It

Status: proposed

Date: 2026-09-03

Answers the follow-up [ADR-0005](ADR-0005-the-repository-is-a-workspace-of-peers.md) left open, and
enforces [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) point 4's "pinned
pipeline" at the only place it can be enforced: the write path. `ADR-0005` decided how the embedder
is delivered; this decides what the corpus does when a client's pipeline and its declared version
stop agreeing.

## Context

The README states the premise the whole project rests on:

> if two contributors disagree about a recording, the disagreement is about the audio, not about
> whose code ran.

That sentence is currently false, and nothing in the corpus would reveal it.

### The same fact is maintained in two repositories, by hand

| | |
|---|---|
| `PIPELINE_VERSION` (`packages/embed/src/clapback_embed/__init__.py:54`) | `laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32` |
| composed from | `CHECKPOINT` (`artifacts.py:48`), `FRONTEND_VERSION` (`mel.py:29`), `ARTIFACT_VERSION` (`artifacts.py:51`), `POOLING_VERSION` (`__init__.py:52`), precision |
| `EMBEDDING_VERSION` (Familiar, `backend/app/config.py:122`) | `7` |
| `CLAP_MODEL_VERSION` (Familiar, `community_cache.py:34`) | `laion/clap-htsat-unfused:v1` |
| what Familiar sends | `analysis_version=7`, `clap_model_version="laion/clap-htsat-unfused:v1"` |

Both sides are individually correct and well documented. This package says two vectors are comparable
only if `PIPELINE_VERSION` matches. Familiar's `ADR-0104` point 6 says `EMBEDDING_VERSION` "carries
the guarantee, and any change to windowing, pooling, mel parameters or truncation must bump it even
when the checkpoint is untouched."

They are the same guarantee, expressed twice, in two repositories, with no link between them.
`PIPELINE_VERSION` appears exactly once in Familiar — `backend/scripts/smoke_test_clap.py:42`, where
it is printed. Nothing compares it to anything.

### The server accepts whatever it is told, audited 2026-09-03

`app/api/routes.py:57-58`:

```python
analysis_version: int = Field(..., ge=1)
clap_model_version: str = Field(..., min_length=1, max_length=100)
```

Any integer above zero and any non-empty string under 100 characters. There is no registry of known
versions, no check that a version has been seen before, and nothing anywhere in the server that
knows what pipeline any stored vector came from. The key is
`(fingerprint_hash, analysis_version, clap_model_version)` (`app/db/models.py:30-32`), and its
version components are labels the client chooses.

### What actually happens when a pipeline moves and a version does not

Suppose `POOLING_VERSION` goes to 2 here and `EMBEDDING_VERSION` stays 7 there — one forgotten line
in a different repository, which is the whole of what it takes.

1. The client computes vectors under the new pooling and submits them as `analysis_version=7`.
2. For a fingerprint the corpus already holds, the key collides. First-write-wins keeps the old
   vector, so **the corpus quietly serves the old pipeline's answer to a client running the new
   one**, and the client cannot tell.
3. `contribute_embedding` scores the submission against the stored vector and writes the result to
   `submission_agreement` (`app/api/routes.py:174-185`).

Step 3 is the part that turns a bug into a corrupted instrument. That table exists to answer
`ADR-0001` point 9's question — *do two machines computing the same audio produce the same vector?*
— and its own docstring says a divergence at 0.05 would mean "a naive threshold would reject honest
data and the design needs a different shape". A version mismatch produces exactly that divergence,
between two honest clients, and records it as evidence about **contributors** when it is evidence
about **versions**. The corpus would show the symptom and blame the wrong cause.

Every threshold `ADR-0001` sets — the `identical` band at 0.999999, the 6.0e-08 storage floor, the
6.6e-11 cross-architecture agreement — assumes the compared vectors came from the same pipeline.
Nothing establishes that they did.

### Why the existing rows cannot simply be labelled

The corpus's 21,890 embeddings were computed by Familiar **before** `ADR-0105` moved it onto this
package, by a `torch`/`transformers` implementation that no longer exists in either repository. That
implementation's output matches the current one to 1.2e-07, which is inside the `identical` band —
but that is a *measured equivalence*, not a record of provenance. Backfilling those rows with
today's `PIPELINE_VERSION` would assert something nobody observed at the time.

`ADR-0001` point 10 already took the honest position on this data: kept, and marked unconfirmed.

## Decision

1. **A contribution declares the pipeline that produced it.** `POST /v1/embeddings` accepts a
   `pipeline_version` string — for any client using this package, `clapback_embed.PIPELINE_VERSION`
   verbatim. It is stored with the vector.

2. **The corpus refuses to hold two pipelines under one key.** The first submission carrying a
   `pipeline_version` for a given `(analysis_version, clap_model_version)` binds that key to that
   pipeline. A later submission declaring a *different* pipeline for the same key is **rejected**
   with an error naming both identities. This is the whole mechanism: the corpus cannot detect that
   a client's pipeline moved, but it can detect that a key now means two things, which is the part
   that damages the data.

3. **A rejected submission is not recorded as disagreement.** It never reaches
   `submission_agreement`. Recording it would put version drift into the measurement that exists to
   detect contributor drift, which is the specific failure this record is written to prevent.

4. **The declaration is optional, and its absence is not an error.** `ADR-0005` point 10 makes the
   endpoint contract a compatibility floor and Familiar sends nothing today. An undeclared
   submission is stored and served exactly as now. It simply binds nothing and can confirm nothing —
   the same shape as `ADR-0004` point 3's treatment of a missing `client_id`, and for the same
   reason: a field worth sending must never be a wall in front of contributing.

5. **Existing rows are not backfilled.** They stay unbound and unconfirmed, per `ADR-0001` point 10.
   A key holding pre-declaration rows is bound by the first declaration it receives, and that binding
   governs everything after it — not what came before, which nobody measured.

6. **The declaration is asserted, not proven, and must be described that way.** A client sends a
   string; the server believes it. This catches the accident — the forgotten bump, the stale build,
   the two-repository drift above — and catches nothing deliberate. `ADR-0004` point 10 made this
   distinction a rule for identity and it applies here unchanged: anywhere the difference blurs,
   something will eventually be relied on that cannot bear the weight.

7. **The reference-clip attestation of `ADR-0001` point 4 is how this becomes proof, and it upgrades
   this field rather than replacing it.** A client that also submits its vector for a fixed
   reference signal lets the server check the declaration against arithmetic instead of trusting it.
   The wire format does not change when that arrives; only the confidence attached to it does. It is
   deliberately not decided here — see the follow-up.

8. **`analysis_version` stays in the key.** It is the client's own pipeline counter, it predates this
   package, and its history (`config.py:111-115`: v2, v5, v6, v7) includes changes this project never
   saw. Removing it is a migration of every row for no gain that `pipeline_version` does not already
   provide.

## Alternatives Considered

- **Make `PIPELINE_VERSION` the key.** The tidiest answer, and nearly free at the wire: the string is
  55 characters and `clap_model_version` is already `String(100)`, so it fits with room to spare, and
  it already contains the checkpoint that column holds today. Rejected because the 21,890 existing
  rows are keyed on `laion/clap-htsat-unfused:v1` and would stop matching any lookup the moment a
  client sent the identity instead. That is either a corpus fork or a backfill asserting provenance
  nobody recorded, and the Context explains why the second is not honest. Worth revisiting the day a
  migration is happening anyway.

- **Have Familiar derive `analysis_version` from `PIPELINE_VERSION`.** Removes the duplicated fact at
  its source, which is the real defect, and needs nothing from the server. Rejected on two counts.
  `EMBEDDING_VERSION` is an integer with four versions of pre-`clapback-embed` history that no
  function of `PIPELINE_VERSION` can reproduce, so the derivation would need a hand-maintained
  mapping table — the same duplicated fact wearing a hat. And it fixes exactly one client:
  `ADR-0001` point 1 says Familiar is the first client and not the owner, and a second client would
  arrive with the identical hazard and no reason to know about it.

- **Validate against a registry of known-good versions on the server.** The server holds a list of
  pipelines it accepts and rejects anything else. Genuinely stronger against a *misconfigured* client
  than point 2 is, since it catches the first bad submission rather than the second. Rejected because
  it makes the server the gatekeeper of which pipelines may exist, so every embedder release requires
  a coordinated server deploy before anyone can contribute — reintroducing across the API exactly the
  two-place lockstep this record exists to remove.

- **Build reference-clip attestation now and skip the declaration.** Strictly more truthful: it
  proves the pipeline instead of believing a string, and `ADR-0001` point 4 already committed to it.
  Rejected as sequencing, not as direction. It needs a reference signal fixed and distributed,
  expected vectors held per pipeline, and a decision about what happens when attestation fails — and
  all of it lands on a corpus that today cannot even say which pipeline a row came from. Point 1 is
  the field attestation would attest *to*. Building the check before the claim inverts the order.

- **Do nothing; the discipline has held so far.** True: one client, one careful maintainer, and no
  incident. Rejected because the discipline has never been tested. `POOLING_VERSION` has been 1 for
  the life of the package, so the two-repository lockstep has not yet had to work even once, and the
  precedent runs the wrong way — `ADR-0104` moved every vector Familiar held by changing pooling
  alone, which is precisely the change that would break this, and it happened before the second
  variable existed to forget.

## Consequences

- **Positive** — the README's founding claim becomes enforceable rather than aspirational. A
  disagreement recorded in `submission_agreement` is about audio or about contributors, because the
  version case is refused at the boundary before it can be measured.
- **Positive** — the corpus can say what produced a row, which is a precondition for
  `ADR-0001` point 4's attestation, point 9's confidence, and any quorum built on either.
- **Positive** — the failure surfaces as a clear rejection naming both pipeline identities, at the
  moment of the mistake, to the person who made it. Today the same mistake is invisible for as long
  as nobody thinks to look.
- **Tradeoff** — a second contributor with a legitimately different pipeline is now rejected rather
  than silently mixed in. That is the intent, and it is also friction for exactly the strangers
  `ADR-0002` wants: the error message has to be good enough to be actionable by someone who has read
  none of these records.
- **Tradeoff** — point 6 means a client that lies is believed. The corpus gains bookkeeping, not a
  guarantee, until attestation lands.
- **Tradeoff** — one more nullable column and one more thing a client may forget to send. Point 4
  keeps it from being a wall, at the cost of the protection being opt-in until clients adopt it —
  including, at first, the client contributing 99.85% of the corpus.
- **Follow-up** — reference-clip attestation: what the signal is, whether it is synthesised
  deterministically rather than shipped as audio, where expected vectors live, and what a failure
  does. `ADR-0001` point 4 committed to it; point 7 above says where it plugs in.
- **Follow-up** — Familiar sends `pipeline_version=clapback_embed.PIPELINE_VERSION`. The same
  one-line change as `ADR-0004`'s `client_id`, in the same file, and worth doing together.
- **Follow-up** — whether the binding should also cover `/v1/features` and `/v1/analysis-detail`.
  `ADR-0001` point 4 gives embeddings the full standard and point 6 gives tier two less, so probably
  not — but those endpoints are live for Familiar under `ADR-0005`, and "probably not" should be
  written down as a decision rather than left as an omission.
