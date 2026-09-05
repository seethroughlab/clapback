"""API routes for the cache server."""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.config import settings
from app.db.models import AnalysisDetail, Embedding, Features, SubmissionAgreement
from app.limiter import limiter

router = APIRouter(prefix="/v1")


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
    #: What produced this vector — `ADR-0006` point 1. Optional in this phase and
    #: required in phase 4, which is the whole shape of point 6: the server learns
    #: to store it before any client is obliged to send it, so no contract breaks
    #: at any point in the sequence (`ADR-0005` point 10).
    #:
    #: Asserted, not proven (point 8). A client sends a string and the server
    #: believes it. That catches the forgotten bump and the stale build, which are
    #: the realistic failures; it is not a defence against a contributor who lies,
    #: and nothing here should be described as if it were.
    pipeline_version: str | None = Field(default=None, min_length=1, max_length=200)


class EmbeddingResponse(BaseModel):
    """Response containing an embedding."""

    fingerprint_hash: str
    embedding: list[float]
    analysis_version: int
    clap_model_version: str
    #: Null for every row contributed before phase 2, which is all of them today.
    #: A caller comparing two vectors should treat null as "unknown", not as
    #: "same as mine".
    pipeline_version: str | None = None
    contributor_count: int


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
    analysis_version: int,
    clap_model_version: str,
    db: DbSession,
) -> EmbeddingResponse:
    """Look up an embedding by fingerprint hash.

    Returns the embedding if found, 404 otherwise.
    """
    result = await db.execute(
        select(Embedding).where(
            Embedding.fingerprint_hash == fingerprint_hash,
            Embedding.analysis_version == analysis_version,
            Embedding.clap_model_version == clap_model_version,
        )
    )
    emb = result.scalar_one_or_none()

    if not emb:
        raise HTTPException(status_code=404, detail="Embedding not found")

    # Update last accessed time
    emb.last_accessed_at = datetime.utcnow()
    await db.commit()

    return EmbeddingResponse(
        fingerprint_hash=emb.fingerprint_hash,
        embedding=list(emb.embedding),
        analysis_version=emb.analysis_version,
        clap_model_version=emb.clap_model_version,
        pipeline_version=emb.pipeline_version,
        contributor_count=emb.contributor_count,
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
    # Check if embedding already exists
    result = await db.execute(
        select(Embedding).where(
            Embedding.fingerprint_hash == req.fingerprint_hash,
            Embedding.analysis_version == req.analysis_version,
            Embedding.clap_model_version == req.clap_model_version,
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
        # disagreement.** Until phase 4 the key does not include the pipeline, so
        # two genuinely incomparable vectors can land on the same row — and their
        # cosine similarity would be a real number that means nothing. Writing it
        # here would put version drift into the measurement that exists to detect
        # contributor drift, and no later analysis could separate them.
        #
        # Equality rather than "both declared": both null is the legacy case and
        # keeps recording exactly as before, while one side declaring and the
        # other not is precisely the unknown this guard exists for.
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

        # **The stored row is not relabelled with the submitted pipeline**, even
        # when it has none and the submission declares one. `ADR-0006` point 5
        # decided the existing rows are recomputed rather than relabelled: writing
        # a pipeline here would assert, on a vector nobody can vouch for, exactly
        # the provenance phase 4 is going to trust.
        existing.contributor_count += 1
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
        # Stored, not keyed on — phase 1 of `ADR-0006` point 6. Phase 4 promotes it
        # to the key, and it can only do that if the rows contributed between now
        # and then already carry it.
        pipeline_version=req.pipeline_version,
    )
    db.add(emb)
    await db.commit()

    return ContributeResponse(status="created", contributor_count=1)


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

    **It returns fingerprint hashes, and that is the known limit rather than an
    oversight.** `ADR-0002` point 4 is explicit: a caller who does not already
    hold the audio cannot resolve what came back, so this is useful for "is this
    recording already known" and not yet for "what does this record I do not own
    sound like". The second needs a recording id as a second key — `ADR-0001`
    deferred item 4 — and that record says plainly: do not ship the endpoint and
    call the capability delivered.

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
    return SimilarResponse(
        neighbours=[
            Neighbour(
                fingerprint_hash=r.fingerprint_hash,
                similarity=float(r.similarity),
                analysis_version=r.analysis_version,
                clap_model_version=r.clap_model_version,
                pipeline_version=r.pipeline_version,
            )
            for r in rows
        ],
        searched=searched or 0,
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
