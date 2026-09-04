"""Admin dashboard routes for the cache server."""

import secrets
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text, update

from app.api.deps import DbSession
from app.config import settings
from app.db.models import AnalysisDetail, BannedIP, Embedding, Features, IPStats
from app.templates import templates

admin_router = APIRouter(prefix="/admin", tags=["admin"])

# Simple session store (in production, use Redis or DB)
_sessions: dict[str, datetime] = {}
SESSION_DURATION = timedelta(hours=24)
SESSION_COOKIE_NAME = "admin_session"


def _check_password_configured() -> None:
    """Ensure admin password is configured."""
    if not settings.admin_password:
        raise HTTPException(
            status_code=503,
            detail="Admin dashboard not configured. Set CACHE_ADMIN_PASSWORD environment variable.",
        )


def _verify_session(request: Request) -> bool:
    """Verify the admin session is valid."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id or session_id not in _sessions:
        return False

    expires = _sessions[session_id]
    if datetime.utcnow() > expires:
        del _sessions[session_id]
        return False

    return True


def _require_auth(request: Request) -> None:
    """Dependency that requires authentication."""
    _check_password_configured()
    if not _verify_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")


RequireAuth = Annotated[None, Depends(_require_auth)]


class BanIPRequest(BaseModel):
    """Request to ban an IP."""

    ip_address: str
    reason: str | None = None


def _render_login(request: Request, error_message: str | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"error_message": error_message},
        status_code=status_code,
    )


@admin_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """Show login page."""
    _check_password_configured()

    if _verify_session(request):
        return RedirectResponse(url="/admin", status_code=302)

    return _render_login(request)


@admin_router.post("/login")
async def login(request: Request, password: Annotated[str, Form()], response: Response) -> Response:
    """Process login."""
    _check_password_configured()

    if not secrets.compare_digest(password, settings.admin_password):
        return _render_login(request, error_message="Invalid password", status_code=401)

    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = datetime.utcnow() + SESSION_DURATION

    response = RedirectResponse(url="/admin", status_code=302)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(SESSION_DURATION.total_seconds()),
    )
    return response


@admin_router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Logout and clear session."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id and session_id in _sessions:
        del _sessions[session_id]

    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@admin_router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: DbSession) -> HTMLResponse:
    """Admin dashboard with stats and IP management."""
    _check_password_configured()

    if not _verify_session(request):
        return RedirectResponse(url="/admin/login", status_code=302)

    embedding_count = (await db.execute(select(func.count()).select_from(Embedding))).scalar() or 0
    features_count = (await db.execute(select(func.count()).select_from(Features))).scalar() or 0
    analysis_detail_count = (await db.execute(select(func.count()).select_from(AnalysisDetail))).scalar() or 0
    banned_count = (await db.execute(
        select(func.count()).select_from(BannedIP).where(BannedIP.is_active == True)
    )).scalar() or 0
    unique_ips = (await db.execute(select(func.count()).select_from(IPStats))).scalar() or 0

    top_contributors = (await db.execute(
        select(IPStats)
        .order_by(IPStats.total_contributions.desc())
        .limit(20)
    )).scalars().all()

    # **The Phase 0 measurement.** Buckets rather than an average: the question is
    # whether honest agreement is *separable* from everything else, and a mean over a
    # bimodal distribution hides exactly that. If the top bucket holds nearly all of
    # them, a consensus threshold is viable; if they are spread, it is not, and the
    # verification design needs a different shape.
    agreement = (await db.execute(
        text("""
            SELECT
              count(*) FILTER (WHERE similarity >= 0.999999) AS identical,
              count(*) FILTER (WHERE similarity >= 0.9999 AND similarity < 0.999999) AS near,
              count(*) FILTER (WHERE similarity >= 0.99 AND similarity < 0.9999) AS close,
              count(*) FILTER (WHERE similarity >= 0.9 AND similarity < 0.99) AS loose,
              count(*) FILTER (WHERE similarity < 0.9) AS divergent,
              count(*) AS total,
              count(DISTINCT client_id) AS distinct_clients
            FROM submission_agreement
        """)
    )).mappings().first()

    flagged_ips = (await db.execute(
        select(IPStats)
        .where(IPStats.flagged == True)
        .order_by(IPStats.last_seen.desc())
        .limit(20)
    )).scalars().all()

    banned_ips = (await db.execute(
        select(BannedIP)
        .where(BannedIP.is_active == True)
        .order_by(BannedIP.banned_at.desc())
        .limit(50)
    )).scalars().all()

    recent_ips = (await db.execute(
        select(IPStats)
        .order_by(IPStats.last_seen.desc())
        .limit(20)
    )).scalars().all()

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "stats": {
                "embeddings": embedding_count,
                "features": features_count,
                "analysis_details": analysis_detail_count,
                "unique_ips": unique_ips,
                "banned": banned_count,
            },
            "recent_ips": recent_ips,
            "top_contributors": top_contributors,
            "agreement": agreement,
            "flagged_ips": flagged_ips,
            "banned_ips": banned_ips,
        },
    )


@admin_router.post("/ban")
async def ban_ip(
    request: Request,
    db: DbSession,
    ip_address: Annotated[str, Form()],
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Ban an IP address."""
    _check_password_configured()
    if not _verify_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    existing = await db.execute(
        select(BannedIP).where(BannedIP.ip_address == ip_address)
    )
    ban = existing.scalar_one_or_none()

    if ban:
        ban.is_active = True
        ban.reason = reason
        ban.banned_at = datetime.utcnow()
    else:
        ban = BannedIP(
            ip_address=ip_address,
            reason=reason,
            banned_by="admin",
        )
        db.add(ban)

    await db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@admin_router.post("/unban")
async def unban_ip(
    request: Request,
    db: DbSession,
    ip_address: Annotated[str, Form()],
) -> RedirectResponse:
    """Unban an IP address."""
    _check_password_configured()
    if not _verify_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    await db.execute(
        update(BannedIP)
        .where(BannedIP.ip_address == ip_address)
        .values(is_active=False)
    )
    await db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@admin_router.post("/flag")
async def flag_ip(
    request: Request,
    db: DbSession,
    ip_address: Annotated[str, Form()],
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Flag an IP for review."""
    _check_password_configured()
    if not _verify_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    await db.execute(
        update(IPStats)
        .where(IPStats.ip_address == ip_address)
        .values(flagged=True, flag_reason=reason)
    )
    await db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@admin_router.post("/unflag")
async def unflag_ip(
    request: Request,
    db: DbSession,
    ip_address: Annotated[str, Form()],
) -> RedirectResponse:
    """Remove flag from an IP."""
    _check_password_configured()
    if not _verify_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    await db.execute(
        update(IPStats)
        .where(IPStats.ip_address == ip_address)
        .values(flagged=False, flag_reason=None)
    )
    await db.commit()
    return RedirectResponse(url="/admin", status_code=302)
