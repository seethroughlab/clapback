# ADR-0008: The Corpus Serves Agreement, Not a Verdict

Status: accepted

Date: 2026-09-03

Completes [ADR-0001](ADR-0001-clapback-is-a-public-clap-embedding-commons.md) point 9 — the
confidence "AcousticBrainz said it lacked" — and answers what its deferred item 2 left: "choosing the
threshold and where it is enforced". The measurement it reports has been accumulating since
`submission_agreement` landed; nothing reads it.

Distinct from [ADR-0007](ADR-0007-a-pipeline-proves-itself-on-a-reference-signal.md), and the
distinction is worth stating because the two are easy to merge. `ADR-0007` asks *is this client
running the pipeline it claims* — a fact about an install. This asks *have independent parties
computed the same vector for this recording* — a fact about a row. They share the word "confirmed"
and nothing else.

Implementation:
- Accepted 2026-09-04. Nothing is built.
- Unlike `ADR-0007` this functions immediately: with one contributor it reports zero independent
  confirmations, which is true and is the most useful thing the corpus can tell a stranger.
- It reports nothing but zero until Familiar sends a `client_id`. That one line is now named by
  `ADR-0004`, `ADR-0006` and this record, which makes it the cheapest unblocking change on the board.

## Context

### The data is being collected and thrown away a second time

`SubmissionAgreement` exists because the answer was being discarded: `contribute_embedding` used to
increment a counter and drop the submitted vector. It now scores every resubmission against the
stored one and records the similarity with the submitting `client_id`
(`packages/server/app/api/routes.py:174-185`), indexed by key (`ix_submission_agreement_key`).

Nothing consumes it. The lookup path (`packages/server/app/api/routes.py:135-141`) returns:

```python
EmbeddingResponse(
    fingerprint_hash=..., embedding=..., analysis_version=...,
    clap_model_version=..., contributor_count=emb.contributor_count,
)
```

`contributor_count` is the only signal a caller gets about how trustworthy a vector is, and it is not
that signal.

### What `contributor_count` actually counts

It is incremented once per POST that hits an existing row (`packages/server/app/api/routes.py:186`, and identically
at `:278` and `:389` for features and analysis detail). It therefore counts **submissions**, and:

- The same install resubmitting after a version bump increments it. `ADR-0004` point 4 says so
  explicitly and says the existing figure "should not be read as" independence.
- A client submitting a *wildly different* vector increments it too. Disagreement raises the number
  that looks like corroboration.
- A submission with no `client_id` — which is every submission Familiar has ever made — increments
  it, though `ADR-0004` point 3 says such a submission can never be confirmed.

So the one number the API serves as confidence goes up for retries, up for disagreements, and up for
submissions that by decision cannot confirm anything.

### The threshold that has to be chosen, and the one that does not

`ADR-0001` deferred item 2 asked how agreement is measured given one contributor, and marked it
"largely answered" by the cross-machine run. Two different questions were folded into that:

**What counts as agreement** is answered, and by measurement rather than choice:

| difference | distance from 1.0 |
|---|---|
| CPU vs CUDA | 6.6e-14 |
| two architectures (arm64 vs x86_64) | 6.6e-11 |
| `float4` storage round-trip | 6.0e-08 |
| fp32 vs fp16 | 1.5e-06 |
| two different rips of one recording | 3e-04 – 3e-03 |

The `identical` band at 0.999999 sits in the gap: it admits every honest architecture and the storage
floor, and excludes fp16, which `ADR-0001` point 4's pinned precision forbids anyway. It is also
worth noting the last row cannot reach a shared key — the corpus keys on the SHA256 of an AcoustID
fingerprint, and two different rips hash differently, so they land in different rows rather than
disagreeing in one. Within a key, honest submissions of the same audio should agree at 1e-7 or
better.

**How many agreements make a vector trustworthy** is not answered, and there is no data with which to
answer it. `submission_agreement` was merged on 2026-09-03 and the corpus has one contributor
accounting for 99.85% of it, so the table is very nearly empty. Any number chosen now would be
invented.

### The failure this is written against

AcousticBrainz gathered duplicate submissions to mitigate quality problems and it did not work,
because their data was a claim about the world and their algorithm was reproducibly wrong —
duplicates agreed and were wrong together. `ADR-0001` point 9's argument is that a CLAP embedding is
different in kind: it is the output of a pinned function, so agreement means something. That argument
only pays off if the agreement is actually served to the people deciding whether to trust a vector.

## Decision

1. **The corpus reports agreement and does not grade it.** A lookup returns how many independent
   clients confirmed the vector, how many contradicted it, and the worst similarity anyone recorded
   against it. It does not return a verdict, a score, or a boolean.

2. **Two agreements make a confirmation only if they come from different clients.** Confirmations
   count **distinct `client_id` values**, per `ADR-0004` point 4. A submission without one confirms
   nothing, per `ADR-0004` point 3 — it is still recorded, still evidence, and still not
   independence.

3. **Agreement is the `identical` band, 0.999999, and it is a measured number rather than a chosen
   one.** The table above is its justification. A submission inside it confirms; a submission outside
   it contradicts. This is the only threshold this record sets.

4. **Disagreement is served, not hidden.** A row with three confirmations and one contradiction is a
   different object from one with three and none, and averaging them into a single score destroys
   precisely the case worth seeing. This is the AcousticBrainz lesson applied to the reporting rather
   than the collection.

5. **No number is defined as "enough".** There is no `confirmed: true`, and no threshold at which the
   corpus vouches for a vector. Different callers need different bars — a recommender ranking a
   listening model can act on an unconfirmed vector; a published dataset should not — and the corpus
   does not know which it is talking to. It reports what happened and lets the caller decide, which
   is also the only honest position available while the evidence table is empty.

