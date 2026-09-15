"""Lead-in and re-encoding sensitivity of five CLAP pipelines, measured on one library.

Question (KalinkaPlayer#128, Q5 and Q3): does a pipeline built from one to three
10-second fragments at fixed fractions of the track lose a recording when the
rip differs by a lead-in of a second or two, where a whole-track mean does not?

Pipelines
  A ref/whole     laion/clap-htsat-unfused, consecutive non-overlapping 10-s windows,
                  trailing partial dropped, mean of raw outputs, L2  (= clapback-embed)
  B music/frag3   music_audioset_epoch_15_esc_90.14 (laion_clap, HTSAT-base), three
                  10-s fragments at 25/50/75% of duration, mean, L2  (= Kalinka)
  C music/whole   music checkpoint with A's windowing  (checkpoint isolated)
  D ref/frag3     reference checkpoint with B's fragments (windowing isolated)
  E ref/mid10     one 10-s window from the middle (Familiar ADR-0104's old rule)

Variants per track: original; lead-in trimmed 0.5 / 1.2 / 3 / 5 s; MP3 320k and 128k transcodes.

Outputs leadin.json (every pooled vector, per pipeline, track and variant) and
prints a summary; `--summary` re-prints it from the JSON.

**This is an offline measurement tool, not part of the package.** It needs torch,
transformers, laion_clap (plus torchvision, which laion_clap uses and does not
declare), soundfile, soxr and ffmpeg — none of which belong in `clapback-embed`
or on the instance. Run it in a scratch venv:

    uv venv leadin && uv pip install --python leadin/bin/python \
        torch torchvision torchaudio transformers laion_clap soundfile soxr huggingface_hub
    leadin/bin/python scripts/measure_leadin.py ~/Music/Familiar        # a directory of FLACs
    leadin/bin/python scripts/measure_leadin.py --summary

The reference pipeline here is transformers' `get_audio_features` per window —
what `export_models.py` exports to ONNX — so this measures the corpus pipeline,
not an approximation of it. The music checkpoint runs through laion_clap's own
`get_audio_embedding_from_data`, which quantises to int16 as Kalinka's ONNX path
does; it is laion_clap's implementation, not Kalinka's export.

Results from the run recorded in `ADR-0008` and `ADR-0011` (2026-09-15, 56 FLACs)
are beside this file as `measure_leadin.results-2026-09-15.csv`: one row per
pipeline × track × variant with the cosine to the track's own original, the
cosine to the nearest *other* original, and whether the variant's nearest
original was still its own.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
import torch

SR = 48_000
WIN = 480_000
SHIFTS = [0.5, 1.2, 3.0, 5.0]
OUT = Path.cwd() / "leadin.json"


# ---------- audio ----------
def decode(path: Path) -> np.ndarray:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        x = soxr.resample(x, sr, SR).astype(np.float32)
    return np.ascontiguousarray(x)


def mp3_roundtrip(path: Path, bitrate: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as d:
        mp3 = Path(d) / "x.mp3"
        # -vn: some FLACs carry cover art as a video stream, which ffmpeg would try to encode
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", "-b:a", bitrate, str(mp3)], check=True)
        return decode(mp3)


def repeatpad(a: np.ndarray) -> np.ndarray:
    if a.size >= WIN:
        return a[:WIN]
    reps = WIN // a.size
    t = np.tile(a, reps) if reps > 1 else a
    return np.pad(t, (0, WIN - t.size))


def whole_windows(a: np.ndarray) -> list[np.ndarray]:
    if a.size < WIN:
        return [repeatpad(a)]
    n = a.size // WIN
    return [a[i * WIN:(i + 1) * WIN] for i in range(n)]


def frag3_windows(a: np.ndarray) -> list[np.ndarray]:
    """Kalinka's `_fragment_starts_s` + `_pad_or_crop`, verbatim in effect."""
    dur = a.size / SR
    if dur < 15.0:
        ratios = [0.0]
    elif dur < 30.0:
        ratios = [0.33, 0.66]
    else:
        ratios = [0.25, 0.5, 0.75]
    max_start = max(0.0, dur - 10.0)
    starts = [min(dur * r, max_start) for r in ratios]
    out = []
    for s in starts:
        seg = a[int(s * SR):int(s * SR) + WIN]
        out.append(repeatpad(seg) if seg.size < WIN else seg)
    return out


def mid10_windows(a: np.ndarray) -> list[np.ndarray]:
    if a.size <= WIN:
        return [repeatpad(a)]
    s = (a.size - WIN) // 2
    return [a[s:s + WIN]]


def pool(vecs: np.ndarray) -> np.ndarray:
    m = vecs.mean(axis=0)
    return m / np.linalg.norm(m)


# ---------- encoders ----------
class Reference:
    """transformers ClapModel — what clapback-embed's ONNX encoder was exported from."""

    def __init__(self):
        from transformers import ClapModel, ClapProcessor

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.proc = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")
        self.model = ClapModel.from_pretrained("laion/clap-htsat-unfused").to(self.device).eval()

    @torch.no_grad()
    def __call__(self, windows: list[np.ndarray]) -> np.ndarray:
        enc = self.proc(audio=[w for w in windows], sampling_rate=SR, return_tensors="pt")
        feats = enc["input_features"].to(self.device)
        longer = enc["is_longer"].to(self.device)
        o = self.model.get_audio_features(input_features=feats, is_longer=longer)
        o = o.pooler_output if hasattr(o, "pooler_output") else o
        return o.float().cpu().numpy()


