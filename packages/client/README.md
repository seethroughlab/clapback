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

row = corpus.lookup(key, pipeline_version)
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
2. **Produce the vector through a declared pipeline.** This package never embeds. The reference
   pipeline is [`clapback-embed`](https://pypi.org/project/clapback-embed/), whose
   `PIPELINE_VERSION` is the identity to send. A tool with its own pipeline declares its own
   identity, and its vectors are comparable with each other rather than with the reference's.
3. **Look up before contributing.** `Corpus.has` or `Corpus.lookup`. A repeat submission is
   recorded as *agreement*, so a tool that re-sent its library would manufacture evidence of one
   install agreeing with itself — the one measurement the commons exists to make honestly.
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

## Naming what you contribute

If your tool knows the MusicBrainz **recording** id — beets' `mb_trackid`, Picard's recording id,
an AcoustID lookup's result — pass it as `recording_mbid=` on `contribute`, or attach it later with
`claim()` to a row you already sent. Never re-send the vector to add an id: a repeat contribution
is recorded as agreement, and your library must not read as agreeing with itself.

An id is a *claim*: the commons counts how many distinct clients assert it and never verifies it
against MusicBrainz. Sending one tells the operator which recording you hold — so send it under
the same setting that sends the vector, and say so in your own "what leaves the machine".

The commons is worth exactly its coverage of the library asking. Early on, expect misses.

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
