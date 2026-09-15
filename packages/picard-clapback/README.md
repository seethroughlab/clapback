# Clapback for MusicBrainz Picard

Look up, name, and — if you say so — contribute CLAP audio embeddings for your files in the
[clapback](https://clapback.seethroughlab.com) commons, and ask what sounds like a track across
every library it holds. `ADR-0011` point 5's second integration: Picard already fingerprints every
file it scans and knows its MusicBrainz recording id, which is exactly the pair the commons needs.

## Install

Download `clapback.zip` from the
[latest `picard-v*` release](https://github.com/seethroughlab/clapback/releases?q=picard-v), then
in Picard: **Options → Plugins → Install plugin…**, pick the zip, enable *Clapback*, restart.
Picard 2.6 through 2.13.

Or by hand: unzip into your plugins directory (Options → Plugins → *Open plugin folder*), so it
holds `clapback/__init__.py`.

## Use

Select tracks or files, right-click:

- **Clapback: look up in the commons…** — per file: does the commons hold this recording? The
  answer lands in the metadata panel as `~clapback_status`. With *Contribute* on, a held
  recording is **named** with its MusicBrainz recording id, and one the commons lacks is embedded
  here and sent — if `clapback-embed` is installed.
- **Clapback: what sounds like this…** — the nearest recordings in the commons, across every
  library it holds. A named neighbour is a MusicBrainz link; one nobody has named yet is shown as
  a hash, because it is one.

Files without a fingerprint are fingerprinted first, with Picard's own fpcalc — the same one
*Scan* uses.

**Options → Plugins → Clapback**: the commons URL, *Contribute* (off by default), and *Also run
after every save* (off by default, and needs *Contribute* for anything to be sent).

## Two modes, and the plugin says which

**Lookup-only** — the default on a bundled Picard. `clapback-embed` and its 614 MB of ONNX encoders
are not something a desktop application should download on your behalf, so without them the plugin
computes nothing. It can still tell you whether the commons holds a recording, name it, and ask
what it sounds like, because for a recording the commons holds it uses the commons's own vector.
A recording the commons lacks is reported as exactly that, not silently skipped.

**Contributing** — if Picard runs from a Python where `pip install clapback-embed` works and the
encoders are exported (`python -m clapback_embed.scripts.export_models --out ~/.cache/clapback/models`,
or `CLAPBACK_MODEL_DIR`), a recording the commons lacks is embedded here and contributed under the
reference pipeline identity. The options page says which mode you are in.

## What leaves the machine

Nothing until *Contribute* is on. Then, per file: a **one-way SHA256 of the AcoustID fingerprint**;
a **512-float vector** when one is computed here; and the **MusicBrainz recording id**, which tells
the commons which recording you hold. Never audio, never paths, never other tags. The id goes out
under the same switch as the vector because it is the larger disclosure of the two, and the
options page says so where the switch is. Everything sent is dedicated to the public domain under CC0 1.0, like every other row in the corpus, and may be republished in its public exports.

The first contribution mints a random client id — a UUID, derived from nothing about you or your
machine — and keeps it in Picard's settings. It lets the commons tell two contributions apart from
one client retrying. Clear it and you are a new contributor; nothing else changes.

## Why the client is copied in

`clapback/clapback_client/` is a verbatim copy of [`clapback-client`](https://pypi.org/project/clapback-client/),
the stdlib-only package that is the contract every tool follows. Picard's bundled application
cannot install packages, so the plugin carries the contract rather than depending on it.
`scripts/sync_client.py` refreshes the copy from `packages/client`, and a test fails when the two
differ, so it is the package and not a fork of it.

## Development

```bash
cd packages/picard-clapback
uv venv && uv pip install "picard>=2.13,<3" pytest ruff
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q     # against real Picard, headless
.venv/bin/ruff check .
python scripts/sync_client.py --check              # the vendored client is current
python scripts/build_zip.py                        # dist/clapback.zip — the name is the module name
```

A release is a tag: bump `PLUGIN_VERSION` in `clapback/__init__.py`, then
`git tag picard-vX.Y.Z && git push origin picard-vX.Y.Z`; `picard-release.yml` builds the zip and
attaches it to a GitHub release. Picard 3's plugin system (git-based, TOML manifest, PyQt6) is a
port, not a rebuild — its migration tool handles this shape — and follows once 3.0 ships.
