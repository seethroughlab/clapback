"""Database session management."""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Determine SSL settings based on database host
connect_args = {}
db_url = settings.get_async_database_url()
# Only disable SSL for Fly.io internal postgres (*.flycast or *.internal)
if ".flycast" in db_url or ".internal" in db_url:
    connect_args["ssl"] = False
# External databases (like Neon) need SSL - don't set ssl=False

# Create async engine
engine = create_async_engine(
    settings.get_async_database_url(),
    echo=settings.debug,
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args=connect_args,
)

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
