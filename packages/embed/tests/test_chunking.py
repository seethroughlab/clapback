"""The windowing rule, which is corpus contract rather than implementation detail.

Needs no ONNX artifacts and no torch, so it runs everywhere. Two implementations
that disagree here produce different vectors for the same audio, and the
disagreement would be invisible — both return 512 plausible floats.
"""

from __future__ import annotations

import numpy as np
import pytest

from clapback_embed.chunking import repeatpad, windows
from clapback_embed.mel import SAMPLE_RATE, WINDOW_SAMPLES


def tone(seconds: float, freq: float = 440.0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (6.66, 1),      # shorter than a window
        (10.0, 1),      # exactly one
        (19.99, 1),     # just under two — the tail is dropped
        (20.0, 2),
        (24.88, 2),
        (143.02, 14),
        (323.0, 32),
    ],
)
def test_window_count(seconds, expected):
    assert len(windows(tone(seconds))) == expected


def test_every_window_is_exactly_one_window_long():
    """The invariant reproducibility rests on.

    The reference extractor defaults to `truncation="rand_trunc"`, which takes a
    *random* crop of anything longer than one window. A window of the wrong length
    does not fail — it silently makes the vector irreproducible.
    """
    for w in windows(tone(143.02)):
        assert w.size == WINDOW_SAMPLES


def test_windows_are_consecutive_and_non_overlapping():
    audio = tone(45.0)
    got = windows(audio)
    assert len(got) == 4
    for i, w in enumerate(got):
        np.testing.assert_array_equal(w, audio[i * WINDOW_SAMPLES : (i + 1) * WINDOW_SAMPLES])


def test_trailing_partial_window_is_dropped_not_padded():
    """Padding would inject silence the track does not contain."""
    audio = tone(24.88)
    got = windows(audio)
    assert len(got) == 2
    np.testing.assert_array_equal(np.concatenate(got), audio[: 2 * WINDOW_SAMPLES])


def test_short_audio_is_repeatpadded_to_exactly_one_window():
    audio = tone(3.0)
    got = windows(audio)
    assert len(got) == 1 and got[0].size == WINDOW_SAMPLES
    # Tiled floor(10/3) = 3 times, then zero-filled — matching the reference's
    # `repeatpad`, which uses a floor and does not fill by repetition alone.
    np.testing.assert_array_equal(got[0][: audio.size], audio)
    assert got[0][-1] == 0.0


def test_repeatpad_does_not_truncate_meaningful_audio():
    audio = tone(9.9)
    padded = repeatpad(audio)
    assert padded.size == WINDOW_SAMPLES
    np.testing.assert_array_equal(padded[: audio.size], audio)


@pytest.mark.parametrize(
    "samples",
    [
        1,
        WINDOW_SAMPLES - 1,
        WINDOW_SAMPLES,
        WINDOW_SAMPLES + 1,
        WINDOW_SAMPLES * 3 - 1,
        WINDOW_SAMPLES * 3,
        WINDOW_SAMPLES * 7 + 12_345,
        int(SAMPLE_RATE * 611.7),
    ],
)
def test_windows_are_always_full_whatever_the_length(samples):
    """The invariant, across the boundaries where off-by-one slicing breaks.

    The `AssertionError` inside `windows()` cannot fire while floor division picks
    the count, so there is no honest test for the raise itself — it guards a
    *future* change to the slicing. This asserts the property it protects, which
    is the thing that would actually break.
    """
    got = windows(np.zeros(samples, dtype=np.float32))
    assert got, "at least one window is always produced"
    assert all(w.size == WINDOW_SAMPLES for w in got)
    assert len(got) == max(1, samples // WINDOW_SAMPLES)
