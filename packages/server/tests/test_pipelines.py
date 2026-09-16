"""The corpus lists what it holds — `ADR-0014` point 3.

`GET /v1/pipelines` is the live form of the export manifest's per-identity list:
each distinct `pipeline_version`, its rows, how many of them anyone has named,
and the first and latest contribution under it. It is a list of what has been
declared, not a registry of what is allowed — the server compares identities
whole and interprets none of them (`ADR-0006`, `ADR-0014` point 4).

No database, like the rest of this suite: shapes and source properties. The
endpoint was exercised against a real Postgres on 2026-09-16 with three rows
across two identities and answered rows 2/1, named 1/1.
"""

import inspect

from app.api import routes
from app.api.routes import PipelineEntry, PipelinesResponse


class TestTheShape:
    def test_an_entry_carries_the_four_facts_the_record_names(self):
        fields = set(PipelineEntry.model_fields)
        assert {"pipeline_version", "rows", "named", "first_contributed_at", "last_contributed_at"} <= fields

    def test_the_route_exists_under_v1_as_a_read(self):
        route = next(r for r in routes.router.routes if r.path == "/v1/pipelines")
        assert route.methods == {"GET"}
        assert route.response_model is PipelinesResponse


class TestItIsAListNotARegistry:
    def test_the_server_does_not_parse_an_identity(self):
        """Point 4: no validator, no token grammar. A string that ignores the
        convention is still a valid key, and this endpoint reports it as one."""
        src = inspect.getsource(routes._fetch_pipelines) + inspect.getsource(routes.pipelines)
        assert ".split(" not in src
        assert "re." not in src

    def test_it_is_on_the_lookup_limit_and_cached(self):
        src = inspect.getsource(routes.pipelines)
        assert "settings.lookup_rate_limit" in src
        assert "stats_cache" in src

    def test_named_counts_claimed_hashes_not_claims(self):
        """Two clients naming one row is one named row, as on the API page."""
        src = inspect.getsource(routes._fetch_pipelines)
        assert "distinct(RecordingClaim.fingerprint_hash)" in src
