"""The front-end, checked against exact arithmetic and against `transformers`.

The `transformers` comparison is the one that matters and the one that cannot run
everywhere — it is the drift guard for the whole corpus. It skips when the
reference is absent rather than being deleted, because the property it asserts is
the package's entire reason to exist.
"""

from __future__ import annotations

import numpy as np
import pytest

from clapback_embed.mel import (
    FMAX,
    FMIN,
    MEL_FLOOR,
    N_FFT,
    N_FRAMES,
    N_MELS,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    log_mel,
)


def test_a_full_window_produces_the_shape_the_encoder_declares():
    """1001x64 is not a coincidence — HTSAT's positional embeddings are sized for it."""
    assert log_mel(np.zeros(WINDOW_SAMPLES, dtype=np.float32)).shape == (N_FRAMES, N_MELS)


def test_silence_is_the_floor_exactly():
    """Zero power clamps to `MEL_FLOOR`, so every bin is 10*log10(1e-10) = -100 dB.

    An exact value, which makes this a real check on the dB conversion rather than
    an approximate one.
    """
    mel = log_mel(np.zeros(WINDOW_SAMPLES, dtype=np.float32))
    assert np.all(mel == pytest.approx(10.0 * np.log10(MEL_FLOOR)))
    assert not np.isnan(mel).any()


@pytest.mark.parametrize(
    "signal",
    [
        np.zeros(WINDOW_SAMPLES, dtype=np.float32),
        np.full(WINDOW_SAMPLES, 1e-12, dtype=np.float32),
        np.full(WINDOW_SAMPLES, 0.9, dtype=np.float32),
        np.ones(WINDOW_SAMPLES, dtype=np.float32),
    ],
    ids=["silence", "denormal", "dc-offset", "clipped"],
)
def test_degenerate_input_never_produces_nan(signal):
    mel = log_mel(signal)
    assert np.isfinite(mel).all()


def test_output_is_float32():
    """The encoder is fed this directly; a float64 mel would cost a copy per window."""
    assert log_mel(np.zeros(WINDOW_SAMPLES, dtype=np.float32)).dtype == np.float32


def test_the_constants_are_the_ones_clap_was_trained_with():
    """A guard on the contract. Changing any of these changes every vector."""
    assert (SAMPLE_RATE, WINDOW_SAMPLES) == (48_000, 480_000)
    assert (N_FFT, N_MELS, FMIN, FMAX) == (1024, 64, 50, 14_000)
    assert N_FRAMES == 1001


# ---------------------------------------------------------------------------
# The drift guard
# ---------------------------------------------------------------------------

transformers = pytest.importorskip(
    "transformers", reason="reference implementation not installed"
)


@pytest.fixture(scope="module")
def reference():
    from transformers import ClapFeatureExtractor

    return ClapFeatureExtractor.from_pretrained("laion/clap-htsat-unfused")


def test_matches_clap_feature_extractor(reference):
    """The property the corpus depends on.

    Measured 2026-09-01 on real audio: 7.6e-06 dB peak over a 102 dB range. The
    tolerance here is deliberately tighter than "close enough to hear no
    difference" — it is close enough that the resulting vectors land inside the
    corpus's identical band.
    """
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(WINDOW_SAMPLES) * 0.3).astype(np.float32)
    theirs = reference._np_extract_fbank_features(audio, reference.mel_filters_slaney)
    np.testing.assert_allclose(log_mel(audio), theirs, atol=1e-4)


def test_uses_the_slaney_filter_bank_not_the_fusion_one(reference):
    """`truncation="fusion"` silently selects torchaudio/HTK filters instead.

    The resulting vectors look entirely normal and are comparable with nothing, so
    this asserts which bank we reproduce rather than trusting a default.
    """
    from clapback_embed.mel import _filter_bank

    np.testing.assert_allclose(_filter_bank().T, reference.mel_filters_slaney, atol=1e-7)
    assert not np.allclose(_filter_bank().T, reference.mel_filters, atol=1e-3)
