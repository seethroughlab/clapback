"""Middleware for IP banning and request tracking."""

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from starlette.middleware.base import BaseHTTPMiddleware

from app.db.models import BannedIP, IPStats
from app.db.session import async_session_maker


class IPBanMiddleware(BaseHTTPMiddleware):
    """Middleware to check if an IP is banned and track request stats."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Get client IP
        client_ip = self._get_client_ip(request)

        # Skip health checks and admin routes from ban check
        path = request.url.path
        if path.startswith("/health") or path.startswith("/admin"):
            return await call_next(request)

        # Check if IP is banned
        async with async_session_maker() as db:
            result = await db.execute(
                select(BannedIP).where(
                    BannedIP.ip_address == client_ip,
                    BannedIP.is_active == True,
                )
            )
            banned = result.scalar_one_or_none()

            if banned:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "IP address banned",
                        "reason": banned.reason or "No reason provided",
                    },
                )

        # Process request
        response = await call_next(request)

        # Track stats for API endpoints (async, non-blocking)
        if path.startswith("/v1/"):
            await self._track_request(request, response, client_ip)

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP, accounting for proxies."""
        # Check X-Forwarded-For header (set by proxies/load balancers)
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # First IP in the list is the original client
            return forwarded.split(",")[0].strip()

        # Fall back to direct connection IP
        if request.client:
            return request.client.host

        return "unknown"

    async def _track_request(
        self, request: Request, response: Response, client_ip: str
    ) -> None:
        """Track request statistics for an IP."""
        path = request.url.path
        method = request.method
        status = response.status_code

        # Determine request type
        is_lookup = method == "GET" and ("/embeddings/" in path or "/features/" in path)
        is_contribute = method == "POST" and ("/embeddings" in path or "/features" in path)

        if not is_lookup and not is_contribute:
            return

        async with async_session_maker() as db:
            # Upsert stats using PostgreSQL INSERT ... ON CONFLICT
            stmt = insert(IPStats).values(
                ip_address=client_ip,
                total_lookups=1 if is_lookup else 0,
                total_contributions=1 if is_contribute else 0,
                lookup_hits=1 if is_lookup and status == 200 else 0,
                lookup_misses=1 if is_lookup and status == 404 else 0,
            ).on_conflict_do_update(
                index_elements=["ip_address"],
                set_={
                    "total_lookups": IPStats.total_lookups + (1 if is_lookup else 0),
                    "total_contributions": IPStats.total_contributions + (1 if is_contribute else 0),
                    "lookup_hits": IPStats.lookup_hits + (1 if is_lookup and status == 200 else 0),
                    "lookup_misses": IPStats.lookup_misses + (1 if is_lookup and status == 404 else 0),
                    "last_seen": func.now(),
                },
            )
            await db.execute(stmt)
            await db.commit()
