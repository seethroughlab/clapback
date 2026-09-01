"""Decoding and windowing — how a track of any length becomes CLAP-sized pieces.

The rule, which is the corpus contract and not an implementation detail:

1. Decode to 48 kHz mono.
2. Take consecutive, non-overlapping 480,000-sample windows.
3. **Drop a trailing partial window.** Zero-padding it would inject silence the
   track does not contain and pull the mean toward "quiet" in proportion to how
   short the remainder is; what is dropped is under ten seconds of material that
   the other windows already represent.
4. A track shorter than one window is `repeatpad`ed — tiled to length, then zero
   filled — which is what the reference extractor does below one window and the
   only case where padding is correct.

Two implementations that differ here produce different vectors, so the rule is
stated rather than left to whoever writes the loop.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

from .mel import SAMPLE_RATE, WINDOW_SAMPLES


class DecodeError(RuntimeError):
    """The audio could not be read."""


def decode(path: str | Path) -> np.ndarray:
    """Decode any supported file to 48 kHz mono float32."""
    try:
        audio, _ = librosa.load(str(path), sr=SAMPLE_RATE, mono=True)
    except Exception as exc:  # librosa raises a wide variety from its backends
        raise DecodeError(f"could not decode {path}: {exc}") from exc
    if audio.size == 0:
        raise DecodeError(f"{path} decoded to zero samples")
    return np.ascontiguousarray(audio, dtype=np.float32)


def repeatpad(audio: np.ndarray) -> np.ndarray:
    """Tile a short clip up to one window, then zero-fill the remainder.

    Mirrors the reference extractor's `padding="repeatpad"`, which tiles
    `int(max_length / len(waveform))` times and zero-pads the rest — note the
    floor, so this does not fill the window by repetition alone.
    """
    if audio.size >= WINDOW_SAMPLES:
        return audio[:WINDOW_SAMPLES]
    repeats = int(WINDOW_SAMPLES / audio.size)
    tiled = np.tile(audio, repeats) if repeats > 1 else audio
    return np.pad(tiled, (0, WINDOW_SAMPLES - tiled.size), mode="constant")


def windows(audio: np.ndarray) -> list[np.ndarray]:
    """Split decoded audio into the windows CLAP will see.

    Every element is exactly `WINDOW_SAMPLES` long. That invariant is what makes
    the embedding reproducible — the reference extractor defaults to
    `truncation="rand_trunc"`, which takes a *random* crop of anything longer, so
    a window of the wrong length silently makes the vector irreproducible while
    still returning 512 plausible floats.
    """
    if audio.size < WINDOW_SAMPLES:
        return [repeatpad(audio)]

    count = audio.size // WINDOW_SAMPLES
    out = [
        np.ascontiguousarray(audio[i * WINDOW_SAMPLES : (i + 1) * WINDOW_SAMPLES])
        for i in range(count)
    ]
    for i, window in enumerate(out):
        if window.size != WINDOW_SAMPLES:
            raise AssertionError(
                f"window {i} is {window.size} samples, expected {WINDOW_SAMPLES}"
            )
    return out
