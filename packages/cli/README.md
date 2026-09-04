# clapback

Search your own music by description, and find duplicates across formats and masters.

```bash
pip install clapback

clapback index ~/Music
clapback search "dreamy ambient with piano"
clapback duplicates
```

## What it does

**Search by description.** CLAP puts audio and text in one space, so "something
slow with brushed drums" is a query rather than a keyword match against filenames
you may never have typed.

**Find near-duplicates.** Two rips of one recording measure 0.9972–0.9995 under
this pipeline; genuinely different music sits far below. That gap is what makes
duplicate detection across formats and masters work — a FLAC and a V0 of the same
master are obvious, and so is the same recording on two different releases.

Both run against your own files, offline. There is no account, no key, and
nothing is sent anywhere.

## What it needs

`clapback-embed`, which arrives with it, and the ONNX encoders it runs on. Those
are **614 MB and not bundled** — a package that downloaded them on install would
be lying about its size. Export them once:

```bash
pip install 'clapback-embed[export]'
python -m clapback_embed.scripts.export_models --out ~/.cache/clapback/models
```

Or point `CLAPBACK_MODEL_DIR` at them if you already have them.

## Where things are kept

`~/.clapback/` — a `vectors.npy` and an `index.json`, both yours. Deleting the
directory loses nothing but the time to rebuild it.

## What it is not

Not a player, not a tagger, not a library manager, not a downloader. It does the
two things a CLAP embedding makes uniquely easy and stops.

## Why it exists

It is the reference implementation's first real client, and the argument for it
is in [`ADR-0009`](../../docs/decisions/ADR-0009-the-tool-is-useful-before-the-corpus-is.md):
a donation client with no local value has no first contributor, and this project
has measured proof that passive accumulation does not happen. What the tool does
locally is the draw; contributing to the [commons](https://clapback.seethroughlab.com)
is a byproduct of it.
