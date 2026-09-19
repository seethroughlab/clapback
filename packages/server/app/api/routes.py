"""API routes for the cache server."""

import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import DbSession
from app.cache import stats_cache
from app.config import settings
from app.db.models import AnalysisDetail, Embedding, Features, RecordingClaim, SubmissionAgreement
from app.limiter import charge, limiter

router = APIRouter(prefix="/v1")


def _decode_pipeline(value: str) -> str:
    """Undo the one encoding mistake every caller of this endpoint will make.

    A pipeline identity is `+`-joined — `laion/clap-htsat-unfused+frontend1+
    artifact1+pool1+fp32` — and in a query string `+` is the legacy encoding of a
    space. So a caller who interpolates the identity into a URL without escaping it
    sends five spaces, matches nothing, and gets a 404 that reads as *the corpus
    does not have this recording* rather than *you encoded it wrong*. Silent, and
    indistinguishable from an empty corpus, which is the failure mode `ADR-0006`
    exists to remove rather than relocate.

    A space cannot occur in a pipeline identity: it is a checkpoint name joined to
    component tags, and nothing in `clapback-embed` can put one there. So mapping it
    back is unambiguous rather than a guess, and a correctly escaped `%2B` still
    arrives as `+` and is untouched.

    This is a decoding tolerance on one query parameter and not a general rule —
    `/v1/similar` takes the same field in a JSON body, where the problem does not
    arise and no leniency is applied.
    """
    return value.replace(" ", "+")


def _cosine_similarity(a: list[float], b: list[float]) -> float | None:
    """Cosine similarity, or `None` when the comparison is meaningless.

    Written out rather than pulled from numpy because this runs inline on a request
    path over 512 floats, and the server has no numpy dependency today — adding one
    for six lines of arithmetic would be the wrong trade.

    Returns `None` for mismatched lengths or a zero-magnitude vector. Those are not
    disagreements, they are broken input, and recording them as similarity 0.0 would
    poison the very distribution this exists to measure.

    **A byte-identical resubmission does not score 1.0, and that is not a bug.**
    `pgvector`'s `Vector` column stores float4, so the vector read back has been
    truncated to single precision while the submitted one is float64. Measured: a
    resubmission of the exact same list scores **0.99999994**. The floor on
    measurable agreement is therefore set by the storage, not by the contributors —
    worth knowing before anyone reads 0.9999999 as evidence of a discrepancy.
    """
    if len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    value = dot / (norm_a * norm_b)
    # Float error can push an identical pair a hair past 1.0, which would look like
    # an impossible similarity in the distribution.
    return max(-1.0, min(1.0, value))


#: A MusicBrainz identifier in canonical form: lowercase hex, hyphenated 8-4-4-4-12.
#: `ADR-0012` point 2 — the server validates the shape and never the referent.
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _canonical_mbid(value: str) -> str:
    """Lowercased, or a `ValueError` naming what was wrong.

    Case-folded rather than rejected because MusicBrainz itself is case-insensitive
    about these and tags in the wild carry both; a claim that differs only by case
    is the same claim and must key the same row.
    """
    v = value.strip().lower()
    if not _MBID.match(v):
        raise ValueError("recording_mbid must be a MusicBrainz recording MBID (a UUID)")
    return v


#: `ADR-0019` point 6: the two kinds of id a claim can carry. Both are UUIDs;
#: they are not the same namespace and are never compared with each other.
CLAIM_MUSICBRAINZ = "musicbrainz_recording"
CLAIM_ACOUSTID = "acoustid_track"

#: `ADR-0020` point 2: what kind of key a row's `fingerprint_hash` is.
KEY_FINGERPRINT = "fingerprint"
KEY_RECORDING = CLAIM_MUSICBRAINZ


def recording_key(mbid: str) -> str:
    """The key of a row contributed with a recording id and no fingerprint —
    `ADR-0020` point 1: `SHA256("musicbrainz_recording:" + mbid)`, hex.

    A 64-hex digest like every fingerprint hash, cryptographically disjoint
    from all of them, so the primary key and every index are unchanged. The
    server derives it — a client never sends it — which makes it the first key
    the server can verify: `ADR-0010` point 7 says a fingerprint hash is
    believed; this one is computed."""
    import hashlib

    return hashlib.sha256(f"{CLAIM_MUSICBRAINZ}:{_canonical_mbid(mbid)}".encode()).hexdigest()


def _canonical_acoustid(value: str) -> str:
    v = value.strip().lower()
    if not _MBID.match(v):
        raise ValueError("acoustid_track_id must be an AcoustID track id (a UUID)")
    return v


def _identity_of(req) -> list[tuple[str, str]]:
    """The ids a request names, as `(claim_type, id)` pairs — either, both, or none."""
    out = []
    if getattr(req, "recording_mbid", None):
        out.append((CLAIM_MUSICBRAINZ, req.recording_mbid))
    if getattr(req, "acoustid_track_id", None):
        out.append((CLAIM_ACOUSTID, req.acoustid_track_id))
    return out


#: `ADR-0008` point 3: the `identical` band. A submission whose cosine against a
#: stored vector is inside it confirms that vector; outside, it contradicts it.
#: Measured, not chosen — the table in that record is its justification.
AGREEMENT_BAND = 0.999999


async def _recording_agreements_for(
    db, keys: list[tuple[str, str, str]]
) -> dict[tuple[str, str, str], tuple[int, int]]:
    """Per `(claim_type, id, pipeline_version)`: how many independent installs
    confirmed the recording's vector, and how many contradicted it.

    `ADR-0019` point 2, computed at read time as `ADR-0008` point 6 requires.
    Counted across every row claimed under the recording, so two fingerprinting
    paths that keyed one file twice are one population. A confirmation is a
    distinct `client_id` with an agreement inside `AGREEMENT_BAND` against any
    of those rows under this pipeline; a contradiction is one outside it —
    served, not hidden (`ADR-0008` point 4). A client is never counted for
    agreeing with a row it contributed, and a submission without a `client_id`
    is evidence but not independence (`ADR-0004` point 3).
    """
    if not keys:
        return {}
    ids = sorted({(t, i) for t, i, _ in keys})
    pipelines = sorted({p for _, _, p in keys})
    claimed = (
        select(
            RecordingClaim.claim_type, RecordingClaim.recording_id, RecordingClaim.fingerprint_hash
        )
        .where(tuple_(RecordingClaim.claim_type, RecordingClaim.recording_id).in_(ids))
        .distinct()
        .subquery()
    )
    a = SubmissionAgreement
    independent = a.client_id.is_not(None) & (
        Embedding.client_id.is_(None) | (Embedding.client_id != a.client_id)
    )
    stmt = (
        select(
            claimed.c.claim_type,
            claimed.c.recording_id,
            a.pipeline_version,
            func.count(func.distinct(a.client_id))
            .filter(a.similarity >= AGREEMENT_BAND)
            .label("confirmed"),
            func.count(func.distinct(a.client_id))
            .filter(a.similarity < AGREEMENT_BAND)
            .label("contradicted"),
        )
        .select_from(claimed)
        .join(a, a.other_hash == claimed.c.fingerprint_hash)
        .join(
            Embedding,
            (Embedding.fingerprint_hash == a.other_hash)
            & (Embedding.pipeline_version == a.pipeline_version),
        )
        .where(a.pipeline_version.in_(pipelines), independent)
        .group_by(claimed.c.claim_type, claimed.c.recording_id, a.pipeline_version)
    )
    return {(t, i, pv): (int(c), int(d)) for t, i, pv, c, d in (await db.execute(stmt)).all()}


async def _record_cross_key_agreements(db, req: "EmbeddingRequest") -> int:
    """Compare a submission that names its recording with every row under the
    same pipeline that any client has claimed under that recording — `ADR-0019`
    point 2 — and record each comparison as a `SubmissionAgreement` naming the
    row it was measured against. The row under the submission's own key is
    excluded: the same-key path already compared it. Returns how many rows were
    compared. Does not commit."""
    # Point 6: the join runs on either id the submission carries. A row claimed
    # under the same MBID *or* the same AcoustID track id is the same recording.
    claimed = (
        select(RecordingClaim.fingerprint_hash)
        .where(
            tuple_(RecordingClaim.claim_type, RecordingClaim.recording_id).in_(_identity_of(req))
        )
        .distinct()
        .scalar_subquery()
    )
    rows = (
        (
            await db.execute(
                select(Embedding).where(
                    Embedding.fingerprint_hash.in_(claimed),
                    Embedding.pipeline_version == req.pipeline_version,
                    Embedding.fingerprint_hash != req.key,
                )
            )
        )
        .scalars()
        .all()
    )
    compared = 0
    for row in rows:
        similarity = _cosine_similarity(req.embedding, list(row.embedding))
        if similarity is None:
            continue
        db.add(
            SubmissionAgreement(
                fingerprint_hash=req.key,
                other_hash=row.fingerprint_hash,
                analysis_version=req.analysis_version,
                clap_model_version=req.clap_model_version,
                pipeline_version=req.pipeline_version,
                similarity=similarity,
                client_id=req.client_id,
            )
        )
        compared += 1
    return compared


