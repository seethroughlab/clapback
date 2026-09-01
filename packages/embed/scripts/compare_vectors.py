#!/usr/bin/env python3
"""Compare two `emit_vectors.py` outputs — the cross-machine conformance check.

    python scripts/compare_vectors.py a.json b.json

**This is the measurement the commons rests on.** Consensus can only distinguish
"two contributors disagree about the audio" from "two contributors ran different
code" if honest, correct computation agrees far more tightly than anything else
in the system. That is a claim about hardware and library versions, and it has to
be measured rather than assumed.

Result, 2026-09-01, Apple M4 Max (arm64/NEON, macOS, py3.12, numpy 2.5.2,
librosa 1.0.0) against Intel i7-6700K (x86_64/AVX2, Linux, py3.11, numpy 2.4.6,
librosa 0.11.0), same artifact bytes and same audio bytes:

    worst cosine        0.999999999934   (6.6e-11 from unity)
    worst element delta 1.5e-06
    bit-identical       0 of 5

Two things follow. Vectors are **not** bit-identical across architectures, so no
check may require that. And the agreement is roughly a thousand times tighter
than the 6.0e-08 that `float4` storage costs — so **the noise floor of the corpus
is set by how vectors are stored, not by whose CPU computed them.**
"""

from __future__ import annotations

import json
import sys

import numpy as np

#: What `pgvector`'s float4 column costs a byte-identical vector on round-trip.
STORAGE_FLOOR = 6.0e-8


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    def _load(path: str) -> dict:
        with open(path) as fh:
            return json.load(fh)

    a, b = _load(argv[1]), _load(argv[2])

    for tag, r in ((argv[1], a), (argv[2], b)):
        v = r["versions"]
        print(f"  {r['arch']:8s} {r['platform'][:44]}")
        print(f"           py{v['python']}  ort{v['onnxruntime']}  "
              f"numpy{v['numpy']}  librosa{v['librosa']}")

    if a["pipeline"] != b["pipeline"]:
        print(f"\nPIPELINE MISMATCH — these vectors are not comparable:\n"
              f"  {a['pipeline']}\n  {b['pipeline']}", file=sys.stderr)
        return 1
    print(f"\n  pipeline: {a['pipeline']}")

    shared = sorted(set(a["tracks"]) & set(b["tracks"]))
    if not shared:
        print("\nno tracks in common", file=sys.stderr)
        return 1

    print(f"\n{'track':34s} {'audio':>9s} {'cosine':>16s} {'max |delta|':>12s}")
    worst_cos, worst_abs, mismatched = 1.0, 0.0, 0
    for name in shared:
        ta, tb = a["tracks"][name], b["tracks"][name]
        # Different input bytes are not a disagreement; they are a broken test.
        same_audio = ta["sha256"] == tb["sha256"]
        mismatched += not same_audio
        va, vb = np.array(ta["vector"]), np.array(tb["vector"])
        cos = float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb)))
        delta = float(np.abs(va - vb).max())
        worst_cos, worst_abs = min(worst_cos, cos), max(worst_abs, delta)
        print(f"  {name[:32]:34s} {'ok' if same_audio else 'DIFFERS':>9s} "
              f"{cos:16.12f} {delta:12.3e}")

    print(f"\n  worst cosine        {worst_cos:.12f}  ({1 - worst_cos:.3e} from unity)")
    print(f"  worst element delta {worst_abs:.3e}")
    print(f"  float4 storage floor {STORAGE_FLOOR:.1e} — computation is "
          f"{(1 - worst_cos) / STORAGE_FLOOR:.4f}x that")

    if mismatched:
        print(f"\n{mismatched} track(s) had different audio — fix the inputs before "
              "reading anything into the numbers above", file=sys.stderr)
        return 1
    if 1 - worst_cos > STORAGE_FLOOR:
        print("\nComputation now costs more than storage does. That inverts the "
              "assumption the threshold was set from — re-examine it.", file=sys.stderr)
        return 1
    print("\n  OK: honest computation agrees far tighter than storage precision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
