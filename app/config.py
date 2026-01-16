"""Configuration for the cache server."""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Cache server settings."""

    database_url: str = "postgresql+asyncpg://cache:cache@localhost:5432/cache"

    # Rate limiting
    lookup_rate_limit: str = "100/minute"
    contribute_rate_limit: str = "10/minute"

    # Server
    debug: bool = False

    model_config = {"env_prefix": "CACHE_"}

    def get_async_database_url(self) -> str:
        """Get database URL with asyncpg driver.

        Handles Fly.io's DATABASE_URL format (postgres://) and converts
        to asyncpg format (postgresql+asyncpg://).
        """
        url = self.database_url

        # Also check DATABASE_URL directly (Fly.io sets this)
        if url == "postgresql+asyncpg://cache:cache@localhost:5432/cache":
            fly_url = os.environ.get("DATABASE_URL")
            if fly_url:
                url = fly_url

        # Convert postgres:// to postgresql+asyncpg://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        # Remove sslmode parameter (asyncpg doesn't support it)
        # Fly.io internal connections don't need SSL anyway
        if "sslmode=" in url:
            # Remove ?sslmode=xxx or &sslmode=xxx
            import re
            url = re.sub(r"[?&]sslmode=[^&]*", "", url)
            # Clean up any trailing ? or &&
            url = url.rstrip("?").replace("&&", "&").rstrip("&")

        return url


settings = Settings()