async def _identities_for(db, hashes: list[str]) -> dict[str, dict[str, tuple[str, int]]]:
    """Per hash, per claim type: the id with the most distinct clients, and how many.

    `ADR-0012` point 1: derived from the claims rather than read from the row. The
    winner is the id with the most distinct clients; ties break on the id text
    so the answer is a **total** ordering — `ADR-0006` learned what a partial one
    costs, being a corpus that answers the same question differently between calls.
    Types are never mixed: an MBID and an AcoustID id are different namespaces
    (`ADR-0019` point 6). Hashes with no claims are absent from the result.
    """
    if not hashes:
        return {}
    stmt = (
        select(
            RecordingClaim.fingerprint_hash,
            RecordingClaim.claim_type,
            RecordingClaim.recording_id,
            func.count(func.distinct(RecordingClaim.client_id)).label("n"),
        )
        .where(RecordingClaim.fingerprint_hash.in_(hashes))
        .group_by(
            RecordingClaim.fingerprint_hash, RecordingClaim.claim_type, RecordingClaim.recording_id
        )
        .order_by(
            RecordingClaim.fingerprint_hash,
            RecordingClaim.claim_type,
            func.count(func.distinct(RecordingClaim.client_id)).desc(),
            RecordingClaim.recording_id,
        )
    )
    out: dict[str, dict[str, tuple[str, int]]] = {}
    for h, t, i, n in (await db.execute(stmt)).all():
        out.setdefault(h, {}).setdefault(t, (i, int(n)))
    return out


async def _recordings_for(db, hashes: list[str]) -> dict[str, tuple[str, int]]:
    """The MusicBrainz recording each hash resolves to, and how many say so —
    the projection of `_identities_for` every read has carried since `ADR-0012`."""
    ids = await _identities_for(db, hashes)
    return {h: v[CLAIM_MUSICBRAINZ] for h, v in ids.items() if CLAIM_MUSICBRAINZ in v}


def _named(ids: dict[str, tuple[str, int]] | None) -> dict:
    """The four name fields every read carries, from a hash's identities."""
    ids = ids or {}
    mb = ids.get(CLAIM_MUSICBRAINZ)
    ac = ids.get(CLAIM_ACOUSTID)
    return {
        "recording_mbid": mb[0] if mb else None,
        "recording_claims": mb[1] if mb else 0,
        "acoustid_track_id": ac[0] if ac else None,
        "acoustid_claims": ac[1] if ac else 0,
    }


def _best_identity(ids: dict[str, tuple[str, int]] | None) -> tuple[str, str] | None:
    """The identity a row is grouped under for agreement and collapse: its MBID
    when it has one, else its AcoustID track id, else nothing."""
    if not ids:
        return None
    for t in (CLAIM_MUSICBRAINZ, CLAIM_ACOUSTID):
        if t in ids:
            return (t, ids[t][0])
    return None


async def _record_claims(db, fingerprint_hash: str, req, client_id: str) -> None:
    """One claim per (hash, type, id, client) for each id the request names.
    Saying it twice is saying it once."""
    for claim_type, recording_id in _identity_of(req):
        await db.execute(
            pg_insert(RecordingClaim)
            .values(
                fingerprint_hash=fingerprint_hash,
                claim_type=claim_type,
                recording_id=recording_id,
                client_id=client_id,
            )
            .on_conflict_do_nothing()
        )


class SimilarRequest(BaseModel):
    """Ask the corpus what a vector is near."""

    embedding: list[float] = Field(..., min_length=512, max_length=512)
    limit: int = Field(default=20, ge=1, le=100)
    #: Only compare against vectors from this pipeline. Defaults to none, meaning
    #: every pipeline in the corpus — which is almost never what a caller wants
    #: and is the default only because filtering to a version they guess wrong
    #: returns nothing at all, silently.
    analysis_version: int | None = Field(default=None, ge=1)
    #: Only compare against vectors from this pipeline. This is the filter that
    #: actually means "comparable" (`ADR-0006` point 1); `analysis_version` above
    #: only approximates it. It matches nothing until phase 2 of point 6 lands,
    #: because no stored row declares a pipeline yet — so it stays optional and
    #: defaults to unfiltered rather than becoming a way to silently get nothing.
    pipeline_version: str | None = Field(default=None, min_length=1, max_length=200)


class Neighbour(BaseModel):
    """One result. A hash, not a recording — see `similar`'s docstring."""

    fingerprint_hash: str
    #: `ADR-0020` point 2, as on `EmbeddingResponse`.
    key_type: str = KEY_FINGERPRINT
    similarity: float
    analysis_version: int
    clap_model_version: str
    #: Null until the corpus holds rows contributed with a declared pipeline. A
    #: caller ranking these should know which results are comparable with its own
    #: vector and which are merely nearby in a mixed space.
    pipeline_version: str | None = None
    #: `ADR-0012` point 5: the MusicBrainz recording this hash resolves to, and the
    #: number of distinct clients who say so. Null and 0 when nobody has claimed
    #: one — in which case this neighbour is still a hash, and the caller should
    #: know that rather than be handed a blank.
    recording_mbid: str | None = None
    recording_claims: int = 0
    #: `ADR-0019` point 6: the AcoustID track id the most distinct clients
    #: assert for this row, and how many. A second kind of name, not a second
    #: opinion about the first; null and 0 when nobody has sent one.
    acoustid_track_id: str | None = None
    acoustid_claims: int = 0
    #: `ADR-0019` point 2, served as `ADR-0008` decides: independent installs
    #: whose vector for this recording, under this pipeline, agreed to the
    #: `identical` band — counted across every key the recording is held under —
    #: and how many disagreed. Both 0 when nobody has named the row, or nobody
    #: else has sent a vector for it. Not a verdict; the caller decides.
    recording_confirmations: int = 0
    recording_contradictions: int = 0


class SimilarResponse(BaseModel):
    neighbours: list[Neighbour]
    searched: int
    #: `ADR-0019` point 4: rows folded into a neighbour above because they share
    #: its recording and pipeline — one file keyed twice by two fingerprinting
    #: paths, or two installs' rips of one recording. 0 until a second path
    #: contributes. Rows nobody has named are never folded.
    collapsed: int = 0


# --- Embedding models ---


class EmbeddingRequest(BaseModel):
    """Request to contribute an embedding."""

    #: The key, when the client fingerprinted the audio — `ADR-0010`. Optional
    #: since `ADR-0020` point 7: a request without it must carry a
    #: `recording_mbid`, and is keyed on that (`recording_key`). A request with
    #: both is a fingerprint-keyed contribution with a claim, exactly as before.
    #: **A client that has a fingerprint sends it, always** (point 5); the
    #: server cannot tell, so the client library enforces it by shape.
    fingerprint_hash: str | None = Field(default=None, min_length=64, max_length=64)
    embedding: list[float] = Field(..., min_length=512, max_length=512)
    analysis_version: int = Field(..., ge=1)
    clap_model_version: str = Field(..., min_length=1, max_length=100)
    #: Opaque per-install identifier, if the client has one. Optional, because every
    #: existing client predates it and must keep working unchanged.
    #:
    #: It exists so that "two submissions" can be distinguished from "one client
    #: retrying" — the distinction `contributor_count` fails to make, since it counts
    #: POSTs rather than contributors. Not an identity: a random UUID generated once
    #: per install is exactly enough, and the server never needs to know more.
    client_id: str | None = Field(default=None, max_length=64)
    #: What produced this vector — `ADR-0006` point 1, and **half the corpus key
    #: since phase 4**.
    #:
    #: Required, which point 4 draws as a deliberate contrast with `client_id`
    #: above: an unattributed submission is still evidence, whereas an unidentified
    #: pipeline is a vector that cannot be compared with anything, including itself
    #: later. There is no sensible key for it.
    #:
    #: Asserted, not proven (point 8). A client sends a string and the server
    #: believes it. That catches the forgotten bump and the stale build, which are
    #: the realistic failures; it is not a defence against a contributor who lies,
    #: and nothing here should be described as if it were.
    pipeline_version: str = Field(..., min_length=1, max_length=200)
    #: `ADR-0012` point 4: an optional MusicBrainz recording MBID, recorded as a
    #: claim by this client alongside the contribution. Needs `client_id` — a
    #: claim nobody can be said to have made cannot be revoked or counted.
    recording_mbid: str | None = Field(default=None, max_length=36)
    #: `ADR-0019` point 6: the AcoustID track id, if the tool holds one (Picard
    #: always does; beets' `chroma` stores it as `acoustid_id`). Admitted, not
    #: required; recorded as a second claim beside the MBID, and point 2's join
    #: runs on either. Needs `client_id` for the same reason the MBID does.
    acoustid_track_id: str | None = Field(default=None, max_length=36)

    @field_validator("recording_mbid")
    @classmethod
    def _mbid_shape(cls, v: str | None) -> str | None:
        return None if v is None else _canonical_mbid(v)

    @field_validator("acoustid_track_id")
    @classmethod
    def _acoustid_shape(cls, v: str | None) -> str | None:
        return None if v is None else _canonical_acoustid(v)

    @model_validator(mode="after")
    def _has_a_key(self):
        # `ADR-0020` point 7: neither is a 422 that says so, not a bare schema error.
        if self.fingerprint_hash is None and self.recording_mbid is None:
            raise ValueError(
                "a contribution needs a fingerprint_hash, or a recording_mbid to be keyed on "
                "(ADR-0020)"
            )
        return self

    @property
    def key(self) -> str:
        """The row key this request lands on: its fingerprint hash, or the
        digest of its recording id when it has no fingerprint."""
        return self.fingerprint_hash or recording_key(self.recording_mbid)  # type: ignore[arg-type]

    @property
    def key_type(self) -> str:
        return KEY_FINGERPRINT if self.fingerprint_hash else KEY_RECORDING