6. **Confidence is computed from `submission_agreement` at read time, not stored on the row.** It is
   an indexed aggregate over a handful of rows. More importantly `ADR-0004` point 6 makes revocation
   cascade — revoking a client marks its submissions unconfirmed and excludes them from quorum — so a
   denormalised counter is a number that must be recomputed on every revocation and will eventually
   be wrong instead. Correct and cheap beats fast and stale at this size.

7. **`contributor_count` stays in the response and stops being described as confidence.** Removing it
   would break Familiar, which `ADR-0005` point 10 forbids. It is documented for what it is — a count
   of submissions — and the new fields are what any caller should read. It is deprecated in the
   response, not deleted.

8. **First-write-wins is unchanged.** Confidence describes what is stored; it does not re-elect it. A
   contradicted vector is still served, with its contradiction visible. Changing what gets served on
   disagreement is a different decision, and one nobody has the data to make.

9. **This applies to embeddings only.** `ADR-0001` point 4 gives the embedding the full standard and
   point 6 gives tier two explicitly less — no quorum before it is served. The features and
   analysis-detail endpoints keep `contributor_count` and gain nothing here.

## Alternatives Considered

- **Fix `contributor_count` to count distinct clients and stop there.** The minimal change, and
  `ADR-0004` point 4 already calls for it, so it needs no new record and no new fields. Rejected
  because it conflates submitting with agreeing: a client that submits a completely different vector
  is a distinct client, so the corrected number would still rise on disagreement. It would be a
  better-defined number that is still the wrong one, and it would be more convincing while being
  wrong, which is worse.

- **Serve a single confidence score between 0 and 1.** Easy to consume, sorts, thresholds, and puts
  one number where callers expect one. Rejected because it compresses two independent facts — how
  many agreed, and how badly the worst dissenter disagreed — into a value that cannot distinguish
  "nobody has checked" from "several checked and one strongly disagreed". Those are the two cases a
  caller most needs to tell apart.

- **Define a threshold now and serve `confirmed: true`.** What most consumers would prefer, and it
  makes the corpus's guarantee legible in one field. Rejected because the number would be invented:
  `submission_agreement` is days old and nearly empty, and `ADR-0001` explicitly refuses to
  grandfather trust that was not measured. A threshold can be added later on evidence; a threshold
  published now would be quoted back as if it meant something.

- **Withhold unconfirmed vectors from lookups.** The strongest guarantee available, and it makes the
  corpus's word mean something absolutely. Rejected because it would serve nothing at all today, and
  because `ADR-0001` point 9's design is a confidence served *alongside* the data rather than a gate
  in front of it. It would also invert `ADR-0002`'s reason for existing: a corpus worth querying by
  people who have contributed nothing cannot begin by refusing to answer.

- **Store the confidence on the embedding row and update it on write.** One less query on the read
  path, which is the hot path. Rejected on `ADR-0004` point 6: revocation cascades, so the stored
  number would need recomputing whenever a client is revoked, and a counter that is only sometimes
  recomputed is worse than a query. Revisit if the aggregate ever shows up in a profile, which at a
  few rows per key it will not.

- **Wait for more contributors before building this.** Consistent with `ADR-0007`'s deferral, and
  with one contributor every row will report zero confirmations. Rejected because the two cases are
  not alike. `ADR-0007` cannot function below two clients — a quorum of one is not a quorum. This
  functions immediately and simply reports the truth, which is that nothing has been independently
  confirmed. That report is itself the most useful thing the corpus can say to a stranger evaluating
  whether to depend on it, and saying it honestly is cheaper than explaining later why
  `contributor_count` read like corroboration.

## Consequences

- **Positive** — `ADR-0001` point 9's confidence stops being a design property and becomes a field. A
  caller can finally distinguish a vector one machine produced from one four machines reproduced.
- **Positive** — the measurement already being collected is finally read. `submission_agreement` was
  built to answer a question and has so far only stored it.
- **Positive** — the corpus stops publishing a number that rises on retries and on disagreement while
  being read as corroboration. That misreading is currently invited by the API rather than merely
  possible.
- **Positive** — it works today, unlike everything else queued. With one contributor it reports zero
  confirmations honestly, which is the correct and useful answer.
- **Tradeoff** — **the honest answer is unflattering.** Every row in the corpus will report zero
  independent confirmations until a second client exists and sends a `client_id`. That is the true
  state of a corpus that is "one library and a handful of visitors", and publishing it plainly is
  part of `ADR-0002`'s bet that the commons is worth strangers' attention.
- **Tradeoff** — an aggregate query on the lookup path, where there was none.
- **Tradeoff** — `contributor_count` lives on as a deprecated field that means something callers
  misread, for as long as `ADR-0005` point 10's compatibility floor holds.
- **Tradeoff** — point 5 pushes the trust decision onto callers, and some will want a boolean and
  implement their own threshold badly. The alternative is the corpus implementing one badly on their
  behalf, with more authority.
- **Follow-up** — Familiar sending a `client_id` is now a prerequisite for this to report anything
  but zero. That is the same one-line change `ADR-0004` and `ADR-0006` both wait on, and this is the
  third record to name it.
- **Follow-up** — a threshold, if evidence ever supports one. Point 5 declines to invent it; it does
  not decide against ever having one.
- **Follow-up** — how confidence appears on the public browse pages, which today show hashes and
  analysis data with no indication of how corroborated any of it is.
- **Follow-up** — recomputation on revocation. Point 6 makes it correct by construction, but
  `ADR-0004` point 6's cascade has no implementation yet to be correct within.
