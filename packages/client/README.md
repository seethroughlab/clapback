# clapback-client

The contract a tool follows to take part in the [clapback](https://clapback.seethroughlab.com)
commons — look up before you contribute, send what the records require, back off when told to —
with no dependency beyond the standard library.

```bash
pip install clapback-client
```

```python
from clapback_client import Corpus, fingerprint_file, hash_fingerprint

key = hash_fingerprint(fingerprint_file("track.flac"))
corpus = Corpus()

row = corpus.lookup(key, pipeline_version)   # or lookup(recording_mbid=mbid, ...) if you hold one
if row is not None:
    vector = row["embedding"]          # the commons already had it — skip the model
else:
    vector = my_embedder(path)         # your pipeline, declared by `pipeline_version`
    corpus.contribute(
        fingerprint_hash=key,
        embedding=vector,
        pipeline_version=pipeline_version,
        client_id=client_id,
    )
```

## What a tool has to do

Four things. Each is decided in one of the project's
[records](https://github.com/seethroughlab/clapback/tree/main/docs/decisions), and this package
is those decisions as code so a tool does not have to reimplement them.

1. **Fingerprint the audio and hash it canonically.** `hash_fingerprint` is SHA256 of the
   AcoustID fingerprint exactly as chromaprint returns it — not as your database happened to
   store it. That distinction split a corpus once; `canonical()` is the guard.
   **The string is a function of the audio and of the fingerprinting path.** Measured
   2026-09-16 on 56 FLACs: the `fpcalc` binary (what Picard and Familiar run) and pyacoustid's
   library path (what beets runs) return the same string for 24 of them, and for 10 of the 24
   CD-quality ones; two official `fpcalc` builds from different ffmpeg generations agree on 37.
   The rest differ by a few bits of ~30,000 — which AcoustID's matcher absorbs and a SHA256
   cannot. So the key is exact within one path and *may* differ across two, and **a miss on
   `lookup` does not mean the corpus lacks the recording**. The fix is not a better hash: it is
   the recording id, below, which is the same on every path
   ([`ADR-0019`](../../docs/decisions/ADR-0019-agreement-is-counted-per-recording-not-per-key.md)).
2. **Produce the vector through a declared pipeline.** This package never embeds. The reference
   pipeline is [`clapback-embed`](https://pypi.org/project/clapback-embed/), whose
   `PIPELINE_VERSION` is the identity to send. A tool with its own pipeline declares its own
   identity, and its vectors are comparable with each other rather than with the reference's.
3. **Look up before contributing — by recording if you hold an id, by hash otherwise.**
   `Corpus.lookup(recording_mbid=mbid, pipeline_version=...)` finds the recording whichever path
   keyed it; `Corpus.lookup(key, ...)` finds your path's row. Contribute under your hash either
   way. A repeat submission is recorded as *agreement*, so a tool that re-sent its library would
   manufacture evidence of one install agreeing with itself — the one measurement the commons
   exists to make honestly.
4. **Send `client_id` and `pipeline_version`.** Both are required by `Corpus.contribute` and have
   no defaults. `client_id` is a random UUID minted once per install — `identity.mint_client_id`
   — on the first contribution, never on install, and stored where the user can find and delete
   it.
5. **Say what the licence is, beside the switch.** Everything sent is dedicated to the public domain under CC0 1.0, like every other row in the corpus, and may be republished in its public exports
   ([`ADR-0013`](../../docs/decisions/ADR-0013-the-corpus-is-public-data-not-just-a-public-endpoint.md)).
   A self-issued client has no account to agree to terms on, so the sentence goes where the user
   turns contribution on.

## What you get back

- **Skip the recompute.** `lookup` returns the stored vector for a recording the commons already
  holds under your pipeline.
- **Similarity across libraries you do not own.** `similar(vector)` returns the nearest recordings
  the commons holds, each with a `recording_mbid` when anyone has claimed one — a MusicBrainz
  recording a person can look up — and a bare hash when nobody has. One neighbour per recording:
  rows two installs keyed differently are folded into the nearest, and the response's `collapsed`
  says how many were. `recording(mbid)` goes the other way: what does recording X sound like,
  without holding X.
- **Confirmation** — every result that names a recording carries `recording_confirmations`
  and `recording_contradictions`: how many independent installs sent a vector for that recording,
  under that pipeline, inside the `identical` band (cosine ≥ 0.999999), and how many sent one
  outside it. Counted across every key the recording is held under, so two fingerprinting paths
  are one population; by distinct `client_id`; never counting an install for agreeing with its own
  row. No verdict — the corpus reports what happened and you decide
  ([`ADR-0008`](../../docs/decisions/ADR-0008-the-corpus-serves-agreement-not-a-verdict.md),
  [`ADR-0019`](../../docs/decisions/ADR-0019-agreement-is-counted-per-recording-not-per-key.md)).

## Looking up a whole library

One `lookup` per track is the right shape for a tool that embeds as it goes. It is the wrong shape
for a tool that already holds a fingerprint or an id for every track and wants to know, before
doing anything else, which of them the commons has: sequential single lookups to the commons
measured 72 ms median on 2026-09-16, which is twelve minutes for 10,000 tracks spent mostly on
round trips answering "no" ([`ADR-0015`](../../docs/decisions/ADR-0015-a-library-is-looked-up-in-batches.md)).

```python
for key, row in corpus.lookup_many(keys, pipeline_version, vectors=False):
    ...   # key is exactly what you passed; row is what lookup() would return, or None
```

`keys` is any iterable of fingerprint hashes and MusicBrainz recording ids, mixed — told apart by
shape — and a tool should pass the id wherever it holds one. Batches of 100 go to
`POST /v1/embeddings/lookup`; the answer comes back in order. `vectors=False` leaves out the 512
floats, which is what a tool asking "which of these do you hold, and what are they called?" wants
and is a fraction of the bytes. The commons counts its lookup limit per key, not per request, so
a large library will be told to wait part-way; `lookup_many` honours `Retry-After` and continues,
and one call walks the whole library. If you want the *whole* corpus rather than your library's
slice of it, the weekly export is the right download, not this.

## Contributing a whole library

`contribute` is one row per request at the commons's write limit of 30 a minute, which is the
right pace for a tool that contributes as it analyses and five and a half hours for a library of
10,000 vectors already computed
([`ADR-0016`](../../docs/decisions/ADR-0016-a-library-is-contributed-in-batches-with-every-guarantee-kept.md)).

```python
for result in corpus.contribute_many(rows):     # rows: dicts of contribute()'s keyword arguments
    if result["status"] == "refused":
        ...   # result["code"] and result["detail"] are what the row would have got alone
```

Batches of 100 go to `POST /v1/embeddings/batch`, 600 rows a minute. **Every guarantee runs per
row, in the same code the single endpoint uses** — agreement recorded, contributor count moved,
the ceiling and the daily quota checked — so a batch that crosses a bound is accepted up to the
line and refused past it, row by row. A batch is **not atomic**: 97 created, 2 confirmed and 1
refused is 99 rows contributed, and you retry a refused row on its own result. `client_id` is
required on every row. And **look up first, as always** — `lookup_many` is the check — because a
library re-sent by the hundred manufactures a hundred agreements of one install with itself.

One `client_id` may write 50,000 rows — created or confirmed — in a rolling 24 hours, ten percent
of the corpus ceiling; past that each row is refused with a `retry_after`.

## A track you never fingerprinted

A tool that identifies tracks through MusicBrainz and never runs chromaprint — or one with a
backlog of vectors computed before it had a fingerprinting step — has a vector and a recording
id and no key. Since 2026-09-19 that is enough
([`ADR-0020`](../../docs/decisions/ADR-0020-a-contribution-without-a-fingerprint-is-keyed-on-its-recording.md)):

```python
corpus.contribute_recording(recording_mbid=mbid, embedding=vector,
                            pipeline_version=PIPELINE, client_id=client_id)
```

The corpus keys the row on `SHA256("musicbrainz_recording:" + mbid)` (`recording_key(mbid)`,
if you need to address it later), a second install that never fingerprinted the same recording
lands on the same row and confirms it, and the row claims its own recording — so it agrees with,
and is folded into similarity results with, every fingerprint-keyed row anyone has claimed under
the same id. Every read serves `key_type`: `fingerprint` for a row keyed on the audio,
`musicbrainz_recording` for one keyed this way. The difference matters to a reader: a
recording-keyed row can never be confirmed by an audio-derived key. It is a claim all the way
down, and a mistagged file becomes a vector *at* the recording rather than a contradicting claim
beside one. The corpus counts it as one voice among the rows naming that recording and says what
kind of key it has; it does not pretend otherwise, and neither should you.

**This is a separate method on purpose.** A tool that has a fingerprint calls `contribute` with
it, always — the fingerprint is the key that is a function of the audio, and the corpus cannot
tell whether you had one. `contribute_many` takes rows of either shape; the result for a
recording-keyed row carries the derived key as its `fingerprint_hash` and `key_type`
`musicbrainz_recording`.

## Which recording is this?

Every `lookup` result carries `recording_mbid` — the MusicBrainz recording id the most independent
installs have asserted for that row — and `recording_claims`, how many. `lookup_many(...,
vectors=False)` is the whole-library form of that question, and `corpus.claims(hash)` lists every
id claimed for a row with its count, dissent included
([`ADR-0018`](../../docs/decisions/ADR-0018-the-corpus-answers-which-recording-is-this.md)).

What this is, in order: it works only for recordings the commons holds; the id is the one most
independent installs asserted, and the count is how many; it is not verified and it is not
AcoustID; and a count of 1 means one install said so. AcoustID answers the same question by fuzzy
match against a database built for it. This is an exact-hash shortcut that is silent when the
commons has not seen the recording — never a replacement, and a tool must not treat it as one.
What it has that AcoustID does not: no key, no per-second limit, and the answer comes with the
vector.

**Never send an id you learned here back as your own claim.** A tool that does counts itself as
independent confirmation of what it copied. Claim only what you established yourself — from your
tags, from AcoustID, from Picard. The server cannot tell the difference; this sentence is the only
defence.

## Naming what you contribute

If your tool knows the MusicBrainz **recording** id — beets' `mb_trackid`, Picard's recording id,
an AcoustID lookup's result — pass it as `recording_mbid=` on `contribute`, or attach it later with
`claim()` to a row you already sent. Never re-send the vector to add an id: a repeat contribution
is recorded as agreement, and your library must not read as agreeing with itself.

If your tool holds an **AcoustID track id** — Picard always does, beets' `chroma` stores it as
`acoustid_id` — pass it as `acoustid_track_id=` beside the MBID, or alone. It is what AcoustID's
matcher assigns to near-identical fingerprints, the same across decoders and `fpcalc` versions, so
it joins two keys of one recording where there is no MusicBrainz match
([`ADR-0019`](../../docs/decisions/ADR-0019-agreement-is-counted-per-recording-not-per-key.md)
point 6). `lookup(acoustid_track_id=)` asks by it; in `lookup_many` pass `("acoustid", id)`,
because an AcoustID id and an MBID are both UUIDs and cannot be told apart by looking.

An id is a *claim*: the commons counts how many distinct clients assert it and never verifies it
against MusicBrainz or AcoustID. Sending one tells the operator which recording you hold — so send it under
the same setting that sends the vector, and say so in your own "what leaves the machine".

The commons is worth exactly its coverage of the library asking. Early on, expect misses.

## Naming your pipeline

`pipeline_version` is half the corpus key. Two vectors are comparable exactly when it matches, and
the corpus compares the whole string — it never parses one, never guesses that two strings with
the same checkpoint are "probably comparable". So the string has to say enough that another tool
with the same pipeline would write the same one, and a tool with a different one would not
([`ADR-0014`](../../docs/decisions/ADR-0014-a-pipeline-identity-is-self-describing-and-the-corpus-lists-what-it-holds.md)).

**Five tokens, in this order, joined by `+`**, each a short token with no `+` and no whitespace:

| Token | What it names | Reference | Kalinka, for example |
|---|---|---|---|
| checkpoint | the weights, as their publisher names them, with a namespace | `laion/clap-htsat-unfused` | `lukewys/laion_clap:music_audioset_epoch_15_esc_90.14` |
| front-end | how audio becomes model input — resampling, mel, normalisation | `frontend1` | `frontend1` |
| windowing | which audio the model sees | `artifact1` — see below | `frag3x10s` (three 10-s fragments) |
| pooling | how window vectors become one | `pool1` (mean of raw outputs, then L2) | `meanl2` |
| precision | of the vector **you send**, not the one you store | `fp32` | `fp32` if sent before quantising; `int8` if dequantised from INT8 |

Reference: `laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32` — what `clapback_embed.PIPELINE_VERSION`
returns. Its third token predates the convention: `artifact1` is the version of the ONNX export of
the checkpoint, and the reference's windowing — the whole track as consecutive 10-second windows —
is fixed by `clapback-embed` and has no token of its own; a change to either moves the identity.
A new tool should put its windowing rule in that slot, as Kalinka's example does. A tool that stores INT8 for itself but sends the fp32 vector it computed contributes
under `+fp32`; one that sends a dequantised vector contributes under `+int8`. Both may exist in
the corpus; they are different pipelines, and nothing is ever recomputed to change one into the
other.

Declare the string once in your own README, with what each token means — the corpus does not
interpret tokens, so their meaning lives with the tool that chose them. Before choosing, ask
`GET /v1/pipelines` (`Corpus.pipelines()`) which identities the corpus already holds and how many
rows each has: if yours exists, contributing under it joins that population; if not, yours starts
one. The server does not validate the grammar. A string that ignores it is still a valid key —
just one nobody else will land on.

## When your model changes

A new checkpoint, a new windowing rule, a new pooling rule — anything that moves the vectors — is
a new identity string. Mint it and carry on; there is nobody to coordinate with and nothing to
ask of the commons. What happens next is the same for every tool, and it is worth knowing before
you commit a library to one identity
([`ADR-0006`](../../docs/decisions/ADR-0006-the-pipeline-identity-is-the-corpus-key.md)):

- **Old rows stay.** The corpus never relabels or migrates a row to a new identity, and never
  deletes one because a newer identity exists. An install still running the old model keeps
  getting hits under the old string for as long as it names it.
- **The new identity starts empty and fills once per population, not once per install.** A
  lookup names the identity it wants. The first install to re-embed a recording under the new
  string contributes it; every other install's re-index of that recording is a lookup hit. The
  join is the recording id, not the fingerprint, so an install on a different `fpcalc` build
  still finds it. For a popular recording most installs never run the new model at all — which
  is the founding premise of the commons applied to the case that hurts most.
- **Do not reuse the old string.** The one thing that damages the corpus is a vector under an
  identity that did not produce it: the key asserts comparability and the assertion is false.
  A forgotten bump is the realistic failure; make the string a function of the pipeline in code,
  not a constant beside it.
- **Ask first.** `Corpus.pipelines()` says whether the identity you are about to mint already
  exists — another tool on the same checkpoint may have moved before you, and joining its
  population is worth more than starting your own.

So the shape of a re-index is the shape of a first index: look up under the new identity, compute
on a miss, contribute what you computed.

```python
row = corpus.lookup(fingerprint_hash, NEW_PIPELINE, recording_mbid=mbid)
if row is None:
    vector = embed(path)                       # the new model, on this machine
    corpus.contribute(fingerprint_hash=fingerprint_hash, embedding=vector,
                      pipeline_version=NEW_PIPELINE, client_id=client_id,
                      recording_mbid=mbid)
```

**What the corpus does not do** is search across identities. While a library is half re-indexed
it is two spaces, and a query in one finds nothing in the other — not approximately, not at all:
measured 2026-09-19 on 493 tracks, a vector from `laion/clap-htsat-unfused` and one from the
`music_audioset` checkpoint for the *same track* have a cosine of −0.009, and in a mixed index the
track's own other-model vector ranks below random. The two spaces do agree about which tracks are
neighbours, and a linear map fitted on a few hundred tracks embedded under both carries a search
across them about as well as a windowing change does within one model (R@10 0.99, top-10 overlap
0.56; `packages/embed/scripts/measure_crosspipe.py`, results beside it). Whether the commons
fits and publishes such a bridge between identities is not decided; until it is, a tool that
needs one during a transition fits it from tracks it holds under both, and the ranking it gets
back is a translation, not a measurement.

## Fingerprinting needs chromaprint, and only fingerprinting does

```bash
brew install chromaprint     # or: apt install libchromaprint-tools
pip install pyacoustid
```

`fingerprint_file` runs it out of process, because chromaprint is a C library that crashes rather
than raises on some malformed inputs and one bad file must not end a run. If your tool already has
fingerprints — beets' `chroma` plugin stores them, Picard computes them natively — hand them to
`hash_fingerprint` directly and skip this.

## Opt-in, off by default

Nothing in this package sends anything until you call `contribute` or `claim`. A tool that embeds
this should keep contribution a separate, explicit setting from lookup, and should tell the user
what leaves the machine — a 512-float vector, a one-way hash, and a MusicBrainz recording id if
you pass one (for `contribute_recording`, the id in place of the hash); never audio, filenames,
or other tags — before the first time it does.