class EmbeddingResponse(BaseModel):
    """Response containing an embedding."""

    fingerprint_hash: str
    #: `ADR-0020` point 2: `fingerprint` when the key is the SHA256 of an
    #: AcoustID fingerprint; `musicbrainz_recording` when the row was contributed
    #: with a recording id and no fingerprint and is keyed on the digest of that
    #: id — a claim all the way down, never confirmable by an audio-derived key.
    #: A reader who wants only audio-keyed evidence filters on this.
    key_type: str = KEY_FINGERPRINT
    embedding: list[float]
    analysis_version: int
    clap_model_version: str
    #: What produced this vector. Every stored row has one since phase 4 — the
    #: rows that could not say were removed by migration `011` rather than
    #: relabelled (`ADR-0006` point 5).
    pipeline_version: str
    contributor_count: int
    #: `ADR-0012` point 5, as on `Neighbour`.
    recording_mbid: str | None = None
    recording_claims: int = 0
    #: `ADR-0019` point 6: the AcoustID track id the most distinct clients
    #: assert for this row, and how many. A second kind of name, not a second
    #: opinion about the first; null and 0 when nobody has sent one.
    acoustid_track_id: str | None = None
    acoustid_claims: int = 0
    #: `ADR-0019` point 2, served as `ADR-0008` decides: independent installs
    #: whose vector for this recording, under this pipeline, agreed to the
    #: `identical` band — counted across every key the recording is held under —
    #: and how many disagreed. Both 0 when nobody has named the row, or nobody
    #: else has sent a vector for it. Not a verdict; the caller decides.
    recording_confirmations: int = 0
    recording_contradictions: int = 0


class ContributeResponse(BaseModel):
    """Response after contributing."""

    status: str
    contributor_count: int | None = None
    #: The key the row is under, and what kind — `ADR-0020`. For a fingerprint
    #: contribution, what was sent; for a recording-keyed one, the digest the
    #: server derived, which the client did not have until now.
    fingerprint_hash: str | None = None
    key_type: str | None = None


# --- Features models ---


class FeaturesRequest(BaseModel):
    """Request to contribute features."""

    fingerprint_hash: str = Field(..., min_length=64, max_length=64)
    analysis_version: int = Field(..., ge=1)
    features: dict  # Client sends whatever it has


class FeaturesResponse(BaseModel):
    """Response containing features."""

    fingerprint_hash: str
    analysis_version: int
    features: dict
    contributor_count: int


@router.get("/embeddings/{fingerprint_hash}", response_model=EmbeddingResponse)
@limiter.limit(settings.lookup_rate_limit)
async def lookup_embedding(
    request: Request,
    fingerprint_hash: str,
    db: DbSession,
    analysis_version: int | None = None,
    clap_model_version: str | None = None,
    pipeline_version: str | None = None,
) -> EmbeddingResponse:
    """Look up an embedding by fingerprint hash.

    Returns the embedding if found, 404 otherwise.

    **Send `pipeline_version` if you intend to use the vector.** It is half the key
    since `ADR-0006` phase 4, so it is the only parameter that selects exactly one
    row, and it is the only one that says whether what comes back is comparable
    with vectors you computed yourself. `analysis_version` and `clap_model_version`
    remain accepted, and remain filters on metadata rather than on identity — a
    caller that sends only those can be handed a vector from a pipeline it cannot
    use, which is the mistake this whole record exists to design out.

    All three became optional here at phase 4. They were required, and a client
    predating the key change sends the two that no longer identify anything; making
    them optional keeps that client working while letting a newer one ask the
    question that has an exact answer (`ADR-0005` point 10 — the contract widens,
    it does not move).
    """
    filters = [Embedding.fingerprint_hash == fingerprint_hash]
    if pipeline_version is not None:
        filters.append(Embedding.pipeline_version == _decode_pipeline(pipeline_version))
    if analysis_version is not None:
        filters.append(Embedding.analysis_version == analysis_version)
    if clap_model_version is not None:
        filters.append(Embedding.clap_model_version == clap_model_version)

    # **Ordering, because the filters above no longer guarantee one row.** Under the
    # old key they did. Now a recording can hold a vector per pipeline, and a caller
    # who did not name one gets the most-confirmed, earliest first. It is a
    # defensible choice among rows the caller failed to distinguish, not a claim
    # that this row is right for them — which is why the response says which
    # pipeline it came from.
    #
    # `pipeline_version` last, and it is not decoration: the first two columns tie
    # readily — two pipelines contributed in one batch have the same count and, to
    # the resolution of a `timestamp`, the same `created_at`. A partial order lets
    # Postgres return either row, so the same request answers differently between
    # calls, which is worse than an arbitrary answer because it looks like the
    # corpus changed. The key's second column is unique among these candidates, so
    # appending it makes the order total.
    result = await db.execute(
        select(Embedding)
        .where(*filters)
        .order_by(
            Embedding.contributor_count.desc(),
            Embedding.created_at,
            Embedding.pipeline_version,
        )
        .limit(1)
    )
    emb = result.scalar_one_or_none()

    if not emb:
        raise HTTPException(status_code=404, detail="Embedding not found")

    # Update last accessed time
    emb.last_accessed_at = datetime.utcnow()
    await db.commit()

    ids = (await _identities_for(db, [emb.fingerprint_hash])).get(emb.fingerprint_hash)
    best = _best_identity(ids)
    agreement = (0, 0)
    if best:
        key = (*best, emb.pipeline_version)
        agreement = (await _recording_agreements_for(db, [key])).get(key, (0, 0))
    return EmbeddingResponse(
        fingerprint_hash=emb.fingerprint_hash,
        key_type=emb.key_type,
        embedding=list(emb.embedding),
        analysis_version=emb.analysis_version,
        clap_model_version=emb.clap_model_version,
        pipeline_version=emb.pipeline_version,
        contributor_count=emb.contributor_count,
        **_named(ids),
        recording_confirmations=agreement[0],
        recording_contradictions=agreement[1],
    )


_QUOTA_WINDOW = text("interval '24 hours'")


async def _client_quota_used(db, client_id: str) -> tuple[int, datetime | None]:
    """Rows this identifier wrote in the last 24 hours, and the oldest of them.

    Created rows from `embeddings`, confirmed ones from `submission_agreement`;
    both carry the `client_id` and both are writes. The oldest is what says
    when the window next frees a row, for `Retry-After`.
    """
    since = func.now() - _QUOTA_WINDOW
    created = await db.execute(
        select(func.count(), func.min(Embedding.created_at)).where(
            Embedding.client_id == client_id, Embedding.created_at > since
        )
    )
    confirmed = await db.execute(
        select(func.count(), func.min(SubmissionAgreement.recorded_at)).where(
            SubmissionAgreement.client_id == client_id, SubmissionAgreement.recorded_at > since
        )
    )
    n1, t1 = created.one()
    n2, t2 = confirmed.one()
    oldest = min((t for t in (t1, t2) if t is not None), default=None)
    return int(n1 or 0) + int(n2 or 0), oldest


