"""`clapback` — search your own music by description, and find duplicates.

`ADR-0009` point 8 of `ADR-0001` is the brief: **the tool must be worth running
with the corpus empty.** So everything here works offline, against your own
files, with the commons unreachable. Contributing is something it can also do.

    clapback index ~/Music
    clapback search "dreamy ambient with piano"
    clapback duplicates
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .store import Store

#: What we will try to embed. `clapback-embed` decodes through soundfile and
#: librosa; anything they refuse is skipped with a line rather than a traceback,
#: because one unreadable file in a library of 20,000 must not end the run.
AUDIO_SUFFIXES = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wma"}

#: `ADR-0009` point 8. Two rips of one recording measure 0.9972–0.9995 under this
#: pipeline, and genuinely different music sits far below; 0.995 is inside that
#: band and adjustable, because "duplicate" is partly a judgement — a remaster is
#: a different master and sometimes a different recording.
DEFAULT_DUPLICATE_THRESHOLD = 0.995


def _embedder():
    """Import lazily, so `--help` and a missing model do not look like the same failure."""
    try:
        import clapback_embed
    except ImportError:
        sys.exit("clapback-embed is not installed. pip install clapback")
    return clapback_embed


def cmd_index(args: argparse.Namespace) -> int:
    embed = _embedder()
    store = Store(args.home).load()
    known = store.known()

    files = [
        p for p in sorted(Path(args.directory).rglob("*"))
        if p.suffix.lower() in AUDIO_SUFFIXES and p.is_file()
    ]
    print(f"{len(files):,} audio files under {args.directory}")

    added = skipped = failed = 0
    for path in files:
        key = str(path.resolve())
        stat = path.stat()
        prior = known.get(key)
        # Re-embedding a file that has not changed costs seconds of CPU for an
        # identical vector. mtime and size together are enough: a file edited in
        # place without changing either is not a case worth slowing every run for.
        if prior and prior.mtime == stat.st_mtime and prior.size == stat.st_size:
            skipped += 1
            continue
        try:
            vector = embed.embed_file(str(path))
        except embed.ArtifactsMissing:
            sys.exit(
                "The ONNX encoders are missing. They are 614 MB and not bundled — "
                "export them once with clapback-embed's scripts/export_models.py, "
                "or set CLAPBACK_MODEL_DIR to where they already are."
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not end the run
            print(f"  skipped {path.name}: {exc}")
            failed += 1
            continue
        store.add(key, stat.st_mtime, stat.st_size, vector)
        added += 1
        if added % 50 == 0:
            print(f"  {added:,} embedded")

    store.pipeline_version = embed.PIPELINE_VERSION
    store.save()
    print(f"indexed {added:,} · unchanged {skipped:,} · unreadable {failed:,}")
    print(f"store: {store.home}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    embed = _embedder()
    store = Store(args.home).load()
    if not len(store.vectors):
        sys.exit("Nothing indexed yet. Try: clapback index ~/Music")

    query = embed.embed_text(args.description)
    for i, score in store.nearest(query, args.limit):
        print(f"{score:.4f}  {store.entries[i].path}")
    return 0


def cmd_duplicates(args: argparse.Namespace) -> int:
    import numpy as np

    store = Store(args.home).load()
    n = len(store.vectors)
    if n < 2:
        sys.exit("Need at least two indexed tracks.")

    # The full n×n similarity matrix, which at a personal library's scale is
    # cheaper than being clever: 20,000 tracks is a 1.6 GB float32 matrix, so it
    # goes in blocks rather than all at once.
    seen: set[tuple[int, int]] = set()
    block = 2000
    for start in range(0, n, block):
        sims = store.vectors[start : start + block] @ store.vectors.T
        for local, row in enumerate(sims):
            i = start + local
            for j in np.nonzero(row >= args.threshold)[0]:
                j = int(j)
                if i < j:
                    seen.add((i, j))

    if not seen:
        print(f"No pairs at or above {args.threshold}.")
        return 0
    print(f"{len(seen):,} pair(s) at or above {args.threshold}:\n")
    for i, j in sorted(seen, key=lambda p: -float(store.vectors[p[0]] @ store.vectors[p[1]])):
        score = float(store.vectors[i] @ store.vectors[j])
        print(f"{score:.4f}")
        print(f"  {store.entries[i].path}")
        print(f"  {store.entries[j].path}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="clapback", description=__doc__.split("\n")[0])
    p.add_argument("--home", type=Path, default=None, help="store directory (default ~/.clapback)")
    sub = p.add_subparsers(dest="command", required=True)

    ix = sub.add_parser("index", help="embed a directory of audio into the local store")
    ix.add_argument("directory")
    ix.set_defaults(func=cmd_index)

    se = sub.add_parser("search", help="find tracks matching a description")
    se.add_argument("description")
    se.add_argument("--limit", type=int, default=10)
    se.set_defaults(func=cmd_search)

    du = sub.add_parser("duplicates", help="find near-duplicates across formats and masters")
    du.add_argument("--threshold", type=float, default=DEFAULT_DUPLICATE_THRESHOLD)
    du.set_defaults(func=cmd_duplicates)

    args = p.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
