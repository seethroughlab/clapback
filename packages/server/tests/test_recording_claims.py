"""A contribution can name its recording — `ADR-0012`.

The record's substance is that a recording id is a *claim* counted per client,
never a column trusted once. These cover the contract that follows from that:
what a claim needs to be valid, what the reads carry, that the second write path
touches nothing but the claims table, that deletion takes claims with it, and
that resolving a hash to a recording is a total ordering.

No database, like the rest of this suite. Shapes and source properties.
"""

from __future__ import annotations

import importlib.util
import inspect
import pathlib

import pytest
from pydantic import ValidationError

from app.api import admin, routes
from app.db import models

MBID = "b1a2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
HASH = "d1" * 32
VEC = [0.0] * 512


def _migration():
    p = pathlib.Path(__file__).parent.parent / "migrations" / "versions" / "012_recording_claims.py"
    spec = importlib.util.spec_from_file_location("m012", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestTheIdentifier:
    """Point 2: a MusicBrainz recording MBID, validated as a UUID and nothing more."""

    def test_canonical_form_passes_through(self):
        assert routes._canonical_mbid(MBID) == MBID

    def test_case_is_folded_not_rejected(self):
        """MusicBrainz is case-insensitive about these and tags in the wild carry
        both. A claim that differs only by case is the same claim."""
        assert routes._canonical_mbid(MBID.upper()) == MBID

    @pytest.mark.parametrize(
        "bad", ["", "not-a-uuid", MBID[:-1], MBID + "0", "b1a2c3d4e5f64a7b8c9d0e1f2a3b4c5d"]
    )
    def test_anything_else_is_refused(self, bad):
        with pytest.raises(ValueError):
            routes._canonical_mbid(bad)

    def test_the_server_never_asks_musicbrainz(self):
        """Point 2 and point 3: it validates the shape and never the referent.
        No HTTP client, no MusicBrainz host, anywhere in the routes."""
        src = inspect.getsource(routes)
        for forbidden in ("musicbrainz.org", "acoustid.org", "httpx", "requests.get"):
            assert forbidden not in src


class TestContributingWithAnId:
    """Point 4's first write path."""

    def test_it_is_optional_and_defaults_to_none(self):
        r = routes.EmbeddingRequest(
            fingerprint_hash=HASH, embedding=VEC, analysis_version=1,
            clap_model_version="x", pipeline_version="p",
        )
        assert r.recording_mbid is None

    def test_it_is_canonicalised_on_the_way_in(self):
        r = routes.EmbeddingRequest(
            fingerprint_hash=HASH, embedding=VEC, analysis_version=1,
            clap_model_version="x", pipeline_version="p", recording_mbid=MBID.upper(),
        )
        assert r.recording_mbid == MBID

    def test_a_malformed_id_is_a_422_not_a_silent_drop(self):
        with pytest.raises(ValidationError):
            routes.EmbeddingRequest(
                fingerprint_hash=HASH, embedding=VEC, analysis_version=1,
                clap_model_version="x", pipeline_version="p", recording_mbid="nope",
            )

    def test_an_id_without_a_client_is_refused(self):
        """Point 1: a claim is keyed by the client that made it. Unattributable,
        it could be neither revoked nor counted."""
        body = inspect.getsource(routes.contribute_embedding)
        assert "req.recording_mbid and not req.client_id" in body
        assert "422" in body

    def test_both_branches_record_the_claim(self):
        """A contribution that confirms an existing row can still name it."""
        body = inspect.getsource(routes.contribute_embedding)
        assert body.count("_record_claim(") == 2


class TestClaimingWithoutResending:
    """Point 4's second write path, which exists so nobody re-POSTs a vector."""

    def test_a_claim_needs_all_three(self):
        routes.ClaimRequest(fingerprint_hash=HASH, recording_mbid=MBID, client_id="c")
        with pytest.raises(ValidationError):
            routes.ClaimRequest(fingerprint_hash=HASH, recording_mbid=MBID, client_id="")
        with pytest.raises(ValidationError):
            routes.ClaimRequest(fingerprint_hash=HASH, recording_mbid="x", client_id="c")

    def test_it_sends_no_vector(self):
        assert "embedding" not in routes.ClaimRequest.model_fields

    def test_the_row_must_exist(self):
        """An identity for a vector the corpus does not hold is an identity for
        nothing."""
        body = inspect.getsource(routes.claim_recording)
        assert "404" in body
        assert "select(func.count())" in body

    def test_it_touches_only_the_claims_table(self):
        """Not `contributor_count`, not `submission_agreement`. Backfilling a
        library's ids must not read as that library agreeing with itself."""
        body = inspect.getsource(routes.claim_recording)
        assert "contributor_count" not in body
        assert "SubmissionAgreement" not in body

    def test_saying_it_twice_is_saying_it_once(self):
        body = inspect.getsource(routes._record_claim)
        assert "on_conflict_do_nothing" in body


class TestWhatTheReadsCarry:
    """Point 5."""

    @pytest.mark.parametrize("model", [routes.Neighbour, routes.EmbeddingResponse])
    def test_hash_bearing_responses_carry_the_recording(self, model):
        f = model.model_fields
        assert "recording_mbid" in f and "recording_claims" in f
        assert f["recording_mbid"].default is None
        assert f["recording_claims"].default == 0

    def test_a_neighbour_without_a_claim_is_still_a_hash(self):
        """Null and 0, not a blank string — the caller should know."""
        n = routes.Neighbour(
            fingerprint_hash=HASH, similarity=1.0, analysis_version=1, clap_model_version="x"
        )
        assert n.recording_mbid is None and n.recording_claims == 0

    def test_similar_and_lookup_resolve_through_the_claims(self):
        for fn in (routes.similar, routes.lookup_embedding):
            assert "_recordings_for(" in inspect.getsource(fn), fn.__name__

    def test_the_recording_read_is_per_pipeline(self):
        """A recording held from two pipelines is two rows that are not
        comparable, and the response says which is which."""
        assert "pipeline_version" in routes.RecordingEmbedding.model_fields
        assert "pipeline_version" in inspect.signature(routes.recording).parameters


class TestResolvingIsATotalOrdering:
    """`ADR-0006` paid for the lesson: a partial order lets Postgres answer the
    same question differently between calls, which reads as the corpus having
    changed."""

    def test_most_distinct_clients_wins_then_the_id_text_breaks_ties(self):
        body = inspect.getsource(routes._recordings_for)
        assert "func.distinct(RecordingClaim.client_id)" in body
        order = body[body.index(".order_by(") :]
        assert order.index(".desc()") < order.index("RecordingClaim.recording_mbid,")

    def test_it_counts_clients_not_rows(self):
        """One client claiming the same thing twice is one vote. The key already
        prevents the duplicate row; the count must not depend on that."""
        assert "count(func.distinct" in inspect.getsource(routes._recordings_for)


class TestDeletionCoversClaims:
    """Point 9."""

    def test_takedown_by_hash_removes_the_claims(self):
        body = inspect.getsource(admin.delete_recording)
        assert "RecordingClaim" in body

    def test_retraction_by_client_removes_only_that_clients_claims(self):
        body = inspect.getsource(admin.delete_client_submissions)
        assert "delete(RecordingClaim).where(RecordingClaim.client_id == client_id)" in body

    def test_the_deletion_result_reports_them_and_defaults_for_old_callers(self):
        f = admin.DeletionResult.model_fields
        assert "recording_claims" in f and f["recording_claims"].default == 0


class TestTheTable:
    """Point 1: a claim, not a column."""

    def test_embeddings_is_untouched(self):
        assert "recording_mbid" not in models.Embedding.__table__.columns

    def test_the_key_is_hash_mbid_client(self):
        pk = [c.name for c in models.RecordingClaim.__table__.primary_key.columns]
        assert pk == ["fingerprint_hash", "recording_mbid", "client_id"]

    def test_client_is_required_here_unlike_on_embeddings(self):
        assert models.RecordingClaim.__table__.c.client_id.nullable is False
        assert models.Embedding.__table__.c.client_id.nullable is True

    def test_the_migration_matches_the_model_and_follows_011(self):
        m = _migration()
        assert m.down_revision == "011_pipeline_version_is_the_key"
        body = inspect.getsource(m.upgrade)
        assert '"fingerprint_hash", "recording_mbid", "client_id"' in body
        assert "ix_recording_claims_mbid" in body
        assert "DELETE" not in body.upper() and "drop_" not in body, "additive, no data moves"
