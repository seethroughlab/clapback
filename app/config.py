"""Configuration for the cache server."""

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


settings = Settings()