class Music:
    """laion_clap's own implementation of the music checkpoint (HTSAT-base, no fusion)."""

    def __init__(self):
        import laion_clap
        from huggingface_hub import hf_hub_download

        ckpt = hf_hub_download("lukewys/laion_clap", "music_audioset_epoch_15_esc_90.14.pt")
        self.model = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-base")
        self.model.load_ckpt(ckpt)
        self.model.eval()

    @torch.no_grad()
    def __call__(self, windows: list[np.ndarray]) -> np.ndarray:
        # get_audio_embedding_from_data quantises to int16 and back, as Kalinka's
        # _quantize does, then runs the audio tower + projection. Exactly 10 s in,
        # so neither truncation nor padding is exercised.
        x = np.stack(windows).astype(np.float32)
        return self.model.get_audio_embedding_from_data(x=x, use_tensor=False)


PIPELINES = {
    "A ref/whole": ("ref", whole_windows),
    "B music/frag3": ("music", frag3_windows),
    "C music/whole": ("music", whole_windows),
    "D ref/frag3": ("ref", frag3_windows),
    "E ref/mid10": ("ref", mid10_windows),
}


def main() -> None:
    root = Path(sys.argv[1]).expanduser()
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    files = sorted(root.rglob("*.flac"))
    if limit:
        files = files[:limit]
    print(f"{len(files)} tracks", flush=True)

    encoders = {"ref": Reference(), "music": Music()}
    results: dict[str, dict[str, dict[str, list[float]]]] = {}  # pipeline -> track -> variant -> vec
    t0 = time.time()
    for i, f in enumerate(files):
        audio = decode(f)
        if audio.size < 30 * SR:
            print(f"skip (<30 s): {f.name}", flush=True)
            continue
        variants = {"orig": audio}
        for s in SHIFTS:
            variants[f"shift{s}"] = audio[int(s * SR):]
        variants["mp3_320k"] = mp3_roundtrip(f, "320k")
        variants["mp3_128k"] = mp3_roundtrip(f, "128k")
        for name, (enc, winfn) in PIPELINES.items():
            for vname, a in variants.items():
                vecs = encoders[enc](winfn(a))
                results.setdefault(name, {}).setdefault(f.name, {})[vname] = pool(vecs).tolist()
        print(f"[{i + 1}/{len(files)}] {f.name}  {audio.size / SR:.0f}s  {time.time() - t0:.0f}s elapsed", flush=True)
        OUT.write_text(json.dumps(results))
    summarise(results)


def summarise(results) -> None:
    print("\n== self-similarity: cosine(variant, original), median [min] over tracks")
    variants = ["shift0.5", "shift1.2", "shift3.0", "shift5.0", "mp3_320k", "mp3_128k"]
    print(f"{'pipeline':16}" + "".join(f"{v:>18}" for v in variants) + f"{'nearest other':>18}")
    for name, tracks in results.items():
        names = sorted(tracks)
        O = np.array([tracks[t]["orig"] for t in names])
        S = O @ O.T
        np.fill_diagonal(S, -1)
        nearest_other = S.max(axis=1)  # per track: most similar *different* track
        row = f"{name:16}"
        for v in variants:
            sims = np.array([float(np.dot(tracks[t][v], tracks[t]["orig"])) for t in names])
            row += f"{np.median(sims):>11.4f} [{sims.min():.3f}]"
        row += f"{np.median(nearest_other):>11.4f} [{nearest_other.max():.3f}]"
        print(row)

    print("\n== rank-1 self-retrieval: is the variant's nearest original still its own? (% of tracks)")
    print(f"{'pipeline':16}" + "".join(f"{v:>10}" for v in variants))
    for name, tracks in results.items():
        names = sorted(tracks)
        O = np.array([tracks[t]["orig"] for t in names])
        row = f"{name:16}"
        for v in variants:
            V = np.array([tracks[t][v] for t in names])
            hit = (np.argmax(V @ O.T, axis=1) == np.arange(len(names))).mean()
            row += f"{100 * hit:>9.0f}%"
        print(row)

    print("\n== margin: cosine(variant, own original) minus cosine(variant, nearest other original); "
          "min over tracks (negative = some track lost)")
    print(f"{'pipeline':16}" + "".join(f"{v:>10}" for v in variants))
    for name, tracks in results.items():
        names = sorted(tracks)
        O = np.array([tracks[t]["orig"] for t in names])
        row = f"{name:16}"
        for v in variants:
            V = np.array([tracks[t][v] for t in names])
            S = V @ O.T
            own = np.diag(S).copy()
            np.fill_diagonal(S, -1)
            row += f"{(own - S.max(axis=1)).min():>10.3f}"
        print(row)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        summarise(json.loads(OUT.read_text()))
    else:
        main()
