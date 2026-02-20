"""Database models for the cache server."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Embedding(Base):
    """CLAP embedding cache entry.

    Keyed by (fingerprint_hash, analysis_version, clap_model_version)
    to ensure we don't mix incompatible embeddings.
    """

    __tablename__ = "embeddings"

    # Composite primary key
    fingerprint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    clap_model_version: Mapped[str] = mapped_column(String(100), primary_key=True)

    # The embedding vector (512 dimensions for CLAP)
    embedding = mapped_column(Vector(512), nullable=False)

    # Metadata
    contributor_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Features(Base):
    """Audio features cache entry.

    Keyed by (fingerprint_hash, analysis_version) to ensure we don't
    mix features from incompatible analysis pipelines.
    """

    __tablename__ = "features"

    # Composite primary key
    fingerprint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_version: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Audio features (all nullable since not all may be computed)
    bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    key: Mapped[str | None] = mapped_column(String(10), nullable=True)
    energy: Mapped[float | None] = mapped_column(Float, nullable=True)
    danceability: Mapped[float | None] = mapped_column(Float, nullable=True)
    valence: Mapped[float | None] = mapped_column(Float, nullable=True)
    acousticness: Mapped[float | None] = mapped_column(Float, nullable=True)
    instrumentalness: Mapped[float | None] = mapped_column(Float, nullable=True)
    speechiness: Mapped[float | None] = mapped_column(Float, nullable=True)
    liveness: Mapped[float | None] = mapped_column(Float, nullable=True)
    loudness: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 1 deep analysis scalars
    harmonic_complexity: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_stability: Mapped[str | None] = mapped_column(String(30), nullable=True)
    modal_character: Mapped[str | None] = mapped_column(String(50), nullable=True)
    modal_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    swing_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    syncopation: Mapped[float | None] = mapped_column(Float, nullable=True)
    tempo_character: Mapped[str | None] = mapped_column(String(30), nullable=True)
    brightness: Mapped[float | None] = mapped_column(Float, nullable=True)
    dynamic_range_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    energy_shape: Mapped[str | None] = mapped_column(String(30), nullable=True)
    section_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    form_string: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avg_section_length: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 1 additional scalars
    replaygain_track_gain: Mapped[float | None] = mapped_column(Float, nullable=True)
    track_peak: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 3 melodic features
    note_density: Mapped[float | None] = mapped_column(Float, nullable=True)
    interval_character: Mapped[str | None] = mapped_column(String(30), nullable=True)
    pitch_range: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Metadata
    contributor_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class BannedIP(Base):
    """Banned IP addresses.

    IPs that have been blocked from accessing the cache.
    """

    __tablename__ = "banned_ips"

    ip_address: Mapped[str] = mapped_column(String(45), primary_key=True)  # IPv6 max length
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    banned_by: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Admin identifier
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class IPStats(Base):
    """Per-IP statistics for monitoring and scraping detection.

    Tracks request patterns to identify suspicious behavior.
    """

    __tablename__ = "ip_stats"

    ip_address: Mapped[str] = mapped_column(String(45), primary_key=True)

    # Request counts
    total_lookups: Mapped[int] = mapped_column(Integer, default=0)
    total_contributions: Mapped[int] = mapped_column(Integer, default=0)
    lookup_hits: Mapped[int] = mapped_column(Integer, default=0)  # Found in cache
    lookup_misses: Mapped[int] = mapped_column(Integer, default=0)  # Not found

    # Timestamps
    first_seen: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Flags
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalysisDetail(Base):
    """Full structured analysis data (JSONB blob).

    Separated from Features because the JSONB blob is 10-100KB vs ~500 bytes
    for scalar features. Clients can opt-in to fetching it.

    Keyed by (fingerprint_hash, analysis_version).
    """

    __tablename__ = "analysis_details"
    __table_args__ = (
        Index("ix_analysis_details_last_accessed", "last_accessed_at"),
    )

    # Composite primary key
    fingerprint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_version: Mapped[int] = mapped_column(Integer, primary_key=True)

    # The full structured analysis data
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Metadata
    contributor_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
