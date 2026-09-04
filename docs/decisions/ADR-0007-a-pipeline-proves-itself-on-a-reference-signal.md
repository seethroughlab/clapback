# ADR-0007: A Pipeline Proves Itself on a Reference Signal

Status: proposed

Date: 2026-09-03

Answers [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) point 4's
"reference-clip attestation", and takes the follow-up
[ADR-0006](ADR-0006-the-pipeline-identity-is-the-corpus-key.md) point 9 named. `ADR-0006` made the
pipeline identity the corpus key; this makes a client demonstrate it is running the pipeline it
claims, instead of being believed.

**It is deliberately not built yet.** Point 11 states the precondition — enough independent clients
for a quorum to mean anything — and point 10 splits the mechanism so the half that is useful with one
contributor can land first.

## Context

`ADR-0006` point 8 states its own limit plainly: a client sends a string and the server believes it.
That is enough for the failure it was written against — a forgotten version bump in another
repository — and it is not enough for anything else.

### What a declaration cannot catch

The declaration comes from the same install that computed the vector, so it is wrong in exactly the
cases where the vector is wrong for the same reason:

- **A stale artifact.** `ARTIFACT_VERSION` is a constant in the source
  (`packages/embed/src/clapback_embed/artifacts.py:51`), and the ONNX files it describes live outside
  the package in `CLAPBACK_MODEL_DIR`. Upgrading the package upgrades the constant; it does not
  re-export the encoders. A client running new code against old artifacts declares the new identity
  truthfully, by its own lights, and computes something else.
- **The wrong precision.** `Precision.FP16` exists for vectors that never leave the machine, and
  `PIPELINE_VERSION` hard-codes `+fp32` in its string
  (`packages/embed/src/clapback_embed/__init__.py:54-59`) rather than deriving it from the session
  that ran. A client embedding at fp16 declares fp32.
- **An unverified execution provider.** `artifacts.py` records that an accelerated provider "is not
  known to produce vectors comparable with the CPU ones, and that has to be measured per provider
  before anything is contributed". Nothing enforces that measurement, and the declaration does not
  mention the provider at all.
- **A partial upgrade.** Any install where the source and the environment disagree.

Every one of these produces a confident, sincere, wrong declaration.

### The corpus cannot check by recomputing

The obvious answer — have the server embed the audio itself and compare — is not available, and it is
worth writing down why so nobody re-derives it. The server has no audio: `ADR-0001`'s privacy
position is that only fingerprint hashes are transmitted, and the whole point of contribution is that
the expensive computation happened on the contributor's machine. Even given the audio, the encoders
are 614 MB and `ADR-0003` sized the host at two gigabytes of RAM to hold an HNSW index. A server that
can verify by recomputation is a server that did not need contributions.

So verification has to be something the client computes and the server can check cheaply.

### The measurements that set the tolerance

`ADR-0001` and the package README already establish, as cosine distance from 1.0:

| difference | distance | |
|---|---|---|
| CPU vs CUDA | 6.6e-14 | honest |
| two architectures (arm64 vs x86_64) | 6.6e-11 | honest |
| `float4` storage round-trip | 6.0e-08 | honest |
| **fp32 vs fp16** | **1.5e-06** | **not corpus-safe** |
| two different rips of one recording | 3e-04 – 3e-03 | different audio |

The `identical` band at 0.999999 — a distance of 1e-6 — sits in the gap. It admits every honest
architecture, every accelerator measured so far, and the storage round-trip, and it excludes fp16 by
half an order of magnitude. The threshold this record needs already exists and is already measured;
it does not have to be invented.

### There is one contributor

Worth stating before designing a consensus mechanism: the corpus has 9 contributing addresses, one of
which accounts for 99.85%, and `ADR-0004` point 4 established that `contributor_count` counts POSTs
rather than clients. Anything requiring two independent parties is inert today. `ADR-0001` point 8
already identified what changes that, and it is not a verification scheme.

## Decision

1. **A pipeline proves itself by embedding a fixed reference signal.** A client submits the vector
   its pipeline produces for that signal, and the corpus compares it with what the same pipeline
   produced elsewhere. A declaration that survives this is a demonstration; one that does not is a
   client whose environment is not what its source says.

2. **The reference signal is generated arithmetically, not shipped as audio.** It is defined by this
   record, in integers, so that any implementation in any language can reproduce it exactly:

   ```
   x₀ = 20260903
   xₙ₊₁ = (1664525 · xₙ + 1013904223) mod 2³²
   sₙ  = (((xₙ₊₁ >> 16) & 0xFFFF) − 32768) / 32768.0
   ```

   1,200,000 samples at 48 kHz — **25 seconds**, chosen because it is two full 10-second windows plus
   a 240,000-sample remainder. That exercises the windowing rule, the dropping of a trailing partial
   window, and the mean-then-normalise pooling, which one window would not.

   No audio file, no decoder, and no transcendental function: the sample values are exact in integer
   arithmetic followed by a single division by a power of two, so every machine starts from a
   bit-identical array. This is deliberate. The decoder is **not** part of `PIPELINE_VERSION`, so a
   `libsndfile` difference showing up as an attestation failure would be a false positive about the
   one thing the identity does not claim.

