"""What the Picard plugin decides, with Picard kept out of it.

Everything here takes plain values — a fingerprint, a recording id, a path — and
a `Corpus`, and returns plain values. The Picard-facing module wraps this in
actions, threads and metadata; this module is what the tests exercise, and it is
the part that has to agree with the beets plugin, because the contract is one
contract however many tools follow it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .clapback_client import Corpus, CorpusError, hash_fingerprint


class Embedder(Protocol):
    """The slice of `clapback_embed` this plugin uses, when it is installed."""

    PIPELINE_VERSION: str

    def embed_file(self, path: str) -> Any: ...


def find_embedder() -> Embedder | None:
    """`clapback_embed` if it is importable, else None — lookup-only mode.

    Picard runs on desktops where the 614 MB of ONNX encoders is a real ask, and
    the bundled application cannot `pip install` anything. `ADR-0011` point 5 says
    the plugin works without them and says so; this is the switch it says it on.
    """
    try:
        import clapback_embed  # type: ignore[import-not-found]
    except ImportError:
        return None
    return clapback_embed  # type: ignore[return-value]


@dataclass(frozen=True)
class Outcome:
    """One file's result, in words a person reads in the metadata panel."""

    #: found · contributed · named · absent · lookup-only · unfingerprinted · error
    status: str
    detail: str = ""
    fingerprint_hash: str | None = None
    #: The recording id the commons now has from this install, if any.
    named: str | None = None
    #: The AcoustID track id it now has from this install, if any (`ADR-0019` point 6).
    named_acoustid: str | None = None

    @property
    def line(self) -> str:
        return f"{self.status} — {self.detail}" if self.detail else self.status


#: `process(prefetched=...)`'s "nothing was prefetched; look it up yourself".
UNSET = object()


def best_key(*, fingerprint: str | None, recording_mbid: str | None, acoustid_track_id: str | None):
    """The one key a file is asked for by in a batch lookup: its recording id if
    it has one, else its AcoustID track id, else its fingerprint hash
    (`ADR-0019` point 3 — the ids are the same on every fingerprinting path;
    the hash may not be). `None` for a file with no fingerprint."""
    mbid = (recording_mbid or "").strip().lower() or None
    acoustid = (acoustid_track_id or "").strip().lower() or None
    if mbid:
        return mbid
    if acoustid:
        return ("acoustid", acoustid)
    return hash_fingerprint(fingerprint) if fingerprint else None


def process(
    corpus: Corpus,
    *,
    fingerprint: str | None,
    path: str,
    recording_mbid: str | None,
    contribute: bool,
    client_id: Callable[[], str],
    embedder: Embedder | None,
    already_named: str | None = None,
    acoustid_track_id: str | None = None,
    already_named_acoustid: str | None = None,
    prefetched: Any = UNSET,
) -> Outcome:
    """Look up; name if we can; embed and contribute only if asked and able.

    The order is the contract's: **look up before anything else**, because a
    repeat submission is recorded as agreement and one install agreeing with
    itself would corrupt the measurement the commons exists to make. A claim is
    the one write that is safe to repeat — the endpoint is idempotent and touches
    no count — so a track the corpus already holds still gets named.

    The lookup is by recording id first, then AcoustID track id, then the hash
    (`ADR-0019` point 3): the ids are the same on every fingerprinting path and
    the hash may not be, so a recording the commons holds under another install's
    key is still found. `prefetched` is the batch's answer for this file's
    `best_key` (`ADR-0015`), when the caller asked for the whole set at once; a
    miss on an id still falls through to the hash.

    `client_id` is a callable so that an id is minted on the first contribution
    and never before (`ADR-0004` point 2): a user who only ever looks up is never
    assigned one.
    """
    if not fingerprint:
        return Outcome("unfingerprinted", "scan the file first (Picard computes the fingerprint)")
    key = hash_fingerprint(fingerprint)
    mbid = (recording_mbid or "").strip().lower() or None
    acoustid = (acoustid_track_id or "").strip().lower() or None

    pipeline = embedder.PIPELINE_VERSION if embedder else None
    try:
        row = None if prefetched is UNSET else prefetched
        if prefetched is UNSET:
            if mbid:
                row = corpus.lookup(recording_mbid=mbid, pipeline_version=pipeline)
            if row is None and acoustid:
                row = corpus.lookup(acoustid_track_id=acoustid, pipeline_version=pipeline)
        if row is None and (mbid or acoustid or prefetched is UNSET):
            row = corpus.lookup(key, pipeline)
    except CorpusError as exc:
        return Outcome("error", f"corpus unreachable: {exc}", key)

    if row is not None:
        # The row may live under another path's key; the claim goes on it.
        claim_mbid = mbid if contribute and mbid and already_named != mbid else None
        claim_acoustid = acoustid if contribute and acoustid and already_named_acoustid != acoustid else None
        if claim_mbid or claim_acoustid:
            try:
                corpus.claim(
                    fingerprint_hash=row.get("fingerprint_hash", key),
                    client_id=client_id(),
                    recording_mbid=claim_mbid,
                    acoustid_track_id=claim_acoustid,
                )
            except CorpusError as exc:
                return Outcome("found", f"held by the commons; not named ({exc})", key)
        n = row.get("contributor_count", 1)
        detail = f"held by the commons, {n} contribution{'s' if n != 1 else ''}"
        if claim_mbid or claim_acoustid:
            detail += "; named by you"
        return Outcome("found", detail, key, claim_mbid, claim_acoustid)

    if not contribute:
        return Outcome("absent", "not in the commons; contribution is off", key)
    if embedder is None:
        return Outcome(
            "lookup-only",
            "not in the commons, and clapback-embed is not installed here so nothing was computed",
            key,
        )
    try:
        vector = [float(x) for x in embedder.embed_file(path)]
    except Exception as exc:  # noqa: BLE001 - one bad file must not end a batch
        return Outcome("error", f"could not embed: {exc}", key)
    try:
        corpus.contribute(
            fingerprint_hash=key,
            embedding=vector,
            pipeline_version=embedder.PIPELINE_VERSION,
            client_id=client_id(),
            recording_mbid=mbid,
            acoustid_track_id=acoustid,
        )
    except CorpusError as exc:
        return Outcome("error", f"computed, not contributed: {exc}", key)
    ids = " with its " + " and ".join(
        n for n, v in (("recording id", mbid), ("AcoustID id", acoustid)) if v
    ) if (mbid or acoustid) else ""
    return Outcome("contributed", "sent to the commons" + ids, key, mbid, acoustid)


