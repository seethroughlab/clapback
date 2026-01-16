"""API routes for the cache server."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import settings
from app.db.models import Embedding, Features
from app.limiter import limiter

router = APIRouter(prefix="/v1")

# Valid musical keys
VALID_KEYS = {
    "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B",
    "Cm", "C#m", "Dbm", "Dm", "D#m", "Ebm", "Em", "Fm", "F#m", "Gbm", "Gm", "G#m", "Abm", "Am", "A#m", "Bbm", "Bm",
}


# --- Embedding models ---


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
    """Response after contributing."""

    status: str
    contributor_count: int | None = None


# --- Features models ---


class FeaturesData(BaseModel):
    """Audio features data with validation."""

    bpm: float | None = Field(None, ge=20, le=400)  # Physical limits of music tempo
    key: str | None = None
    energy: float | None = Field(None, ge=0.0, le=1.0)
    danceability: float | None = Field(None, ge=0.0, le=1.0)
    valence: float | None = Field(None, ge=0.0, le=1.0)
    acousticness: float | None = Field(None, ge=0.0, le=1.0)
    instrumentalness: float | None = Field(None, ge=0.0, le=1.0)
    speechiness: float | None = Field(None, ge=0.0, le=1.0)
    liveness: float | None = Field(None, ge=0.0, le=1.0)
    loudness: float | None = Field(None, ge=-60.0, le=0.0)  # dB scale

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_KEYS:
            raise ValueError(f"Invalid key: {v}. Must be a valid musical key.")
        return v


class FeaturesRequest(BaseModel):
    """Request to contribute features."""

    fingerprint_hash: str = Field(..., min_length=64, max_length=64)
    analysis_version: int = Field(..., ge=1)
    features: FeaturesData


class FeaturesResponse(BaseModel):
    """Response containing features."""

    fingerprint_hash: str
    analysis_version: int
    features: FeaturesData
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
        features=FeaturesData(
            bpm=feat.bpm,
            key=feat.key,
            energy=feat.energy,
            danceability=feat.danceability,
            valence=feat.valence,
            acousticness=feat.acousticness,
            instrumentalness=feat.instrumentalness,
            speechiness=feat.speechiness,
            liveness=feat.liveness,
            loudness=feat.loudness,
        ),
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

    If the features already exist, increments the contributor count.
    """
    # Check if features already exist
    result = await db.execute(
        select(Features).where(
            Features.fingerprint_hash == req.fingerprint_hash,
            Features.analysis_version == req.analysis_version,
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

    # Create new features entry
    feat = Features(
        fingerprint_hash=req.fingerprint_hash,
        analysis_version=req.analysis_version,
        bpm=req.features.bpm,
        key=req.features.key,
        energy=req.features.energy,
        danceability=req.features.danceability,
        valence=req.features.valence,
        acousticness=req.features.acousticness,
        instrumentalness=req.features.instrumentalness,
        speechiness=req.features.speechiness,
        liveness=req.features.liveness,
        loudness=req.features.loudness,
    )
    db.add(feat)
    await db.commit()

    return ContributeResponse(status="created", contributor_count=1)
