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

from .corpus import DEFAULT_BASE_URL as DEFAULT_CORPUS_URL
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


#: This tool's own counter, and nothing more. `ADR-0006` points 2 and 3 took
#: `analysis_version` out of the key and left it as a recorded column, so it is no
#: longer a claim about whether two vectors are comparable — `pipeline_version` is.
#: A new client therefore starts at 1 rather than pretending to share Familiar's
#: history, which is what the number used to imply.
ANALYSIS_VERSION = 1

#: How long to wait between writes. The server rate-limits contributions and the
#: client backs off on 429; pacing just means it rarely has to.
CONTRIBUTE_PACE_SECONDS = 0.15


def cmd_contribute(args: argparse.Namespace) -> int:
    """`ADR-0009` point 6 — the byproduct, and the only reason the corpus grows.

    Everything this tool does locally works with the commons unreachable and this
    command never run. That is `ADR-0001` point 8 and it is the whole argument:
    a donation client with no local value has no first contributor.
    """
    import time

    from .corpus import Corpus, CorpusError
    from .fingerprint import FingerprintUnavailable, fingerprint_file, hash_fingerprint

    embed = _embedder()
    store = Store(args.home).load()
    if not len(store.vectors):
        sys.exit("Nothing indexed yet. Try: clapback index ~/Music")

    # **A store indexed by a different pipeline cannot be contributed.** Since
    # `ADR-0006` phase 4 the pipeline identity is half the corpus key, so sending
    # these vectors under the installed embedder's identity would assert that a
    # pipeline produced vectors it did not. Re-indexing is the honest fix and it
    # is the one the record chose (`ADR-0006` point 5: recomputed, not relabelled).
    if store.pipeline_version and store.pipeline_version != embed.PIPELINE_VERSION:
        sys.exit(
            f"This store was indexed by {store.pipeline_version}\n"
            f"and the installed embedder is  {embed.PIPELINE_VERSION}.\n"
            "Contributing would key these vectors to a pipeline that did not produce "
            "them. Re-index first: clapback index <directory>"
        )

    corpus = Corpus(args.url)
    pipeline_version = embed.PIPELINE_VERSION
    # The checkpoint is already the first component of the pipeline identity, so
    # taking it from there keeps the two from ever disagreeing about one fact.
    clap_model_version = pipeline_version.split("+")[0]

    entries = store.entries[: args.limit] if args.limit else store.entries
    print(f"{len(entries):,} indexed track(s)")
    print(f"corpus:   {corpus.base_url}")
    print(f"pipeline: {pipeline_version}")

    if args.dry_run:
        need = sum(1 for e in entries if not e.fingerprint_hash)
        print("\ndry run — nothing will be sent.")
        print(f"{need:,} would need fingerprinting first.")
        return 0

    client_id = store.ensure_client_id()
    print(f"client:   {client_id}\n")

    sent = present = unfingerprintable = missing = 0
    try:
        # `entries` is a prefix of `store.entries`, so the loop index addresses
        # the matching row of `store.vectors` directly. Looking the entry up by
        # value instead would be quadratic, and would pick the wrong vector for
        # two entries that happen to compare equal.
        for idx, entry in enumerate(entries):
            n = idx + 1
            if not entry.fingerprint_hash:
                if not Path(entry.path).exists():
                    missing += 1
                    continue
                try:
                    entry.fingerprint_hash = hash_fingerprint(fingerprint_file(entry.path))
                except FingerprintUnavailable as exc:
                    # Point 5: a missing chromaprint is a plain statement, not a
                    # traceback, and it is fatal only because nothing downstream
                    # can proceed without it.
                    if "not installed" in str(exc):
                        store.save()
                        sys.exit(f"\n{exc}")
                    unfingerprintable += 1
                    continue

            try:
                if corpus.has(entry.fingerprint_hash, pipeline_version):
                    present += 1
                    continue
                corpus.contribute(
                    fingerprint_hash=entry.fingerprint_hash,
                    embedding=[float(x) for x in store.vectors[idx]],
                    pipeline_version=pipeline_version,
                    clap_model_version=clap_model_version,
                    analysis_version=ANALYSIS_VERSION,
                    client_id=client_id,
                )
                sent += 1
                time.sleep(CONTRIBUTE_PACE_SECONDS)
            except CorpusError as exc:
                store.save()
                sys.exit(f"\nstopped at {n:,}: {exc}")

            if n % 25 == 0:
                # Save as we go: fingerprints cost a subprocess each, and an
                # interrupted run must not throw that away.
                store.save()
                print(f"  {n:,}/{len(entries):,} · contributed {sent:,} · already there {present:,}")
    finally:
        store.save()

    print(
        f"\ncontributed {sent:,} · already in corpus {present:,} · "
        f"no fingerprint {unfingerprintable:,} · file gone {missing:,}"
    )
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

    co = sub.add_parser("contribute", help="send your embeddings to the commons (opt-in)")
    co.add_argument("--url", default=DEFAULT_CORPUS_URL, help="corpus base URL")
    co.add_argument("--limit", type=int, default=0, help="stop after this many tracks")
    co.add_argument("--dry-run", action="store_true", help="say what would be sent, send nothing")
    co.set_defaults(func=cmd_contribute)

    args = p.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
