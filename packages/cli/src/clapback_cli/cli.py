"""`clapback` — search your own music by description, and find duplicates.

`ADR-0009` point 8 of `ADR-0001` is the brief: **the tool must be worth running
with the corpus empty.** So everything here works offline, against your own
files, with the commons unreachable. Contributing is something it can also do,
and — `ADR-0022` point 7 — so is asking the commons what sounds like a track,
including recordings you do not own.

    clapback index ~/Music
    clapback search "dreamy ambient with piano"
    clapback duplicates
    clapback similar "Gantz Graf"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clapback_client import DEFAULT_BASE_URL as DEFAULT_CORPUS_URL

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


#: A neighbour this close to the seed *is* the seed: the same vector, held by the
#: corpus under a key this store never learned (contributed from another install,
#: or before this store cached its hash). Two rips of one recording measure at
#: most 0.9995, so nothing that is merely a duplicate is hidden by this.
SAME_VECTOR = 0.99999


def _find_seed(store: Store, track: str) -> int:
    """The store row for a path — exact first, then a unique substring of one."""
    key = str(Path(track).expanduser().resolve())
    for i, e in enumerate(store.entries):
        if e.path == key:
            return i
    matches = [i for i, e in enumerate(store.entries) if track.lower() in e.path.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"not indexed: {track}\nTry: clapback index <directory>")
    sys.exit(
        f"{len(matches)} indexed tracks match {track!r}; be more specific:\n"
        + "\n".join(f"  {store.entries[i].path}" for i in matches[:10])
    )


def cmd_similar(args: argparse.Namespace) -> int:
    """`ADR-0022` point 7 — what sounds like this, across every library the commons holds.

    The reason to reach the commons that is not altruism. The seed's vector is
    already in the store, so this needs neither the embedder nor a fingerprint; it
    needs the network, and says so plainly when it cannot get there. A
    neighbour is shown as your own file when its hash is one this store has
    cached — which means one it has contributed — as a MusicBrainz recording
    you can open when someone has named it, and as the bare hash it still is
    otherwise. It says *sounds like*, never *will like*, and where to hear a
    recording you do not hold is not this tool's business (`ADR-0009` point 9).
    """
    from clapback_client import Corpus, CorpusError

    store = Store(args.home).load()
    if not len(store.vectors):
        sys.exit("Nothing indexed yet. Try: clapback index ~/Music")
    seed = _find_seed(store, args.track)
    vector = [float(x) for x in store.vectors[seed]]

    corpus = Corpus(args.url)
    try:
        # One more than asked, because the seed's own row comes back first
        # whenever this library has contributed it. Only vectors comparable
        # with this store's are asked for (`ADR-0006`).
        neighbours = corpus.similar(
            vector, limit=args.limit + 1, pipeline_version=store.pipeline_version
        )
    except CorpusError as exc:
        sys.exit(f"corpus unreachable: {exc}")

    seed_hash = store.entries[seed].fingerprint_hash
    mine = {e.fingerprint_hash: e.path for e in store.entries if e.fingerprint_hash}
    print(f"sounds like: {store.entries[seed].path}")
    shown = owned = named = unnamed = 0
    for n in neighbours:
        if n["fingerprint_hash"] == seed_hash or n["similarity"] >= SAME_VECTOR:
            continue
        if n["fingerprint_hash"] in mine:
            what = f"{mine[n['fingerprint_hash']]}  (in your library)"
            owned += 1
        elif n.get("recording_mbid"):
            claims = n.get("recording_claims", 0)
            what = (
                f"https://musicbrainz.org/recording/{n['recording_mbid']}"
                f"  ({claims} claim{'s' if claims != 1 else ''})"
            )
            named += 1
        else:
            what = f"{n['fingerprint_hash'][:16]}…  (not yet named by anyone)"
            unnamed += 1
        print(f"{n['similarity']:.4f}  {what}")
        shown += 1
        if shown >= args.limit:
            break

    if not shown:
        print("nothing comparable in the corpus yet — yours would be the first")
        return 0
    # The number `ADR-0022` turns on: how much of what came back is past the
    # edge of this library. A library that has never contributed cannot be
    # told apart from one that owns none of these, and the count says so.
    print(
        f"\n{shown} shown · in your library {owned} · not in your library {named + unnamed}"
        f" ({unnamed} not yet named)"
        + ("" if mine else " · this store has contributed nothing, so nothing can be recognised as yours")
    )
    return 0


#: How long to wait between writes. The server rate-limits contributions and the
#: client backs off on 429; pacing just means it rarely has to.
CONTRIBUTE_PACE_SECONDS = 0.15  # between batches of a hundred, since `ADR-0016`


def cmd_contribute(args: argparse.Namespace) -> int:
    """`ADR-0009` point 6 — the byproduct, and the only reason the corpus grows.

    Everything this tool does locally works with the commons unreachable and this
    command never run. That is `ADR-0001` point 8 and it is the whole argument:
    a donation client with no local value has no first contributor.
    """
    import time

    # `ADR-0011` point 2: the contract lives in `clapback-client` and this tool
    # imports it rather than carrying a copy, so the reference client and the
    # published contract are the same code and cannot drift.
    from clapback_client import (
        Corpus,
        CorpusError,
        FingerprintUnavailable,
        fingerprint_file,
        hash_fingerprint,
    )

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

    entries = store.entries[: args.limit] if args.limit else store.entries
    print(f"{len(entries):,} indexed track(s)")
    print(f"corpus:   {corpus.base_url}")
    print(f"pipeline: {pipeline_version}")
    print("licence:  CC0 1.0 — everything sent is public domain and may be republished")

    if args.dry_run:
        need = sum(1 for e in entries if not e.fingerprint_hash)
        print("\ndry run — nothing will be sent.")
        print(f"{need:,} would need fingerprinting first.")
        return 0

    client_id = store.ensure_client_id()
    print(f"client:   {client_id}\n")

    sent = present = refused = unfingerprintable = missing = 0
    try:
        # 1. Every key first — fingerprints cost a subprocess each and are
        #    cached on the entry, so an interrupted run keeps them.
        #
        # `entries` is a prefix of `store.entries`, so the loop index addresses
        # the matching row of `store.vectors` directly. Looking the entry up by
        # value instead would be quadratic, and would pick the wrong vector for
        # two entries that happen to compare equal.
        keyed: list[int] = []
        for idx, entry in enumerate(entries):
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
                if (idx + 1) % 25 == 0:
                    store.save()
            keyed.append(idx)
        store.save()

        # 2. Look the whole set up, a hundred keys a request (`ADR-0015`), and
        #    offer only what the corpus lacks: a repeat POST is recorded as
        #    agreement, and a library re-sent would be one install agreeing
        #    with itself — the measurement `ADR-0008` rests on.
        try:
            held = {
                key
                for key, row in corpus.lookup_many(
                    (entries[i].fingerprint_hash for i in keyed), pipeline_version, vectors=False
                )
                if row is not None
            }
        except CorpusError as exc:
            sys.exit(f"\nstopped before sending anything: {exc}")
        to_send = [i for i in keyed if entries[i].fingerprint_hash not in held]
        present = len(keyed) - len(to_send)

        # 3. Contribute by the hundred (`ADR-0016`): every guarantee per row, the
        #    result per row, a refusal one row's result rather than the run's.
        def rows():
            for i in to_send:
                yield {
                    "fingerprint_hash": entries[i].fingerprint_hash,
                    "embedding": [float(x) for x in store.vectors[i]],
                    "pipeline_version": pipeline_version,
                    "client_id": client_id,
                }

        try:
            for n, result in enumerate(corpus.contribute_many(rows()), start=1):
                if result.get("status") in ("created", "confirmed"):
                    sent += 1
                else:
                    refused += 1
                    print(f"  refused {result.get('fingerprint_hash', '')[:12]}: {result.get('detail')}")
                if n % 100 == 0:
                    print(f"  {n:,}/{len(to_send):,} · contributed {sent:,} · already there {present:,}")
                    time.sleep(CONTRIBUTE_PACE_SECONDS)
        except CorpusError as exc:
            store.save()
            sys.exit(f"\nstopped after {sent:,} contributed: {exc}")
    finally:
        store.save()

    print(
        f"\ncontributed {sent:,} · already in corpus {present:,} · refused {refused:,} · "
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

    si = sub.add_parser(
        "similar", help="what sounds like a track, across every library the commons holds"
    )
    si.add_argument("track", help="an indexed file, or a unique part of its path")
    si.add_argument("--limit", type=int, default=10)
    si.add_argument("--url", default=DEFAULT_CORPUS_URL, help="corpus base URL")
    si.set_defaults(func=cmd_similar)

    co = sub.add_parser("contribute", help="send your embeddings to the commons (opt-in)")
    co.add_argument("--url", default=DEFAULT_CORPUS_URL, help="corpus base URL")
    co.add_argument("--limit", type=int, default=0, help="stop after this many tracks")
    co.add_argument("--dry-run", action="store_true", help="say what would be sent, send nothing")
    co.set_defaults(func=cmd_contribute)

    args = p.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
