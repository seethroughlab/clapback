"""Deterministic CLAP embeddings, without torch or transformers.

    >>> from clapback_embed import embed_file
    >>> vector = embed_file("track.flac")      # 512 floats, unit length

The point of this package is that **two machines running it produce the same
vector**, so a corpus built from many contributors can tell disagreement about
audio from disagreement about implementations. Everything that could vary is
pinned and versioned: the mel front-end, the windowing rule, the pooling, the
checkpoint, and the precision.

`PIPELINE_VERSION` is the identity of all of that together. It is not the
checkpoint — a change to windowing or pooling moves every vector while
`laion/clap-htsat-unfused` stays fixed, which is exactly what happened when
Familiar moved from middle-ten-seconds to a whole-track mean.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np

from .artifacts import (
    ARTIFACT_VERSION,
    CHECKPOINT,
    ArtifactsMissing,
    Precision,
    audio_session,
    model_dir,
    providers,
    text_session,
    tokenizer,
)
from .chunking import DecodeError, decode, stream_windows, windows
from .mel import FRONTEND_VERSION, N_FRAMES, SAMPLE_RATE, WINDOW_SAMPLES, log_mel

#: The public API. `audio_session`, `model_dir` and `providers` are here because
#: they were already depended on from outside — Familiar imports all three out of
#: `clapback_embed.artifacts` (`ADR-0005` point 11). Three names that worked but
#: were not declared is a contract nobody can safely refactor against, including
#: whoever wrote them, so the declaration widens to the truth rather than the use
#: narrowing to the declaration.
__all__ = [
    "PIPELINE_VERSION",
    "SAMPLE_RATE",
    "WINDOW_SAMPLES",
    "ArtifactsMissing",
    "DecodeError",
    "Precision",
    "audio_session",
    "decode",
    "embed_audio",
    "embed_file",
    "embed_text",
    "model_dir",
    "providers",
]

#: Bumped when windowing, pooling, the front-end, the checkpoint or the precision
#: changes. Two vectors are comparable only if this matches.
POOLING_VERSION = 1

PIPELINE_VERSION = (
    f"{CHECKPOINT}"
    f"+frontend{FRONTEND_VERSION}"
    f"+artifact{ARTIFACT_VERSION}"
    f"+pool{POOLING_VERSION}"
    f"+fp32"
)


def _pool(vectors: list[np.ndarray]) -> list[float]:
    """Mean of the raw encoder outputs, then L2-normalise.

    **Not** a mean of already-normalised vectors — the two differ, and this is the
    one that was measured. Normalising first would weight every window equally
    regardless of the encoder's output magnitude, which may well be better and is
    a different pipeline version, not a free change.
    """
    mean = np.mean(vectors, axis=0)
    magnitude = float(np.linalg.norm(mean))
    if magnitude == 0.0:
        # No direction, so no similarity to anything. Returning it would place the
        # track at an arbitrary point in the space rather than nowhere.
        raise ValueError("embedding has zero magnitude")
    return (mean / magnitude).astype(np.float64).tolist()


def embed_audio(
    audio: np.ndarray, *, precision: Precision = Precision.FP32
) -> list[float]:
    """Embed already-decoded 48 kHz mono audio.

    Args:
        audio: mono float samples at `SAMPLE_RATE`.
        precision: fp32 unless the vector will never leave this machine — see
            `artifacts` for why fp16 is not corpus-safe.
    """
    return _embed_windows(
        windows(np.ascontiguousarray(audio, dtype=np.float32)), precision
    )


def _embed_windows(source: Iterable[np.ndarray], precision: Precision) -> list[float]:
    """Run the encoder over windows from any source, streamed or materialised."""
    session = audio_session(precision)
    name = session.get_inputs()[0].name
    vectors = []
    for window in source:
        mel = log_mel(window)
        if mel.shape != (N_FRAMES, 64):
            raise AssertionError(
                f"mel is {mel.shape}, expected ({N_FRAMES}, 64) — the encoder "
                "accepts exactly one shape and would reject or mis-handle this"
            )
        feed = {name: mel[None, None, :, :].astype(np.float32)}
        vectors.append(session.run(None, feed)[0][0].astype(np.float64))
    return _pool(vectors)


def embed_file(
    path: str | Path, *, precision: Precision = Precision.FP32
) -> list[float]:
    """Embed an audio file end to end. The function almost every caller wants.

    Decodes a window at a time, so peak memory is a few megabytes whatever the
    track length rather than scaling with it. `stream_windows` records why the
    vector is identical to decoding the whole file first.
    """
    return _embed_windows(stream_windows(path), precision)


def embed_text(text: str) -> list[float]:
    """Embed a natural-language description into the same 512-d space.

    CLAP puts text and audio in one space, so this is what makes "something dreamy
    with piano" a query rather than a keyword search.
    """
    session = text_session()
    names = {i.name for i in session.get_inputs()}
    encoded = tokenizer().encode(text)
    feed = {
        "input_ids": np.array([encoded.ids], dtype=np.int64),
        "attention_mask": np.array([encoded.attention_mask], dtype=np.int64),
    }
    vector = session.run(None, {k: v for k, v in feed.items() if k in names})[0][0]
    magnitude = float(np.linalg.norm(vector))
    if magnitude == 0.0:
        raise ValueError("text embedding has zero magnitude")
    return (vector.astype(np.float64) / magnitude).tolist()
