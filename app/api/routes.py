"""API routes for the cache server."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import settings
from app.db.models import AnalysisDetail, Embedding, Features
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


VALID_KEY_STABILITIES = {"stable", "moderate", "unstable"}
VALID_TEMPO_CHARACTERS = {"steady", "moderate", "variable", "live"}
VALID_ENERGY_SHAPES = {"steady", "building", "declining", "dynamic", "peak_early", "peak_late"}
VALID_INTERVAL_CHARACTERS = {"conjunct", "mixed", "disjunct"}


class FeaturesData(BaseModel):
    """Audio features data with validation."""

    # Original 10 basic features
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

    # Phase 1 deep analysis scalars
    harmonic_complexity: float | None = Field(None, ge=0.0, le=1.0)
    key_stability: str | None = None
    modal_character: str | None = Field(None, max_length=50)
    modal_confidence: float | None = Field(None, ge=0.0, le=1.0)
    swing_ratio: float | None = Field(None, ge=0.0)
    syncopation: float | None = Field(None, ge=0.0, le=1.0)
    tempo_character: str | None = None
    brightness: float | None = Field(None, ge=0.0, le=1.0)
    dynamic_range_db: float | None = Field(None, ge=0.0)
    energy_shape: str | None = None
    section_count: int | None = Field(None, ge=0)
    form_string: str | None = Field(None, max_length=200)
    avg_section_length: float | None = Field(None, ge=0.0)

    # Phase 1 additional scalars
    replaygain_track_gain: float | None = None
    track_peak: float | None = Field(None, ge=0.0)

    # Phase 3 melodic features
    note_density: float | None = Field(None, ge=0.0)
    interval_character: str | None = None
    pitch_range: int | None = Field(None, ge=0)

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_KEYS:
            raise ValueError(f"Invalid key: {v}. Must be a valid musical key.")
        return v

    @field_validator("key_stability")
    @classmethod
    def validate_key_stability(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_KEY_STABILITIES:
            raise ValueError(f"Invalid key_stability: {v}")
        return v

    @field_validator("tempo_character")
    @classmethod
    def validate_tempo_character(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_TEMPO_CHARACTERS:
            raise ValueError(f"Invalid tempo_character: {v}")
        return v

    @field_validator("energy_shape")
    @classmethod
    def validate_energy_shape(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_ENERGY_SHAPES:
            raise ValueError(f"Invalid energy_shape: {v}")
        return v

    @field_validator("interval_character")
    @classmethod
    def validate_interval_character(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_INTERVAL_CHARACTERS:
            raise ValueError(f"Invalid interval_character: {v}")
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
            harmonic_complexity=feat.harmonic_complexity,
            key_stability=feat.key_stability,
            modal_character=feat.modal_character,
            modal_confidence=feat.modal_confidence,
            swing_ratio=feat.swing_ratio,
            syncopation=feat.syncopation,
            tempo_character=feat.tempo_character,
            brightness=feat.brightness,
            dynamic_range_db=feat.dynamic_range_db,
            energy_shape=feat.energy_shape,
            section_count=feat.section_count,
            form_string=feat.form_string,
            avg_section_length=feat.avg_section_length,
            replaygain_track_gain=feat.replaygain_track_gain,
            track_peak=feat.track_peak,
            note_density=feat.note_density,
            interval_character=feat.interval_character,
            pitch_range=feat.pitch_range,
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

    # All feature column names (must match Features model attributes)
    _feature_columns = [
        "bpm", "key", "energy", "danceability", "valence", "acousticness",
        "instrumentalness", "speechiness", "liveness", "loudness",
        "harmonic_complexity", "key_stability", "modal_character", "modal_confidence",
        "swing_ratio", "syncopation", "tempo_character", "brightness",
        "dynamic_range_db", "energy_shape", "section_count", "form_string",
        "avg_section_length", "replaygain_track_gain", "track_peak",
        "note_density", "interval_character", "pitch_range",
    ]

    if existing:
        # Increment contributor count and backfill any NULL columns
        existing.contributor_count += 1
        for col in _feature_columns:
            new_val = getattr(req.features, col, None)
            if new_val is not None and getattr(existing, col) is None:
                setattr(existing, col, new_val)
        await db.commit()
        return ContributeResponse(
            status="confirmed",
            contributor_count=existing.contributor_count,
        )

    # Create new features entry with all available fields
    feat = Features(
        fingerprint_hash=req.fingerprint_hash,
        analysis_version=req.analysis_version,
    )
    for col in _feature_columns:
        val = getattr(req.features, col, None)
        if val is not None:
            setattr(feat, col, val)
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
    import json
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
