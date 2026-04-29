"""Public browse routes for the cache server.

Renders HTML pages so anyone can see what's in the community cache.
All stored data is already anonymized (SHA256 fingerprint hashes), so
public browsing is consistent with the project's "community cache" framing.
"""

import json
import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, tuple_

from app.api.deps import DbSession
from app.config import settings
from app.db.models import AnalysisDetail, Embedding, Features
from app.limiter import limiter
from app.templates import fmt_vec_preview, templates

browse_router = APIRouter(tags=["browse"])

PAGE_SIZE = 50
MAX_PAGE = 200  # caps drive-by enumeration to ~10K rows
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


@browse_router.get("/", response_class=HTMLResponse)
@limiter.limit(settings.lookup_rate_limit)
async def index(
    request: Request,
    db: DbSession,
    page: int = Query(1, ge=1, le=MAX_PAGE),
    sort: str = Query("recent", pattern="^(recent|popular|accessed)$"),
    q: str | None = Query(None, max_length=64),
) -> HTMLResponse:
    """Public landing page: stats + paginated browsable embeddings table."""
    embedding_count = (await db.execute(select(func.count()).select_from(Embedding))).scalar() or 0
    track_count = (await db.execute(select(func.count(func.distinct(Embedding.fingerprint_hash))))).scalar() or 0
    features_count = (await db.execute(select(func.count()).select_from(Features))).scalar() or 0
    detail_count = (await db.execute(select(func.count()).select_from(AnalysisDetail))).scalar() or 0

    base = select(Embedding)
    if q:
        if not HEX_RE.match(q):
            raise HTTPException(status_code=422, detail="Search must be hex (0-9, a-f)")
        base = base.where(Embedding.fingerprint_hash.like(f"{q.lower()}%"))

    if sort == "popular":
        base = base.order_by(Embedding.contributor_count.desc(), Embedding.created_at.desc())
    elif sort == "accessed":
        base = base.order_by(Embedding.last_accessed_at.desc())
    else:
        base = base.order_by(Embedding.created_at.desc())

    offset = (page - 1) * PAGE_SIZE
    rows = (await db.execute(base.offset(offset).limit(PAGE_SIZE + 1))).scalars().all()
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]

    # Batch lookups for has_features / has_detail (avoid N+1)
    pairs = {(r.fingerprint_hash, r.analysis_version) for r in rows}
    feat_keys: set[str] = set()
    detail_keys: set[str] = set()
    if pairs:
        pair_list = list(pairs)
        feat_rows = (await db.execute(
            select(Features.fingerprint_hash, Features.analysis_version).where(
                tuple_(Features.fingerprint_hash, Features.analysis_version).in_(pair_list)
            )
        )).all()
        feat_keys = {f"{h}:{v}" for h, v in feat_rows}
        detail_rows = (await db.execute(
            select(AnalysisDetail.fingerprint_hash, AnalysisDetail.analysis_version).where(
                tuple_(AnalysisDetail.fingerprint_hash, AnalysisDetail.analysis_version).in_(pair_list)
            )
        )).all()
        detail_keys = {f"{h}:{v}" for h, v in detail_rows}

    qs_parts = []
    if q:
        qs_parts.append(f"q={q}")
    if sort != "recent":
        qs_parts.append(f"sort={sort}")
    qs_suffix = ("&" + "&".join(qs_parts)) if qs_parts else ""

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "stats": {
                "embeddings": embedding_count,
                "tracks": track_count,
                "features": features_count,
                "details": detail_count,
            },
            "rows": rows,
            "feat_keys": feat_keys,
            "detail_keys": detail_keys,
            "page": page,
            "page_size": PAGE_SIZE,
            "max_page": MAX_PAGE,
            "has_next": has_next,
            "sort": sort,
            "q": q,
            "qs_suffix": qs_suffix,
        },
    )


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
