"""The route that stands between this corpus and accepting a stranger's writes.

`ADR-0004` point 7 is the only "non-optional" in that record:

    A delete path exists before the endpoint is public. Non-optional. A public
    corpus needs takedown for legal requests and retraction for poisoned
    recordings, and there is no DELETE route anywhere today. Deletion is by
    fingerprint hash and by client identifier, admin-only.

These cover the contract rather than the database: that both paths exist, that
neither is reachable without a session, and — the part easiest to get wrong —
what each one is scoped to delete. A takedown that missed the feature rows would
be a takedown in name only, and a retraction that silently reached rows it could
not attribute would be worse than one that admits its limit.
"""

import inspect

import pytest

from app.api import admin


def _routes():
    return {r.path: r for r in admin.admin_router.routes if "corpus" in getattr(r, "path", "")}


class TestBothPathsExist:
    def test_deletion_by_fingerprint_hash(self):
        """Point 7's first half: takedown for legal requests."""
        r = _routes()["/admin/corpus/recordings/{fingerprint_hash}"]
        assert r.methods == {"DELETE"}

    def test_deletion_by_client_identifier(self):
        """Point 7's second half, and point 6's cascade."""
        r = _routes()["/admin/corpus/clients/{client_id}"]
        assert r.methods == {"DELETE"}

    def test_there_are_exactly_two(self):
        """Point 7 names two axes. A third would be a decision nobody took."""
        assert len(_routes()) == 2


class TestNeitherIsReachableWithoutASession:
    """`ADR-0004` point 7 says admin-only, and this is the assertion that matters.

    Caddy 404s /admin publicly, but a deployment without Caddy — the development
    compose, a future host, someone running this locally — has no such shield.
    The check has to live in the application.
    """

    @pytest.mark.parametrize("fn", [admin.delete_recording, admin.delete_client_submissions])
    def test_it_calls_require_auth_before_touching_the_database(self, fn):
        body = inspect.getsource(fn)
        assert "_require_auth(request)" in body, "no authentication check at all"
        assert body.index("_require_auth") < body.index("db.execute"), (
            "authentication must be checked before anything is deleted"
        )


class TestWhatEachOneIsScopedTo:
    def test_a_takedown_removes_every_table_keyed_on_the_hash(self):
        """Features are keyed on the same fingerprint and describe the same
        recording. Leaving them would answer a takedown request with a partial
        deletion, which is not an answer."""
        body = inspect.getsource(admin.delete_recording)
        for model in ("Embedding", "Features", "AnalysisDetail", "SubmissionAgreement"):
            assert model in body, f"a takedown must reach {model}"

    def test_a_retraction_does_not_claim_to_delete_what_it_cannot_select(self):
        """Features carry no client id, so a retraction cannot find them. The
        result reports 0 rather than pretending, because a caller who believes a
        client's features were removed is worse off than one who knows they were
        not."""
        body = inspect.getsource(admin.delete_client_submissions)
        assert "features=0" in body and "analysis_details=0" in body

    def test_deletion_is_hard_rather_than_a_flag(self):
        """A legal request is not answered by a flag the server keeps honouring."""
        body = inspect.getsource(admin.delete_recording)
        assert "delete(" in body
        assert "is_active" not in body and "deleted_at" not in body


class TestTheResultSaysWhatHappened:
    def test_it_reports_per_table_counts(self):
        fields = set(admin.DeletionResult.model_fields)
        assert fields == {"embeddings", "features", "analysis_details", "submission_agreements"}

    def test_counts_are_integers_so_zero_is_distinguishable_from_absent(self):
        r = admin.DeletionResult(embeddings=0, features=0, analysis_details=0, submission_agreements=0)
        assert r.embeddings == 0


class TestRevocationCanActuallySelectRows:
    """`ADR-0004` point 6's cascade needs the corpus to know who contributed.

    Before migration 008 it did not: only `submission_agreement` carried a
    client id, and only for duplicate submissions — the row that created an
    embedding recorded nothing. Point 6 was a promise the schema could not keep.
    """

    def test_embeddings_record_their_contributor(self):
        from app.db.models import Embedding

        assert "client_id" in Embedding.__table__.columns

    def test_it_stays_nullable(self):
        """Every row before 2026-09-04 has none, and no backfill is honest —
        nobody recorded who sent them. `ADR-0004` point 3 already says what an
        unattributed submission is worth."""
        from app.db.models import Embedding

        assert Embedding.__table__.columns["client_id"].nullable is True

    def test_the_contribute_path_records_it(self):
        from app.api import routes

        body = inspect.getsource(routes.contribute_embedding)
        assert "client_id=req.client_id" in body
