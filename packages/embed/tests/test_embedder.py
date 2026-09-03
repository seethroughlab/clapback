"""Pooling, the pipeline identity, and end-to-end determinism.

The pooling tests stub the encoder so they run without the 614 MB of artifacts.
The determinism tests need them and are marked `artifacts`; they are the ones
that actually prove the package's claim, so they skip loudly rather than being
quietly absent.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np
import pytest

import clapback_embed
from clapback_embed import PIPELINE_VERSION, Precision, embed_audio
from clapback_embed.artifacts import ArtifactsMissing, audio_session
from clapback_embed.mel import SAMPLE_RATE


class _StubSession:
    """Returns a caller-supplied vector per window, in order."""

    def __init__(self, vectors):
        self._vectors = list(vectors)
        self._calls = 0

    def get_inputs(self):
        class _I:
            name = "input_features"

        return [_I()]

    def run(self, _outputs, _feed):
        v = self._vectors[min(self._calls, len(self._vectors) - 1)]
        self._calls += 1
        return [np.asarray([v], dtype=np.float32)]


def _audio(seconds: float) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    return (0.4 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)


def test_pooling_is_mean_of_raw_vectors_then_normalised():
    """Not a mean of already-normalised vectors — the two differ."""
    a = np.concatenate([[3.0, 4.0], np.zeros(510)])
    b = np.concatenate([[9.0, 12.0], np.zeros(510)])
    with patch.object(clapback_embed, "audio_session", return_value=_StubSession([a, b])):
        got = embed_audio(_audio(20.0))
    expected = np.mean([a, b], axis=0)
    np.testing.assert_allclose(got, expected / np.linalg.norm(expected), atol=1e-12)


def test_result_is_unit_length_and_512_dimensional():
    vecs = [np.full(512, float(i + 1)) for i in range(3)]
    with patch.object(clapback_embed, "audio_session", return_value=_StubSession(vecs)):
        got = embed_audio(_audio(30.0))
    assert len(got) == 512
    assert np.linalg.norm(got) == pytest.approx(1.0)


def test_every_window_contributes_equally():
    """A long track is not represented by any single window of it.

    Windows must differ in *direction*: vectors differing only in magnitude
    normalise to the same thing, so a magnitude-only fixture would pass against an
    implementation that returned just one of them.
    """
    vecs = []
    for i in range(30):
        v = np.zeros(512)
        v[i] = 1.0
        vecs.append(v)
    with patch.object(clapback_embed, "audio_session", return_value=_StubSession(vecs)):
        got = embed_audio(_audio(300.0))
    middle = vecs[15]
    assert not np.allclose(got, middle / np.linalg.norm(middle))
    expected = np.mean(vecs, axis=0)
    np.testing.assert_allclose(got, expected / np.linalg.norm(expected), atol=1e-12)


def test_a_zero_magnitude_result_is_refused():
    """No direction means no similarity to anything.

    Returning it would place the track at an arbitrary point rather than nowhere,
    and it would look like an ordinary row.
    """
    with patch.object(
        clapback_embed, "audio_session", return_value=_StubSession([np.zeros(512)])
    ), pytest.raises(ValueError, match="zero magnitude"):
        embed_audio(_audio(20.0))


def test_pipeline_version_names_every_thing_that_can_move_a_vector():
    """The identity is the pipeline, not the checkpoint.

    Familiar changed every vector it holds while `laion/clap-htsat-unfused` stayed
    fixed, by moving from middle-ten-seconds to a whole-track mean. A version that
    named only the checkpoint would have called those comparable.
    """
    for part in ("laion/clap-htsat-unfused", "frontend", "artifact", "pool", "fp32"):
        assert part in PIPELINE_VERSION


def test_missing_artifacts_say_how_to_produce_them():
    audio_session.cache_clear()
    with patch.dict("os.environ", {"CLAPBACK_MODEL_DIR": "/nonexistent/clapback"}), \
            pytest.raises(ArtifactsMissing, match="export_models.py"):
        audio_session(Precision.FP32)
    audio_session.cache_clear()


# ---------------------------------------------------------------------------
# Needs the real encoders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _artifacts():
    try:
        audio_session(Precision.FP32)
    except ArtifactsMissing as exc:
        pytest.skip(str(exc).splitlines()[0])


@pytest.mark.artifacts
def test_the_same_audio_embeds_to_the_same_vector(_artifacts):
    """Determinism, which every other guarantee is built on.

    `rand_trunc` would break this — and it would break it silently, since a random
    crop still yields 512 plausible floats.
    """
    audio = _audio(47.0)
    first = embed_audio(audio)
    second = embed_audio(audio)
    np.testing.assert_array_equal(first, second)


@pytest.mark.artifacts
def test_a_real_embedding_is_unit_length_and_512_dimensional(_artifacts):
    got = embed_audio(_audio(25.0))
    assert len(got) == 512
    assert np.linalg.norm(got) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.artifacts
def test_leading_silence_barely_moves_the_vector(_artifacts):
    """Why whole-track pooling exists.

    Two rips of one recording differ by trim. Measured 2026-09-01: a 1.2s
    difference moves a middle-ten-seconds embedding to 0.95 — as far as genuinely
    different music — and a chunked mean to 0.997 or better.
    """
    audio = _audio(60.0)
    shifted = np.concatenate([np.zeros(int(1.2 * SAMPLE_RATE), dtype=np.float32), audio])
    similarity = float(np.dot(embed_audio(audio), embed_audio(shifted)))
    assert similarity > 0.99


@pytest.mark.artifacts
def test_fp16_diverges_enough_to_matter(_artifacts):
    """The measurement behind "contributed vectors are fp32".

    fp16 is ~1.5e-6 from fp32 — an order of magnitude past the mel and runtime
    differences, and outside the corpus's identical band. Asserted as a bound in
    both directions: close enough to be useful locally, far enough to be unsafe
    to contribute.
    """
    try:
        audio_session(Precision.FP16)
    except ArtifactsMissing:
        pytest.skip("fp16 artifact not exported")
    audio = _audio(40.0)
    similarity = float(np.dot(embed_audio(audio), embed_audio(audio, precision=Precision.FP16)))
    assert 0.99 < similarity < 0.9999999


# ---------------------------------------------------------------------------
# Execution providers
# ---------------------------------------------------------------------------


def test_providers_default_to_cpu():
    """CPU is the only provider whose vectors have been shown to agree."""
    from clapback_embed.artifacts import providers

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("CLAPBACK_PROVIDERS", None)
        assert providers() == ["CPUExecutionProvider"]


def test_providers_can_be_overridden_for_acceleration():
    """Acceleration is available, not foreclosed.

    `onnxruntime-gpu` is ~200 MB against the ~5 GB of a CUDA torch build, so this
    is a cheaper route to GPU inference than the one it replaces — but the
    resulting vectors are unvalidated, so it is opt-in rather than automatic.

    The driver probe is patched rather than left to the host: on a machine
    without `libcuda.so.1` the request is filtered out, which is the subject of
    `test_cuda_is_dropped_when_the_driver_is_absent` below.
    """
    from clapback_embed.artifacts import providers

    with patch.dict(
        "os.environ",
        {"CLAPBACK_PROVIDERS": "CUDAExecutionProvider, CPUExecutionProvider"},
    ), patch("clapback_embed.artifacts._cuda_driver_present", return_value=True):
        assert providers() == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_an_empty_provider_override_falls_back_to_cpu():
    """An unset-but-present variable must not produce an empty provider list."""
    from clapback_embed.artifacts import providers

    with patch.dict("os.environ", {"CLAPBACK_PROVIDERS": "   "}):
        assert providers() == ["CPUExecutionProvider"]


def test_cuda_is_dropped_when_the_driver_is_absent():
    """Requesting CUDA without `libcuda.so.1` **segfaults**; it does not fall back.

    An image carrying onnxruntime-gpu and the CUDA pip packages has everything
    except the driver library, which comes from the host. On a machine with no
    GPU, initialisation gets far enough to die with SIGSEGV — observed as exit
    code 139 on a CI runner, after ONNX Runtime had printed its provider list.

    A try/except cannot catch that, so the provider is filtered out before it is
    ever requested.
    """
    from clapback_embed.artifacts import providers

    with patch.dict(
        "os.environ",
        {"CLAPBACK_PROVIDERS": "CUDAExecutionProvider,CPUExecutionProvider"},
    ), patch("clapback_embed.artifacts._cuda_driver_present", return_value=False):
        assert providers() == ["CPUExecutionProvider"]


def test_cuda_is_kept_when_the_driver_is_present():
    from clapback_embed.artifacts import providers

    with patch.dict(
        "os.environ",
        {"CLAPBACK_PROVIDERS": "CUDAExecutionProvider,CPUExecutionProvider"},
    ), patch("clapback_embed.artifacts._cuda_driver_present", return_value=True):
        assert providers() == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_the_driver_probe_never_raises():
    """It runs on every provider resolution, including hosts with no NVIDIA stack."""
    from clapback_embed.artifacts import _cuda_driver_present

    assert isinstance(_cuda_driver_present(), bool)


@pytest.mark.artifacts
def test_streaming_and_whole_file_decode_agree_below_the_storage_floor(
    _artifacts, tmp_path
):
    """The corpus contract: how a contributor decodes must not change the vector.

    `embed_file` streams and `embed_audio(decode(...))` does not. If these
    diverge, streamed and whole-file contributors disagree about identical audio
    and consensus reads it as disagreement about the *recording* — the failure
    this package exists to prevent.

    The bound is the float4 floor, not exact equality. `pgvector` stores float4,
    so a byte-identical resubmission already scores 0.99999994 — a 6.0e-08
    divergence — and every agreement threshold derives from that. Measured
    divergence here is 0.0 for 48 kHz material and 3.4e-14 for a 56-minute
    44.1 kHz mp3. Asserting `==` would pass on this 25-second fixture and fail on
    a long track, reporting a correct implementation as broken.
    """
    import soundfile as sf

    from clapback_embed import embed_file
    from clapback_embed.chunking import decode

    n = int(25.0 * 44100)
    t = np.arange(n, dtype=np.float64) / 44100
    sweep = (0.4 * np.sin(2 * np.pi * (200 + 1800 * t / t[-1]) * t)).astype(np.float32)
    path = tmp_path / "sweep.wav"
    sf.write(path, sweep, 44100, subtype="FLOAT")

    streamed = np.array(embed_file(path))
    whole = np.array(embed_audio(decode(path)))

    divergence = 1 - float(np.dot(streamed, whole))
    assert divergence < 1e-9, (
        f"streamed and whole-file vectors diverge by {divergence:.2e}, which is "
        "within sight of the 6.0e-08 floor the corpus treats as identical"
    )
