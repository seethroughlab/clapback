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
  recording a person can look up — and a bare hash when nobody has. `recording(mbid)` goes the
  other way: what does recording X sound like, without holding X.
- **Confirmation** — whether your vector for a recording agrees with others' independently
  computed one.

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

An id is a *claim*: the commons counts how many distinct clients assert it and never verifies it
against MusicBrainz. Sending one tells the operator which recording you hold — so send it under
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
you pass one; never audio, filenames, or other tags — before the first time it does.
