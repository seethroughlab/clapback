"""Jinja2 template engine singleton, and the site's one description of itself.

Imported by routers that render HTML pages, and by `main.py` for the OpenAPI
description, so the sentence that says what this is lives in one place.
"""

from datetime import datetime

from fastapi.templating import Jinja2Templates

#: Said once, used everywhere: the header, `<meta name="description">`, the OG
#: card, the OpenAPI description. The sentence is the pitch `ADR-0022` decided:
#: discovery past the edge of one library, promised as *how it sounds* and
#: never as what you will like. Saved compute is stated after, not here.
SITE = {
    "name": "clapback",
    "tagline": "Find what you don’t own, by how it sounds.",
    "description": (
        "A public commons of CLAP audio embeddings: one 512-float vector per recording, "
        "keyed on a hash any tool can reproduce from the audio. Ask what sounds like a "
        "track you own and be shown recordings you don’t, from every library that has "
        "plugged in; contribute what yours computes and it is what others can be shown. "
        "Reads need no key; contribution is open and opt-in."
    ),
    "url": "https://clapback.seethroughlab.com",
}


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


def _fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fmt_dt"] = _fmt_dt
templates.env.filters["fmt_pct"] = _fmt_pct
templates.env.globals["site"] = SITE
