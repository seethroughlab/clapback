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

from collections.abc import Iterator
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

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


def _mono(block: np.ndarray) -> np.ndarray:
    """Average channels, matching `librosa.to_mono`'s plain mean."""
    return block[:, 0] if block.shape[1] == 1 else block.mean(axis=1)


def stream_windows(path: str | Path) -> Iterator[np.ndarray]:
    """Yield the same windows as `windows(decode(path))`, without holding the track.

    `decode` materialises the whole file, and librosa holds a native-rate and a
    resampled copy at once. Measured on a 56-minute mp3: **3.42 GB peak RSS
    against 0.15 GB here**, for the same 337 windows. That cost is paid per
    concurrent worker and scales with the longest file anyone owns, which is why
    it forced Familiar's analysis pool from three workers to two. The buffer here
    is one window plus one block, whatever the duration.

    **Resampling is done by a *stateful* `soxr.ResampleStream`**, so the polyphase
    filter history carries across block boundaries. This is the load-bearing
    detail. Resampling each block independently — the obvious implementation —
    gives per-sample errors up to 1.64 and a vector 3.8e-06 from the whole-file
    one: **sixty times the 6.0e-08 float4 floor** that every agreement threshold
    in the corpus derives from. It is inaudible and fatal to consensus, because
    the corpus would read it as two contributors disagreeing about the recording.

    What the vectors actually agree to, measured rather than assumed:

    - **48 kHz sources: bit-identical**, always. No resampler runs.
    - **Resampled sources: identical for short material**; on a 56-minute 44.1 kHz
      mp3 the cosine divergence is **3.4e-14**, from a 5.4e-07 per-sample
      difference in the resampler's tail that mostly dies in the mel filterbank.

    3.4e-14 is six orders of magnitude below the 6.0e-08 floor at which a
    *byte-identical* resubmission already fails to score exactly 1.0, because
    `pgvector` stores float4. Streamed and whole-file contributors are therefore
    indistinguishable to anything the corpus can measure, and this does **not**
    move `PIPELINE_VERSION`. Note the claim is "below what storage preserves",
    not "bit-identical" — the latter holds only for short or 48 kHz material, and
    asserting it would make a correct implementation look broken on a long track.

    Falls back to whole-file decode when libsndfile cannot open the container at
    all — some AAC and ALAC files — since `librosa` reaches formats it does not.
    """
    try:
        native_sr = sf.info(str(path)).samplerate
    except Exception:  # noqa: BLE001 - any failure to open means try the other backends
        # libsndfile does not know this container; librosa's other backends may.
        yield from windows(decode(path))
        return

    resampler = None
    if native_sr != SAMPLE_RATE:
        try:
            import soxr

            resampler = soxr.ResampleStream(native_sr, SAMPLE_RATE, 1, quality="HQ")
        except Exception:  # noqa: BLE001 - no streaming resampler, so decode whole
            yield from windows(decode(path))
            return

    # One window's worth of native-rate samples per read.
    blocksize = max(1, round(WINDOW_SAMPLES * native_sr / SAMPLE_RATE))
    # A single reused window, filled across block boundaries. Accumulating into a
    # list and concatenating instead would be simpler and would hold several
    # windows at the peak, which is the cost this function exists to remove.
    buffer = np.empty(WINDOW_SAMPLES, dtype=np.float32)
    filled = emitted = 0

    def take(chunk: np.ndarray) -> Iterator[np.ndarray]:
        nonlocal filled, emitted
        position = 0
        while position < chunk.size:
            span = min(WINDOW_SAMPLES - filled, chunk.size - position)
            buffer[filled : filled + span] = chunk[position : position + span]
            filled += span
            position += span
            if filled == WINDOW_SAMPLES:
                # Copied because the caller holds this while `buffer` refills.
                yield buffer.copy()
                emitted += 1
                filled = 0

    def convert(block: np.ndarray, *, last: bool) -> np.ndarray:
        mono = _mono(block)
        if resampler is None:
            return mono
        # `last=True` flushes the filter tail, and must be passed with the final
        # block rather than afterwards with an empty one.
        return resampler.resample_chunk(mono, last=last)

    try:
        # One block of lookahead, so the last read is known to be the last.
        previous = None
        for block in sf.blocks(
            str(path), blocksize=blocksize, dtype="float32", always_2d=True
        ):
            if previous is not None:
                yield from take(convert(previous, last=False))
            previous = block
        if previous is not None:
            yield from take(convert(previous, last=True))
    except Exception as exc:
        raise DecodeError(f"could not decode {path}: {exc}") from exc

    if emitted == 0:
        # Shorter than one window, or empty. `windows` pads the former; the
        # latter is an error, and must stay one — a silently zeroed window would
        # pass every downstream check.
        if filled == 0:
            raise DecodeError(f"{path} decoded to zero samples")
        yield repeatpad(np.ascontiguousarray(buffer[:filled], dtype=np.float32))
    # A trailing partial window is dropped — rule 3.
