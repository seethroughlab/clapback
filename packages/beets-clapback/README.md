# beets-clapback

Look up, embed, and — if you say so — contribute CLAP embeddings for your beets library to the
[clapback](https://clapback.seethroughlab.com) commons. `absubmit` reborn, with a payload that can
actually be reconciled across contributors.

```bash
pip install beets-clapback
```

```yaml
plugins: chroma clapback

clapback:
  contribute: no      # opt-in. Turn it on when you have read "what leaves the machine".
```

```bash
beet clapback                        # every track: look up, else embed; contribute if enabled
beet clapback artist:Autechre        # any beets query
beet clapback -p                     # say what would happen, touch nothing
beet clapback-search "dreamy ambient with piano"
```

## What it does

For each track: hash the AcoustID fingerprint the `chroma` plugin already stored (or compute one),
ask the commons whether it holds an embedding for that recording from the reference pipeline, and
if so take it — the model does not run. If not, embed locally with
[`clapback-embed`](https://pypi.org/project/clapback-embed/), keep the vector, and contribute it
back only when `contribute: yes`.

Two flexible attributes land on each item, so you can query them like anything else:

| field | values |
|---|---|
| `clapback_hash` | the corpus key — SHA256 of the fingerprint |
| `clapback_status` | `found` · `contributed` · `local` · `unfingerprinted` · `unembedded` |

```bash
beet ls clapback_status:local        # embedded here, not yet in the commons
beet ls -a clapback_status:found     # albums the commons already had
```

The vectors live in `clapback/` under your beets config directory, so `clapback-search` works
offline over everything embedded or fetched. Delete that directory and nothing is lost but time.

## What it needs

- **`chroma`** enabled, or `chromaprint` installed so this plugin can fingerprint itself. `chroma`
  is the better answer — it stores the fingerprint once and every plugin benefits.
- **The ONNX encoders** for `clapback-embed`, 614 MB, not bundled:
  ```bash
  pip install 'clapback-embed[export]'
  python -m clapback_embed.scripts.export_models --out ~/.cache/clapback/models
  ```
  Without them, lookups still work; embedding does not, and the plugin says so.

## What leaves the machine

Nothing until `contribute: yes`. Then, per track the commons did not already hold: a 512-float
vector and a one-way hash. **Never audio, never paths, never tags.** The hash cannot be reversed
into the fingerprint, and the fingerprint is not the audio.

The first contribution mints a random `client_id` — a UUID, derived from nothing about you or
your machine — and stores it at `clapback/client_id` under your config directory. It lets the
commons tell two contributions apart from one client retrying. Delete it and you are a new
contributor; nothing else changes.

## Options

```yaml
clapback:
  url: https://clapback.seethroughlab.com
  contribute: no     # send vectors the commons lacks
  auto: no           # run on every import (embedding is minutes per album; off by default)
  pace: 0.15         # seconds between contributions
```

## Why this exists

beets shipped `acousticbrainz` and `absubmit` for years. Both were deprecated when AcousticBrainz
shut down, and the suggested replacement was to compute locally and keep the results. This is the
other half back — with a difference that matters: AcousticBrainz pooled *estimates* (bpm, key,
mood) that could never be reconciled between contributors. An embedding from a pinned pipeline
can be. When two people contribute the same recording, the commons can say whether they agree.
The reasoning is in
[`ADR-0011`](https://github.com/seethroughlab/clapback/blob/main/docs/decisions/ADR-0011-the-commons-is-what-other-tools-plug-into.md).
