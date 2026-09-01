# ADR-0001: Clapback Is a Public CLAP Embedding Commons

Status: proposed

Date: 2026-09-01

The first architectural decision record in this repository. It is framing: it says what this project
is, what it is not, and which decisions must follow it. It decides no schema and no endpoint.

## Context

This repository is `familiar-cache`: a server that shares CLAP embeddings and audio features between
Familiar installations so a track analysed once need not be analysed again. It works, it is deployed
on Fly with Neon, and it is 1,511 lines of FastAPI.

It is also being asked to become something else. [`ADR-0102`][adr102] in the Familiar repository
proposed keying it by MusicBrainz recording id so that music *nobody here owns* becomes rankable by
how it sounds. That only pays off if the corpus has coverage, and coverage requires contributors that
a self-hosted music player will never have on its own.

So the corpus is the product, and Familiar is one of its clients.

### What is actually here, measured 2026-09-01

| | |
|---|---|
| Embeddings | **21,890** |
| Feature rows | **77,770** |
| Rows with `contributor_count > 1` | **86** |
| Distinct IPs ever seen | **44** |
| **Distinct IPs that ever contributed** | **9** |
| **Share of all contributions from one IP** | **99.85%** |

The 44 figure has been cited as a contributor count, including in `ADR-0102` and in this repository's
own test comments. **It is wrong.** `ip_stats` records any request to `/v1`, so 44 counts lookups and
health checks. Nine addresses have ever contributed anything, and one of them is responsible for
essentially all of it.

This matters beyond bookkeeping. The 86 rows with more than one contributor are not 86 independent
agreements: `contributor_count` increments when the *same* address resubmits, which is what a
re-analysis after a version bump looks like. **There is currently no evidence that two independent
machines have ever agreed on a vector here**, because there has effectively been one machine.

### The premise this contradicts

An open pull request on this repository (`#1`, `phase0-measure-agreement`) records similarity between
conflicting submissions in order to measure the noise floor between independent contributors. Its
reasoning is sound and its arithmetic is tested. **Its assumption that data will accumulate passively
is not**, and the numbers above are why: with one real contributor, conflicting submissions are one
machine meeting itself.

The measurement is still the right first question. It cannot be answered by waiting.

### What killed AcousticBrainz, from MetaBrainz's own post-mortem

Not what one would assume:

1. **Resources**, stated first — they lacked the means to reboot it.
2. **The data was not good enough.** Key detection worked for some genres and not others, BPM was
   often wrong, and the algorithms could not express a **confidence level**, so bad rows were
   indistinguishable from good ones.
3. **Consensus was tried and it failed.** They gathered duplicate submissions to mitigate exactly
   this, and the algorithms "consistently produced incorrect results over duplicate submissions of
   the same recordings". The duplicates agreed with each other and were wrong together.

Point 3 defeats the obvious verification design and has to be answered rather than repeated.

### The distinction the project rests on

**AcousticBrainz stored claims about the world. This stores representations.**

"This recording is in D minor" is falsifiable, and their algorithm was reproducibly wrong about it —
so two contributors agreeing proved only that the algorithm was deterministic. A CLAP embedding
asserts nothing about the world. There is no correct vector to be wrong about; the vector *is* the
output of a pinned function applied to audio.

That changes what consensus can establish. The question stops being "is this value correct?", which
consensus demonstrably could not answer, and becomes "**did two independent parties compute the same
thing from the same audio?**" — which is well-formed and answerable.

The limit, stated plainly so nobody mistakes the claim: consensus cannot tell you CLAP is a *good*
model of music similarity. That is one judgement about one checkpoint, made offline against public
benchmarks, identical for every row, and not something contributions can invalidate. AcousticBrainz
needed per-item correctness *and* model quality; this needs per-item **integrity** plus one model
decision.

### What "features" actually means here, and why the category is not one thing

The 77,770 feature rows hold 25 distinct keys, and treating them as a single category is the mistake
that made an earlier draft of this ADR reject all of them for the wrong reason. Enumerated from the
deployed corpus on 2026-09-01:

