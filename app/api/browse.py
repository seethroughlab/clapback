"""Public browse routes for the cache server.

Renders HTML pages so anyone can see what's in the community cache.
All stored data is already anonymized (SHA256 fingerprint hashes), so
public browsing is consistent with the project's "community cache" framing.
"""

import html
import json
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, tuple_

from app.api.deps import DbSession
from app.config import settings
from app.db.models import AnalysisDetail, Embedding, Features
from app.limiter import limiter

browse_router = APIRouter(tags=["browse"])

PAGE_SIZE = 50
MAX_PAGE = 200  # caps drive-by enumeration to ~10K rows
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


# ---------- shared style ----------

_BASE_CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1a2e;
    color: #eee;
    margin: 0;
    padding: 20px;
}
.container { max-width: 1400px; margin: 0 auto; }
a { color: #a78bfa; text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { margin: 0 0 10px; display: flex; align-items: center; gap: 10px; font-size: 28px; }
h2 { margin: 30px 0 15px; color: #a78bfa; }
h3 { margin: 20px 0 10px; color: #c4b5fd; font-size: 16px; }
.tagline { color: #aaa; margin: 0 0 20px; }
.header-links { display: flex; gap: 16px; font-size: 14px; margin-bottom: 24px; }
.stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 30px;
}
.stat { background: #16213e; padding: 20px; border-radius: 12px; }
.stat-value { font-size: 32px; font-weight: bold; color: #7c3aed; }
.stat-label { color: #888; margin-top: 5px; }
table {
    width: 100%;
    border-collapse: collapse;
    background: #16213e;
    border-radius: 12px;
    overflow: hidden;
}
th, td { padding: 10px 14px; text-align: left; }
th { background: #0f0f23; color: #7c3aed; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }
tr:nth-child(even) { background: #1a1a3e; }
code { background: #0f0f23; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
.controls {
    display: flex;
    gap: 10px;
    margin-bottom: 16px;
    flex-wrap: wrap;
    align-items: center;
}
.controls input, .controls select {
    padding: 8px 12px;
    border: 1px solid #333;
    border-radius: 6px;
    background: #0f0f23;
    color: #eee;
    font-size: 14px;
}
.controls input { width: 320px; }
.controls button {
    padding: 8px 16px;
    background: #7c3aed;
    color: white;
    border: none;
    border-radius: 6px;
    cursor: pointer;
}
.controls button:hover { background: #6d28d9; }
.pagination {
    display: flex;
    gap: 8px;
    margin-top: 16px;
    align-items: center;
    color: #888;
}
.pagination a, .pagination span {
    padding: 6px 12px;
    border-radius: 6px;
    background: #16213e;
}
.pagination .current { background: #7c3aed; color: white; }
.pagination .disabled { opacity: 0.4; pointer-events: none; }
.yes { color: #22c55e; font-weight: bold; }
.no { color: #444; }
.muted { color: #888; font-size: 13px; }
.empty {
    background: #16213e;
    padding: 40px;
    border-radius: 12px;
    text-align: center;
    color: #888;
}
pre {
    background: #0f0f23;
    padding: 16px;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.5;
    max-height: 400px;
}
details { background: #16213e; border-radius: 8px; padding: 12px 16px; margin: 12px 0; }
details > summary { cursor: pointer; color: #a78bfa; user-select: none; }
details[open] > summary { margin-bottom: 12px; }
.vector-row {
    background: #16213e;
    padding: 16px;
    border-radius: 8px;
    margin-bottom: 12px;
}
.vector-meta { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 12px; font-size: 14px; }
.vector-meta strong { color: #a78bfa; }
.back-link { display: inline-block; margin-bottom: 16px; }
"""


def _layout(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>{html.escape(title)}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{_BASE_CSS}</style>
</head>
<body>
    <div class="container">{body}</div>
</body>
</html>"""


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_vec_preview(vec: list[float], n: int = 8) -> str:
    if len(vec) <= 2 * n:
        return ", ".join(f"{x:+.4f}" for x in vec)
    head = ", ".join(f"{x:+.4f}" for x in vec[:n])
    tail = ", ".join(f"{x:+.4f}" for x in vec[-n:])
    return f"{head}, … , {tail}"


# ---------- routes ----------


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

    # Stats
    embedding_count = (await db.execute(select(func.count()).select_from(Embedding))).scalar() or 0
    track_count = (await db.execute(select(func.count(func.distinct(Embedding.fingerprint_hash))))).scalar() or 0
    features_count = (await db.execute(select(func.count()).select_from(Features))).scalar() or 0
    detail_count = (await db.execute(select(func.count()).select_from(AnalysisDetail))).scalar() or 0

    # Build query with optional hex prefix filter
    base = select(Embedding)
    if q:
        if not HEX_RE.match(q):
            raise HTTPException(status_code=422, detail="Search must be hex (0-9, a-f)")
        base = base.where(Embedding.fingerprint_hash.like(f"{q.lower()}%"))

    if sort == "popular":
        base = base.order_by(Embedding.contributor_count.desc(), Embedding.created_at.desc())
    elif sort == "accessed":
        base = base.order_by(Embedding.last_accessed_at.desc())
    else:  # recent
        base = base.order_by(Embedding.created_at.desc())

    offset = (page - 1) * PAGE_SIZE
    rows = (await db.execute(base.offset(offset).limit(PAGE_SIZE + 1))).scalars().all()
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]

    # Batch lookups for has_features / has_detail (avoid N+1)
    keys = {(r.fingerprint_hash, r.analysis_version) for r in rows}
    feat_keys: set[tuple[str, int]] = set()
    detail_keys: set[tuple[str, int]] = set()
    if keys:
        key_list = list(keys)
        feat_rows = (await db.execute(
            select(Features.fingerprint_hash, Features.analysis_version).where(
                tuple_(Features.fingerprint_hash, Features.analysis_version).in_(key_list)
            )
        )).all()
        feat_keys = {(h, v) for h, v in feat_rows}
        detail_rows = (await db.execute(
            select(AnalysisDetail.fingerprint_hash, AnalysisDetail.analysis_version).where(
                tuple_(AnalysisDetail.fingerprint_hash, AnalysisDetail.analysis_version).in_(key_list)
            )
        )).all()
        detail_keys = {(h, v) for h, v in detail_rows}

    # Render rows
    def row_html(e: Embedding) -> str:
        h_short = html.escape(e.fingerprint_hash[:12])
        h_full = html.escape(e.fingerprint_hash)
        cmv = html.escape(e.clap_model_version)
        if len(cmv) > 36:
            cmv_disp = f'<span title="{cmv}">{cmv[:33]}…</span>'
        else:
            cmv_disp = cmv
        has_feat = '<span class="yes">●</span>' if (e.fingerprint_hash, e.analysis_version) in feat_keys else '<span class="no">○</span>'
        has_det = '<span class="yes">●</span>' if (e.fingerprint_hash, e.analysis_version) in detail_keys else '<span class="no">○</span>'
        return (
            f'<tr>'
            f'<td><a href="/browse/{h_full}"><code>{h_short}…</code></a></td>'
            f'<td>{e.analysis_version}</td>'
            f'<td><code>{cmv_disp}</code></td>'
            f'<td>{e.contributor_count:,}</td>'
            f'<td style="text-align:center">{has_feat}</td>'
            f'<td style="text-align:center">{has_det}</td>'
            f'<td class="muted">{_fmt_dt(e.created_at)}</td>'
            f'<td class="muted">{_fmt_dt(e.last_accessed_at)}</td>'
            f'</tr>'
        )

    if rows:
        table_body = "\n".join(row_html(e) for e in rows)
        table_html = f"""
        <table>
            <thead>
                <tr>
                    <th>Fingerprint</th>
                    <th>Analysis v.</th>
                    <th>CLAP model</th>
                    <th>Contributors</th>
                    <th title="Has audio features">Feat.</th>
                    <th title="Has analysis detail">Det.</th>
                    <th>Created</th>
                    <th>Last accessed</th>
                </tr>
            </thead>
            <tbody>{table_body}</tbody>
        </table>
        """
    else:
        msg = "No embeddings match your search." if q else "Cache is empty — be the first to contribute!"
        table_html = f'<div class="empty">{html.escape(msg)}</div>'

    # Pagination
    qs_parts = []
    if q:
        qs_parts.append(f"q={html.escape(q)}")
    if sort != "recent":
        qs_parts.append(f"sort={sort}")
    base_qs = ("&" + "&".join(qs_parts)) if qs_parts else ""

    prev_cls = "disabled" if page <= 1 else ""
    next_cls = "disabled" if not has_next or page >= MAX_PAGE else ""
    pagination = f"""
    <div class="pagination">
        <a class="{prev_cls}" href="?page={page - 1}{base_qs}">← Prev</a>
        <span class="current">Page {page}</span>
        <a class="{next_cls}" href="?page={page + 1}{base_qs}">Next →</a>
        <span class="muted" style="margin-left: auto">Page size {PAGE_SIZE} · max page {MAX_PAGE}</span>
    </div>
    """

    # Sort selector preserves q
    def sort_opt(val: str, label: str) -> str:
        sel = " selected" if sort == val else ""
        return f'<option value="{val}"{sel}>{label}</option>'

    q_val = html.escape(q) if q else ""
    controls = f"""
    <form class="controls" method="GET" action="/">
        <input type="text" name="q" placeholder="Search by fingerprint hash prefix (hex)" value="{q_val}" pattern="[0-9a-fA-F]*" maxlength="64">
        <select name="sort">
            {sort_opt("recent", "Recent")}
            {sort_opt("popular", "Most contributors")}
            {sort_opt("accessed", "Recently accessed")}
        </select>
        <button type="submit">Apply</button>
    </form>
    """

    body = f"""
    <h1>🎵 Familiar Cache</h1>
    <p class="tagline">Community embedding cache for the <a href="https://github.com/seethroughlab/familiar">Familiar music player</a>. All entries are anonymous SHA256 fingerprint hashes.</p>
    <div class="header-links">
        <a href="/docs">API docs</a>
        <a href="https://github.com/seethroughlab/familiar-cache">GitHub</a>
        <a href="/health/db">Health</a>
    </div>

    <div class="stats">
        <div class="stat"><div class="stat-value">{embedding_count:,}</div><div class="stat-label">Embeddings</div></div>
        <div class="stat"><div class="stat-value">{track_count:,}</div><div class="stat-label">Unique tracks</div></div>
        <div class="stat"><div class="stat-value">{features_count:,}</div><div class="stat-label">Feature sets</div></div>
        <div class="stat"><div class="stat-value">{detail_count:,}</div><div class="stat-label">Analysis details</div></div>
    </div>

    {controls}
    {table_html}
    {pagination}
    """
    return HTMLResponse(content=_layout("Familiar Cache", body))


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

    # Group embeddings by analysis_version
    versions: dict[int, list[Embedding]] = {}
    for e in embeddings:
        versions.setdefault(e.analysis_version, []).append(e)

    sections = []
    for av in sorted(versions.keys(), reverse=True):
        embs = versions[av]
        embs_html_parts = []
        for e in embs:
            vec = [float(x) for x in e.embedding] if e.embedding is not None else []
            preview = _fmt_vec_preview(vec)
            full_json = html.escape(json.dumps(vec))
            embs_html_parts.append(f"""
            <div class="vector-row">
                <div class="vector-meta">
                    <span><strong>CLAP model:</strong> <code>{html.escape(e.clap_model_version)}</code></span>
                    <span><strong>Contributors:</strong> {e.contributor_count:,}</span>
                    <span><strong>Dims:</strong> {len(vec)}</span>
                    <span class="muted">Created {_fmt_dt(e.created_at)} · Last accessed {_fmt_dt(e.last_accessed_at)}</span>
                </div>
                <div class="muted" style="margin-bottom:8px">Embedding preview (first 8 + last 8):</div>
                <pre>{html.escape(preview)}</pre>
                <details>
                    <summary>Show full vector ({len(vec)} dims)</summary>
                    <pre>{full_json}</pre>
                </details>
            </div>
            """)
        embs_html = "\n".join(embs_html_parts)

        section_parts = [f"<h2>Analysis version {av}</h2>", "<h3>Embeddings</h3>", embs_html]

        if av in feat_by_av:
            f = feat_by_av[av]
            features_json = html.escape(json.dumps(f.features, indent=2, sort_keys=True))
            section_parts.append(f"""
            <h3>Features <span class="muted">· {f.contributor_count:,} contributors · created {_fmt_dt(f.created_at)}</span></h3>
            <pre>{features_json}</pre>
            """)
        else:
            section_parts.append('<h3>Features</h3><div class="muted">No features recorded for this version.</div>')

        if av in detail_by_av:
            d = detail_by_av[av]
            detail_json = html.escape(json.dumps(d.detail, indent=2, sort_keys=True))
            size_kb = len(detail_json) / 1024
            section_parts.append(f"""
            <h3>Analysis detail <span class="muted">· {d.contributor_count:,} contributors · {size_kb:.1f} KB · created {_fmt_dt(d.created_at)}</span></h3>
            <details>
                <summary>Show full analysis detail JSON</summary>
                <pre>{detail_json}</pre>
            </details>
            """)
        else:
            section_parts.append('<h3>Analysis detail</h3><div class="muted">No analysis detail recorded for this version.</div>')

        sections.append("\n".join(section_parts))

    sections_html = "\n".join(sections)
    body = f"""
    <a class="back-link" href="/">← Back to index</a>
    <h1>Fingerprint detail</h1>
    <p><code style="font-size: 14px">{html.escape(fp)}</code></p>
    <p class="muted">{len(embeddings)} embedding row(s) · {len(features)} feature set(s) · {len(details)} analysis detail(s)</p>
    {sections_html}
    """
    return HTMLResponse(content=_layout(f"Fingerprint {fp[:12]}… · Familiar Cache", body))