@dataclass(frozen=True)
class Neighbour:
    similarity: float
    fingerprint_hash: str
    recording_mbid: str | None
    recording_claims: int
    is_query: bool

    @property
    def url(self) -> str | None:
        return f"https://musicbrainz.org/recording/{self.recording_mbid}" if self.recording_mbid else None


def neighbours(
    corpus: Corpus,
    *,
    fingerprint: str | None,
    path: str,
    embedder: Embedder | None,
    limit: int = 10,
) -> list[Neighbour]:
    """What sounds like this, across every library the commons holds.

    The query vector is the corpus's own when it holds the recording — under
    whatever pipeline it holds it, which is then the filter, so the neighbours
    are comparable with it. Only when the corpus does not hold it and an embedder
    is installed is anything computed. Raises `CorpusError` with a sentence a
    person can act on otherwise.
    """
    if not fingerprint:
        raise CorpusError("scan the file first — Picard computes the fingerprint")
    key = hash_fingerprint(fingerprint)
    row = corpus.lookup(key, embedder.PIPELINE_VERSION if embedder else None)
    if row is not None:
        vector, pipeline = row["embedding"], row.get("pipeline_version")
    elif embedder is not None:
        vector = [float(x) for x in embedder.embed_file(path)]
        pipeline = embedder.PIPELINE_VERSION
    else:
        raise CorpusError(
            "the commons does not hold this recording yet, and clapback-embed is not "
            "installed here, so there is no vector to ask with"
        )
    out = []
    for n in corpus.similar(vector, limit=limit + 1, pipeline_version=pipeline):
        h = n.get("fingerprint_hash", "")
        out.append(
            Neighbour(
                similarity=float(n.get("similarity", 0.0)),
                fingerprint_hash=h,
                recording_mbid=n.get("recording_mbid"),
                recording_claims=int(n.get("recording_claims") or 0),
                is_query=(h == key),
            )
        )
    return out[:limit]


def neighbour_html(n: Neighbour) -> str:
    """One line of the results dialog. Honest about what a hash is."""
    score = f"{n.similarity:.4f}"
    if n.is_query:
        return f"{score}&nbsp; this file"
    if n.url:
        claims = f" ({n.recording_claims} claim{'s' if n.recording_claims != 1 else ''})"
        return f'{score}&nbsp; <a href="{n.url}">{n.recording_mbid}</a>{claims}'
    return f"{score}&nbsp; <code>{n.fingerprint_hash[:16]}…</code> (not yet named by anyone)"
