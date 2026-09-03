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


# --- Streaming decode ------------------------------------------------------
#
# `stream_windows` exists so a 57-minute mix does not cost 2 GB of resident
# audio. That is only worth having if it yields *the same windows*, so these
# compare it against the whole-file path rather than merely checking shapes.


def _write(tmp_path, audio: np.ndarray, sr: int, channels: int = 1):
    import soundfile as sf

    path = tmp_path / f"probe_{sr}_{channels}.wav"
    data = audio if channels == 1 else np.stack([audio] * channels, axis=1)
    sf.write(path, data, sr, subtype="FLOAT")
    return path


def _sweep(seconds: float, sr: int) -> np.ndarray:
    """Broadband, non-repeating — a pure tone can hide resampler errors."""
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float64) / sr
    return (0.4 * np.sin(2 * np.pi * (200 + 1800 * t / max(t[-1], 1e-9)) * t)).astype(
        np.float32
    )


def test_streaming_is_bit_identical_when_no_resampling_is_needed(tmp_path):
    from clapback_embed.chunking import decode, stream_windows

    path = _write(tmp_path, _sweep(25.0, SAMPLE_RATE), SAMPLE_RATE)
    streamed = list(stream_windows(path))
    whole = windows(decode(path))

    assert len(streamed) == len(whole) == 2
    for i, (a, b) in enumerate(zip(streamed, whole)):
        assert np.array_equal(a, b), f"window {i} differs with no resampler involved"


def test_streaming_matches_the_whole_file_path_through_a_resampler(tmp_path):
    """44.1 kHz is the case that made naive block-streaming wrong.

    Resampling each block independently gives per-sample errors up to 1.64 and a
    vector 3.8e-06 from the whole-file one — sixty times the corpus's 6.0e-08
    floor. A stateful resampler leaves only tail-handling noise, which does not
    survive the mel filterbank.
    """
    from clapback_embed.chunking import decode, stream_windows

    path = _write(tmp_path, _sweep(25.0, 44100), 44100)
    streamed = list(stream_windows(path))
    whole = windows(decode(path))

    assert len(streamed) == len(whole) == 2
    for i, (a, b) in enumerate(zip(streamed, whole)):
        assert np.allclose(a, b, atol=1e-5), f"window {i} diverged: {np.abs(a-b).max()}"


def test_streaming_mixes_channels_the_same_way(tmp_path):
    from clapback_embed.chunking import decode, stream_windows

    path = _write(tmp_path, _sweep(12.0, SAMPLE_RATE), SAMPLE_RATE, channels=2)
    first = next(iter(stream_windows(path)))
    assert np.array_equal(first, windows(decode(path))[0])


def test_streaming_drops_the_trailing_partial_window_too(tmp_path):
    from clapback_embed.chunking import stream_windows

    path = _write(tmp_path, _sweep(29.5, SAMPLE_RATE), SAMPLE_RATE)
    out = list(stream_windows(path))
    assert len(out) == 2
    assert all(w.size == WINDOW_SAMPLES for w in out)


def test_streaming_repeatpads_a_track_shorter_than_one_window(tmp_path):
    from clapback_embed.chunking import decode, stream_windows

    path = _write(tmp_path, _sweep(3.0, SAMPLE_RATE), SAMPLE_RATE)
    out = list(stream_windows(path))
    assert len(out) == 1
    assert out[0].size == WINDOW_SAMPLES
    assert np.array_equal(out[0], windows(decode(path))[0])


def test_streaming_refuses_an_empty_file(tmp_path):
    from clapback_embed.chunking import DecodeError, stream_windows

    path = _write(tmp_path, np.zeros(0, np.float32), SAMPLE_RATE)
    with pytest.raises(DecodeError):
        list(stream_windows(path))


def test_streaming_falls_back_when_libsndfile_cannot_open_the_container(tmp_path):
    """Some AAC and ALAC files open in librosa and not in libsndfile."""
    from unittest.mock import patch

    from clapback_embed.chunking import decode, stream_windows

    path = _write(tmp_path, _sweep(12.0, SAMPLE_RATE), SAMPLE_RATE)
    expected = windows(decode(path))
    with patch("clapback_embed.chunking.sf.info", side_effect=RuntimeError("nope")):
        out = list(stream_windows(path))
    assert len(out) == len(expected)
    assert np.array_equal(out[0], expected[0])


def test_streaming_peak_memory_does_not_track_track_length(tmp_path):
    """The whole point, and the claim is constancy rather than a small number.

    Whole-file decode makes peak memory a function of duration — 2.01 GB for a
    57-minute mix, paid per concurrent worker, which is what forced Familiar's
    analysis pool from three workers down to two. Doubling the track must not
    move the peak, so this measures two durations rather than asserting a
    threshold that only holds for whatever length the test happens to use.
    """
    import tracemalloc

    from clapback_embed.chunking import decode, stream_windows

    def peak(seconds: float, work) -> int:
        path = _write(tmp_path / f"{seconds}", _sweep(seconds, SAMPLE_RATE), SAMPLE_RATE)
        tracemalloc.start()
        count = work(path)
        _, high = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert count == int(seconds) // 10
        return high

    (tmp_path / "60.0").mkdir()
    (tmp_path / "180.0").mkdir()
    short = peak(60.0, lambda p: sum(1 for _ in stream_windows(p)))
    long = peak(180.0, lambda p: sum(1 for _ in stream_windows(p)))

    assert long < short * 1.5, (
        f"peak grew {short / 1e6:.1f} MB -> {long / 1e6:.1f} MB when the track "
        "tripled — the buffer is holding the track"
    )

    # And confirm the comparison is meaningful: the whole-file path does grow.
    whole_short = peak(60.0, lambda p: len(windows(decode(p))))
    whole_long = peak(180.0, lambda p: len(windows(decode(p))))
    assert whole_long > whole_short * 2, (
        "whole-file decode did not grow with duration, so this test would pass "
        "even if streaming were removed"
    )
