"""Public browse routes for the cache server.

`/`        — community dashboard (stats + charts).
`/browse/<fingerprint_hash>` — direct lookup of a specific fingerprint
                               (URL is paste-friendly for developers).
"""

import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession
from app.cache import stats_cache
from app.config import settings
from app.db.models import AnalysisDetail, Embedding, Features, IPStats
from app.limiter import limiter
from app.templates import fmt_vec_preview, templates

browse_router = APIRouter(tags=["browse"])

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
SECONDS_PER_ANALYSIS = 15  # rough CLAP analysis time on a typical client


# ---------- aggregation helpers (each cached separately under stats_cache) ----------


async def _fetch_counts(db: AsyncSession) -> dict:
    """Row counts for the API page. Two cheap aggregates, not the dashboard's full set."""
    embeddings = await db.scalar(select(func.count()).select_from(Embedding))
    features = await db.scalar(select(func.count()).select_from(Features))
    return {"embeddings": embeddings or 0, "features": features or 0}


async def _fetch_top_stats(db: AsyncSession) -> dict:
    """Headline numbers: tracks, analyses, hours saved, hit rate."""
    tracks = (await db.execute(
        select(func.count(func.distinct(Embedding.fingerprint_hash)))
    )).scalar() or 0
    analyses = (await db.execute(select(func.count()).select_from(Features))).scalar() or 0
    hits = (await db.execute(
        select(func.coalesce(func.sum(IPStats.lookup_hits), 0))
    )).scalar() or 0
    total = (await db.execute(
        select(func.coalesce(func.sum(IPStats.total_lookups), 0))
    )).scalar() or 0
    hit_rate = (hits / total) if total else 0.0
    hours_saved = hits * SECONDS_PER_ANALYSIS / 3600
    return {
        "tracks": int(tracks),
        "analyses": int(analyses),
        "hours_saved": float(hours_saved),
        "hit_rate": float(hit_rate),
    }


async def _fetch_velocity(db: AsyncSession) -> dict:
    last_24h = (await db.execute(
        select(func.count()).select_from(Embedding)
        .where(Embedding.created_at > func.now() - text("interval '24 hours'"))
    )).scalar() or 0
    last_7d = (await db.execute(
        select(func.count()).select_from(Embedding)
        .where(Embedding.created_at > func.now() - text("interval '7 days'"))
    )).scalar() or 0
    return {"last_24h": int(last_24h), "last_7d": int(last_7d)}


async def _fetch_growth(db: AsyncSession) -> list[dict]:
    """Daily-bucketed cumulative count for the last 90 days."""
    rows = (await db.execute(text("""
        SELECT day, sum(daily_count) OVER (ORDER BY day) AS cumulative
        FROM (
            SELECT date_trunc('day', created_at) AS day, count(*) AS daily_count
            FROM embeddings
            WHERE created_at > now() - interval '90 days'
            GROUP BY 1
        ) t
        ORDER BY day
    """))).all()
    return [{"day": r.day.date().isoformat(), "cumulative": int(r.cumulative)} for r in rows]


async def _fetch_bpm_histogram(db: AsyncSession) -> list[dict]:
    """10-bucket histogram from 60..200 BPM. Skips rows without bpm."""
    rows = (await db.execute(text("""
        SELECT width_bucket((features->>'bpm')::float, 60, 200, 10) AS bucket,
               count(*) AS n
        FROM features
        WHERE features ? 'bpm' AND (features->>'bpm') ~ '^-?[0-9.]+$'
        GROUP BY 1
        ORDER BY 1
    """))).all()
    return [{"bucket": int(r.bucket), "n": int(r.n)} for r in rows]


