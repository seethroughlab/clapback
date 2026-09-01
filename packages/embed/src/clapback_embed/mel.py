"""The pinned CLAP front-end.

Every constant here is part of the contract, not a tuning parameter. Changing any
of them changes every vector the corpus holds, so they carry a version
(`FRONTEND_VERSION`) rather than being adjustable.

**This reproduces `transformers.ClapFeatureExtractor` without importing it.**
Verified 2026-09-01 against `laion/clap-htsat-unfused`:

- filter banks differ by 1.15e-09
- the log-mel differs by 7.6e-06 dB peak over a 102 dB range, 5.3e-07 mean
- digital silence, denormals, DC offset and clipping differ by exactly zero
- full chunked means agree at cosine 1.0000000000 on four of five real tracks,
  worst 0.9999998808 — the same order as the 6.0e-08 that `float4` storage costs

The one trap worth naming: `ClapFeatureExtractor` selects a **different filter
bank** when `truncation="fusion"` — torchaudio/HTK rather than slaney. The
resulting vectors look entirely normal and are comparable with nothing. This
module implements the slaney path only, which is what `rand_trunc` (the
checkpoint's default) uses.
"""

from __future__ import annotations

import librosa
import numpy as np

#: Bumped when any constant below changes. Part of the pipeline identity.
FRONTEND_VERSION = 1

SAMPLE_RATE = 48_000
#: CLAP's HTSAT encoder has positional embeddings sized for a 1001x64 mel, so it
#: accepts exactly ten seconds. Both ONNX and PyTorch reject anything else, the
#: latter with "the wav size should be less than or equal to the swin input size".
WINDOW_SAMPLES = 480_000
N_FFT = 1024
HOP_LENGTH = 480
N_MELS = 64
FMIN = 50
FMAX = 14_000
#: `spectrogram(..., mel_floor=1e-10)` in the reference implementation.
MEL_FLOOR = 1e-10
#: Frames a full window produces, and the encoder's declared input height.
N_FRAMES = 1001


def _filter_bank() -> np.ndarray:
    """Slaney-normalised mel filters — `librosa.filters.mel` defaults.

    The reference calls this `mel_filters_slaney` and uses it for every truncation
    mode except `fusion`.
    """
    return librosa.filters.mel(
        sr=SAMPLE_RATE,
        n_fft=N_FFT,
        n_mels=N_MELS,
        fmin=FMIN,
        fmax=FMAX,
        norm="slaney",
        htk=False,
    )


_FB = _filter_bank()


def log_mel(window: np.ndarray) -> np.ndarray:
    """Log-mel spectrogram of one window, shaped (frames, 64) as the encoder wants.

    Args:
        window: mono float samples at 48 kHz. Exactly `WINDOW_SAMPLES` for a full
            window; shorter input is accepted and produces fewer frames, which the
            caller must not feed to the encoder.

    Returns:
        float32 array of shape (frames, 64) in dB.
    """
    spectrum = librosa.stft(
        window,
        n_fft=N_FFT,
        hop_length=HOP_LENGTH,
        win_length=N_FFT,
        window="hann",  # periodic, matching `window_function(..., periodic=True)`
        center=True,
        pad_mode="reflect",
    )
    power = np.abs(spectrum) ** 2
    mel = np.maximum(MEL_FLOOR, _FB @ power)
    # log_mel="dB" with reference=1.0 and no db_range clamp (`top_db` is None on
    # this checkpoint), which reduces to a plain 10*log10.
    return (10.0 * np.log10(np.maximum(mel, MEL_FLOOR))).T.astype(np.float32)