async def _check_client_quota(db, client_id: str) -> None:
    """Refuse the write with a 429 and `Retry-After` when the identifier has
    reached `client_quota_rows_per_day` in the rolling window. Per row: a batch
    that crosses the line is accepted up to it and refused past it."""
    limit = settings.client_quota_rows_per_day
    if not limit:
        return
    used, oldest = await _client_quota_used(db, client_id)
    if used < limit:
        return
    retry_after = 3600
    if oldest is not None:
        now = (await db.execute(select(func.now()))).scalar_one()
        # Both naive UTC from the database's clock; the difference is real.
        remaining = (oldest + timedelta(hours=24)) - now.replace(tzinfo=None)
        retry_after = max(1, int(remaining.total_seconds()) + 1)
    raise HTTPException(
        status_code=429,
        detail=(
            f"This client_id has written {used} rows in the last 24 hours, its quota of "
            f"{limit} (ADR-0016 point 7; ADR-0004 point 9). Lookups are unaffected."
        ),
        headers={"Retry-After": str(retry_after)},
    )


async def _contribute_one(db, req: EmbeddingRequest) -> ContributeResponse:
    """One contribution, every guarantee — the unit the write path is built from.

    `ADR-0016` point 2: the single endpoint and the batch endpoint both call
    this, once per row, so a row contributed by the hundred gets exactly what a
    row contributed alone gets — the claim check, the agreement record, the
    contributor count, the ceiling, the quota — from the same lines. Raises
    `HTTPException` for a refusal; the caller decides whether that is the
    response or one entry in a list of them. Commits.
    """
    # **The key, since `ADR-0006` phase 4.** A submission confirms an existing row
    # exactly when it is for the same recording from the same pipeline, which is
    # exactly when the two vectors are comparable. Under the old key this test could
    # match a row from a different pipeline, and miss a row from the same one.
    #
    # `pipeline_version` is required on the request, so point 4's rejection of an
    # undeclared contribution is Pydantic's 422 rather than a branch here. That is
    # the right place for it: the field is not optional-and-then-checked, it is
    # part of what a contribution is.
    # `ADR-0012` point 1: a claim is keyed by the client that made it. A recording
    # id with nobody behind it could be neither revoked nor counted, so it is
    # refused up front rather than dropped on the floor.
    if _identity_of(req) and not req.client_id:
        raise HTTPException(
            status_code=422,
            detail="a recording id needs a client_id: a claim must be attributable (ADR-0012)",
        )

    # **The quota is checked before anything is written, for creations and
    # confirmations alike.** `ADR-0004` point 9's third bound, built by
    # `ADR-0016` point 7: a batch endpoint without it is a faster way for one
    # identifier to reach the ceiling alone. A confirmation counts because it is
    # a write — an agreement row and a count — and because the manufactured
    # agreement `ADR-0008` guards against is exactly a client confirming by the
    # ten thousand. Unattributed contributions cannot be counted and are not.
    if req.client_id:
        await _check_client_quota(db, req.client_id)

    # `ADR-0020` point 1: the key is the fingerprint hash the client sent, or —
    # when it sent none — the digest of the recording id it named. Everything
    # below is written against `key`; nothing below cares which it was, except
    # the row's `key_type`, which says so forever.
    key = req.key
    result = await db.execute(
        select(Embedding).where(
            Embedding.fingerprint_hash == key,
            Embedding.pipeline_version == req.pipeline_version,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # **Record how far apart the two vectors are, rather than discarding the
        # submission.** This is the only measurement that can tell us whether a
        # consensus scheme is even viable: if independent machines agree to within
        # a rounding error, agreement is strong evidence of a correct computation;
        # if they routinely diverge, no threshold separates honest data from bad.
        #
        # Recording only. First-write-wins is unchanged, the stored vector is
        # untouched, and no client can observe any difference.
        # **`ADR-0006` point 7: a mismatched submission is never recorded as
        # disagreement.** Since phase 4 the key makes this structurally true —
        # `existing` was selected *by* the submitted pipeline, so the two always
        # match. It is kept as an explicit test rather than deleted because the
        # guarantee now lives in the shape of a query several lines above, and a
        # future change to that query would silently take the guarantee with it.
        # What it costs is one comparison; what it protects is the only measurement
        # the corpus makes.
        comparable = req.pipeline_version == existing.pipeline_version

        similarity = _cosine_similarity(req.embedding, list(existing.embedding))
        if similarity is not None and comparable:
            db.add(
                SubmissionAgreement(
                    fingerprint_hash=key,
                    other_hash=key,
                    analysis_version=req.analysis_version,
                    clap_model_version=req.clap_model_version,
                    pipeline_version=req.pipeline_version,
                    similarity=similarity,
                    client_id=req.client_id,
                )
            )

        # No relabelling to guard against any more: migration `011` removed every
        # row that could not say what produced it, so there is no null left to fill
        # in. `ADR-0006` point 5 is discharged rather than ongoing.
        existing.contributor_count += 1
        if _identity_of(req):
            # `ADR-0019` point 2: the recording joins what the key could not —
            # a row for this recording under another fingerprinting path is
            # compared too, and the agreement is counted per recording.
            await _record_cross_key_agreements(db, req)
            await _record_claims(db, key, req, req.client_id)
        await db.commit()
        return ContributeResponse(
            status="confirmed",
            contributor_count=existing.contributor_count,
            fingerprint_hash=key,
            key_type=existing.key_type,
        )

    # **The ceiling is checked here and not above.** A submission that confirms an
    # existing vector adds no row, so refusing it would reject evidence the corpus
    # wants while doing nothing for the disk. Only a new key grows the corpus.
    #
    # `ADR-0004` point 9: rejected "with a clear error". A contributor who hits
    # this has done nothing wrong and should be told what happened rather than
    # given a bare 507.
    if settings.max_embeddings:
        total = await db.scalar(select(func.count()).select_from(Embedding))
        if total is not None and total >= settings.max_embeddings:
            raise HTTPException(
                status_code=507,
                detail=(
                    f"The corpus has reached its configured ceiling of "
                    f"{settings.max_embeddings} embeddings and is not accepting new "
                    f"recordings. Lookups and confirmations of existing recordings "
                    f"are unaffected. See ADR-0004 point 9."
                ),
            )

    # Create new embedding
    emb = Embedding(
        fingerprint_hash=key,
        # `ADR-0020` point 2: says which kind of key this is, forever.
        key_type=req.key_type,
        embedding=req.embedding,
        analysis_version=req.analysis_version,
        clap_model_version=req.clap_model_version,
        # Who sent it, so `ADR-0004` point 6's revocation has something to
        # cascade over. Absent for a client that sends none, which is every
        # client that predates the field.
        client_id=req.client_id,
        # Half the key since phase 4 of `ADR-0006` point 6, and required on the
        # request, so a row cannot exist without saying what produced it.
        pipeline_version=req.pipeline_version,
    )
    db.add(emb)
    if _identity_of(req):
        # `ADR-0019` point 2, and this is the branch that matters: a second
        # client on another fingerprinting path lands here, under a new key,
        # and without this its vector would agree with nothing.
        # `ADR-0020` point 3: a recording-keyed row claims its own recording
        # here, by the same line — that self-claim is how it joins everything
        # else, and there is no other code for it.
        await _record_cross_key_agreements(db, req)
        await _record_claims(db, key, req, req.client_id)
    await db.commit()

    return ContributeResponse(
        status="created", contributor_count=1, fingerprint_hash=key, key_type=req.key_type
    )


@router.post("/embeddings", status_code=201, response_model=ContributeResponse)
@limiter.limit(settings.contribute_rate_limit)
async def contribute_embedding(
    request: Request,
    req: EmbeddingRequest,
    db: DbSession,
) -> ContributeResponse:
    """Contribute an embedding to the cache.

    If the embedding already exists, increments the contributor count.
    """
    return await _contribute_one(db, req)


# --- Batch contribute: a library is contributed in batches (`ADR-0016`) ---


#: `ADR-0016` point 1's ceiling. Over it is a 422, not a partial answer.
CONTRIBUTE_BATCH_MAX = 100

#: A hundred contributions is ~1 MB of JSON. The body limit admits that and
#: refuses ten times it (`ADR-0016`, Consequences), in `app.middleware`.
CONTRIBUTE_BATCH_MAX_BYTES = 10 * 1024 * 1024


class ContributeBatchRequest(BaseModel):
    #: Each entry is exactly an `EmbeddingRequest`. The batch is a container;
    #: the row is the unit.
    contributions: list[EmbeddingRequest] = Field(
        ..., min_length=1, max_length=CONTRIBUTE_BATCH_MAX
    )

    @field_validator("contributions")
    @classmethod
    def _every_row_is_attributed(cls, rows: list[EmbeddingRequest]) -> list[EmbeddingRequest]:
        # Point 5: `client_id` is required here. The single endpoint accepts a
        # contribution without one because clients predate the field; no
        # client predates this endpoint. A contribution nobody can confirm is
        # admissible one at a time and not by the hundred.
        missing = [i for i, r in enumerate(rows) if not r.client_id]
        if missing:
            raise ValueError(
                f"client_id is required on every batch contribution (ADR-0016 point 5); "
                f"missing at {missing[:5]}{'...' if len(missing) > 5 else ''}"
            )
        return rows


class ContributeResult(BaseModel):
    """One row's outcome, in the order sent: a `ContributeResponse` when the row
    was created or confirmed, or the refusal it would have been alone."""

    fingerprint_hash: str
    #: `ADR-0020`: what kind of key the row went under. For a recording-keyed
    #: row `fingerprint_hash` above is the digest the server derived.
    key_type: str = KEY_FINGERPRINT
    status: str
    contributor_count: int | None = None
    #: The HTTP status the row would have got alone — 201, 429, 507, 422 — so
    #: a client retries per row on the row's result, exactly as if it had sent
    #: it alone.
    code: int
    detail: str | None = None
    retry_after: int | None = None


class ContributeBatchResponse(BaseModel):
    results: list[ContributeResult]
    created: int
    confirmed: int
    refused: int


@router.post("/embeddings/batch", response_model=ContributeBatchResponse)
async def contribute_batch(
    request: Request, req: ContributeBatchRequest, db: DbSession
) -> ContributeBatchResponse:
    """Contribute up to a hundred rows in one request — `ADR-0016`.

    **Every per-row guarantee runs per row, in the same code** (point 2): each
    entry goes through `_contribute_one`, the function the single endpoint is,
    in its own transaction. Agreement is recorded per row with the submitter's
    `client_id`; `contributor_count` moves per row; the ceiling and the
    per-client quota are checked per row, so a batch that crosses either is
    accepted up to the line and refused past it, row by row, with the refusal
    in the result. No new write code touches the tables.

    **Not atomic, and says so** (point 4). A hundred rows that come back 97
    created, 2 confirmed and 1 refused have contributed 99 rows. Rolling back
    correct confirmations because a later row hit a bound would be evidence
    thrown away. Retry per row, on the row's result.

    **The rate limit counts rows** (point 3): a batch of a hundred spends a
    hundred of this route's 600 per minute per address, charged after parsing.
    """
    charge(request, settings.contribute_batch_rate_limit, len(req.contributions), unit="row")
    results: list[ContributeResult] = []
    for row in req.contributions:
        try:
            outcome = await _contribute_one(db, row)
            results.append(
                ContributeResult(
                    fingerprint_hash=row.key,
                    key_type=outcome.key_type or row.key_type,
                    status=outcome.status,
                    contributor_count=outcome.contributor_count,
                    code=201,
                )
            )
        except HTTPException as exc:
            await db.rollback()
            retry = (exc.headers or {}).get("Retry-After")
            results.append(
                ContributeResult(
                    fingerprint_hash=row.key,
                    key_type=row.key_type,
                    status="refused",
                    code=exc.status_code,
                    detail=str(exc.detail),
                    retry_after=int(retry) if retry else None,
                )
            )
        except Exception as exc:  # one row's failure is that row's result, not the batch's
            await db.rollback()
            results.append(
                ContributeResult(
                    fingerprint_hash=row.key,
                    key_type=row.key_type,
                    status="refused",
                    code=500,
                    detail=f"{type(exc).__name__}: {exc}"[:200],
                )
            )
    return ContributeBatchResponse(
        results=results,
        created=sum(r.status == "created" for r in results),
        confirmed=sum(r.status == "confirmed" for r in results),
        refused=sum(r.status == "refused" for r in results),
    )


#: `ADR-0019` point 4's over-fetch ceiling. A window this wide that still cannot
#: fill `limit` distinct recordings is a corpus where one recording holds hundreds
#: of rows, which is not a case worth optimising for before it exists.
_COLLAPSE_MAX_WINDOW = 1000


async def _collapse_by_recording(db, ranked, limit: int):
    """Take the ranked query and return `(rows, recordings, collapsed)`: at most
    `limit` rows with one per (identity, pipeline), their identities, and how
    many rows were folded away. See `similar` for why."""
    window = limit * 2
    while True:
        # **HNSW returns at most `hnsw.ef_search` candidates, whatever the LIMIT.**
        # pgvector's default is 40, and measured on the instance 2026-09-16 a
        # `LIMIT 200` came back with 40 rows — which means `limit` above 40 had
        # been silently truncated since the index was built, and any window
        # here would be. Set it to the window, for this transaction only.
        await db.execute(text(f"SET LOCAL hnsw.ef_search = {min(window, _COLLAPSE_MAX_WINDOW)}"))
        rows = (await db.execute(ranked.limit(window))).all()
        identities = await _identities_for(db, [r.fingerprint_hash for r in rows])
        kept, seen, collapsed = [], set(), 0
        for r in rows:
            best = _best_identity(identities.get(r.fingerprint_hash))
            if best is not None:
                key = (*best, r.pipeline_version)
                if key in seen:
                    collapsed += 1
                    continue
                seen.add(key)
            kept.append(r)
            if len(kept) == limit:
                break
        exhausted = len(rows) < window
        if len(kept) == limit or exhausted or window >= _COLLAPSE_MAX_WINDOW:
            return kept, identities, collapsed
        window = min(window * 2, _COLLAPSE_MAX_WINDOW)


@router.post("/similar", response_model=SimilarResponse)
@limiter.limit(settings.lookup_rate_limit)
async def similar(
    request: Request,
    req: SimilarRequest,
    db: DbSession,
) -> SimilarResponse:
    """Nearest recordings to a vector — `ADR-0002` point 1's whole reason to exist.

    "Given a vector, it returns the nearest recordings in the corpus. This is the
    capability the commons exists to provide; exact-key lookup is a cache, and a
    cache is not worth a public endpoint."

    **Each neighbour carries a recording id when anyone has claimed one, and is a
    bare hash when nobody has.** `ADR-0002` point 4 said not to call this
    capability delivered while results were hashes a caller could not resolve;
    `ADR-0012` is the record that resolves them, by counting per-client claims
    rather than trusting a column. `recording_claims` says how many distinct
    clients stand behind the id, and with one contributor that number is 1 for
    every claimed row — evidence of nothing yet, which the field makes visible
    rather than hides.

    A read, so it is unauthenticated (`ADR-0004` point 8) and carries the lookup
    rate limit rather than the contribution one.
    """
    stmt = select(
        Embedding.fingerprint_hash,
        Embedding.key_type,
        Embedding.analysis_version,
        Embedding.clap_model_version,
        Embedding.pipeline_version,
        # `<=>` is cosine *distance*; similarity is what every other number in
        # this project is quoted as, so it is converted here rather than leaving
        # a caller to notice the sign.
        (1 - Embedding.embedding.cosine_distance(req.embedding)).label("similarity"),
    )
    # Built once and applied to both the ranking query and the `searched` count
    # below. Spelling them out twice is how a count starts quietly disagreeing
    # with its own result set the moment a filter is added.
    filters = []
    if req.analysis_version is not None:
        filters.append(Embedding.analysis_version == req.analysis_version)
    if req.pipeline_version is not None:
        filters.append(Embedding.pipeline_version == req.pipeline_version)
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.order_by(Embedding.embedding.cosine_distance(req.embedding))

    # **`ADR-0019` point 4: one neighbour per claimed recording, the nearest of
    # its rows.** A recording two installs keyed differently is two rows a
    # fraction apart in this space, and without this it would be two adjacent
    # results a caller cannot tell from two recordings. The ranking query has no
    # notion of a recording — the recording is a derived, most-claimed id — so
    # it over-fetches, resolves recordings, and keeps the first row seen per
    # (recording, pipeline), widening the window until `limit` survive or the
    # corpus runs out. Unnamed rows are kept as they are: nothing says two of
    # them are one recording, and folding on a guess is what the corpus refuses.
    rows, identities, collapsed = await _collapse_by_recording(db, stmt, req.limit)
    count_stmt = select(func.count()).select_from(Embedding)
    if filters:
        count_stmt = count_stmt.where(*filters)
    searched = await db.scalar(count_stmt)
    bests = {r.fingerprint_hash: _best_identity(identities.get(r.fingerprint_hash)) for r in rows}
    agreements = await _recording_agreements_for(
        db, [(*b, r.pipeline_version) for r in rows if (b := bests[r.fingerprint_hash])]
    )

    def _agreement(r) -> tuple[int, int]:
        b = bests[r.fingerprint_hash]
        return agreements.get((*b, r.pipeline_version), (0, 0)) if b else (0, 0)

    return SimilarResponse(
        collapsed=collapsed,
        neighbours=[
            Neighbour(
                fingerprint_hash=r.fingerprint_hash,
                key_type=r.key_type,
                similarity=float(r.similarity),
                analysis_version=r.analysis_version,
                clap_model_version=r.clap_model_version,
                pipeline_version=r.pipeline_version,
                **_named(identities.get(r.fingerprint_hash)),
                recording_confirmations=_agreement(r)[0],
                recording_contradictions=_agreement(r)[1],
            )
            for r in rows
        ],
        searched=searched or 0,
    )


# --- Recording claims (`ADR-0012`) ---


class ClaimRequest(BaseModel):
    """Attach a recording id to a row that already exists.

    `ADR-0012` point 4: this exists so nobody re-sends a vector to name it. A
    repeat `POST /v1/embeddings` increments `contributor_count` and writes an
    agreement row — one installation agreeing with itself — and a client
    backfilling ids for a library it already contributed would do that thousands
    of times. This path records the claim and touches nothing else.
    """

    fingerprint_hash: str = Field(..., min_length=64, max_length=64)
    #: Either, or both — `ADR-0019` point 6. At least one.
    recording_mbid: str | None = Field(default=None, max_length=36)
    acoustid_track_id: str | None = Field(default=None, max_length=36)
    client_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("recording_mbid")
    @classmethod
    def _mbid_shape(cls, v: str | None) -> str | None:
        return None if v is None else _canonical_mbid(v)

    @field_validator("acoustid_track_id")
    @classmethod
    def _acoustid_shape(cls, v: str | None) -> str | None:
        return None if v is None else _canonical_acoustid(v)

    @model_validator(mode="after")
    def _at_least_one(self) -> "ClaimRequest":
        if not self.recording_mbid and not self.acoustid_track_id:
            raise ValueError("a claim names a recording_mbid, an acoustid_track_id, or both")
        return self


class ClaimResponse(BaseModel):
    status: str
    #: What the hash resolves to after this claim, and how many clients agree.
    recording_mbid: str | None
    recording_claims: int
    acoustid_track_id: str | None = None
    acoustid_claims: int = 0


class RecordingEmbedding(BaseModel):
    """One row claimed under a recording. Per pipeline, since a recording the
    corpus holds from two pipelines is two rows that are not comparable."""

    fingerprint_hash: str
    #: `ADR-0020` point 2. A `musicbrainz_recording` row here *is* the recording
    #: asked for, keyed on it; a `fingerprint` row was fingerprinted and claimed.
    key_type: str = KEY_FINGERPRINT
    pipeline_version: str
    embedding: list[float]
    contributor_count: int
    #: How many distinct clients claim *this hash* is this recording.
    recording_claims: int
    #: `ADR-0019` point 2: agreement across every key of the recording, under
    #: this row's pipeline — the same figures a lookup serves.
    recording_confirmations: int = 0
    recording_contradictions: int = 0


class RecordingResponse(BaseModel):
    #: The id asked for, and its kind — `musicbrainz_recording` unless the
    #: request said `type=acoustid_track` (`ADR-0019` point 6).
    recording_mbid: str
    type: str = CLAIM_MUSICBRAINZ
    embeddings: list[RecordingEmbedding]


@router.post("/recordings/claims", status_code=201, response_model=ClaimResponse)
@limiter.limit(settings.contribute_rate_limit)
async def claim_recording(
    request: Request,
    req: ClaimRequest,
    db: DbSession,
) -> ClaimResponse:
    """Say which recording a hash the corpus already holds is.

    A write, so it carries the contribution rate limit. The row must exist: a
    claim about a vector the corpus does not hold would be an identity for
    nothing, and `ADR-0012` point 9 removes claims when their row goes for the
    same reason.
    """
    exists = await db.scalar(
        select(func.count())
        .select_from(Embedding)
        .where(Embedding.fingerprint_hash == req.fingerprint_hash)
    )
    if not exists:
        raise HTTPException(
            status_code=404,
            detail="No embedding with that fingerprint_hash; contribute one first",
        )
    await _record_claims(db, req.fingerprint_hash, req, req.client_id)
    await db.commit()
    ids = (await _identities_for(db, [req.fingerprint_hash])).get(req.fingerprint_hash)
    return ClaimResponse(status="claimed", **_named(ids))


@router.get("/recordings/{recording_mbid}", response_model=RecordingResponse)
@limiter.limit(settings.lookup_rate_limit)
async def recording(
    request: Request,
    recording_mbid: str,
    db: DbSession,
    pipeline_version: str | None = None,
    type: str = CLAIM_MUSICBRAINZ,
) -> RecordingResponse:
    """What does recording X sound like — without holding X.

    `ADR-0012` point 5's third read, and Familiar's `ADR-0102`'s whole purpose.
    Returns every row any client has claimed under this id, one per pipeline,
    each with the count of distinct clients behind *that* row's claim. Filter by
    `pipeline_version` to get only vectors comparable with your own.

    A read, unauthenticated, on the lookup rate limit.
    """
    # `type=acoustid_track` asks by the AcoustID track id instead — `ADR-0019`
    # point 6's second way to name a recording, the same across decoders and
    # `fpcalc` versions. The two are never mixed: an id is looked up as the kind
    # the caller said it was.
    if type not in (CLAIM_MUSICBRAINZ, CLAIM_ACOUSTID):
        raise HTTPException(
            status_code=422, detail=f"type must be {CLAIM_MUSICBRAINZ} or {CLAIM_ACOUSTID}"
        )
    try:
        mbid = (
            _canonical_mbid(recording_mbid)
            if type == CLAIM_MUSICBRAINZ
            else _canonical_acoustid(recording_mbid)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    claimed = (
        select(
            RecordingClaim.fingerprint_hash,
            func.count(func.distinct(RecordingClaim.client_id)).label("n"),
        )
        .where(RecordingClaim.claim_type == type, RecordingClaim.recording_id == mbid)
        .group_by(RecordingClaim.fingerprint_hash)
        .subquery()
    )
    stmt = (
        select(Embedding, claimed.c.n)
        .join(claimed, claimed.c.fingerprint_hash == Embedding.fingerprint_hash)
        .order_by(claimed.c.n.desc(), Embedding.contributor_count.desc(), Embedding.created_at)
    )
    if pipeline_version is not None:
        stmt = stmt.where(Embedding.pipeline_version == _decode_pipeline(pipeline_version))
    rows = (await db.execute(stmt)).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No embedding claimed under that recording")
    agreements = await _recording_agreements_for(
        db, [(type, mbid, e.pipeline_version) for e, _ in rows]
    )
    return RecordingResponse(
        recording_mbid=mbid,
        type=type,
        embeddings=[
            RecordingEmbedding(
                fingerprint_hash=e.fingerprint_hash,
                key_type=e.key_type,
                pipeline_version=e.pipeline_version,
                embedding=list(e.embedding),
                contributor_count=e.contributor_count,
                recording_claims=int(n),
                recording_confirmations=agreements.get((type, mbid, e.pipeline_version), (0, 0))[0],
                recording_contradictions=agreements.get((type, mbid, e.pipeline_version), (0, 0))[
                    1
                ],
            )
            for e, n in rows
        ],
    )


# --- Batch lookup: a library is looked up in batches (`ADR-0015`) ---


#: `ADR-0015` point 1's ceiling. Above it is a 422, not a partial answer.
LOOKUP_BATCH_MAX = 100


class LookupKey(BaseModel):
    """One key in a batch: a fingerprint hash, or — `ADR-0019` point 3 — a
    MusicBrainz recording id, which is the same on every fingerprinting path.
    Exactly one of the two."""

    fingerprint_hash: str | None = Field(default=None, min_length=1, max_length=64)
    recording_mbid: str | None = Field(default=None, max_length=36)
    #: `ADR-0019` point 6: the AcoustID track id as a third kind of key.
    acoustid_track_id: str | None = Field(default=None, max_length=36)

    @field_validator("recording_mbid")
    @classmethod
    def _mbid_shape(cls, v: str | None) -> str | None:
        return None if v is None else _canonical_mbid(v)

    @field_validator("acoustid_track_id")
    @classmethod
    def _acoustid_shape(cls, v: str | None) -> str | None:
        return None if v is None else _canonical_acoustid(v)

    @model_validator(mode="after")
    def _exactly_one(self) -> "LookupKey":
        given = [
            k
            for k in (self.fingerprint_hash, self.recording_mbid, self.acoustid_track_id)
            if k is not None
        ]
        if len(given) != 1:
            raise ValueError(
                "a key is exactly one of fingerprint_hash, recording_mbid or acoustid_track_id"
            )
        return self

    @property
    def identity(self) -> tuple[str, str] | None:
        if self.recording_mbid:
            return (CLAIM_MUSICBRAINZ, self.recording_mbid)
        if self.acoustid_track_id:
            return (CLAIM_ACOUSTID, self.acoustid_track_id)
        return None


class LookupBatchRequest(BaseModel):
    keys: list[LookupKey] = Field(..., min_length=1, max_length=LOOKUP_BATCH_MAX)
    #: As on the single lookup: send it if you intend to use the vector.
    pipeline_version: str | None = Field(default=None, min_length=1, max_length=200)
    #: Point 2: `false` returns everything but the 512 floats — "which of these
    #: do you hold, and what are they called?" in under 20 KB per hundred.
    vectors: bool = True


class LookupRow(EmbeddingResponse):
    """`EmbeddingResponse` with the vector optional, for `vectors: false`."""

    embedding: list[float] | None = None  # type: ignore[assignment]


class LookupResult(BaseModel):
    """The key as asked, and the row or null — order preserved so a client can
    zip the answer with its request."""

    key: LookupKey
    row: LookupRow | None


class LookupBatchResponse(BaseModel):
    results: list[LookupResult]


def _row_for(
    emb: Embedding,
    ids: dict[str, tuple[str, int]] | None,
    agreement: tuple[int, int],
    *,
    vectors: bool,
) -> LookupRow:
    fields: dict = {
        "fingerprint_hash": emb.fingerprint_hash,
        "key_type": emb.key_type,
        "analysis_version": emb.analysis_version,
        "clap_model_version": emb.clap_model_version,
        "pipeline_version": emb.pipeline_version,
        "contributor_count": emb.contributor_count,
        **_named(ids),
        "recording_confirmations": agreement[0],
        "recording_contradictions": agreement[1],
    }
    if vectors:
        fields["embedding"] = list(emb.embedding)
    return LookupRow(**fields)


@router.post(
    "/embeddings/lookup",
    response_model=LookupBatchResponse,
    response_model_exclude_unset=True,
)
async def lookup_batch(
    request: Request, req: LookupBatchRequest, db: DbSession
) -> LookupBatchResponse:
    """Look up to a hundred keys at once — `ADR-0015`.

    One entry per key, in the order sent: the row if held, null if not. A held
    entry is exactly what the single `GET` returns for that key, so nothing a
    client learned there changes here; with `vectors: false` it is that minus
    the embedding. A hash answers with its own row (the most-confirmed under the
    pipeline asked for, or under any); a recording id answers with the row most
    clients have claimed under it (`ADR-0019` point 3), which is how a tool
    holding an id never misses a recording the corpus holds because two
    fingerprinting paths keyed one file differently.

    **The rate limit counts keys, not requests** (point 3): a batch of a hundred
    spends a hundred of this route's per-address window. Charged after parsing,
    so a malformed batch costs nothing and a batch over the ceiling is refused
    by validation before it is counted.
    """
    charge(request, settings.lookup_rate_limit, len(req.keys))
    pipeline = _decode_pipeline(req.pipeline_version) if req.pipeline_version is not None else None

    hashes = [k.fingerprint_hash for k in req.keys if k.fingerprint_hash is not None]
    identities = sorted({k.identity for k in req.keys if k.identity is not None})

    # By hash: one row per hash, chosen as the single lookup chooses — most
    # confirmed, earliest, then pipeline, a total order — by taking the first
    # row per hash from the same ordering.
    by_hash: dict[str, Embedding] = {}
    if hashes:
        stmt = select(Embedding).where(Embedding.fingerprint_hash.in_(hashes))
        if pipeline is not None:
            stmt = stmt.where(Embedding.pipeline_version == pipeline)
        stmt = stmt.order_by(
            Embedding.fingerprint_hash,
            Embedding.contributor_count.desc(),
            Embedding.created_at,
            Embedding.pipeline_version,
        )
        for emb in (await db.execute(stmt)).scalars():
            by_hash.setdefault(emb.fingerprint_hash, emb)

    # By recording: the row most distinct clients have claimed under the id,
    # then the most confirmed — the order `GET /v1/recordings/{id}` serves.
    # An id is looked up as the kind the key said it was (`ADR-0019` point 6).
    by_id: dict[tuple[str, str], Embedding] = {}
    if identities:
        claimed = (
            select(
                RecordingClaim.claim_type,
                RecordingClaim.recording_id,
                RecordingClaim.fingerprint_hash,
                func.count(func.distinct(RecordingClaim.client_id)).label("n"),
            )
            .where(tuple_(RecordingClaim.claim_type, RecordingClaim.recording_id).in_(identities))
            .group_by(
                RecordingClaim.claim_type,
                RecordingClaim.recording_id,
                RecordingClaim.fingerprint_hash,
            )
            .subquery()
        )
        stmt = (
            select(claimed.c.claim_type, claimed.c.recording_id, Embedding)
            .join(claimed, claimed.c.fingerprint_hash == Embedding.fingerprint_hash)
            .order_by(
                claimed.c.claim_type,
                claimed.c.recording_id,
                claimed.c.n.desc(),
                Embedding.contributor_count.desc(),
                Embedding.created_at,
                Embedding.pipeline_version,
            )
        )
        if pipeline is not None:
            stmt = stmt.where(Embedding.pipeline_version == pipeline)
        for t, i, emb in (await db.execute(stmt)).all():
            by_id.setdefault((t, i), emb)

    found = list(by_hash.values()) + list(by_id.values())
    ids_for = await _identities_for(db, sorted({e.fingerprint_hash for e in found}))
    bests = {e.fingerprint_hash: _best_identity(ids_for.get(e.fingerprint_hash)) for e in found}
    agreements = await _recording_agreements_for(
        db, sorted({(*b, e.pipeline_version) for e in found if (b := bests[e.fingerprint_hash])})
    )
    if found:
        now = datetime.utcnow()
        for emb in found:
            emb.last_accessed_at = now
        await db.commit()

    results = []
    for key in req.keys:
        emb = by_hash.get(key.fingerprint_hash) if key.fingerprint_hash else by_id.get(key.identity)
        row = None
        if emb:
            b = bests[emb.fingerprint_hash]
            agreement = agreements.get((*b, emb.pipeline_version), (0, 0)) if b else (0, 0)
            row = _row_for(emb, ids_for.get(emb.fingerprint_hash), agreement, vectors=req.vectors)
        results.append(LookupResult(key=key, row=row))
    return LookupBatchResponse(results=results)


# --- The claims on a row, all of them (`ADR-0018` point 2) ---


class ClaimEntry(BaseModel):
    #: `musicbrainz_recording` today; `ADR-0019` point 6 admits `acoustid_track`
    #: later, and the field is here from the first version so that a reader
    #: never has to guess what kind of id it was handed.
    type: str
    id: str
    #: Distinct `client_id`s asserting this id for this row.
    clients: int


class ClaimsResponse(BaseModel):
    fingerprint_hash: str
    #: Most-supported first; ties on the id text, so the order is total.
    claims: list[ClaimEntry]


@router.get("/recordings/by-hash/{fingerprint_hash}", response_model=ClaimsResponse)
@limiter.limit(settings.lookup_rate_limit)
async def claims_for_hash(request: Request, fingerprint_hash: str, db: DbSession) -> ClaimsResponse:
    """Every recording id claimed for a row, with how many clients say so.

    `ADR-0018` point 2 — "which recording is this?", with the dissent. The
    single lookup's `recording_mbid` is the summary (the most-supported id);
    this is the whole list, no vector. It is also the read side of `ADR-0019`'s
    cross-key join: a tool that learns the recording a hash is claimed under can
    find every other key the corpus holds for it through
    `GET /v1/recordings/{mbid}`.

    What this is not: verified, or AcoustID. It works only for a row the corpus
    holds, and a count of one means one install said so. `404` when the row is
    unknown; an empty list when it is held and nobody has named it.
    """
    held = await db.scalar(
        select(func.count())
        .select_from(Embedding)
        .where(Embedding.fingerprint_hash == fingerprint_hash)
    )
    if not held:
        raise HTTPException(status_code=404, detail="Embedding not found")
    stmt = (
        select(
            RecordingClaim.claim_type,
            RecordingClaim.recording_id,
            func.count(func.distinct(RecordingClaim.client_id)).label("n"),
        )
        .where(RecordingClaim.fingerprint_hash == fingerprint_hash)
        .group_by(RecordingClaim.claim_type, RecordingClaim.recording_id)
        .order_by(
            func.count(func.distinct(RecordingClaim.client_id)).desc(),
            RecordingClaim.claim_type,
            RecordingClaim.recording_id,
        )
    )
    return ClaimsResponse(
        fingerprint_hash=fingerprint_hash,
        claims=[
            ClaimEntry(type=t, id=i, clients=int(n)) for t, i, n in (await db.execute(stmt)).all()
        ],
    )


# --- Pipelines: what the corpus holds, by identity (`ADR-0014` point 3) ---


class PipelineEntry(BaseModel):
    """One pipeline identity the corpus holds rows under, with its population."""

    pipeline_version: str
    rows: int
    #: Rows under this identity that any client has claimed a recording for.
    named: int
    #: `ADR-0020` point 2: of `rows`, how many are keyed on a recording id
    #: rather than a fingerprint — claims all the way down, counted in `named`
    #: too, since each claims itself.
    recording_keyed: int = 0
    first_contributed_at: datetime
    last_contributed_at: datetime


class PipelinesResponse(BaseModel):
    pipelines: list[PipelineEntry]


async def _fetch_pipelines(db) -> list[PipelineEntry]:
    # Named per identity: rows whose hash carries any claim. A hash is claimed
    # once however many pipelines hold it, so the join is against distinct
    # claimed hashes rather than claims — two clients naming a row is one name.
    claimed = select(func.distinct(RecordingClaim.fingerprint_hash).label("h")).subquery()
    stmt = (
        select(
            Embedding.pipeline_version,
            func.count().label("rows"),
            func.count(claimed.c.h).label("named"),
            func.count(Embedding.key_type)
            .filter(Embedding.key_type == KEY_RECORDING)
            .label("recording_keyed"),
            func.min(Embedding.created_at).label("first"),
            func.max(Embedding.created_at).label("last"),
        )
        .outerjoin(claimed, claimed.c.h == Embedding.fingerprint_hash)
        .group_by(Embedding.pipeline_version)
        .order_by(func.count().desc(), Embedding.pipeline_version)
    )
    return [
        PipelineEntry(
            pipeline_version=pv,
            rows=int(rows),
            named=int(named),
            recording_keyed=int(rkeyed),
            first_contributed_at=first,
            last_contributed_at=last,
        )
        for pv, rows, named, rkeyed, first, last in (await db.execute(stmt)).all()
    ]


@router.get("/pipelines", response_model=PipelinesResponse)
@limiter.limit(settings.lookup_rate_limit)
async def pipelines(request: Request, db: DbSession) -> PipelinesResponse:
    """Every pipeline identity the corpus holds, with how populated each is.

    `ADR-0014` point 3: the live form of the export manifest's per-identity
    list. A tool deciding whether to contribute under its own identity or
    adopt an existing one asks here which identities exist and how many rows
    each has. The corpus does not interpret the strings — it compares them
    whole (`ADR-0006`) — so this is a list of what has been declared, ordered
    by population, not a registry of what is allowed.

    A read, unauthenticated, on the lookup rate limit, cached like the landing
    page's counts (60 s) because it is a full scan grouped by identity.
    """
    return PipelinesResponse(
        pipelines=await stats_cache.get_or_compute("pipelines", lambda: _fetch_pipelines(db))
    )


# --- Features endpoints ---


@router.get("/features/{fingerprint_hash}", response_model=FeaturesResponse)
@limiter.limit(settings.lookup_rate_limit)
async def lookup_features(
    request: Request,
    fingerprint_hash: str,
    analysis_version: int,
    db: DbSession,
) -> FeaturesResponse:
    """Look up audio features by fingerprint hash.

    Returns the features if found, 404 otherwise.
    """
    result = await db.execute(
        select(Features).where(
            Features.fingerprint_hash == fingerprint_hash,
            Features.analysis_version == analysis_version,
        )
    )
    feat = result.scalar_one_or_none()

    if not feat:
        raise HTTPException(status_code=404, detail="Features not found")

    # Update last accessed time
    feat.last_accessed_at = datetime.utcnow()
    await db.commit()

    return FeaturesResponse(
        fingerprint_hash=feat.fingerprint_hash,
        analysis_version=feat.analysis_version,
        features=feat.features,
        contributor_count=feat.contributor_count,
    )


@router.post("/features", status_code=201, response_model=ContributeResponse)
@limiter.limit(settings.contribute_rate_limit)
async def contribute_features(
    request: Request,
    req: FeaturesRequest,
    db: DbSession,
) -> ContributeResponse:
    """Contribute audio features to the cache.

    If the features already exist, increments the contributor count
    and backfills any missing keys from the new contribution.
    Max payload size: 64KB.
    """
    if not req.features:
        raise HTTPException(status_code=422, detail="Features dict must not be empty")

    features_size = len(json.dumps(req.features))
    if features_size > 64 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"Features payload too large: {features_size} bytes (max 64KB)",
        )

    # Check if features already exist
    result = await db.execute(
        select(Features).where(
            Features.fingerprint_hash == req.fingerprint_hash,
            Features.analysis_version == req.analysis_version,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Increment contributor count and backfill missing keys
        existing.contributor_count += 1
        merged = {**existing.features}
        for k, v in req.features.items():
            if v is not None and k not in merged:
                merged[k] = v
        existing.features = merged
        await db.commit()
        return ContributeResponse(
            status="confirmed",
            contributor_count=existing.contributor_count,
        )

    # Create new features entry
    feat = Features(
        fingerprint_hash=req.fingerprint_hash,
        analysis_version=req.analysis_version,
        features={k: v for k, v in req.features.items() if v is not None},
    )
    db.add(feat)
    await db.commit()

    return ContributeResponse(status="created", contributor_count=1)


# --- Analysis Detail models ---


class AnalysisDetailRequest(BaseModel):
    """Request to contribute analysis detail."""

    fingerprint_hash: str = Field(..., min_length=64, max_length=64)
    analysis_version: int = Field(..., ge=1)
    detail: dict = Field(..., description="Full structured analysis data (JSONB)")


class AnalysisDetailResponse(BaseModel):
    """Response containing analysis detail."""

    fingerprint_hash: str
    analysis_version: int
    detail: dict
    contributor_count: int


# --- Analysis Detail endpoints ---


@router.get("/analysis-detail/{fingerprint_hash}", response_model=AnalysisDetailResponse)
@limiter.limit(settings.lookup_rate_limit)
async def lookup_analysis_detail(
    request: Request,
    fingerprint_hash: str,
    analysis_version: int,
    db: DbSession,
) -> AnalysisDetailResponse:
    """Look up analysis detail by fingerprint hash.

    Returns the full structured analysis data if found, 404 otherwise.
    """
    result = await db.execute(
        select(AnalysisDetail).where(
            AnalysisDetail.fingerprint_hash == fingerprint_hash,
            AnalysisDetail.analysis_version == analysis_version,
        )
    )
    ad = result.scalar_one_or_none()

    if not ad:
        raise HTTPException(status_code=404, detail="Analysis detail not found")

    # Update last accessed time
    ad.last_accessed_at = datetime.utcnow()
    await db.commit()

    return AnalysisDetailResponse(
        fingerprint_hash=ad.fingerprint_hash,
        analysis_version=ad.analysis_version,
        detail=ad.detail,
        contributor_count=ad.contributor_count,
    )


@router.post("/analysis-detail", status_code=201, response_model=ContributeResponse)
@limiter.limit(settings.contribute_rate_limit)
async def contribute_analysis_detail(
    request: Request,
    req: AnalysisDetailRequest,
    db: DbSession,
) -> ContributeResponse:
    """Contribute analysis detail to the cache.

    If the detail already exists, increments the contributor count.
    Max payload size: 512KB.
    """
    detail_size = len(json.dumps(req.detail))
    if detail_size > 512 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"Analysis detail too large: {detail_size} bytes (max 512KB)",
        )

    # Check if detail already exists
    result = await db.execute(
        select(AnalysisDetail).where(
            AnalysisDetail.fingerprint_hash == req.fingerprint_hash,
            AnalysisDetail.analysis_version == req.analysis_version,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.contributor_count += 1
        await db.commit()
        return ContributeResponse(
            status="confirmed",
            contributor_count=existing.contributor_count,
        )

    # Create new analysis detail entry
    ad = AnalysisDetail(
        fingerprint_hash=req.fingerprint_hash,
        analysis_version=req.analysis_version,
        detail=req.detail,
    )
    db.add(ad)
    await db.commit()

    return ContributeResponse(status="created", contributor_count=1)