async def _fetch_key_distribution(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT features->>'key' AS k, count(*) AS n
        FROM features
        WHERE features ? 'key' AND (features->>'key') <> ''
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 12
    """))).all()
    return [{"key": r.k, "n": int(r.n)} for r in rows]


async def _fetch_mood_grid(db: AsyncSession) -> list[dict]:
    """10×10 valence × energy density grid for a heatmap."""
    rows = (await db.execute(text("""
        SELECT width_bucket((features->>'valence')::float, 0, 1, 10) AS vx,
               width_bucket((features->>'energy')::float,  0, 1, 10) AS ex,
               count(*) AS n
        FROM features
        WHERE features ? 'valence' AND features ? 'energy'
          AND (features->>'valence') ~ '^-?[0-9.]+$'
          AND (features->>'energy')  ~ '^-?[0-9.]+$'
        GROUP BY 1, 2
    """))).all()
    return [{"vx": int(r.vx), "ex": int(r.ex), "n": int(r.n)} for r in rows]


# ---------- routes ----------


@browse_router.get("/", response_class=HTMLResponse)
@limiter.limit(settings.lookup_rate_limit)
async def index(request: Request, db: DbSession) -> HTMLResponse:
    """Public dashboard: cache size, growth, musical landscape."""
    top = await stats_cache.get_or_compute("top_stats", lambda: _fetch_top_stats(db))
    velocity = await stats_cache.get_or_compute("velocity", lambda: _fetch_velocity(db))
    growth = await stats_cache.get_or_compute("growth", lambda: _fetch_growth(db))
    bpm = await stats_cache.get_or_compute("bpm_hist", lambda: _fetch_bpm_histogram(db))
    keys = await stats_cache.get_or_compute("key_dist", lambda: _fetch_key_distribution(db))
    mood = await stats_cache.get_or_compute("mood_grid", lambda: _fetch_mood_grid(db))

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "top": top,
            "velocity": velocity,
            "growth": growth,
            "bpm": bpm,
            "keys": keys,
            "mood": mood,
        },
    )


@browse_router.get("/api", response_class=HTMLResponse)
async def api_page(request: Request, db: DbSession) -> HTMLResponse:
    """The page a developer lands on, as opposed to the schema at `/docs`.

    `/docs` answers "what are the fields"; nobody arrives with that question. They
    arrive asking what this is, whether it costs anything, and what to do when the
    corpus does not have their track — which is the question the published package
    answers and the schema cannot.
    """
    counts = await _fetch_counts(db)
    return templates.TemplateResponse(
        request,
        "api.html",
        {
            "counts": counts,
            "host": request.url.hostname or "clapback.seethroughlab.com",
            "lookup_limit": settings.lookup_rate_limit.replace("/", " per "),
        },
    )


@browse_router.get("/map", response_class=HTMLResponse)
async def map_page(request: Request) -> HTMLResponse:
    """The corpus as a picture.

    Serves a template and a static artifact; the projection itself is computed
    offline by `scripts/build_map.py`, because UMAP's dependencies have no
    business on an instance sized for a vector index (`ADR-0001` point 3).
    """
    return templates.TemplateResponse(request, "map.html", {})


@browse_router.get("/browse/{fingerprint_hash}", response_class=HTMLResponse)
@limiter.limit(settings.lookup_rate_limit)
async def detail(
    request: Request,
    fingerprint_hash: str,
    db: DbSession,
) -> HTMLResponse:
    """Detail view for a single fingerprint: all embeddings, features, and analysis detail."""
    if len(fingerprint_hash) != 64 or not HEX_RE.match(fingerprint_hash):
        raise HTTPException(status_code=422, detail="Invalid fingerprint hash")

    fp = fingerprint_hash.lower()

    embeddings = (await db.execute(
        select(Embedding)
        .where(Embedding.fingerprint_hash == fp)
        .order_by(Embedding.analysis_version.desc(), Embedding.clap_model_version)
    )).scalars().all()

    if not embeddings:
        raise HTTPException(status_code=404, detail="Fingerprint not found")

    features = (await db.execute(
        select(Features)
        .where(Features.fingerprint_hash == fp)
        .order_by(Features.analysis_version.desc())
    )).scalars().all()

    details = (await db.execute(
        select(AnalysisDetail)
        .where(AnalysisDetail.fingerprint_hash == fp)
        .order_by(AnalysisDetail.analysis_version.desc())
    )).scalars().all()

    feat_by_av = {f.analysis_version: f for f in features}
    detail_by_av = {d.analysis_version: d for d in details}

    embeddings_by_av: dict[int, list[Embedding]] = {}
    for e in embeddings:
        embeddings_by_av.setdefault(e.analysis_version, []).append(e)

    versions = []
    for av in sorted(embeddings_by_av.keys(), reverse=True):
        emb_views = []
        for e in embeddings_by_av[av]:
            vec = [float(x) for x in e.embedding] if e.embedding is not None else []
            emb_views.append({
                "clap_model_version": e.clap_model_version,
                # Null on every row contributed before `ADR-0006` phase 2, which
                # is the whole corpus today. The template says so rather than
                # omitting the line, because "not recorded" is the interesting
                # fact about these rows.
                "pipeline_version": e.pipeline_version,
                "contributor_count": e.contributor_count,
                "dims": len(vec),
                "preview": fmt_vec_preview(vec),
                "vec_json": json.dumps(vec),
                "created_at": e.created_at,
                "last_accessed_at": e.last_accessed_at,
            })

        feat = feat_by_av.get(av)
        det = detail_by_av.get(av)
        det_json = json.dumps(det.detail, indent=2, sort_keys=True) if det else None
        versions.append({
            "av": av,
            "embeddings": emb_views,
            "features": feat,
            "features_json": json.dumps(feat.features, indent=2, sort_keys=True) if feat else None,
            "detail": det,
            "detail_json": det_json,
            "detail_size_kb": (len(det_json) / 1024) if det_json else None,
        })

    return templates.TemplateResponse(
        request,
        "browse_detail.html",
        {
            "fingerprint_hash": fp,
            "totals": {
                "embeddings": len(embeddings),
                "features": len(features),
                "details": len(details),
            },
            "versions": versions,
        },
    )
