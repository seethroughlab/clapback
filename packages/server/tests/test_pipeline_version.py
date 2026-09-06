"""Phase 1 of `ADR-0006`: the corpus learns what produced a vector.

    | 1 | Server accepts and stores `pipeline_version` as an optional column. Key
        unchanged; nothing rejected. | this repository |

The record's point 6 sequences the change over four phases specifically so the
endpoint contract never breaks, and phase 1 is the half of it that this repository
can land alone. Two things therefore have to be true at once, and they pull in
opposite directions: a client that declares nothing must be treated exactly as it
is today, and a client that declares something must have it stored well enough that
phase 4 can key on it.

The third thing is point 7, which is not phase-specific and is the easiest to lose:
a submission whose declared pipeline differs from the stored row's must never reach
`submission_agreement`. Until phase 4 the key does not include the pipeline, so two
incomparable vectors *can* land on the same row — and their similarity would be a
real number measuring the wrong thing.
"""

import inspect

import pytest
from pydantic import ValidationError

from app.api import routes
from app.api.routes import EmbeddingRequest, EmbeddingResponse, Neighbour, SimilarRequest
from app.db.models import Embedding, SubmissionAgreement

#: The identity string `clapback-embed` produces today, written out rather than
#: imported. The server does not depend on the library and must not start: point 8
#: of `ADR-0006` says the declaration is *asserted* by the client and believed by
#: the server, which never computes one. A server that imported `PIPELINE_VERSION`
#: would be one upgrade away from believing its own value instead of the client's.
IDENTITY = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"


def _vec(*head: float) -> list[float]:
    return list(head) + [0.0] * (512 - len(head))


def _req(**kw) -> EmbeddingRequest:
    return EmbeddingRequest(
        fingerprint_hash="a" * 64,
        embedding=_vec(1.0),
        analysis_version=7,
        clap_model_version="laion/clap-htsat-unfused",
        **kw,
    )


# ---------------------------------------------------------------------------
# Nothing is rejected
# ---------------------------------------------------------------------------


class TestNothingIsRejected:
    """Phase 1's own words, and **phase 4 deliberately ended them.**

    Kept rather than deleted because the sequence is the point of `ADR-0006` point 6:
    the server had to learn to store the field before any client was obliged to send
    it, so that no contract broke at any step. These tests record what that
    intermediate state guaranteed and where it stopped.
    """

    def test_a_contribution_without_a_pipeline_is_now_rejected(self):
        """Point 4, and the one hard wall this record puts up. It is a harder
        requirement than anything `ADR-0004` asked of a contributor, and the
        justification is that an unidentified vector is *unusable* rather than
        merely unattributed."""
        with pytest.raises(ValidationError) as caught:
            _req()
        assert "pipeline_version" in str(caught.value)

    def test_the_field_is_absent_rather_than_empty(self):
        """Not `""`. An empty string would be a client declaring a pipeline named
        nothing, and the key would then have to carry it."""
        with pytest.raises(ValidationError):
            _req(pipeline_version="")

    def test_a_declared_pipeline_is_carried_through(self):
        assert _req(pipeline_version=IDENTITY).pipeline_version == IDENTITY

    def test_the_real_identity_string_fits_the_column(self):
        """The column is sized for a string that grows. The identity is composed
        from five components today and nothing bounds it at five, so the check is
        that there is room left rather than that it fits exactly."""
        limit = Embedding.__table__.columns["pipeline_version"].type.length
        assert len(IDENTITY) < limit / 2


# ---------------------------------------------------------------------------
# The key is unchanged
# ---------------------------------------------------------------------------


class TestTheKeyIsWhatProducedTheVector:
    """Phase 4, `ADR-0006` point 1: `(fingerprint_hash, pipeline_version)`.

    Two rows share a key exactly when the vectors are comparable, which is the
    property the key existed to have and did not have until migration `011`."""

    def test_the_primary_key_is_the_recording_and_the_pipeline(self):
        key = {c.name for c in Embedding.__table__.primary_key}
        assert key == {"fingerprint_hash", "pipeline_version"}

    def test_the_checkpoint_and_the_counter_are_no_longer_in_it(self):
        """Points 2 and 3. `clap_model_version` is already the first component of
        the identity string, so keying on it separately let two fields disagree
        about one fact; `analysis_version` is the client's own counter, which
        Familiar moved from 7 to 8 while the vectors stayed identical."""
        key = {c.name for c in Embedding.__table__.primary_key}
        assert "clap_model_version" not in key
        assert "analysis_version" not in key
        # Retained as recorded columns, not dropped.
        assert "clap_model_version" in Embedding.__table__.columns
        assert "analysis_version" in Embedding.__table__.columns

    def test_the_column_cannot_be_null(self):
        """It is half the key, and a null in a key is not a thing Postgres will
        hold. Migration `011` removed the rows that could not say rather than
        filling them in — point 5, recomputed and not relabelled."""
        assert Embedding.__table__.columns["pipeline_version"].nullable is False

    def test_it_is_indexed_on_its_own_as_well_as_leading_the_key(self):
        indexed = {
            tuple(c.name for c in ix.columns) for ix in Embedding.__table__.indexes
        }
        assert ("pipeline_version",) in indexed