3. **The package ships the generator and the attestation call.** `reference_signal()` returns the
   array and `attest()` returns the vector, so no client reimplements the recurrence above and no
   client's attestation differs because it read the spec differently.

4. **The expected vector is agreed, not configured.** The corpus is not told what a pipeline should
   produce; it learns it:

   - The first attestation for a pipeline records it and marks the pipeline **provisional**.
   - Once **K distinct client identifiers** have attested within the `identical` band of the first,
     the pipeline is **confirmed**.
   - An attestation outside the band from a confirmed pipeline is **rejected**, and that client's
     contributions under that pipeline are refused.

   Configuration was the alternative and it is the one `ADR-0006` already rejected in another form:
   a server holding expected vectors is a server that must be deployed before anyone can contribute
   under a new release, which is the two-place lockstep this line of records exists to remove.

5. **K is a threshold, starts at 2, and is raised deliberately.** Two is the smallest number that
   means anything, since one client agreeing with itself is not evidence. It is raised as the corpus
   acquires contributors, the same way `ADR-0004` point 9 raises the row ceiling — a decision, not a
   surprise.

6. **The tolerance is the `identical` band, 0.999999.** Not a new number: the table above shows it
   admits every honest difference measured to date and excludes fp16, which `ADR-0001` point 4's
   pinned precision forbids anyway. Attestation vectors are stored like any other, so the 6.0e-08
   `float4` floor applies to the comparison and sits comfortably inside.

7. **A provisional pipeline still accepts contributions.** This is the bootstrap, and without it the
   scheme cannot start: confirmation needs two clients, the second client arrives after the first, and
   a corpus that refused contributions until confirmation would refuse the submissions that lead to
   it. What changes with confirmation is the confidence served alongside the data under `ADR-0001`
   point 9 — not whether the data is accepted.

8. **Attestation is per pipeline and per client, and never per contribution.** It costs two windows of
   encoder work, once, when a client first contributes under a pipeline it has not attested. It does
   not gate, delay or slow a submission, which `ADR-0001` point 4 requires of anything sharing the
   embedding's path.

9. **This proves the client can compute the reference, and nothing more.** It does not prove the same
   code produced their contributions, and a client that attests correctly can still submit anything
   it likes. Attestation moves the guarantee from *says so* to *demonstrably has a working copy of the
   pipeline*, which covers every failure in the Context and no deliberate one. `ADR-0004` point 10 and
   `ADR-0006` point 8 made this distinction a rule; it applies here unchanged, and the protection
   remains consensus.

10. **Attestation records before it refuses, and the two are separate steps.** The first build stores
    attestations and reports on them; it rejects nothing. Refusal — point 4's rejection of a
    contradicting attestation, and the confirmed/provisional state it depends on — is a later step
    taken on evidence.

    This is not caution for its own sake, it is the pattern this project already used and was right
    about. `SubmissionAgreement` exists because the answer to "do two machines agree?" was being
    thrown away, and its docstring is explicit that it "records, it does not gate... to measure the
    noise floor **before** designing a verification scheme on top of it". Attestation is that
    verification scheme. Building its refusal before its measurement would repeat exactly the mistake
    that table was written to correct.

    Recording is also useful with one contributor, which refusal is not: one client on two machines,
    two architectures or two artifact exports produces comparable attestations, and that is the
    measurement the README's cross-machine table already came from.

11. **This is decided and deliberately not built yet.** Nothing here ships until there are enough
    independent clients for K to mean something, and the corpus cannot even count them today —
    `ADR-0004` point 4 established that `contributor_count` counts POSTs rather than clients, and
    Familiar sends no `client_id` at all. `ADR-0004` point 1 is therefore a prerequisite of the
    trigger, not only of the mechanism.

    **The count is a signal to revisit, never an automatic switch.** `ADR-0004` point 9 already
    settled this shape for the row ceiling — raised "deliberately as the corpus grows, so growth is a
    decision rather than a surprise" — and a threshold that changes what the corpus refuses without
    anyone deciding is that surprise pointed the other way: a contributor fine yesterday is refused
    today because a stranger appeared. What the threshold earns is an alert and a reread of this
    record.

## Alternatives Considered

- **Ship a short audio file as the reference.** The obvious implementation, and it tests more: the
  decode path runs, so a broken `soundfile` or a resampler difference would surface. Rejected on that
  same ground. Decoding is not part of `PIPELINE_VERSION`, so a decoder difference failing an
  attestation is a false positive about a claim the identity never made — and false positives here
  are expensive, because the response to a failed attestation is refusing a contributor's data. It
  also puts a binary in a package that deliberately vendors nothing, not even its 614 MB of encoders.

- **The server holds the expected vector for each pipeline as configuration.** Simplest to reason
  about, catches a bad client on its *first* attestation rather than needing a second opinion, and
  removes the bootstrap window in point 7 entirely. Rejected because it makes a server deploy a
  precondition of every embedder release: nobody can contribute under a new `PIPELINE_VERSION` until
  an operator has added its expected vector. `ADR-0006` rejected the same shape as a version registry
  and the argument is unchanged.

