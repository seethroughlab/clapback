"""API routes for the cache server."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.db.models import Embedding

router = APIRouter(prefix="/v1")


class EmbeddingRequest(BaseModel):
    """Request to contribute an embedding."""

    fingerprint_hash: str = Field(..., min_length=64, max_length=64)
    embedding: list[float] = Field(..., min_length=512, max_length=512)
    analysis_version: int = Field(..., ge=1)
    clap_model_version: str = Field(..., min_length=1, max_length=100)


class EmbeddingResponse(BaseModel):
    """Response containing an embedding."""

    fingerprint_hash: str
    embedding: list[float]
    analysis_version: int
    clap_model_version: str
    contributor_count: int


class ContributeResponse(BaseModel):
    """Response after contributing an embedding."""

    status: str
    contributor_count: int | None = None


@router.get("/embeddings/{fingerprint_hash}", response_model=EmbeddingResponse)
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
        # Increment contributor count
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
