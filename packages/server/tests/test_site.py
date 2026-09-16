"""The public site: what it says, what it carries, and what it no longer draws.

Rendered through the real Jinja environment with fake contexts, no database.
What must hold: the landing page makes the maintainer's case in the words the
records use; every public page carries an identity (favicon, description, link
preview); the legacy feature charts are gone from the page and from the route;
the stylesheet has no colour outside its two palettes; and the mark in the
header is the mark in the favicon and the OG card.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.templates import SITE, templates

SERVER = Path(__file__).resolve().parents[1]
TEMPLATES = SERVER / "app" / "templates"
STATIC = SERVER / "app" / "static"


class _Url:
    path = "/"


class _Request:
    """Enough of Starlette's Request for `url_for` and `request.url.path`."""

    url = _Url()

    @staticmethod
    def url_for(name: str, **kw) -> str:
        return f"/static/{kw.get('path', '')}"


def render(name: str, **ctx) -> str:
    return templates.env.get_template(name).render(request=_Request(), **ctx)


INDEX_CTX = {
    "top": {"embeddings": 25886, "named": 23196, "named_pct": 23196 / 25886, "contributors": 1},
    "velocity": {"last_24h": 12, "last_7d": 371},
    "growth": [
        {"day": "2026-07-01", "cumulative": 100},
        {"day": "2026-08-01", "cumulative": 14000},
        {"day": "2026-09-15", "cumulative": 25886},
    ],
    "growth_points": "0.0,120.0 280.0,66.0 560.0,20.0",
    "growth_max": 25886,
    "similarity_ms": 3,
    "similarity_measured": "2026-09",
    "map_meta": {
        "generated": "2026-09-05",
        "count": 25587,
        "pipeline_version": None,
        "available": True,
    },
}

API_CTX = {
    "counts": {"embeddings": 25886, "features": 77770, "named": 23196},
    "host": "clapback.seethroughlab.com",
    "lookup_limit": "300 per minute",
    "contribute_limit": "30 per minute",
}


class TestTheLandingPage:
    def test_it_makes_the_case_in_order(self):
        html = render("index.html", **INDEX_CTX)
        order = [
            "A public commons of CLAP audio embeddings.",
            "pip install clapback-client",
            "recordings held",
            "resolve to a MusicBrainz recording",
            "to ask what sounds like a vector",
            "yours would be the second",
            "Why plug your tool in",
            "What plugging in takes",
            "Already plugged in",
            "<h2>Explore the corpus</h2>",
            "The corpus, dated",
            "How it keeps its word",
        ]
        positions = [html.index(s) for s in order]
        assert positions == sorted(positions), "sections are out of the order the plan fixed"

    def test_the_numbers_are_the_contexts(self):
        html = render("index.html", **INDEX_CTX)
        assert "25,886" in html and "89.6%" in html and "3&nbsp;ms" in html
        assert "+371" in html and "25,587" in html and "2026-09-05" in html
        assert 'points="0.0,120.0 280.0,66.0 560.0,20.0"' in html

    def test_the_tagline_is_the_sites_one_tagline(self):
        html = render("index.html", **INDEX_CTX)
        assert SITE["tagline"] in html
        assert f"<title>{SITE['name']} — {SITE['tagline']}</title>" in html

    def test_two_contributors_changes_the_tile(self):
        ctx = dict(INDEX_CTX, top=dict(INDEX_CTX["top"], contributors=2))
        html = render("index.html", **ctx)
        assert "contributing installations" in html and "yours would be the second" not in html

    def test_empty_growth_has_an_empty_state(self):
        ctx = dict(INDEX_CTX, growth=[], growth_points="", growth_max=0)
        html = render("index.html", **ctx)
        assert "Nothing has arrived in the last 90 days." in html and "<polyline" not in html

    def test_no_map_means_no_script(self):
        ctx = dict(
            INDEX_CTX,
            map_meta={"generated": None, "count": 0, "pipeline_version": None, "available": False},
        )
        html = render("index.html", **ctx)
        assert "map.js" not in html and "has not been generated yet" in html


class TestTheOtherPages:
    def test_api_renders_with_the_shared_header(self):
        html = render("api.html", **API_CTX)
        assert "site-header" in html and "23,196 rows are named so far" in html
        assert "Name a recording" in html and "For tool authors" in html

    def test_map_renders_as_the_explorer(self):
        html = render("map.html", map_meta=INDEX_CTX["map_meta"])
        assert (
            "Explore the corpus" in html
            and 'id="query"' in html
            and "Random named recording" in html
        )
        assert "names.js" in html and "/browse/recent" in html and "/browse/hash/" in html
        assert "musicbrainz.org/ws/2" not in html, (
            "the page must not talk to MusicBrainz; names.js does"
        )

    def test_detail_links_to_the_explorer(self):
        html = render(
            "browse_detail.html",
            fingerprint_hash="d8" * 32,
            totals={"embeddings": 1, "features": 0, "details": 0},
            versions=[],
        )
        assert f"/map?hash={'d8' * 32}" in html

    def test_admin_pages_carry_no_site_chrome(self):
        html = render("admin/login.html", error_message=None)
        assert "site-header" not in html and "site-footer" not in html


