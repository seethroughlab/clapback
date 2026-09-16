# ADR-0014: A Pipeline Identity Is Self-Describing, and the Corpus Lists What It Holds

Status: accepted

Date: 2026-09-15

Implementation:
- **Accepted 2026-09-16** as written, on the day's second walk-through of the set. Nothing is
  built. `ADR-0017`, first in point 6's order, was rejected on 2026-09-16 by its own
  measurement, so the order is now this record → `ADR-0015` and `ADR-0018` together →
  `ADR-0016`. Point 6's Decision text is left as written; this line is the correction.
- Owed: the convention section in `packages/client/README.md` with ours and Kalinka's strings
  as the worked examples (point 4), the `contribute` docstring pointer (point 4), and
  `GET /v1/pipelines` (point 3).

Extends [ADR-0006](ADR-0006-the-pipeline-identity-is-the-corpus-key.md) and
[ADR-0011](ADR-0011-the-commons-is-what-other-tools-plug-into.md) point 3. One of five records
proposed together on 2026-09-15 after the first prospective second contributor's questions
([madenvel/KalinkaPlayer#128](https://github.com/madenvel/KalinkaPlayer/issues/128)); the set and
its execution order are in this record's point 6.

## Context

`ADR-0006` made the pipeline identity half of the corpus key, and `ADR-0011` point 3 said any tool
may contribute under its own. Both records say what an identity *is for* — two vectors are
comparable only if it matches — and neither says what one *contains*. As of 2026-09-15 the only
definition is the code that builds ours:
`packages/embed/src/clapback_embed/__init__.py` composes
`{CHECKPOINT}+frontend{N}+artifact{N}+pool{N}+fp32`. The server accepts any string of 1 to 200
characters (`packages/server/app/api/routes.py`, `EmbeddingRequest.pipeline_version`). A tool
author choosing their own identity has no rule to follow, and two authors with the same pipeline
would choose two strings — which the corpus would then keep apart, correctly and uselessly.

**The question arrived on 2026-09-15**, as KalinkaPlayer#128's fourth: their vectors are stored
as INT8; may a dequantised vector be contributed, and must existing rows be recomputed? The
answer — "contribute it under an identity that says `int8`; nothing is recomputed" — was true and
was written nowhere a tool author could find it. The reply also offered an identity string for
their pipeline (`lukewys/laion_clap:music_audioset_epoch_15_esc_90.14+frag3x10s+mean+l2+fp32`)
that this project invented on the spot, which is exactly the situation a convention exists to
prevent.

A second gap is enumerability. `ADR-0013`'s export manifest lists the identities in the corpus
with row counts, weekly, in a file; nothing live does. A tool deciding whether to contribute under
its own identity or adopt an existing one cannot ask the corpus which identities exist and how
populated each is. The landing page says "1 contributing installation"; it does not say "1
pipeline identity, 25,886 rows".

### What is actually in the corpus, measured 2026-09-15

One identity: `laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32`, 25,886 rows (the
export manifest, `ADR-0013`). The measurement in `ADR-0008`'s Implementation block found that a
second plausible identity — the music checkpoint over three fragments — behaves differently
enough under MP3 128k (median 0.81 to its own lossless self versus 0.93) that a reader of the
corpus needs to know *which* identity a population is under before drawing conclusions from it.

## Decision

1. **An identity string names five things, in order, separated by `+`:** the checkpoint (as its
   publisher names it, with a namespace: `laion/clap-htsat-unfused`,
   `lukewys/laion_clap:music_audioset_epoch_15_esc_90.14`), the front-end, the windowing rule,
   the pooling rule, and the stored precision. Each component is a short token without `+` or
   whitespace. Ours reads `laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32`; the
   convention is what those tokens already are, stated. The reference for token meanings is the
   contributing tool's own documentation — the corpus does not interpret components, it compares
   whole strings, exactly as `ADR-0006` decided.

2. **The precision token names what was contributed, not what the tool stores.** A tool that
   computes fp32 and quantises to INT8 for its own storage contributes under `+fp32` if it sends
   the vector before quantising, and under `+int8` if it sends a dequantised one. Both may exist
   in the corpus; they are different pipelines. Nothing is ever recomputed to change precision.

3. **The corpus lists the identities it holds.** `GET /v1/pipelines` returns each distinct
   `pipeline_version` with its row count, its count of named rows, and the date of its first and
   most recent contribution. Unauthenticated, rate-limited as a read, cached like the landing
   page's counts. It is the live version of the export manifest's `embeddings` list.

4. **A tool declares once, in its own README, the identity it contributes under and what each
   token means.** The convention is documented in `packages/client/README.md` with ours and
   Kalinka's as worked examples, and the client's `contribute` docstring points there. The corpus
   does not validate the grammar — a string that ignores the convention is still a valid key —
   because a validator would be a second place the convention lives, and it would reject the
   25,886 rows of any tool that got a token slightly wrong.

5. **Not decided here:** which identities the commons treats as *reference* — `ADR-0002`'s open
   question — and whether two tools with the same checkpoint and the same windowing should
   share one string. Both need a second population to have the conversation with.

6. **The set, and its order.** Proposed together on 2026-09-15: this record; `ADR-0015` (a
   library is looked up in batches); `ADR-0016` (a library is contributed in batches);
   `ADR-0017` (the client fingerprints audio the tool has already decoded); `ADR-0018` (the corpus
   answers "which recording is this?"). Execution order: `ADR-0017` first, because its
   measurement decides whether it exists; then this record; then `ADR-0015` and `ADR-0018`
   together, both reads; `ADR-0016` last, because it touches the write path every other record
   depends on.

## Alternatives Considered

- **A registry of pipelines the server validates against.** Rejected. It moves the decision of
  what counts as a pipeline from the tool to the operator, which is the relationship `ADR-0011`
  says the commons must not have with the tools that plug into it — and a registry with one
  entry is a list of one.
- **Structured fields instead of a string** — `checkpoint`, `windowing`, `precision` as separate
  columns. Rejected for the reason `ADR-0006` chose a string: the key must be opaque and
  compared whole. Structure would invite the server to reason about partial matches ("same
  checkpoint, different pooling — probably comparable"), which is precisely the inference the
  corpus refuses to make.
- **Nothing — leave it to the tool authors.** Rejected on the evidence: the first tool author to
  ask was answered with a string this project made up, and the second would be too.

## Consequences

- **Positive** — the Q4 answer in KalinkaPlayer#128 becomes a link rather than a paragraph, and
  the next tool author never asks it.
- **Positive** — `/v1/pipelines` makes the corpus's shape visible: one identity today, which the
  landing page should say alongside "1 contributing installation".
- **Tradeoff** — a convention without a validator will be broken by someone. The cost is a
  misfiled population, which the corpus already tolerates by design; the alternative cost is a
  validator that rejects honest contributions.
- **Follow-up** — a "for plug-in authors" section on `/api`, written from the six questions in
  KalinkaPlayer#128, with this convention as the answer to the fourth.
- **Follow-up** — when a second identity has rows, the landing page's "the corpus, dated" section
  should break its count down by identity.
