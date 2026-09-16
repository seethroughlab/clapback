"""API routes for the cache server."""

import json
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
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


async def _recordings_for(db, hashes: list[str]) -> dict[str, tuple[str, int]]:
    """The recording each hash resolves to, and how many distinct clients say so.

    `ADR-0012` point 1: derived from the claims rather than read from the row. The
    winner is the MBID with the most distinct clients; ties break on the MBID text
    so the answer is a **total** ordering — `ADR-0006` learned what a partial one
    costs, being a corpus that answers the same question differently between calls.
    Hashes with no claims are absent from the result, which callers render as null.
    """
    if not hashes:
        return {}
    stmt = (
        select(
            RecordingClaim.fingerprint_hash,
            RecordingClaim.recording_mbid,
            func.count(func.distinct(RecordingClaim.client_id)).label("n"),
        )
        .where(RecordingClaim.fingerprint_hash.in_(hashes))
        .group_by(RecordingClaim.fingerprint_hash, RecordingClaim.recording_mbid)
        .order_by(
            RecordingClaim.fingerprint_hash,
            func.count(func.distinct(RecordingClaim.client_id)).desc(),
            RecordingClaim.recording_mbid,
        )
    )
    out: dict[str, tuple[str, int]] = {}
    for h, mbid, n in (await db.execute(stmt)).all():
        out.setdefault(h, (mbid, int(n)))
    return out


