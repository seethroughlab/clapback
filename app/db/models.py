"""Database models for the cache server."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, Integer, String, func
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

    # Metadata
    contributor_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