| kind | keys | rows |
|---|---|---|
| Standardised measurement | `loudness_lufs`, `replaygain_track_gain`, `track_peak` | 46,133 – 46,171 |
| Physical, unstandardised | `brightness`, `dynamic_range_db` | 48,037 |
| Musical ground-truth estimates | `bpm`, `key`, `key_stability`, `modal_character`, `modal_confidence`, `tempo_character`, `tempo_cv`, `swing_ratio`, `syncopation`, `section_count`, `avg_section_length`, `form_string`, `energy_shape` | 976 – 77,770 |
| Perceptual scores | `valence`, `energy`, `danceability`, `acousticness`, `instrumentalness`, `speechiness`, `harmonic_complexity` | 48,034 – 77,770 |

**Verifiability is not what separates these from an embedding.** Consensus establishes *integrity* —
whether two machines computed the same thing — just as well for a BPM as for a vector, and it
establishes *truth* for neither. Nobody can prove the embedding is right either. An earlier draft
claimed features were unverifiable in a way embeddings are not; that was wrong, and the real
distinctions are narrower.

`loudness_lufs` is the clearest case that the blanket rejection was too broad. `pyloudnorm`
implements ITU-R BS.1770, a published specification with a defined answer that independent
implementations agree on by construction. That is a stronger footing than the embedding has, which
rests on a pinned checkpoint rather than a standard.

What genuinely separates the last two rows of that table is that they **assert something a listener
can contradict**. A wrong vector degrades similarity slightly and uniformly, and nobody forms an
opinion about float 237. A wrong key is individually visible and reads as a broken database — which
is the reputational damage MetaBrainz actually took.

### The failure mode is already in this corpus

This is not a hypothetical inherited from another project. Familiar computes `instrumentalness` and
`speechiness` from silero-VAD (`app/services/analysis.py:571–586`), and **silero detects speech, not
singing** — so across sung music both keys measure the wrong phenomenon, for all 77,770 rows. The
code's own comment at line 576 records the other half: when VAD fails, a spectral fallback "wrote a
saturated value that looked like a measurement".

Two keys, already shipped, already known to be wrong, indistinguishable from the correct ones without
reading the source. That is exactly what MetaBrainz described, and it is in the data this project
would inherit.

### Why the reference implementation belongs in this repository

[`ADR-0105`][adr105] in the Familiar repository removes `torch` and `transformers` from Familiar in
favour of an external embedder package, on measurements showing the ONNX path reproduces both audio
and text outputs within storage precision.

That package has to live somewhere, and where it lives is a verification decision rather than a
packaging one. The design depends on a client and a server agreeing on the exact embedding function.
Across two repositories that agreement is asserted at a boundary and drifts silently. In one
repository a single CI job runs the embedder against a reference clip and compares it to the constant
the server serves — a test that only exists if both are present.

## Decision

1. **This project is a public CLAP embedding commons, not a Familiar feature.** Its purpose is a
   corpus that many applications can contribute to and query. Familiar is its first client and its
   largest contributor, and neither of those makes it the owner.

2. **It is this repository, renamed and restructured — not a new one.** `familiar-cache` becomes
   `clapback`. MetaBrainz named resources as their first cause of failure and said explicitly that
   focusing on one project rather than two would have helped; this project already spans three
   repositories and one maintainer, and a fourth is a cost with no benefit.

3. **The repository holds three members: the embedder, the tool, and the server.** The embedder is
   the reference implementation and is published for others to depend on. The server keeps its own
   dependencies; adding audio libraries to the repository must not add them to the deployed image.

4. **The CLAP embedding is the purpose of the corpus, and is held to the full standard.** Pinned
   pipeline, pinned precision, reference-clip attestation, quorum, and a confidence served alongside
   the data. Every verification mechanism this project builds exists for the embedding. Nothing else
   is allowed to dilute it, delay it, or share its guarantees.

5. **A second quantity may be admitted, at a lower tier and under a stated bar.** It must be
   **reproducible** — the output of a pinned function or a published standard, so consensus can
   establish integrity — and it must be **a measurement of the signal rather than an estimate of a
   musical property a listener can contradict**. Both conditions, not either.

   Of the 25 keys held today, `loudness_lufs`, `replaygain_track_gain` and `track_peak` clear the
   bar; `brightness` and `dynamic_range_db` clear it if a specific implementation is pinned. The 13
   ground-truth estimates and 7 perceptual scores do not, and `instrumentalness` and `speechiness`
   fail on the first condition as well as the second.