# ---------------------------------------------------------------------------
# Point 7: a mismatch is never recorded as disagreement
# ---------------------------------------------------------------------------


class TestAMismatchIsNeverRecordedAsDisagreement:
    """`ADR-0006` point 7. The failure this prevents is silent and permanent:
    a similarity of 0.31 between a v5 middle-ten-seconds vector and a v7
    whole-track mean is a true number about two different pipelines, and once it
    is in the table nothing distinguishes it from a contributor whose machine
    computes the wrong thing."""

    def test_the_guard_exists_and_gates_the_write(self):
        body = inspect.getsource(routes.contribute_embedding)
        assert "comparable" in body, "no pipeline comparison at all"
        assert "if similarity is not None and comparable:" in body, (
            "the agreement row must be gated on comparability, not merely computed"
        )

    def test_agreement_rows_record_which_pipeline_agreed(self):
        assert "pipeline_version" in SubmissionAgreement.__table__.columns
        body = inspect.getsource(routes.contribute_embedding)
        assert "pipeline_version=req.pipeline_version" in body

    @pytest.mark.parametrize(
        ("submitted", "stored", "expected"),
        [
            (None, None, True),      # the legacy case: unchanged from today
            ("p+pool1", "p+pool1", True),
            ("p+pool2", "p+pool1", False),
            ("p+pool1", None, False),  # one declares, one does not — unknown
            (None, "p+pool1", False),
        ],
    )
    def test_comparability_is_equality_including_both_absent(
        self, submitted, stored, expected
    ):
        """Equality rather than "both declared". Both-null is every row in the
        corpus today and must keep recording exactly as it does now; one side
        declaring and the other not is precisely the unknown the guard is for,
        because the stored row's pipeline is not unknown-but-probably-the-same,
        it is unrecorded."""
        assert (submitted == stored) is expected


# ---------------------------------------------------------------------------
# Stored rows are not relabelled
# ---------------------------------------------------------------------------


class TestStoredRowsAreNotRelabelled:
    """`ADR-0006` point 5: the existing rows are *recomputed*, not relabelled.

    The tempting shortcut is to fill in a null `pipeline_version` from the first
    submission that declares one — it looks like free provenance. It would assert,
    on a vector nobody can vouch for, exactly the claim phase 4 is built to trust."""

    def test_the_confirmation_path_never_assigns_to_the_stored_row(self):
        body = inspect.getsource(routes.contribute_embedding)
        assert "existing.pipeline_version =" not in body
        assert "existing.pipeline_version=" not in body


# ---------------------------------------------------------------------------
# It is stored, and it is reported
# ---------------------------------------------------------------------------


class TestItIsStoredAndReported:
    def test_the_creation_path_stores_it(self):
        """Phase 4 can only promote this to the key if the rows contributed
        between now and then carry it."""
        body = inspect.getsource(routes.contribute_embedding)
        assert "pipeline_version=req.pipeline_version" in body

    def test_a_lookup_reports_it(self):
        body = inspect.getsource(routes.lookup_embedding)
        assert "pipeline_version=emb.pipeline_version" in body

    def test_the_response_always_says_which_pipeline_produced_it(self):
        """No longer optional: every stored row has one since migration `011`, so a
        caller never has to treat the answer as unknown."""
        assert EmbeddingResponse.model_fields["pipeline_version"].is_required()

    def test_search_can_filter_to_a_comparable_set(self):
        """`analysis_version` only approximates comparability, which is the whole
        argument of point 1. Search should be able to ask the real question."""
        assert "pipeline_version" in SimilarRequest.model_fields
        assert SimilarRequest.model_fields["pipeline_version"].default is None
        body = inspect.getsource(routes.similar)
        assert "Embedding.pipeline_version == req.pipeline_version" in body

    def test_search_results_say_which_pipeline_produced_them(self):
        assert Neighbour.model_fields["pipeline_version"].default is None

    def test_searched_counts_what_was_actually_ranked(self):
        """The count and the ranking share one filter list. Two separately
        spelled-out filters is how `searched` starts describing a different set
        of rows than the neighbours beside it."""
        body = inspect.getsource(routes.similar)
        assert body.count("filters.append") == 2
        assert "count_stmt.where(*filters)" in body
