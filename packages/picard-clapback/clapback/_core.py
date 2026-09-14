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

    @property
    def line(self) -> str:
        return f"{self.status} — {self.detail}" if self.detail else self.status


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
) -> Outcome:
    """Look up; name if we can; embed and contribute only if asked and able.

    The order is the contract's: **look up before anything else**, because a
    repeat submission is recorded as agreement and one install agreeing with
    itself would corrupt the measurement the commons exists to make. A claim is
    the one write that is safe to repeat — the endpoint is idempotent and touches
    no count — so a track the corpus already holds still gets named.

    `client_id` is a callable so that an id is minted on the first contribution
    and never before (`ADR-0004` point 2): a user who only ever looks up is never
    assigned one.
    """
    if not fingerprint:
        return Outcome("unfingerprinted", "scan the file first (Picard computes the fingerprint)")
    key = hash_fingerprint(fingerprint)
    mbid = (recording_mbid or "").strip().lower() or None

    pipeline = embedder.PIPELINE_VERSION if embedder else None
    try:
        row = corpus.lookup(key, pipeline)
    except CorpusError as exc:
        return Outcome("error", f"corpus unreachable: {exc}", key)

    if row is not None:
        named = None
        if contribute and mbid and already_named != mbid:
            try:
                corpus.claim(fingerprint_hash=key, recording_mbid=mbid, client_id=client_id())
            except CorpusError as exc:
                return Outcome("found", f"held by the commons; not named ({exc})", key)
            named = mbid
        n = row.get("contributor_count", 1)
        detail = f"held by the commons, {n} contribution{'s' if n != 1 else ''}"
        if named:
            detail += "; named by you"
        return Outcome("found", detail, key, named)

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
        )
    except CorpusError as exc:
        return Outcome("error", f"computed, not contributed: {exc}", key)
    return Outcome("contributed", "sent to the commons" + (" with its recording id" if mbid else ""), key, mbid)


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