6. **A tier-two quantity receives less scrutiny, and is correspondingly less load-bearing.** It needs
   no reference-clip attestation and no quorum before it is served; it is labelled as tier two so a
   client knows what it is holding; it never gates or delays an embedding submission; and **it can be
   dropped without a corpus migration.** That revocability is what makes the lower scrutiny safe, and
   it is the condition on which the tier exists at all.

7. **Nothing currently in the `features` table is migrated.** Familiar keeps its own private feature
   cache and loses nothing. The tier-two path is a door with a bar on it, not a backlog to work
   through, and the 77,770 existing rows do not enter merely because they are already here.

8. **The tool must be worth running with the corpus empty.** A donation client with no local value
   has no first contributor, and this project has measured proof that passive accumulation does not
   happen. What the tool does locally — search your own library by description, find duplicates
   across formats and masters — is the draw; contribution is a byproduct of it.

9. **Verification is a property of the design, not a moderation queue.** Because a vector is the
   output of a pinned function rather than a claim, the corpus can record *how many independent
   parties computed the same thing* and serve that alongside the data. **That is the confidence level
   AcousticBrainz said it lacked**, and it falls out of storing submissions rather than answers.

10. **The existing 21,890 embeddings are kept and marked unconfirmed.** They were accepted under no
   validation, so nothing is grandfathered as trustworthy. The rate at which independent clients later
   confirm them is itself a measurement of whether the pre-validation data was sound.

11. **Familiar's contribution stays opt-in and off by default.** `community_cache_contribute` is
   `False` today and this ADR is not a reason to change it. A commons that acquires contributors by
   default acquires them without consent.

### Decisions this defers

Each needs its own ADR, and the execution order differs from the numbering:

| order | decision | why here |
|---|---|---|
| 1 | The embedder package and its pinned front-end | Everything else depends on one implementation existing. `ADR-0105` already specifies the contract. |
| 2 | How agreement is measured, given one contributor | PR `#1`'s premise is contradicted above; the measurement needs contributors or a deliberate multi-machine run. |
| 3 | Identity, revocation and deletion | IP-only identity, no delete path anywhere, and flagging that is enforced nowhere. A public corpus cannot ship without these. |
| 4 | The recording-id key | `ADR-0102`'s substance, and the reason other applications would query this at all. |
| 5 | The tool's local features | Point 5's draw, and the only thing that produces a second contributor. |
| 6 | The rename itself, and what the domain serves | Cheap, and last on purpose: nothing above depends on it. |

## Alternatives Considered

- **Leave this as Familiar's private cache and drop the commons idea.** It works, it saves real
  analysis time, and it costs nothing to maintain. Rejected because `ADR-0102`'s capability — ranking
  music you do not own by how it sounds — has no other route, and because the corpus is more valuable
  to more people than to one music player.

- **Build the commons as a new repository and leave `familiar-cache` alone.** Cleanest identity, no
  migration, and the new project would not inherit a schema built for a different purpose. Rejected
  on the resources argument in point 2, and because the deployed service, its 21,890 rows and its
  operational history are worth more than a clean start.

- **Put the embedder in Familiar and depend on it from here.** Familiar is where the audio pipeline
  already lives, and it is the only real contributor today. Rejected because it inverts the
  dependency the project needs: a third-party application cannot reasonably depend on a music
  player's repository, and it puts the reference implementation outside the repository whose CI must
  prove client and server agree.

- **Carry all 77,770 feature rows across, since they already exist.** They cost nothing extra to
  serve and some client might want them. Rejected because two of the keys are known to be measuring
  the wrong phenomenon and are indistinguishable from the rest without reading Familiar's source.
  "They are already here" is how a commons acquires data nobody can vouch for, and points 5 and 7
  make admission a bar to clear rather than an inheritance.

- **Reject every non-embedding quantity outright.** This was an earlier draft of this ADR, carrying
  the slogan "the corpus stores embeddings, it does not store features". Rejected because its stated
  reason did not survive scrutiny: it claimed
  features are unverifiable in a way embeddings are not, when consensus establishes integrity equally
  for both and truth for neither. `loudness_lufs` is defined by ITU-R BS.1770 and rests on firmer
  ground than a pinned checkpoint does. A blanket exclusion would have been right by accident, with a
  reason a future contributor could correctly dismantle.