- **Require attestation before accepting any contribution.** Strictly safer and much simpler to
  explain. Rejected because with point 4's consensus it is circular — no pipeline can reach K
  attestations if contributions are refused until it has — and because `ADR-0001` point 5 is emphatic
  that nothing may stand between a would-be contributor and contributing.

- **Have the server recompute a submitted embedding to verify it.** The only mechanism that actually
  proves a *contribution* rather than a capability. Rejected as impossible here rather than
  undesirable: the server holds no audio by design under `ADR-0001`'s privacy position, and
  `ADR-0003` sized the host at 2 GB of RAM for an index, against 614 MB of encoders plus per-request
  CPU. A corpus whose server can verify by recomputation did not need contributions in the first
  place.

- **Sign contributions cryptographically.** Proves who sent something and that it was not altered in
  transit. Rejected as answering a different question: `ADR-0004` made identity self-issued and
  explicitly not an abuse control, and a signature over a wrong vector is a wrong vector with a
  signature. It addresses tampering, which is not the failure mode in the Context.

- **Enable enforcement automatically once the corpus passes X contributors.** Appealing: it needs no
  one to remember, and it matches the fact that the mechanism is inert below two clients anyway.
  Rejected on three grounds. It saves no work — attestation is a package function, an endpoint, a
  table and a state machine, none of which appears at a threshold, so the "automatic" version is
  everything built now plus a flag. The count it would trigger on does not exist: `contributor_count`
  counts POSTs (`ADR-0004` point 4) and the one client sends no identifier, so the condition is
  unevaluable until identity lands. And `ADR-0004` point 9 already decided this class of question in
  the other direction, making the row ceiling something raised deliberately "so growth is a decision
  rather than a surprise" — a switch that changes what the corpus refuses because a stranger arrived
  is the same surprise. Point 11 keeps the threshold as an alert instead.

- **Do nothing beyond `ADR-0006`'s declaration.** Defensible: the declaration catches the drift that
  motivated it, and every failure listed in the Context is hypothetical today. Rejected because they
  are hypothetical only in the sense that nobody has looked — there is no mechanism by which any of
  them would have been noticed — and because `ADR-0001` point 4 already committed to attestation as
  part of the standard the embedding is held to.

## Consequences

- **Positive** — `ADR-0006` point 8's honest limitation is closed for the accident case, which is the
  case that actually occurs. A stale artifact, an fp16 session or an unmeasured execution provider
  becomes a rejected attestation rather than silently divergent data.
- **Positive** — `ADR-0001` point 4's attestation requirement is met with a mechanism rather than an
  intention, and the confidence of point 9 gains a second input beyond submission agreement.
- **Positive** — the corpus learns what a pipeline produces without anyone configuring it, so a new
  embedder release needs no server deploy and no coordination. Point 4 keeps `ADR-0006`'s central
  argument intact instead of quietly reintroducing what it rejected.
- **Positive** — the tolerance is a measured number that already exists, so this record adds no new
  threshold anyone has to defend.
- **Positive** — points 10 and 11 mean the expensive half is not built until it can work, and the
  cheap half produces data as soon as it exists. The corpus learns what its own pipeline's
  reproducibility looks like before anything depends on the answer.
- **Tradeoff** — **with one contributor, nothing can ever be confirmed.** K=2 means every pipeline
  stays provisional until a second independent client exists. That is the honest state of the corpus
  rather than a flaw in the design, and it is the same dependency `ADR-0001` point 8 already
  identified: the tool is what produces a second attester. Point 11 makes it a stated precondition
  instead of a surprise found during implementation.
- **Tradeoff** — a decided-but-unbuilt record is a liability of its own. `ADR-0002` and `ADR-0003`
  are both accepted with nothing shipped, and this adds a third. The `Implementation:` block is the
  only thing keeping that legible, which is an argument for writing one here the day work starts.
- **Tradeoff** — the bootstrap window in point 7 is real. Contributions accepted under a provisional
  pipeline may turn out to have come from a broken one, and what happens to them then is
  `ADR-0004` point 6's revocation cascade doing work it was written for.
- **Tradeoff** — the package cannot verify its own reference vector in CI today. The ONNX artifacts
  are 614 MB and `embed-ci.yml` does not fetch them, so `pytest -m artifacts` skips. The one test that
  would catch the reference vector drifting is the one CI cannot run.
- **Tradeoff** — one more endpoint, one more table, and a state machine (`provisional` → `confirmed`
  → refused) on a service that `ADR-0003` deliberately sized small.
- **Follow-up** — a second reference signal shorter than one window, to exercise the `repeatpad` path
  (`chunking.py:46`) that the 25-second signal does not reach.
- **Follow-up** — how a failed attestation interacts with data already accepted from that client:
  `ADR-0004` point 6 says revocation cascades, and this is its first concrete trigger.
- **Follow-up** — whether the execution provider should be named in the attestation. `artifacts.py`
  says comparability must be measured per provider; attestation could record which one was used and
  turn that requirement into data rather than a warning in a docstring.
