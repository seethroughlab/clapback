#!/usr/bin/env python3
"""Project the corpus to two dimensions, so it can be looked at — and, since
`ADR-0012`, clicked.

**This runs offline and its dependencies never reach the server.** `ADR-0001`
point 3: "the server keeps its own dependencies; adding audio libraries to the
repository must not add them to the deployed image." UMAP pulls in numba and
llvmlite; on a 2 GB instance sized for an HNSW index (`ADR-0003` point 3) that is
not a trade worth making for a picture that changes when the corpus does, which
is rarely.

So the output is a committed artifact with a date on it, regenerated
deliberately — closer to a release than to a cache.

**It reads an export, not the database.** `ADR-0005` point 12 gives the corpus no
reachable database port — the API is the only way in — so there is no DSN to hand
this, and UMAP cannot run on the instance because that is the entire reason this
is offline. The workflow is two steps and both are honest about where they run:

    # on the instance, where the database is — one pipeline, by its identity
    docker compose -f docker-compose.aws.yml exec -T postgres psql -U cache -c "
      COPY (
        SELECT e.fingerprint_hash,
               EXISTS (SELECT 1 FROM recording_claims c
                        WHERE c.fingerprint_hash = e.fingerprint_hash) AS named,
               e.embedding::text
        FROM embeddings e
        WHERE e.pipeline_version = 'laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32'
      ) TO STDOUT WITH (FORMAT csv)" | gzip -9 > corpus.csv.gz

    # anywhere with umap-learn
    uv run --with umap-learn --with scikit-learn --with numpy \
        python scripts/build_map.py --from corpus.csv.gz \
        --pipeline-version 'laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32' \
        --out app/static/map.json

What it emits, and why it is two files:

**`map.json` — coordinates and a `named` flag, no hashes.** The landing page
draws this in its hero and pays for nothing it does not draw: ~85 KB gzipped.
`named` is one bit per point, so the explorer can draw a clickable recording
brighter than one that is still a hash.

**`map-index.json` — the first twelve hex characters of each point's hash, in the
same order.** Loaded only by the explorer. Until 2026-09-14 this file had no
reason to exist: `ADR-0002` point 4 said nobody could resolve a fingerprint hash,
so labelling points was 1.4 MB of payload for nothing. `ADR-0012` and Familiar's
`ADR-0115` made 89.6% of rows resolve to a MusicBrainz recording, and a point
became something a person can click. Twelve characters is 48 bits — enough to
be unique across tens of thousands of rows and a fifth the size of the full hash;
`/browse/hash/{prefix}` turns one back into a row.

**Quantised to a 0–1000 grid.** Screen pixels are integers and the projection has
no meaning below that resolution; float coordinates would be five times the bytes
for precision nobody can see.

**One pipeline at a time.** Vectors from two pipelines are not comparable
(`ADR-0006`), so projecting them together would produce structure that reflects
which pipeline ran rather than what anything sounds like. The filter is the
pipeline identity itself; `analysis_version` was a client's own counter and the
plug-ins all send 1.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from datetime import UTC, datetime

try:
    import numpy as np
    import umap
except ImportError as exc:  # pragma: no cover - offline tool
    sys.exit(f"{exc}. This is an offline tool — see the module docstring for how to run it.")


def fetch(path: str) -> tuple[list[str], list[bool], np.ndarray]:
    """Read `(hash, named, vector)` rows from a `COPY ... TO STDOUT WITH (FORMAT csv)`
    export. Also accepts the pre-2026-09-15 one-column shape, in which case the
    hashes are empty and no index is written.

    `csv.field_size_limit` is raised because a 512-float literal is around 6 KB
    and the default is 128 KB — fine today, and a silent failure the first time a
    wider vector appears.
    """
    csv.field_size_limit(10**7)
    opener = gzip.open if path.endswith(".gz") else open
    hashes: list[str] = []
    named: list[bool] = []
    rows = []
    with opener(path, "rt") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            vec = row[-1]
            if not vec.startswith("["):
                continue
            if len(row) >= 3:
                hashes.append(row[0])
                named.append(row[1].strip().lower() in ("t", "true", "1"))
            rows.append(np.fromstring(vec.strip("[]"), sep=",", dtype=np.float32))
    if not rows:
        sys.exit(f"no vectors found in {path}")
    return hashes, named, np.vstack(rows)


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
    p.add_argument("--from", dest="source", required=True,
                   help="a CSV (or .csv.gz) export of (fingerprint_hash, named, embedding) rows")
    p.add_argument("--pipeline-version", required=True,
                   help="recorded in the output; the export is what actually selects a pipeline")
    p.add_argument("--out", required=True, help="map.json; map-index.json is written beside it")
    p.add_argument("--seed", type=int, default=0, help="UMAP is stochastic; pin it so the map is reproducible")
    args = p.parse_args()

    hashes, named, X = fetch(args.source)
    print(f"projecting {X.shape[0]:,} vectors from {args.pipeline_version}")
    Y = project(X, args.seed)
    payload = {
        "generated": datetime.now(UTC).date().isoformat(),
        "pipeline_version": args.pipeline_version,
        "count": int(X.shape[0]),
        "grid": 1000,
        "xy": quantise(Y),
    }
    if named:
        payload["named"] = [1 if n else 0 for n in named]
    with open(args.out, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    print(f"wrote {args.out} — {X.shape[0]:,} points, {sum(named):,} named")

    if hashes:
        index_path = args.out.replace("map.json", "map-index.json")
        with open(index_path, "w") as fh:
            json.dump([h[:12] for h in hashes], fh, separators=(",", ":"))
        print(f"wrote {index_path} — {len(hashes):,} prefixes")
    else:
        print("no hashes in the export; the explorer will show the picture but nothing is clickable")


if __name__ == "__main__":
    main()