class TestIdentity:
    @pytest.mark.parametrize(
        "name,ctx",
        [
            ("index.html", INDEX_CTX),
            ("api.html", API_CTX),
            ("map.html", {"map_meta": INDEX_CTX["map_meta"]}),
        ],
    )
    def test_every_public_page_has_a_head(self, name, ctx):
        html = render(name, **ctx)
        for needle in (
            'property="og:image"',
            'property="og:title"',
            'name="twitter:card"',
            'rel="icon"',
            'rel="apple-touch-icon"',
            'name="description"',
            'name="color-scheme"',
        ):
            assert needle in html, f"{name} lacks {needle}"
        assert html.count('name="theme-color"') == 2
        assert "🎵" not in html

    def test_the_mark_does_not_drift(self):
        """One drawing, three files. The header include is the source of truth;
        the favicon and the OG card carry copies, and a copy that drifts is a
        second logo nobody decided on."""
        paths = re.compile(r'd="([^"]+)"')
        mark = paths.findall((TEMPLATES / "_wordmark.html").read_text())
        assert len(mark) == 2
        for f in ("favicon.svg", "og.svg"):
            found = paths.findall((STATIC / "img" / f).read_text())
            assert mark[0] in found and mark[1] in found, f"{f} carries a different mark"

    def test_the_brand_files_exist(self):
        for f in ("favicon.svg", "favicon-32.png", "apple-touch-icon.png", "og.svg", "og.png"):
            assert (STATIC / "img" / f).exists(), f
        assert (STATIC / "img" / "og.png").stat().st_size < 400_000

    def test_the_description_is_shared_with_openapi(self):
        from app.main import app

        assert app.description == SITE["description"]
        assert "not yet open" not in app.description


class TestWhatIsGone:
    def test_no_feature_charts_anywhere(self):
        from app.api import browse

        html = render("index.html", **INDEX_CTX)
        src = inspect.getsource(browse)
        for needle in ("bpm", "chart-keys", "mood", "features->>", "cdn.jsdelivr", "Chart("):
            assert needle not in html, needle
            assert needle not in src, needle
        assert not (STATIC / "js" / "dashboard.js").exists()

    def test_the_stylesheet_has_no_colour_outside_its_palettes(self):
        css = (STATIC / "css" / "main.css").read_text()
        # Strip the two palette blocks — `:root { ... }` at the top and the one
        # inside the light-mode media query — and look for a hex literal anywhere else.
        stripped = re.sub(r":root\s*\{[^}]*\}", "", css)
        leftovers = re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
        assert leftovers == [], leftovers

    def test_the_stylesheet_has_a_light_mode(self):
        css = (STATIC / "css" / "main.css").read_text()
        assert "@media (prefers-color-scheme: light)" in css


class TestTheExplorersRoutes:
    def test_prefix_validation_is_in_the_route(self):
        from app.api import browse

        src = inspect.getsource(browse.resolve_prefix)
        assert "len(prefix) < 12" in src and "409" in src and "404" in src

    def test_recent_is_bounded(self):
        from app.api import browse

        src = inspect.getsource(browse.recent)
        assert "min(limit, 100)" in src

    def test_recent_is_declared_before_the_catch_all(self):
        """`/browse/{fingerprint_hash}` matches "recent" if it is registered first —
        which it was, on 2026-09-15, for the eight minutes between deploy and check."""
        from app.api import browse

        paths = [r.path for r in browse.browse_router.routes]
        assert paths.index("/browse/recent") < paths.index("/browse/{fingerprint_hash}")

    def test_the_map_builder_filters_on_the_pipeline_identity(self):
        src = (SERVER / "scripts" / "build_map.py").read_text()
        assert "--pipeline-version" in src and "analysis_version = 7" not in src
        assert "map-index.json" in src and "h[:12]" in src

    def test_growth_points_scale_to_the_box(self):
        from app.api.browse import growth_points

        pts = growth_points(
            [{"day": "a", "cumulative": 0}, {"day": "b", "cumulative": 10}],
            width=100,
            height=50,
            pad=5,
        )
        assert pts == "0.0,45.0 100.0,5.0"
        assert growth_points([]) == ""


