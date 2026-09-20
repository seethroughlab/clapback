# clapback

Search your own music by description, find duplicates across formats and masters,
and ask the commons what sounds like a track — including recordings you don't own.

```bash
pip install clapback-cli

clapback index ~/Music
clapback search "dreamy ambient with piano"
clapback duplicates
clapback similar "Gantz Graf"
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

**Ask what sounds like a track, past the edge of your library.** This is the one
thing here that reaches the [commons](https://clapback.seethroughlab.com), and the
reason to, that is not altruism
([`ADR-0022`](../../docs/decisions/ADR-0022-the-commons-is-pitched-on-discovery-not-saved-compute.md)):

```bash
clapback similar ~/Music/Autechre/Confield/01\ VI\ Scose\ Poise.flac
clapback similar "Scose Poise"            # a unique part of an indexed path works too
```

```
sounds like: /Users/you/Music/Autechre/Confield/01 VI Scose Poise.flac
0.9410  https://musicbrainz.org/recording/1e149025-fcdd-4a5c-b050-4da62aa5b26e  (1 claim)
0.9309  754ae12130505f1b…  (not yet named by anyone)
0.9294  /Users/you/Music/Autechre/Draft 7.30/03 Tapr.flac  (in your library)

3 shown · in your library 1 · not in your library 2 (1 not yet named)
```

It sends the track's vector — already in your store, so no model runs and no
fingerprint is taken — and gets back the nearest recordings across every library
that has plugged in. A neighbour is your own file when this store has contributed
it and so knows its hash; a MusicBrainz recording you can open when somebody has
named it; and a bare hash otherwise, shown as one. It says *sounds like*, never
*will like* — a CLAP neighbour is what the model heard as similar, nothing more —
and where to hear a recording you don't hold is not this tool's job.

**Contribute, if you want to.** Opt-in and off unless you type it:

```bash
clapback contribute --dry-run   # say what would be sent, send nothing
clapback contribute
```

This sends the vectors — never your audio, never filenames, never your library's
contents. A recording is identified by the SHA256 of its AcoustID fingerprint,
which is one-way, and this tool sends no recording id. Be clear about what that
does and does not hide: the corpus cannot recover a title from the hash, but if
another contributor has already named that same hash — 89.6% of rows are named —
the corpus knows which recording your row is. Everything sent is dedicated to the
public domain under CC0 1.0, like every other row in the corpus, and may be
republished in its public exports.

The whole library is looked up before anything is offered — a hundred tracks a
request — so re-running contributes only what is new. That is not politeness about
bandwidth: a repeat submission is recorded as agreement, and one install agreeing
with itself would corrupt the one measurement the commons exists to make.
Contributions go out a hundred at a time too, every guarantee per row, and a
refused row is that row's result rather than the run's.

Contributing needs `chromaprint`, and only contributing does:

```bash
brew install chromaprint     # or: apt install libchromaprint-tools
pip install pyacoustid
```

Without it, indexing, search, duplicates and `similar` work exactly as well.

## Why `clapback-cli` and not `clapback`

The bare name on PyPI belongs to an unrelated package from 2018 that adds clap
emojis to sentences. The distribution is therefore `clapback-cli`, matching
`clapback-embed`; the command you type is still `clapback`.

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

If you contribute, `index.json` also holds a `client_id`: a random UUID minted the
first time you contribute and never before, derived from nothing about you or your
machine. It exists so the corpus can tell two contributions apart from one client
retrying. Delete it and you are a new contributor; nothing else changes.

## What it is not

Not a player, not a tagger, not a library manager, not a downloader. It does the
two things a CLAP embedding makes uniquely easy, asks the commons the one question
a shared corpus of them can answer, and stops.

## Why it exists

It is the **reference client** — the place the commons's contract is exercised end
to end — and not the way most people are expected to arrive. The argument for it
is in [`ADR-0009`](../../docs/decisions/ADR-0009-the-tool-is-useful-before-the-corpus-is.md):
a donation client with no local value has no first contributor, and this project
has measured proof that passive accumulation does not happen. What the tool does
locally is the draw; contributing to the [commons](https://clapback.seethroughlab.com)
is a byproduct of it.

The route to the commons for most people is the tool they already run.
[`ADR-0011`](../../docs/decisions/ADR-0011-the-commons-is-what-other-tools-plug-into.md)
says so: if you use beets, [`beets-clapback`](https://pypi.org/project/beets-clapback/)
does everything above against your library and can name what it contributes with
`mb_trackid`. If you are writing a tool, the contract this CLI follows is published
on its own as [`clapback-client`](https://pypi.org/project/clapback-client/) — no
dependency beyond the standard library, so a tool with its own embedder can take
part without ONNX Runtime. This CLI is built on it, which is what keeps the reference
client and the published contract from drifting apart.
