"""Familiar Cache - Community embedding cache server."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.admin import admin_router
from app.api.browse import browse_router
from app.api.routes import router
from app.limiter import limiter
from app.middleware import IPBanMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    yield
    # Shutdown


app = FastAPI(
    title="Familiar Cache",
    description="Community embedding cache for Familiar music player",
    version="0.1.0",
    lifespan=lifespan,
)

# Add rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# IP ban middleware (must be added before CORS)
app.add_middleware(IPBanMiddleware)

# CORS - allow all origins for now (embeddings are not sensitive)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Static assets (CSS, JS) for HTML pages
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include API routes
app.include_router(router)
app.include_router(admin_router)
app.include_router(browse_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/health/db")
async def health_db() -> dict[str, Any]:
    """Database health check."""
    from sqlalchemy import text

    from app.db.session import async_session_maker

    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": str(e)},
        )
