"""API routes for the cache server."""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

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


class EmbeddingResponse(BaseModel):
    """Response containing an embedding."""

    fingerprint_hash: str
    embedding: list[float]
    analysis_version: int
    clap_model_version: str
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
        similarity = _cosine_similarity(req.embedding, list(existing.embedding))
        if similarity is not None:
            db.add(
                SubmissionAgreement(
                    fingerprint_hash=req.fingerprint_hash,
                    analysis_version=req.analysis_version,
                    clap_model_version=req.clap_model_version,
                    similarity=similarity,
                    client_id=req.client_id,
                )
            )

        existing.contributor_count += 1
        await db.commit()
        return ContributeResponse(
            status="confirmed",
            contributor_count=existing.contributor_count,
        )

    # Create new embedding
    emb = Embedding(
        fingerprint_hash=req.fingerprint_hash,
        embedding=req.embedding,
        analysis_version=req.analysis_version,
        clap_model_version=req.clap_model_version,
    )
    db.add(emb)
    await db.commit()

    return ContributeResponse(status="created", contributor_count=1)


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
