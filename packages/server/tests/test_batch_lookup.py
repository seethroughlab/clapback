"""A library is looked up in batches — `ADR-0015`, as amended by `ADR-0019` point 3.

`POST /v1/embeddings/lookup`: up to 100 keys, hashes and recording ids mixed,
answered in order, each entry naming the key it answered; the limit counted per
key. And `GET /v1/recordings/by-hash/{hash}` — `ADR-0018` point 2 — every id
claimed for a row with its distinct-client count.

No database, like the rest of this suite: shapes and source properties. Both
routes were exercised against a real Postgres on 2026-09-16 (four mixed keys
answered in order, a dissenting claim listed second, the fourth batch of a
hundred refused with `Retry-After`); the numbers are in the records.
"""

import inspect

import pytest
from pydantic import ValidationError

from app import limiter as limiter_mod
from app.api import routes
from app.api.routes import (
    LOOKUP_BATCH_MAX,
    ClaimEntry,
    LookupBatchRequest,
    LookupKey,
    LookupRow,
)

HASH = "d1" * 32
MBID = "b1a2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"


class TestAKey:
    def test_is_a_hash_or_a_recording_id(self):
        assert LookupKey(fingerprint_hash=HASH).recording_mbid is None
        assert LookupKey(recording_mbid=MBID.upper()).recording_mbid == MBID

    @pytest.mark.parametrize("kw", [{}, {"fingerprint_hash": HASH, "recording_mbid": MBID}])
    def test_but_not_both_or_neither(self, kw):
        with pytest.raises(ValidationError):
            LookupKey(**kw)


class TestABatch:
    def test_is_at_most_one_hundred_and_over_is_refused_not_truncated(self):
        LookupBatchRequest(keys=[{"fingerprint_hash": HASH}] * LOOKUP_BATCH_MAX)
        with pytest.raises(ValidationError):
            LookupBatchRequest(keys=[{"fingerprint_hash": HASH}] * (LOOKUP_BATCH_MAX + 1))
        assert LOOKUP_BATCH_MAX == 100

    def test_vectors_default_on_and_pipeline_optional(self):
        req = LookupBatchRequest(keys=[{"recording_mbid": MBID}])
        assert req.vectors is True and req.pipeline_version is None

    def test_a_held_row_is_the_single_lookups_shape(self):
        """Point 1: nothing a client learned from the single endpoint changes."""
        assert set(routes.EmbeddingResponse.model_fields) <= set(LookupRow.model_fields)

    def test_without_vectors_the_embedding_is_absent_not_null(self):
        """Point 2: `response_model_exclude_unset` drops what `_row_for` never
        set, so the row is smaller rather than carrying 512 nulls."""
        route = next(r for r in routes.router.routes if r.path == "/v1/embeddings/lookup")
        assert route.response_model_exclude_unset is True
        assert "if vectors:" in inspect.getsource(routes._row_for)


class TestTheLimitCountsKeys:
    def test_the_handler_charges_per_key_after_parsing(self):
        src = inspect.getsource(routes.lookup_batch)
        assert "charge(request, settings.lookup_rate_limit, len(req.keys))" in src
        # Not also decorated — that would charge one more per request.
        assert "@limiter.limit" not in src.split("async def lookup_batch")[0]

    def test_charge_keys_its_window_the_way_slowapi_does(self):
        """Measured 2026-09-16: `slowapi` keys by address and request path, so
        the batch route's per-key window is its own. Charging against the
        single route's function name would have been a window nothing else
        uses."""
        src = inspect.getsource(limiter_mod.charge)
        assert 'request["path"]' in src
        assert "Retry-After" in src


class TestWhichRecordingIsThis:
    def test_an_entry_says_what_kind_of_id_it_is(self):
        """`ADR-0019` point 6 admits a second claim type later; the field is
        here from the first version."""
        assert (
            ClaimEntry(type="musicbrainz_recording", id=MBID, clients=1).type
            == "musicbrainz_recording"
        )

    def test_the_route_exists_and_says_what_it_is_not(self):
        route = next(
            r for r in routes.router.routes if r.path == "/v1/recordings/by-hash/{fingerprint_hash}"
        )
        assert route.methods == {"GET"}
        doc = inspect.getdoc(routes.claims_for_hash).lower()
        assert "not" in doc and "acoustid" in doc and "verified" in doc

    def test_it_never_asks_musicbrainz_or_acoustid(self):
        src = inspect.getsource(routes)
        for forbidden in ("musicbrainz.org", "acoustid.org", "httpx", "requests.get"):
            assert forbidden not in src
