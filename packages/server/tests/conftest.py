"""Suite-wide fixtures.

Almost every test here is pure — shapes and source properties, no database.
`ADR-0019` point 2's test is the first that cannot be: it contributes one
recording under two keys from two clients and asserts the confirmation is
counted, which is a fact about what the write path did to three tables.

Set `CACHE_TEST_DATABASE_URL` to a Postgres with pgvector and the `db_client`
fixture migrates it to head, truncates the tables between tests, and serves the
application over an in-process ASGI transport. Unset, every test that asks for
it is skipped, and the pure suite runs as it always has. CI sets it
(`server-ci.yml`); locally the compose Postgres on 5433 is the usual target.

The URL must be exported before `app.config` is imported — settings read the
environment once — which is why this runs at conftest import and not in a
fixture.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

TEST_DB = os.environ.get("CACHE_TEST_DATABASE_URL")
if TEST_DB:
    os.environ["CACHE_DATABASE_URL"] = TEST_DB
    os.environ.setdefault("CACHE_DB_DISABLE_SSL", "true")

needs_db = pytest.mark.skipif(not TEST_DB, reason="CACHE_TEST_DATABASE_URL not set")

TABLES = (
    "submission_agreement",
    "recording_claims",
    "embeddings",
    "features",
    "analysis_details",
    "ip_stats",
)


@pytest.fixture(scope="session")
def migrated_db():
    if not TEST_DB:
        pytest.skip("CACHE_TEST_DATABASE_URL not set")
    # In a subprocess: alembic's env runs its own event loop.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], check=True, capture_output=True
    )
    return TEST_DB


@pytest.fixture
async def db_client(migrated_db):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.db.session import engine
    from app.limiter import limiter
    from app.main import app

    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)}"))
    limiter.enabled = (
        False  # the limits are tested by their own source tests; here they would only count
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        limiter.enabled = True
        await engine.dispose()