async def _record_claim(db, fingerprint_hash: str, recording_mbid: str, client_id: str) -> None:
    """One claim per (hash, mbid, client). Saying it twice is saying it once."""
    await db.execute(
        pg_insert(RecordingClaim)
        .values(
            fingerprint_hash=fingerprint_hash,
            recording_mbid=recording_mbid,
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


class SimilarResponse(BaseModel):
    neighbours: list[Neighbour]
    searched: int


# --- Embedding models ---


class EmbeddingRequest(BaseModel):
    """Request to contribute an embedding."""

    fingerprint_hash: str = Field(..., min_length=64, max_length=64)
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

    @field_validator("recording_mbid")
    @classmethod
    def _mbid_shape(cls, v: str | None) -> str | None:
        return None if v is None else _canonical_mbid(v)


class EmbeddingResponse(BaseModel):
    """Response containing an embedding."""

    fingerprint_hash: str
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


class ContributeResponse(BaseModel):
    """Response after contributing."""

    status: str
    contributor_count: int | None = None


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

    recording = (await _recordings_for(db, [emb.fingerprint_hash])).get(emb.fingerprint_hash)
    return EmbeddingResponse(
        fingerprint_hash=emb.fingerprint_hash,
        embedding=list(emb.embedding),
        analysis_version=emb.analysis_version,
        clap_model_version=emb.clap_model_version,
        pipeline_version=emb.pipeline_version,
        contributor_count=emb.contributor_count,
        recording_mbid=recording[0] if recording else None,
        recording_claims=recording[1] if recording else 0,
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
    if req.recording_mbid and not req.client_id:
        raise HTTPException(
            status_code=422,
            detail="recording_mbid needs a client_id: a claim must be attributable (ADR-0012)",
        )

    result = await db.execute(
        select(Embedding).where(
            Embedding.fingerprint_hash == req.fingerprint_hash,
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
                    fingerprint_hash=req.fingerprint_hash,
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
        if req.recording_mbid:
            await _record_claim(db, req.fingerprint_hash, req.recording_mbid, req.client_id)
        await db.commit()
        return ContributeResponse(
            status="confirmed",
            contributor_count=existing.contributor_count,
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
        fingerprint_hash=req.fingerprint_hash,
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
    if req.recording_mbid:
        await _record_claim(db, req.fingerprint_hash, req.recording_mbid, req.client_id)
    await db.commit()

    return ContributeResponse(status="created", contributor_count=1)


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
    stmt = stmt.order_by(Embedding.embedding.cosine_distance(req.embedding)).limit(req.limit)

    rows = (await db.execute(stmt)).all()
    count_stmt = select(func.count()).select_from(Embedding)
    if filters:
        count_stmt = count_stmt.where(*filters)
    searched = await db.scalar(count_stmt)
    # `ADR-0012` point 5: a neighbour with a recording is a title and a page; one
    # without is still a hash, and the response says which is which.
    recordings = await _recordings_for(db, [r.fingerprint_hash for r in rows])
    return SimilarResponse(
        neighbours=[
            Neighbour(
                fingerprint_hash=r.fingerprint_hash,
                similarity=float(r.similarity),
                analysis_version=r.analysis_version,
                clap_model_version=r.clap_model_version,
                pipeline_version=r.pipeline_version,
                recording_mbid=recordings[r.fingerprint_hash][0]
                if r.fingerprint_hash in recordings
                else None,
                recording_claims=recordings[r.fingerprint_hash][1]
                if r.fingerprint_hash in recordings
                else 0,
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
    recording_mbid: str = Field(..., max_length=36)
    client_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("recording_mbid")
    @classmethod
    def _mbid_shape(cls, v: str) -> str:
        return _canonical_mbid(v)


class ClaimResponse(BaseModel):
    status: str
    #: What the hash resolves to after this claim, and how many clients agree.
    recording_mbid: str | None
    recording_claims: int


class RecordingEmbedding(BaseModel):
    """One row claimed under a recording. Per pipeline, since a recording the
    corpus holds from two pipelines is two rows that are not comparable."""

    fingerprint_hash: str
    pipeline_version: str
    embedding: list[float]
    contributor_count: int
    #: How many distinct clients claim *this hash* is this recording.
    recording_claims: int


class RecordingResponse(BaseModel):
    recording_mbid: str
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
    await _record_claim(db, req.fingerprint_hash, req.recording_mbid, req.client_id)
    await db.commit()
    recording = (await _recordings_for(db, [req.fingerprint_hash])).get(req.fingerprint_hash)
    return ClaimResponse(
        status="claimed",
        recording_mbid=recording[0] if recording else None,
        recording_claims=recording[1] if recording else 0,
    )


@router.get("/recordings/{recording_mbid}", response_model=RecordingResponse)
@limiter.limit(settings.lookup_rate_limit)
async def recording(
    request: Request,
    recording_mbid: str,
    db: DbSession,
    pipeline_version: str | None = None,
) -> RecordingResponse:
    """What does recording X sound like — without holding X.

    `ADR-0012` point 5's third read, and Familiar's `ADR-0102`'s whole purpose.
    Returns every row any client has claimed under this id, one per pipeline,
    each with the count of distinct clients behind *that* row's claim. Filter by
    `pipeline_version` to get only vectors comparable with your own.

    A read, unauthenticated, on the lookup rate limit.
    """
    try:
        mbid = _canonical_mbid(recording_mbid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    claimed = (
        select(
            RecordingClaim.fingerprint_hash,
            func.count(func.distinct(RecordingClaim.client_id)).label("n"),
        )
        .where(RecordingClaim.recording_mbid == mbid)
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
    return RecordingResponse(
        recording_mbid=mbid,
        embeddings=[
            RecordingEmbedding(
                fingerprint_hash=e.fingerprint_hash,
                pipeline_version=e.pipeline_version,
                embedding=list(e.embedding),
                contributor_count=e.contributor_count,
                recording_claims=int(n),
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

    @field_validator("recording_mbid")
    @classmethod
    def _mbid_shape(cls, v: str | None) -> str | None:
        return None if v is None else _canonical_mbid(v)

    @model_validator(mode="after")
    def _exactly_one(self) -> "LookupKey":
        if (self.fingerprint_hash is None) == (self.recording_mbid is None):
            raise ValueError("a key is a fingerprint_hash or a recording_mbid, not both or neither")
        return self


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


def _row_for(emb: Embedding, recording: tuple[str, int] | None, *, vectors: bool) -> LookupRow:
    fields: dict = {
        "fingerprint_hash": emb.fingerprint_hash,
        "analysis_version": emb.analysis_version,
        "clap_model_version": emb.clap_model_version,
        "pipeline_version": emb.pipeline_version,
        "contributor_count": emb.contributor_count,
        "recording_mbid": recording[0] if recording else None,
        "recording_claims": recording[1] if recording else 0,
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
    mbids = [k.recording_mbid for k in req.keys if k.recording_mbid is not None]

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
    # then the most confirmed — the order `GET /v1/recordings/{mbid}` serves.
    by_mbid: dict[str, Embedding] = {}
    if mbids:
        claimed = (
            select(
                RecordingClaim.recording_mbid,
                RecordingClaim.fingerprint_hash,
                func.count(func.distinct(RecordingClaim.client_id)).label("n"),
            )
            .where(RecordingClaim.recording_mbid.in_(mbids))
            .group_by(RecordingClaim.recording_mbid, RecordingClaim.fingerprint_hash)
            .subquery()
        )
        stmt = (
            select(claimed.c.recording_mbid, Embedding)
            .join(claimed, claimed.c.fingerprint_hash == Embedding.fingerprint_hash)
            .order_by(
                claimed.c.recording_mbid,
                claimed.c.n.desc(),
                Embedding.contributor_count.desc(),
                Embedding.created_at,
                Embedding.pipeline_version,
            )
        )
        if pipeline is not None:
            stmt = stmt.where(Embedding.pipeline_version == pipeline)
        for mbid, emb in (await db.execute(stmt)).all():
            by_mbid.setdefault(mbid, emb)

    found = list(by_hash.values()) + list(by_mbid.values())
    recordings = await _recordings_for(db, sorted({e.fingerprint_hash for e in found}))
    if found:
        now = datetime.utcnow()
        for emb in found:
            emb.last_accessed_at = now
        await db.commit()

    results = []
    for key in req.keys:
        emb = (
            by_hash.get(key.fingerprint_hash)
            if key.fingerprint_hash
            else by_mbid.get(key.recording_mbid)
        )
        row = (
            _row_for(emb, recordings.get(emb.fingerprint_hash), vectors=req.vectors)
            if emb
            else None
        )
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
            RecordingClaim.recording_mbid,
            func.count(func.distinct(RecordingClaim.client_id)).label("n"),
        )
        .where(RecordingClaim.fingerprint_hash == fingerprint_hash)
        .group_by(RecordingClaim.recording_mbid)
        .order_by(
            func.count(func.distinct(RecordingClaim.client_id)).desc(),
            RecordingClaim.recording_mbid,
        )
    )
    return ClaimsResponse(
        fingerprint_hash=fingerprint_hash,
        claims=[
            ClaimEntry(type="musicbrainz_recording", id=mbid, clients=int(n))
            for mbid, n in (await db.execute(stmt)).all()
        ],
    )


# --- Pipelines: what the corpus holds, by identity (`ADR-0014` point 3) ---


class PipelineEntry(BaseModel):
    """One pipeline identity the corpus holds rows under, with its population."""

    pipeline_version: str
    rows: int
    #: Rows under this identity that any client has claimed a recording for.
    named: int
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
            first_contributed_at=first,
            last_contributed_at=last,
        )
        for pv, rows, named, first, last in (await db.execute(stmt)).all()
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
