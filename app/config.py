"""Configuration for the cache server."""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Cache server settings."""

    database_url: str = "postgresql+asyncpg://cache:cache@localhost:5432/cache"

    # Disable SSL for the DB connection (self-hosted Postgres with no TLS,
    # e.g. the local pgvector container on OMV). Set CACHE_DB_DISABLE_SSL=true.
    db_disable_ssl: bool = False

    # Rate limiting
    lookup_rate_limit: str = "300/minute"
    contribute_rate_limit: str = "30/minute"

    # Admin dashboard
    admin_password: str = ""  # Set via CACHE_ADMIN_PASSWORD env var

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

        # Handle SSL for asyncpg
        # Fly.io internal connections don't need SSL
        import re
        if "sslmode=disable" in url:
            # Replace sslmode=disable with ssl=disable (asyncpg format)
            url = re.sub(r"sslmode=disable", "ssl=disable", url)
        elif "sslmode=" in url:
            # Remove other sslmode values
            url = re.sub(r"[?&]sslmode=[^&]*", "", url)
            url = url.rstrip("?").replace("&&", "&").rstrip("&")

        return url


settings = Settings()
