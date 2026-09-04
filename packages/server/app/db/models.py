"""Database models for the cache server."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""



class Embedding(Base):
    """CLAP embedding cache entry.

    Keyed by (fingerprint_hash, analysis_version, clap_model_version)
    to ensure we don't mix incompatible embeddings.
    """

    __tablename__ = "embeddings"
    __table_args__ = (
        Index("ix_embeddings_created_at", "created_at"),
    )

    # Composite primary key
    fingerprint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    clap_model_version: Mapped[str] = mapped_column(String(100), primary_key=True)

    # The embedding vector (512 dimensions for CLAP)
    embedding = mapped_column(Vector(512), nullable=False)

    #: The installation that first contributed this vector (`ADR-0004` point 1).
    #:
    #: **This is what makes revocation possible at all.** Point 6 says revoking a
    #: client cascades over its submissions; without recording who sent an
    #: embedding there is nothing to cascade over, and point 7's "deletion by
    #: client identifier" has no rows to select.
    #:
    #: Nullable, and every row from before 2026-09-04 is null. Nobody recorded who
    #: contributed those, and `ADR-0004` point 3 already decided what that means:
    #: accepted, stored, never confirmable. It is not a gap to backfill.
    client_id: Mapped[str | None] = mapped_column(String(64))

    # Metadata
    contributor_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SubmissionAgreement(Base):
    """How closely an independent submission matched what was already stored.

    **This exists because the answer was being thrown away.** `contribute_embedding`
    used to increment `contributor_count` and discard the submitted vector, so the
    only question that matters for a verified commons — *do two machines computing
    the same audio produce the same vector?* — had no data behind it despite 44
    contributing installations.

    It records, it does not gate. First-write-wins still decides what is served, and
    no client can observe any difference. The point is to measure the noise floor
    before designing a verification scheme on top of it: if honest contributors agree
    to 1e-6, consensus is trivially strong; if they diverge at 0.05 because of BLAS
    versions or CPU-vs-GPU, a naive threshold would reject honest data and the design
    needs a different shape.

    AcousticBrainz is the cautionary case. They gathered duplicate submissions to
    mitigate quality problems and it did not work, because their data was a *claim*
    about the world and their algorithm was reproducibly wrong — duplicates agreed
    and were wrong together. A CLAP embedding is not a claim, so agreement means
    something different here. This table is how we find out what.
    """

    __tablename__ = "submission_agreement"
    __table_args__ = (
        Index("ix_submission_agreement_recorded_at", "recorded_at"),
        Index(
            "ix_submission_agreement_key",
            "fingerprint_hash",
            "analysis_version",
            "clap_model_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Which stored embedding this submission was compared against.
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    clap_model_version: Mapped[str] = mapped_column(String(100), nullable=False)

    #: Cosine similarity between the submitted vector and the stored one. 1.0 is
    #: identical. This is the measurement; everything else here is context for it.
    similarity: Mapped[float] = mapped_column(Float, nullable=False)

    #: Opaque per-install identifier, when the client sends one. **Without it a
    #: "second submission" may just be one client retrying**, which is precisely the
    #: mistake `contributor_count` already makes — it counts POSTs, not contributors.
    #: Nullable because existing clients do not send it and must keep working.
    client_id: Mapped[str | None] = mapped_column(String(64))

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Features(Base):
    """Audio features cache entry.

    Keyed by (fingerprint_hash, analysis_version) to ensure we don't
    mix features from incompatible analysis pipelines.

    All feature values are stored as a single JSONB blob — the server
    is a dumb store and never inspects individual feature keys.
    """

    __tablename__ = "features"

    # Composite primary key
    fingerprint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_version: Mapped[int] = mapped_column(Integer, primary_key=True)

    # All features as a JSONB blob
    features: Mapped[dict] = mapped_column(JSONB, nullable=False, default={})

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