- **Admit a second quantity on reproducibility alone.** Simpler bar, and it is the property consensus
  can actually check. Rejected because `bpm` and `key` pass it: two machines running the same pinned
  library agree, and the value is still something a listener can contradict. Reproducibility
  establishes that the corpus is intact, not that its contents are defensible, which is precisely the
  gap MetaBrainz fell into.

- **Gate the corpus behind accounts from day one.** Solves identity, revocation and abuse at once.
  Rejected as premature and as a contributor barrier at the moment the project has effectively one.
  Point 3 of the deferred set takes it up with something to protect.

- **Adopt a stronger or newer audio model before building anything.** CLAP is not the last word, and
  committing to a checkpoint is committing to its limits. Rejected because it is the one decision
  that can be revisited without re-deciding anything above: the durable asset is not the vectors but
  the set of people who hold the audio and will run the tool again. Model migration is a re-run
  campaign, which is why client identity and model versioning must exist from the start.

## Consequences

- **Positive** — a corpus keyed to recordings rather than to one library is the only mechanism in
  sight for ranking unowned music by sound, and it is useful to applications that have nothing to do
  with Familiar.
- **Positive** — one embedder implementation shared by every contributor means a disagreement is
  about the audio, not about whose code ran. Verification becomes possible rather than nominal.
- **Positive** — the confidence level AcousticBrainz identified as missing is structural here, not a
  feature to be added later.
- **Positive** — Familiar gets smaller by adopting the package: `ADR-0105` removes 593 MB of
  packages and a 1.1 GB checkpoint download.
- **Positive** — the embedding keeps every verification mechanism to itself, so a second quantity
  arriving later cannot weaken the guarantee that is the project's reason to exist.
- **Tradeoff** — **none of the 77,770 existing feature rows migrate**, including the three that would
  clear point 5's bar. `loudness_lufs`, `replaygain_track_gain` and `track_peak` are defensible and
  are still left out, because point 7 declines to inherit data merely for being present. A client
  that wants them has to ask, and that is deliberate friction.
- **Tradeoff** — a two-tier corpus is more to explain and more to get wrong than a single-purpose
  one. The bar in point 5 is a judgement each time it is applied, and "measurement of the signal
  rather than an estimate of a musical property" will have arguable cases. Point 6's revocability is
  the safety net, and it only works if tier two genuinely never becomes load-bearing.
- **Tradeoff** — a public corpus becomes legible in a way an opaque-hash corpus is not, as
  `ADR-0102` recorded. No person is identified, but "these recordings are in somebody's library"
  becomes readable. That tradeoff is inherited here and is not softened by scale.
- **Tradeoff** — one maintainer, now explicitly across three repositories with a public commitment
  attached to one of them. MetaBrainz cited resources first, and this project has less of them.
- **Tradeoff** — the pinned checkpoint becomes a promise to strangers rather than an internal detail.
  A better model does not invalidate the corpus, but adopting one becomes a campaign rather than a
  version bump.
- **Follow-up** — the "44 contributors" figure appears in `ADR-0102`'s context, in this repository's
  `007_submission_agreement` migration docstring, and in `tests/test_submission_agreement.py`. All
  three should be corrected to the measured 9, and the test comment that says rejecting a required
  `client_id` "would reject all 44 contributing installations" is wrong on both the number and the
  reasoning.
- **Follow-up** — embedding inversion is an open research area. A 512-float vector of a ten-second
  window is heavily lossy and reconstruction looks impractical today, but the licensing and takedown
  story should assume it may improve rather than assert it safe.
- **Follow-up** — running cost must stay near zero, because resources are what MetaBrainz cited
  first. Fly scale-to-zero plus Neon achieves that today, and no decision below should acquire a
  component that needs babysitting.

[adr102]: https://github.com/seethroughlab/familiar/blob/main/docs/decisions/ADR-0102-the-community-cache-gains-a-recording-key.md
[adr105]: https://github.com/seethroughlab/familiar/blob/main/docs/decisions/ADR-0105-familiars-clap-runtime-is-an-external-package.md
