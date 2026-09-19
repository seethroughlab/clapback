"""Can vectors from two CLAP checkpoints be searched against each other? Measured on one library.

Question (KalinkaPlayer#128, third comment): the corpus's rows are `laion/clap-htsat-unfused`
over whole-track windows; Kalinka's are `music_audioset_epoch_15_esc_90.14` over three
10-second fragments. Do the two spaces (a) agree about which tracks are neighbours, (b) work
when mixed in one index, and (c) admit a linear bridge fitted from tracks embedded under both —
which is also what a model *upgrade* would need if old and new vectors are to be searched
together while a library is re-indexed gradually.

Pipelines (as in `measure_leadin.py`)
  A ref/whole     laion/clap-htsat-unfused, consecutive 10-s windows, mean, L2   (= the corpus)
  B music/frag3   music checkpoint, three 10-s fragments at 25/50/75 %, mean, L2  (= Kalinka)
  C music/whole   music checkpoint with A's windowing   (isolates the checkpoint)
  D ref/frag3     reference checkpoint with B's windowing (isolates the windowing)

Measurements, per pair of spaces
  1. same-track cosine across spaces, against the cosine to a *different* track across spaces
  2. neighbour agreement: top-10 overlap per track, and Spearman rho over each track's full ranking
  3. mixed pool: both spaces in one index; for each query, how many of the top 10 are from the
     other space, and where the track's own other-space vector ranks
  4. a linear bridge, 5-fold cross-validated: orthogonal Procrustes (rotation only) and ridge
     least squares; on held-out tracks, does the translated vector retrieve its own vector in
     the other space (recall@1/@10), and does it land in the same neighbourhood (top-10 overlap
     with the true vector's neighbours)?

Sampling: one track per album directory, seeded, so the set spans the library rather than one
box set. Tracks under 30 s are skipped.

**Offline measurement tool, not part of the package** — same scratch venv as `measure_leadin.py`
plus scipy:

    uv venv leadin && uv pip install --python leadin/bin/python \
        torch torchvision torchaudio transformers laion_clap soundfile soxr huggingface_hub scipy
    ssh nas 'find /path/to/music -type f -name "*.flac" -o -name "*.mp3" | sed "s|^/path/to/music/||"' > library.txt
    leadin/bin/python scripts/measure_crosspipe.py --list library.txt --root /Volumes/silo/music --n 500
    leadin/bin/python scripts/measure_crosspipe.py --summary

Results from the run of 2026-09-19 (493 tracks, one per album, FLAC and MP3 mixed, M4 Max;
the music checkpoint through laion_clap's PyTorch implementation, not Kalinka's ONNX export)
are beside this file as `measure_crosspipe.results-2026-09-19.csv`, one row per track with the
same-track cross-space cosine and top-10 overlap for every pair. The summary:

  raw, corpus vs Kalinka   same-track cosine median -0.009 (a different track: -0.006); in a
                           mixed index 0 of a query's top 10 are from the other space and its
                           own other-space vector ranks a median 769th of 985 — below random.
  neighbour agreement      top-10 overlap 0.34, Spearman rho 0.72 — against 0.53 / 0.94 for a
                           windowing change within one checkpoint (whole vs frag3).
  bridge, corpus → Kalinka ridge λ=0.03, 5-fold CV: R@1 0.86, R@10 0.99, same-track cosine
                           median 0.876 (min 0.34), top-10 overlap 0.56 — more than the
                           windowing change alone preserves (0.53). Kalinka → corpus: 0.85 /
                           0.98 / 0.947 / 0.58. Checkpoint only (both whole-track): 0.96 /
                           1.00 / 0.92 / 0.64. Procrustes is close behind with no parameter.
  in words                 the two checkpoints are orthogonal coordinate systems over largely
                           the same structure; a linear map fitted on ~400 paired tracks
                           carries a search across them about as well as a windowing change
                           does within one. R@1 rose from 0.76 to 0.86 between 200 and 493
                           tracks, so more pairs help — which is what `ADR-0019`'s recording
                           claims would supply if two identities ever overlap in the corpus.

Writes crosspipe.json incrementally (every pooled vector per pipeline and track), so a run
interrupted at 300 tracks still summarises. Decoding is ffmpeg → 48 kHz mono float32, identical
input to every pipeline, so the resampler is not a variable here.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

SR = 48_000
WIN = 480_000
OUT = Path.cwd() / "crosspipe.json"
K = 10
#: Windows per forward pass. Per-window outputs are independent, so this changes
#: nothing but memory — an 80-minute mix is ~480 windows, which one batch on MPS
#: cannot hold.
BATCH = 32


# ---------- audio ----------
def decode(path: Path) -> np.ndarray:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


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


def pool(vecs: np.ndarray) -> np.ndarray:
    m = vecs.mean(axis=0)
    return m / np.linalg.norm(m)


# ---------- encoders ----------
class Reference:
    def __init__(self):
        import torch
        from transformers import ClapModel, ClapProcessor

        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.proc = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")
        self.model = ClapModel.from_pretrained("laion/clap-htsat-unfused").to(self.device).eval()

    def __call__(self, windows: list[np.ndarray]) -> np.ndarray:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(windows), BATCH):
                enc = self.proc(audio=windows[i:i + BATCH], sampling_rate=SR, return_tensors="pt")
                feats = enc["input_features"].to(self.device)
                longer = enc["is_longer"].to(self.device)
                o = self.model.get_audio_features(input_features=feats, is_longer=longer)
                o = o.pooler_output if hasattr(o, "pooler_output") else o
                out.append(o.float().cpu().numpy())
        return np.concatenate(out)


class Music:
    def __init__(self):
        import laion_clap
        import torch
        from huggingface_hub import hf_hub_download

        self.torch = torch
        ckpt = hf_hub_download("lukewys/laion_clap", "music_audioset_epoch_15_esc_90.14.pt")
        self.model = laion_clap.CLAP_Module(enable_fusion=False, amodel="HTSAT-base")
        self.model.load_ckpt(ckpt)
        self.model.eval()

    def __call__(self, windows: list[np.ndarray]) -> np.ndarray:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(windows), BATCH):
                x = np.stack(windows[i:i + BATCH]).astype(np.float32)
                out.append(self.model.get_audio_embedding_from_data(x=x, use_tensor=False))
        return np.concatenate(out)


PIPELINES = {
    "A ref/whole": ("ref", whole_windows),
    "B music/frag3": ("music", frag3_windows),
    "C music/whole": ("music", whole_windows),
    "D ref/frag3": ("ref", frag3_windows),
}


# ---------- sampling ----------
def sample(list_file: Path, n: int, seed: int) -> list[str]:
    by_dir: dict[str, list[str]] = {}
    for line in list_file.read_text().splitlines():
        line = line.strip()
        if line:
            by_dir.setdefault(str(Path(line).parent), []).append(line)
    rng = random.Random(seed)
    picks = [rng.choice(sorted(v)) for _, v in sorted(by_dir.items())]
    rng.shuffle(picks)
    return picks[:n]


def run(args) -> None:
    files = sample(Path(args.list), args.n, args.seed)
    root = Path(args.root)
    results: dict[str, dict[str, list[float]]] = {}
    if OUT.exists():
        results = json.loads(OUT.read_text())
        print(f"resuming: {len(next(iter(results.values()), {}))} tracks already in {OUT}", flush=True)
    done = set(next(iter(results.values()), {}).keys())
    print(f"{len(files)} tracks sampled from {args.list}", flush=True)

    encoders = {"ref": Reference(), "music": Music()}
    t0 = time.time()
    n_done = 0
    for i, rel in enumerate(files):
        if rel in done:
            continue
        t1 = time.time()
        try:
            audio = decode(root / rel)
        except subprocess.CalledProcessError as e:
            print(f"decode failed: {rel}: {e.stderr.decode(errors='replace')[:200]}", flush=True)
            continue
        if audio.size < 30 * SR:
            print(f"skip (<30 s): {rel}", flush=True)
            continue
        t_dec = time.time() - t1
        t1 = time.time()
        for name, (enc, winfn) in PIPELINES.items():
            vecs = encoders[enc](winfn(audio))
            results.setdefault(name, {})[rel] = pool(vecs).tolist()
        n_done += 1
        if args.verbose:
            print(f"  decode {t_dec:5.1f} s  embed {time.time() - t1:5.1f} s  {audio.size / SR / 60:5.1f} min  {rel}", flush=True)
        if n_done % 10 == 0 or i == len(files) - 1:
            OUT.write_text(json.dumps(results))
            el = time.time() - t0
            print(f"{i + 1}/{len(files)}  {el / n_done:.1f} s/track  elapsed {el / 60:.0f} min", flush=True)
    OUT.write_text(json.dumps(results))
    summary()


# ---------- analysis ----------
def load() -> tuple[list[str], dict[str, np.ndarray]]:
    data = json.loads(OUT.read_text())
    names = sorted(set.intersection(*(set(v) for v in data.values())))
    mats = {p: np.array([data[p][n] for n in names], dtype=np.float64) for p in data}
    return names, mats


def topk(sim: np.ndarray, k: int, exclude_self: bool = True) -> np.ndarray:
    s = sim.copy()
    if exclude_self:
        np.fill_diagonal(s, -np.inf)
    return np.argsort(-s, axis=1)[:, :k]


def overlap(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.array([len(set(x) & set(y)) for x, y in zip(a, b)]) / a.shape[1]


def spearman_rows(sa: np.ndarray, sb: np.ndarray) -> np.ndarray:
    from scipy.stats import spearmanr

    out = []
    n = sa.shape[0]
    for i in range(n):
        mask = np.arange(n) != i
        out.append(spearmanr(sa[i, mask], sb[i, mask]).correlation)
    return np.array(out)


def procrustes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    u, _, vt = np.linalg.svd(x.T @ y)
    return u @ vt


def ridge(x: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    d = x.shape[1]
    return np.linalg.solve(x.T @ x + lam * np.eye(d), x.T @ y)


def l2rows(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def bridge_cv(x: np.ndarray, y: np.ndarray, fit, folds: int = 5, seed: int = 0) -> dict[str, float]:
    """Fit x→y on 4/5 of tracks, evaluate held-out queries against the full y gallery."""
    n = x.shape[0]
    idx = np.arange(n)
    np.random.default_rng(seed).shuffle(idx)
    sim_y = y @ y.T
    nn_y = topk(sim_y, K)
    r1 = r10 = 0
    same, ovl = [], []
    for f in range(folds):
        test = idx[f::folds]
        train = np.setdiff1d(idx, test)
        w = fit(x[train], y[train])
        q = l2rows(x[test] @ w)
        s = q @ y.T  # held-out translated queries against every true y
        same.extend(s[np.arange(len(test)), test])
        s_ex = s.copy()
        s_ex[np.arange(len(test)), test] = -np.inf  # neighbours other than itself
        nn_q = np.argsort(-s_ex, axis=1)[:, :K]
        ovl.extend(overlap(nn_q, nn_y[test]))
        rank = (s > s[np.arange(len(test)), test][:, None]).sum(axis=1)
        r1 += int((rank == 0).sum())
        r10 += int((rank < K).sum())
    same = np.array(same)
    return {
        "recall@1": r1 / n, "recall@10": r10 / n,
        "same-track cos median": float(np.median(same)), "same-track cos min": float(same.min()),
        "top-10 overlap mean": float(np.mean(ovl)),
    }


def summary() -> None:
    names, mats = load()
    n = len(names)
    keys = sorted(mats)
    print(f"\n{n} tracks, {len(keys)} pipelines: {', '.join(keys)}")
    sims = {p: mats[p] @ mats[p].T for p in keys}
    nns = {p: topk(sims[p], K) for p in keys}
    rng = np.random.default_rng(0)
    other = rng.permutation(n)
    other = np.where(other == np.arange(n), (other + 1) % n, other)

    # 1 + 2 + 3: pairwise
    print("\n== pairwise, same track across spaces (cosine) | neighbour agreement | mixed pool ==")
    hdr = f"{'pair':<28}{'same med':>9}{'same min':>9}{'other med':>10} | {'top10 ovl':>9}{'rho med':>8} | {'other-space in top10':>21}{'own rank med':>13}"
    print(hdr)
    rows = []
    for i, p in enumerate(keys):
        for q in keys[i + 1:]:
            cross = mats[p] @ mats[q].T
            same = np.diag(cross)
            oth = cross[np.arange(n), other]
            ovl = overlap(nns[p], nns[q]).mean()
            rho = np.median(spearman_rows(sims[p], sims[q]))
            # mixed pool: gallery = p ∪ q, query each p vector (excluding itself)
            gal = np.vstack([mats[p], mats[q]])
            s = mats[p] @ gal.T
            s[np.arange(n), np.arange(n)] = -np.inf
            top = np.argsort(-s, axis=1)[:, :K]
            frac_other = float((top >= n).mean())
            own_rank = np.median((s > s[np.arange(n), n + np.arange(n)][:, None]).sum(axis=1))
            print(f"{p[:11] + ' ~ ' + q[:13]:<28}{np.median(same):>9.3f}{same.min():>9.3f}{np.median(oth):>10.3f} | "
                  f"{ovl:>9.2f}{rho:>8.2f} | {frac_other:>21.3f}{own_rank:>13.0f}")
            rows.append((p, q, same, ovl, rho))
    print("  same med/min: cosine between a track's vector in space p and its own vector in q;")
    print("  other med: the same across spaces to a random different track — the floor.")
    print("  top10 ovl: fraction of a track's 10 nearest in p that are also its 10 nearest in q.")
    print("  other-space in top10: with p and q in one index, fraction of a p-query's top 10 that are q-vectors.")
    print(f"  own rank med: where the query's own q-vector sits in that mixed index (0 = first, of {2 * n - 1}).")

    # within-space reference: how tight is 'the same neighbourhood' inside one space?
    print("\n== within-space reference: top-10 overlap between the two windowings of one checkpoint ==")
    for a, b in [("A ref/whole", "D ref/frag3"), ("C music/whole", "B music/frag3")]:
        if a in mats and b in mats:
            print(f"  {a} vs {b}: same-track cos median {np.median(np.diag(mats[a] @ mats[b].T)):.3f}, "
                  f"top-10 overlap {overlap(nns[a], nns[b]).mean():.2f}")

    # 4: bridges
    print(f"\n== linear bridge, 5-fold CV over {n} tracks, held-out queries against the full gallery ==")
    pairs = [("A ref/whole", "B music/frag3"), ("B music/frag3", "A ref/whole"),
             ("A ref/whole", "C music/whole"), ("C music/whole", "A ref/whole")]
    fits = {"procrustes": procrustes}
    for lam in (0.01, 0.03, 0.1, 1.0):
        fits[f"ridge λ={lam}"] = (lambda l: lambda x, y: ridge(x, y, l))(lam)
    print(f"{'from → to':<34}{'fit':<14}{'R@1':>6}{'R@10':>6}{'same med':>10}{'same min':>10}{'top10 ovl':>11}")
    for src, dst in pairs:
        if src not in mats or dst not in mats:
            continue
        for fname, fit in fits.items():
            r = bridge_cv(mats[src], mats[dst], fit)
            print(f"{src[:11] + ' → ' + dst[:13]:<34}{fname:<14}{r['recall@1']:>6.2f}{r['recall@10']:>6.2f}"
                  f"{r['same-track cos median']:>10.3f}{r['same-track cos min']:>10.3f}{r['top-10 overlap mean']:>11.2f}")
    print("  R@k: held-out track's translated vector retrieves its own vector in the target space within k, of", n)
    print("  top10 ovl: the translated query's 10 nearest in the target space vs the true vector's 10 nearest.")

    # per-track CSV
    csv = Path.cwd() / "crosspipe.results.csv"
    with csv.open("w") as fh:
        fh.write("track," + ",".join(f"cos_{p[0]}{q[0]},ovl_{p[0]}{q[0]}" for p, q, *_ in rows) + "\n")
        per = [(np.diag(mats[p] @ mats[q].T), overlap(nns[p], nns[q])) for p, q, *_ in rows]
        for i, name in enumerate(names):
            fh.write(json.dumps(name) + "," + ",".join(f"{c[i]:.4f},{o[i]:.2f}" for c, o in per) + "\n")
    print(f"\nper-track results: {csv}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", help="text file, one library-relative audio path per line")
    ap.add_argument("--root", help="directory the paths are relative to")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="print each track's timing")
    a = ap.parse_args()
    if a.summary:
        summary()
    elif a.list and a.root:
        run(a)
    else:
        ap.error("--list and --root, or --summary")
        sys.exit(2)
