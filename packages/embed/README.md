# clapback-embed

Deterministic CLAP audio and text embeddings, without `torch` or `transformers`.

```python
from clapback_embed import embed_file, embed_text

vector = embed_file("track.flac")          # 512 floats, unit length
query  = embed_text("dreamy ambient with piano")   # same space
```

## Why this exists

Two machines running this produce **the same vector**. That is the whole point: a
corpus built from many contributors can only tell disagreement about *audio* from
disagreement about *implementations* if there is one implementation.

So everything that could vary is pinned and versioned — the mel front-end, the
windowing rule, the pooling, the checkpoint, and the precision. `PIPELINE_VERSION`
is the identity of all of it together.

**It is not the checkpoint.** A change to windowing or pooling moves every vector
while `laion/clap-htsat-unfused` stays fixed. That is not hypothetical: Familiar
changed every embedding it held by moving from middle-ten-seconds to a whole-track
mean, without touching the checkpoint.

## What it does

1. Decode to 48 kHz mono.
2. Split into consecutive, non-overlapping 10-second windows. **CLAP cannot see
   more than ten seconds** — HTSAT's positional embeddings are sized for a
   1001×64 mel, and both ONNX and PyTorch reject anything else. A long track is
   several observations no matter what.
3. Drop a trailing partial window; `repeatpad` a track shorter than one window.
4. Embed each window, mean-pool the **raw** outputs, then L2-normalise.

Step 4 is deliberately not a mean of already-normalised vectors. The two differ.

## Precision

Measured against a full chunked mean, as cosine distance from 1.0:

| difference | distance |
|---|---|
| `float4` storage round-trip | 6.0e-08 |
| mel implementation (this vs `transformers`) | 1.2e-07 |
| runtime (`torch` vs ONNX fp32) | 1.2e-07 |
| **fp32 vs fp16** | **1.5e-06** |
| different rip of the same recording | 3e-04 – 3e-03 |

fp16 is the first entry that leaves the corpus's identical band, so **anything
contributed must be fp32**. `Precision.FP16` exists for vectors that never leave
your machine.

## Models

The encoders are 112 MB (audio) and 502 MB (text) and are not vendored. Produce
them from the pinned checkpoint:

```bash
uv pip install -e '.[export]'
python scripts/export_models.py --out ~/.cache/clapback/models
```

Exporting needs `torch` and `transformers`; **using the result needs neither.**
Every export verifies itself against PyTorch before writing — an artifact that
disagrees is worse than a missing one, because it produces plausible vectors that
are comparable with nothing.

Override the location with `CLAPBACK_MODEL_DIR`.

## One trap worth knowing

`ClapFeatureExtractor` selects a **different filter bank** when
`truncation="fusion"` — torchaudio/HTK rather than slaney. Nothing about the
resulting vectors looks wrong and they are comparable with nothing. This package
implements the slaney path only, which is what the checkpoint's default
(`rand_trunc`) uses, and asserts it rather than trusting a default.

Relatedly, `rand_trunc` takes a *random* crop of anything longer than one window.
Reproducibility here rests on every window being exactly 480,000 samples, which is
checked rather than assumed.

## Tests

```bash
uv pip install -e '.[dev]'
pytest                      # windowing, front-end arithmetic, pooling
pytest -m artifacts         # adds determinism against the real encoders
```

The comparison against `transformers` is the drift guard for the whole corpus. It
skips when the reference is not installed rather than being deleted.
