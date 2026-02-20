"""Admin dashboard routes for the cache server."""

import secrets
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update

from app.api.deps import DbSession
from app.config import settings
from app.db.models import AnalysisDetail, BannedIP, Embedding, Features, IPStats

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


@admin_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """Show login page."""
    _check_password_configured()

    if _verify_session(request):
        return RedirectResponse(url="/admin", status_code=302)

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Familiar Cache - Admin Login</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a2e;
                color: #eee;
                margin: 0;
                padding: 20px;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .login-box {
                background: #16213e;
                padding: 40px;
                border-radius: 12px;
                max-width: 400px;
                width: 100%;
            }
            h1 { margin: 0 0 20px; font-size: 24px; }
            input {
                width: 100%;
                padding: 12px;
                margin: 10px 0;
                border: 1px solid #333;
                border-radius: 6px;
                background: #0f0f23;
                color: #eee;
                font-size: 16px;
            }
            button {
                width: 100%;
                padding: 12px;
                background: #7c3aed;
                color: white;
                border: none;
                border-radius: 6px;
                cursor: pointer;
                font-size: 16px;
                margin-top: 10px;
            }
            button:hover { background: #6d28d9; }
            .error { color: #ef4444; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h1>🔒 Admin Login</h1>
            <form method="POST" action="/admin/login">
                <input type="password" name="password" placeholder="Password" required autofocus>
                <button type="submit">Login</button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@admin_router.post("/login")
async def login(password: Annotated[str, Form()], response: Response) -> RedirectResponse:
    """Process login."""
    _check_password_configured()

    if not secrets.compare_digest(password, settings.admin_password):
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Familiar Cache - Admin Login</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * { box-sizing: border-box; }
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: #1a1a2e;
                    color: #eee;
                    margin: 0;
                    padding: 20px;
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                .login-box {
                    background: #16213e;
                    padding: 40px;
                    border-radius: 12px;
                    max-width: 400px;
                    width: 100%;
                }
                h1 { margin: 0 0 20px; font-size: 24px; }
                input {
                    width: 100%;
                    padding: 12px;
                    margin: 10px 0;
                    border: 1px solid #333;
                    border-radius: 6px;
                    background: #0f0f23;
                    color: #eee;
                    font-size: 16px;
                }
                button {
                    width: 100%;
                    padding: 12px;
                    background: #7c3aed;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    cursor: pointer;
                    font-size: 16px;
                    margin-top: 10px;
                }
                button:hover { background: #6d28d9; }
                .error { color: #ef4444; margin-top: 10px; }
            </style>
        </head>
        <body>
            <div class="login-box">
                <h1>🔒 Admin Login</h1>
                <form method="POST" action="/admin/login">
                    <input type="password" name="password" placeholder="Password" required autofocus>
                    <button type="submit">Login</button>
                </form>
                <p class="error">Invalid password</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html, status_code=401)

    # Create session
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

    # Get stats
    embedding_count = (await db.execute(select(func.count()).select_from(Embedding))).scalar() or 0
    features_count = (await db.execute(select(func.count()).select_from(Features))).scalar() or 0
    analysis_detail_count = (await db.execute(select(func.count()).select_from(AnalysisDetail))).scalar() or 0
    banned_count = (await db.execute(
        select(func.count()).select_from(BannedIP).where(BannedIP.is_active == True)  # noqa: E712
    )).scalar() or 0

    # Get unique IPs (from ip_stats)
    unique_ips = (await db.execute(select(func.count()).select_from(IPStats))).scalar() or 0

    # Get top contributors (by contributions)
    top_contributors = (await db.execute(
        select(IPStats)
        .order_by(IPStats.total_contributions.desc())
        .limit(20)
    )).scalars().all()

    # Get flagged IPs
    flagged_ips = (await db.execute(
        select(IPStats)
        .where(IPStats.flagged == True)  # noqa: E712
        .order_by(IPStats.last_seen.desc())
        .limit(20)
    )).scalars().all()

    # Get banned IPs
    banned_ips = (await db.execute(
        select(BannedIP)
        .where(BannedIP.is_active == True)  # noqa: E712
        .order_by(BannedIP.banned_at.desc())
        .limit(50)
    )).scalars().all()

    # Get recent activity (IPs with recent activity)
    recent_ips = (await db.execute(
        select(IPStats)
        .order_by(IPStats.last_seen.desc())
        .limit(20)
    )).scalars().all()

    # Build HTML
    def format_ip_row(ip: IPStats, show_ban: bool = True) -> str:
        hit_rate = (ip.lookup_hits / ip.total_lookups * 100) if ip.total_lookups > 0 else 0
        flagged_badge = '<span class="badge flagged">⚠ Flagged</span>' if ip.flagged else ''
        ban_btn = f'''<form method="POST" action="/admin/ban" style="display:inline">
            <input type="hidden" name="ip_address" value="{ip.ip_address}">
            <button type="submit" class="btn-ban" onclick="return confirm('Ban {ip.ip_address}?')">Ban</button>
        </form>''' if show_ban else ''

        return f'''<tr>
            <td><code>{ip.ip_address}</code> {flagged_badge}</td>
            <td>{ip.total_lookups:,}</td>
            <td>{ip.total_contributions:,}</td>
            <td>{hit_rate:.1f}%</td>
            <td>{ip.last_seen.strftime('%Y-%m-%d %H:%M')}</td>
            <td>{ban_btn}</td>
        </tr>'''

    def format_banned_row(ban: BannedIP) -> str:
        return f'''<tr>
            <td><code>{ban.ip_address}</code></td>
            <td>{ban.reason or '-'}</td>
            <td>{ban.banned_at.strftime('%Y-%m-%d %H:%M')}</td>
            <td>
                <form method="POST" action="/admin/unban" style="display:inline">
                    <input type="hidden" name="ip_address" value="{ban.ip_address}">
                    <button type="submit" class="btn-unban">Unban</button>
                </form>
            </td>
        </tr>'''

    recent_rows = '\n'.join(format_ip_row(ip) for ip in recent_ips)
    top_rows = '\n'.join(format_ip_row(ip) for ip in top_contributors)
    flagged_rows = '\n'.join(format_ip_row(ip) for ip in flagged_ips) if flagged_ips else '<tr><td colspan="6">No flagged IPs</td></tr>'
    banned_rows = '\n'.join(format_banned_row(ban) for ban in banned_ips) if banned_ips else '<tr><td colspan="4">No banned IPs</td></tr>'

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Familiar Cache - Admin</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a2e;
                color: #eee;
                margin: 0;
                padding: 20px;
            }}
            .container {{ max-width: 1400px; margin: 0 auto; }}
            h1 {{ margin: 0 0 20px; display: flex; align-items: center; gap: 10px; }}
            h2 {{ margin: 30px 0 15px; color: #a78bfa; }}
            .stats {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            .stat {{
                background: #16213e;
                padding: 20px;
                border-radius: 12px;
            }}
            .stat-value {{ font-size: 32px; font-weight: bold; color: #7c3aed; }}
            .stat-label {{ color: #888; margin-top: 5px; }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: #16213e;
                border-radius: 12px;
                overflow: hidden;
            }}
            th, td {{ padding: 12px 15px; text-align: left; }}
            th {{ background: #0f0f23; color: #7c3aed; }}
            tr:nth-child(even) {{ background: #1a1a3e; }}
            code {{ background: #0f0f23; padding: 2px 6px; border-radius: 4px; }}
            .badge {{
                display: inline-block;
                padding: 2px 8px;
                border-radius: 4px;
                font-size: 12px;
                margin-left: 8px;
            }}
            .flagged {{ background: #fbbf24; color: #000; }}
            .btn-ban {{
                background: #ef4444;
                color: white;
                border: none;
                padding: 4px 10px;
                border-radius: 4px;
                cursor: pointer;
            }}
            .btn-unban {{
                background: #22c55e;
                color: white;
                border: none;
                padding: 4px 10px;
                border-radius: 4px;
                cursor: pointer;
            }}
            .btn-logout {{
                background: #333;
                color: #eee;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                text-decoration: none;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }}
            .section {{ margin-bottom: 40px; }}
            .tabs {{
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
            }}
            .tab {{
                padding: 8px 16px;
                background: #16213e;
                border-radius: 6px;
                cursor: pointer;
                border: none;
                color: #888;
            }}
            .tab.active {{ background: #7c3aed; color: white; }}
            .tab-content {{ display: none; }}
            .tab-content.active {{ display: block; }}
            .ban-form {{
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
                padding: 15px;
                background: #16213e;
                border-radius: 8px;
            }}
            .ban-form input {{
                padding: 8px 12px;
                border: 1px solid #333;
                border-radius: 4px;
                background: #0f0f23;
                color: #eee;
            }}
            .ban-form input[name="ip_address"] {{ width: 200px; }}
            .ban-form input[name="reason"] {{ flex: 1; }}
            .ban-form button {{
                background: #ef4444;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                cursor: pointer;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎵 Familiar Cache Admin</h1>
                <a href="/admin/logout" class="btn-logout">Logout</a>
            </div>

            <div class="stats">
                <div class="stat">
                    <div class="stat-value">{embedding_count:,}</div>
                    <div class="stat-label">Embeddings</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{features_count:,}</div>
                    <div class="stat-label">Features</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{analysis_detail_count:,}</div>
                    <div class="stat-label">Analysis Details</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{unique_ips:,}</div>
                    <div class="stat-label">Unique IPs</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{banned_count:,}</div>
                    <div class="stat-label">Banned IPs</div>
                </div>
            </div>

            <div class="tabs">
                <button class="tab active" onclick="showTab('recent')">Recent Activity</button>
                <button class="tab" onclick="showTab('top')">Top Contributors</button>
                <button class="tab" onclick="showTab('flagged')">Flagged</button>
                <button class="tab" onclick="showTab('banned')">Banned IPs</button>
            </div>

            <div id="recent" class="tab-content active">
                <table>
                    <thead>
                        <tr>
                            <th>IP Address</th>
                            <th>Lookups</th>
                            <th>Contributions</th>
                            <th>Hit Rate</th>
                            <th>Last Seen</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {recent_rows}
                    </tbody>
                </table>
            </div>

            <div id="top" class="tab-content">
                <table>
                    <thead>
                        <tr>
                            <th>IP Address</th>
                            <th>Lookups</th>
                            <th>Contributions</th>
                            <th>Hit Rate</th>
                            <th>Last Seen</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {top_rows}
                    </tbody>
                </table>
            </div>

            <div id="flagged" class="tab-content">
                <table>
                    <thead>
                        <tr>
                            <th>IP Address</th>
                            <th>Lookups</th>
                            <th>Contributions</th>
                            <th>Hit Rate</th>
                            <th>Last Seen</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {flagged_rows}
                    </tbody>
                </table>
            </div>

            <div id="banned" class="tab-content">
                <h3>Ban IP Address</h3>
                <form class="ban-form" method="POST" action="/admin/ban">
                    <input type="text" name="ip_address" placeholder="IP Address" required>
                    <input type="text" name="reason" placeholder="Reason (optional)">
                    <button type="submit">Ban IP</button>
                </form>

                <table>
                    <thead>
                        <tr>
                            <th>IP Address</th>
                            <th>Reason</th>
                            <th>Banned At</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {banned_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            function showTab(name) {{
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
                document.getElementById(name).classList.add('active');
                event.target.classList.add('active');
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


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

    # Check if already banned
    existing = await db.execute(
        select(BannedIP).where(BannedIP.ip_address == ip_address)
    )
    ban = existing.scalar_one_or_none()

    if ban:
        # Reactivate if exists
        ban.is_active = True
        ban.reason = reason
        ban.banned_at = datetime.utcnow()
    else:
        # Create new ban
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