def test_every_page_states_the_data_licence():
    """`ADR-0013` point 1: the licence is stated on the site, not only in the README.
    The footer carries it on every page; the API page says it where the row counts are."""
    for name, ctx in (("index.html", INDEX_CTX), ("api.html", API_CTX)):
        html = render(name, **ctx)
        assert "CC0 1.0" in html, name
        assert "ADR-0013" in html, name
    assert "Contributing dedicates what you" in render("api.html", **API_CTX)


MANIFEST = {
    "schema_version": 1,
    "generated": "2026-09-21T05:12:40Z",
    "licence": "CC0-1.0",
    "url": "https://clapback-export.s3.us-east-1.amazonaws.com/exports/2026-09-21/",
    "embeddings": [
        {
            "pipeline_version": "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32",
            "file": "embeddings-laion_clap.htsat.unfused_frontend1_artifact1_pool1_fp32.csv.gz",
            "rows": 25886,
        },
    ],
    "embeddings_total": 25886,
    "claims": {"file": "claims.csv.gz", "rows": 23196},
}


class TestTheExportPage:
    """`ADR-0013` point 5 — and honest in both directions."""

    def test_with_a_manifest_it_lists_the_files_and_the_date(self):
        html = render("export.html", manifest=MANIFEST, public_url="https://x")
        assert "2026-09-21" in html
        assert "25,886" in html and "23,196" in html
        assert MANIFEST["url"] + MANIFEST["embeddings"][0]["file"] in html
        assert "CC0 1.0" in html and "cannot" in html  # the recall caveat, point 6

    def test_without_one_it_says_decided_not_published(self):
        html = render("export.html", manifest=None, public_url="")
        assert "not yet published" in html
        assert "2026-09-21" not in html

    def test_the_footer_links_to_it(self):
        assert 'href="/export"' in render("api.html", **API_CTX)

    def test_a_missing_manifest_reads_as_none_not_an_error(self, tmp_path, monkeypatch):
        from app.api import browse

        monkeypatch.setattr(browse.settings, "export_manifest_path", str(tmp_path / "nope.json"))
        assert browse._export_manifest() is None
        (tmp_path / "m.json").write_text('{"generated": "2026-09-21T05:12:40Z"}')
        monkeypatch.setattr(browse.settings, "export_manifest_path", str(tmp_path / "m.json"))
        assert browse._export_manifest()["generated"].startswith("2026-09-21")

    async def test_latest_json_redirects_only_once_a_bucket_is_configured(self, monkeypatch):
        from fastapi import HTTPException

        from app.api import browse

        monkeypatch.setattr(browse.settings, "export_public_url", "")
        with pytest.raises(HTTPException) as e:
            await browse.export_latest(_Request())
        assert e.value.status_code == 404
        monkeypatch.setattr(browse.settings, "export_public_url", "https://bucket.example/")
        r = await browse.export_latest(_Request())
        assert r.status_code == 307
        assert r.headers["location"] == "https://bucket.example/latest/manifest.json"


class TestTheExportScript:
    """`ADR-0013` point 4, enforced on the script rather than trusted.

    The export is a shell script running `COPY` through psql, so the guarantee
    that no private column leaves is a property of its text. Comments may name
    the forbidden things (they explain the rule); code may not.
    """

    SCRIPT = (SERVER / "deploy" / "export.sh").read_text()
    CODE = "\n".join(line for line in SCRIPT.splitlines() if not line.lstrip().startswith("#"))

    def test_no_private_column_or_table_is_selected(self):
        for forbidden in (
            "client_id",
            "ip_stats",
            "banned_ips",
            "submission_agreement",
            "ip_address",
        ):
            assert forbidden not in self.CODE, forbidden

    def test_the_embedding_rows_keep_the_shape_the_map_builder_reads(self):
        """hash first, vector last — `scripts/build_map.py` takes row[0] and row[-1]."""
        sel = self.CODE[
            self.CODE.index("SELECT e.fingerprint_hash") : self.CODE.index("FROM embeddings e")
        ]
        cols = [
            c.strip().split(" AS ")[-1].split(".")[-1]
            for c in sel.replace("SELECT", "").split(",\n")
        ]
        assert cols[0] == "fingerprint_hash" and cols[-1] == "embedding"
        assert "created_at::date" in sel  # day precision, point 4
        assert "HEADER" in self.CODE

    def test_it_never_shares_a_bucket_with_the_backup(self):
        assert "BACKUP_S3_BUCKET" not in self.CODE
        assert "EXPORT_S3_BUCKET" in self.CODE

    def test_the_writer_policy_cannot_delete(self):
        import json

        pol = json.loads((SERVER / "deploy" / "iam-export-policy.json").read_text())
        actions = {
            a
            for st in pol["Statement"]
            for a in ([st["Action"]] if isinstance(st["Action"], str) else st["Action"])
        }
        assert "s3:DeleteObject" not in actions and "s3:PutObject" in actions
        assert all("clapback-backup" not in json.dumps(st) for st in pol["Statement"])
