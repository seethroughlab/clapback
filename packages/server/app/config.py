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

    # `ADR-0004` point 9's first bound: a ceiling on corpus rows, checked on write
    # and rejecting past it with a clear error, "raised deliberately as the corpus
    # grows, so growth is a decision rather than a surprise".
    #
    # It is the only one of that point's three bounds that needs nothing from
    # identity, which is why it can exist before `ADR-0004` is built. It bounds
    # the total rather than the rate: rate limits let a determined client add rows
    # forever, just slowly, and the failure this guards against is a full disk on
    # a 2 GB instance rather than an attacker.
    #
    # 0 disables it. The default is `ADR-0003` point 11's stated comfortable limit.
    max_embeddings: int = 500_000

    # `ADR-0004` point 9's third bound, built by `ADR-0016` point 7: rows one
    # `client_id` may write — created or confirmed — in a rolling 24 hours.
    # Generous enough for any library and tight enough that the ceiling cannot
    # be reached by one install in one day: ten percent of it. Reached, the
    # write is a 429 with `Retry-After`, per row. 0 disables it. Only an
    # identified contribution can be counted, so an unattributed one is bounded
    # by the ceiling and the rate alone, as before.
    client_quota_rows_per_day: int = 50_000

    # `ADR-0016` point 3: the batch contribute route's own per-address limit,
    # counted per row. A starting figure with a rationale, to be measured
    # against the instance after the first real batch contributor.
    contribute_batch_rate_limit: str = "600/minute"

    # `ADR-0013` point 5: where the weekly public export is served from. The base
    # URL of the export bucket; /export/latest.json redirects into it. Blank
    # means no export is published yet, and /export says so rather than 404ing.
    export_public_url: str = ""
    # The manifest deploy/export.sh leaves beside the compose project, mounted
    # read-only into the container. Read per request: it changes weekly and the
    # page's whole job is to show the date it carries.
    export_manifest_path: str = "/exports/manifest.json"

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
