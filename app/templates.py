"""Jinja2 template engine singleton.

Imported by routers that render HTML pages.
"""

from datetime import datetime

from fastapi.templating import Jinja2Templates


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_vec_preview(vec: list[float], n: int = 8) -> str:
    """First n + last n dims as a compact preview string."""
    if len(vec) <= 2 * n:
        return ", ".join(f"{x:+.4f}" for x in vec)
    head = ", ".join(f"{x:+.4f}" for x in vec[:n])
    tail = ", ".join(f"{x:+.4f}" for x in vec[-n:])
    return f"{head}, … , {tail}"


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fmt_dt"] = _fmt_dt
