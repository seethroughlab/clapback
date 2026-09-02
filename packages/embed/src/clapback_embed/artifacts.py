"""Locating and loading the ONNX encoders.

**Precision is part of the contract, not a local optimisation.** Measured
2026-09-01, cosine distance from 1.0 for a full chunked mean:

| difference | distance |
|---|---|
| `float4` storage round-trip | 6.0e-08 |
| mel implementation | 1.2e-07 |
| runtime (torch vs ONNX fp32) | 1.2e-07 |
| **fp32 vs fp16** | **1.5e-06** |

fp16 is the first of those that leaves the corpus's `identical` band (which
begins at 0.999999), so anything contributed must be fp32. Reduced precision
stays available for local-only work and is recorded with the vector rather than
assumed — hence `Precision` appearing in the pipeline identity.

Artifacts are not vendored: the audio encoder is 112 MB and the text encoder
502 MB, which do not belong in a git repository. They are produced by
`scripts/export_models.py` from the pinned checkpoint, and located here.

**Execution providers are configurable, and default to CPU for a reason.** ONNX
Runtime can run this graph on CUDA, DirectML or ROCm, and doing so is a genuine
option — `onnxruntime-gpu` is roughly 200 MB against the ~5 GB of a CUDA-enabled
torch build, so acceleration is *cheaper* to adopt here than it was before. But
an accelerated provider is not known to produce vectors comparable with the CPU
ones, and that has to be measured per provider before anything is contributed:
`scripts/compare_vectors.py` is the tool for exactly this.

CoreML is measured **non-functional** for this graph, not merely divergent. It
supports 735 of 3086 nodes, warns that it "does not support shapes with dimension
values of 0" across HTSAT's Swin attention slices, and the partition it does take
fails at runtime with "Unable to compute the prediction using a neural network
model". Do not spend an afternoon rediscovering this.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path

import onnxruntime as ort

#: The checkpoint every artifact is derived from. Changing this fragments the
#: corpus into islands that silently return nothing for each other.
CHECKPOINT = "laion/clap-htsat-unfused"

#: Bumped when an artifact is re-exported in a way that moves vectors.
ARTIFACT_VERSION = 1

AUDIO_FP32 = "clap_audio.onnx"
AUDIO_FP16 = "clap_audio_fp16.onnx"
TEXT_FP32 = "clap_text.onnx"


class Precision(str, Enum):
    """fp32 is the only precision valid for a contributed vector."""

    FP32 = "fp32"
    FP16 = "fp16"


class ArtifactsMissing(RuntimeError):
    """The ONNX encoders are not where we looked."""


DEFAULT_PROVIDERS = ("CPUExecutionProvider",)


def providers() -> list[str]:
    """Execution providers, CPU unless `CLAPBACK_PROVIDERS` says otherwise.

    Comma-separated and ordered by preference, e.g.
    `CLAPBACK_PROVIDERS=CUDAExecutionProvider,CPUExecutionProvider`.

    Overriding this is supported and unvalidated: a vector produced on a
    non-CPU provider has not been shown to match one produced on CPU, and until
    it has been, treat it the way `Precision.FP16` is treated — useful locally,
    not safe to contribute.
    """
    raw = os.environ.get("CLAPBACK_PROVIDERS", "").strip()
    if not raw:
        return list(DEFAULT_PROVIDERS)
    return [p.strip() for p in raw.split(",") if p.strip()]


def model_dir() -> Path:
    """Where artifacts live. `CLAPBACK_MODEL_DIR` overrides the default."""
    override = os.environ.get("CLAPBACK_MODEL_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "clapback" / "models"


def _resolve(filename: str) -> Path:
    path = model_dir() / filename
    if not path.is_file():
        raise ArtifactsMissing(
            f"{filename} not found in {model_dir()}.\n"
            f"Produce it with:  python scripts/export_models.py --out {model_dir()}\n"
            f"(exporting needs torch and transformers; using the result does not)"
        )
    return path


@lru_cache(maxsize=4)
def audio_session(precision: Precision = Precision.FP32) -> ort.InferenceSession:
    """The audio encoder. Cached — loading costs about 1.6s."""
    name = AUDIO_FP32 if precision is Precision.FP32 else AUDIO_FP16
    return ort.InferenceSession(str(_resolve(name)), providers=providers())


@lru_cache(maxsize=1)
def text_session() -> ort.InferenceSession:
    """The text encoder.

    Exported with external data, so `clap_text.onnx.data` must sit beside the
    graph file. `onnxruntime` picks it up by relative path; moving one without the
    other fails at load rather than silently.
    """
    return ort.InferenceSession(str(_resolve(TEXT_FP32)), providers=providers())


@lru_cache(maxsize=1)
def tokenizer():
    """RoBERTa tokenizer via `tokenizers` alone.

    Deliberately not `transformers`: `tokenizer.json` loads directly, and the text
    embedding it produces matches `transformers` + `torch` at cosine
    1.0000000000 (verified 2026-09-01 across three prompts). That measurement is
    why this package has no `transformers` dependency at all.
    """
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    tk = Tokenizer.from_file(hf_hub_download(CHECKPOINT, "tokenizer.json"))
    tk.enable_padding(pad_id=1, pad_token="<pad>")  # RoBERTa's pad id
    return tk
