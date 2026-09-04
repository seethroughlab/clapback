#!/usr/bin/env python3
"""Project the corpus to two dimensions, so it can be looked at.

**This runs offline and its dependencies never reach the server.** `ADR-0001`
point 3: "the server keeps its own dependencies; adding audio libraries to the
repository must not add them to the deployed image." UMAP pulls in numba and
llvmlite; on a 2 GB instance sized for an HNSW index (`ADR-0003` point 3) that is
not a trade worth making for a picture that changes when the corpus does, which
is rarely.

So the output is a committed artifact with a date on it, regenerated
deliberately — closer to a release than to a cache.

    uv run --with umap-learn --with numpy --with psycopg2-binary \\
        python scripts/build_map.py --dsn postgresql://... --out app/static/map.json

What it emits and what it leaves out:

**Coordinates only, no hashes.** 21,802 full hashes would be 1.4 MB of payload to
label points that `ADR-0002` point 4 says nobody can resolve anyway — a caller
who does not already hold the audio cannot turn a fingerprint hash into a
recording. The map shows the *shape* of the corpus, which is the part that is
legible without them.

**Quantised to a 0–1000 grid.** Screen pixels are integers and the projection has
no meaning below that resolution; float coordinates would be five times the bytes
for precision nobody can see.

**One pipeline at a time.** Vectors from two pipelines are not comparable
(`ADR-0006`), so projecting them together would produce structure that reflects
which pipeline ran rather than what anything sounds like.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

try:
    import numpy as np
    import psycopg2
    import umap
except ImportError as exc:  # pragma: no cover - offline tool
    sys.exit(f"{exc}. This is an offline tool — see the module docstring for how to run it.")


def fetch(dsn: str, analysis_version: int) -> np.ndarray:
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT embedding::text FROM embeddings WHERE analysis_version = %s",
            (analysis_version,),
        )
        rows = cur.fetchall()
    if not rows:
        sys.exit(f"no embeddings at analysis_version {analysis_version}")
    return np.vstack([np.fromstring(r[0].strip("[]"), sep=",", dtype=np.float32) for r in rows])


def project(X: np.ndarray, seed: int) -> np.ndarray:
    # PCA first: UMAP on 512 raw dimensions spends most of its time on distances
    # that 50 components already capture (~93% of the variance, measured), and
    # the projection is indistinguishable.
    from sklearn.decomposition import PCA

    X50 = PCA(n_components=min(50, X.shape[1]), random_state=seed).fit_transform(X)
    # Cosine, because these are unit vectors in an angular space — the same
    # metric the corpus is indexed and compared under.
    reducer = umap.UMAP(
        n_components=2, n_neighbors=25, min_dist=0.12, metric="cosine", random_state=seed
    )
    return reducer.fit_transform(X50)


def quantise(Y: np.ndarray, grid: int = 1000) -> list[int]:
    lo, hi = Y.min(axis=0), Y.max(axis=0)
    span = np.where(hi - lo == 0, 1, hi - lo)
    scaled = ((Y - lo) / span * grid).round().astype(int)
    return [int(v) for v in scaled.flatten()]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dsn", required=True, help="postgresql://… connection string")
    p.add_argument("--analysis-version", type=int, default=7,
                   help="project one pipeline only — mixing them is meaningless")
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=0, help="UMAP is stochastic; pin it so the map is reproducible")
    args = p.parse_args()

    X = fetch(args.dsn, args.analysis_version)
    print(f"projecting {X.shape[0]:,} vectors from analysis_version {args.analysis_version}")
    Y = project(X, args.seed)
    payload = {
        "generated": datetime.now(UTC).date().isoformat(),
        "analysis_version": args.analysis_version,
        "count": int(X.shape[0]),
        "grid": 1000,
        "xy": quantise(Y),
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    print(f"wrote {args.out} — {X.shape[0]:,} points")


if __name__ == "__main__":
    main()
