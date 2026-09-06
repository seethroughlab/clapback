"""Phase 4 of `ADR-0006`: the key changes, and rows that cannot say what produced
them are removed.

    | 4 | Server switches the key, drops the legacy rows, and begins rejecting
        undeclared contributions per point 4. | this repository |

**This is the only migration in the repository that removes data**, and it is the
only one whose failure mode is silent and total: run before phases 2 and 3 have
reached a deployment, it does exactly what it says and takes the entire corpus with
it, because every row that predates the declaration is a row that cannot say. The
guard against that is the single most important thing here, and the tests below
spend more effort on it than on the key itself.

The read path is the other half. Under the old key, `(hash, analysis_version,
clap_model_version)` selected at most one row. Under the new one it selects any
number, so a client that asks the old question can be handed a vector from a
pipeline it cannot use — which is the exact mistake this record exists to design
out, reappearing on the read side after being closed on the write side.
"""

import inspect
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api import routes
from app.api.routes import EmbeddingRequest, EmbeddingResponse
from app.db.models import Embedding, SubmissionAgreement

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/011_pipeline_version_is_the_key.py"
)


def _vec(*head: float) -> list[float]:
    return list(head) + [0.0] * (512 - len(head))


def _req(**kw) -> EmbeddingRequest:
    base = {
        "fingerprint_hash": "a" * 64,
        "embedding": _vec(1.0),
        "analysis_version": 8,
        "clap_model_version": "laion/clap-htsat-unfused",
        "pipeline_version": "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32",
    }
    base.update(kw)
    return EmbeddingRequest(**base)


# ---------------------------------------------------------------------------
# The guard, which matters more than the key
# ---------------------------------------------------------------------------


class TestItRefusesToEmptyTheCorpus:
    """Phases 2, 3 and 4 have to happen in order, and only phase 4 is destructive.

    Nothing in Alembic knows about `ADR-0006`, so nothing stops `alembic upgrade
    head` on a deployment the client changes have not reached. The corpus is
    contributed data; a restore from last night's dump costs whatever was
    contributed since, plus the afternoon spent working out what went wrong.
    """

    def test_it_checks_for_declared_rows_before_removing_anything(self):
        source = MIGRATION.read_text()
        body = source[source.index("def upgrade()") :]
        guard = body.index("RuntimeError")
        removal = body.index("DELETE FROM embeddings")
        assert guard < removal, (
            "the guard must be evaluated before any row is removed"
        )

    def test_the_message_says_what_went_wrong_and_what_to_do(self):
        """An exception during a migration is read by someone under time pressure
        who did not write it. "Refusing to run" without a reason produces a forced
        override, which is the outcome the guard exists to prevent."""
        source = MIGRATION.read_text()
        message = source[source.index("Refusing to run") : source.index('")\n', source.index("Refusing to run"))]
        assert "ADR-0006" in message
        assert "pipeline_version" in message
        assert "CLAPBACK_ALLOW_EMPTYING_THE_CORPUS" in source

    def test_an_empty_database_is_not_blocked(self):
        """`total and not declared` — on a fresh database there is nothing to
        remove, and refusing there would block every new deployment for a reason
        that does not apply to it."""
        source = MIGRATION.read_text()
        assert "if total and not declared" in source

    def test_the_override_must_be_deliberate(self):
        """Not "set to anything". An operator who exports the variable to see what
        it does should not thereby arm it."""
        source = MIGRATION.read_text()
        assert 'os.environ.get(_OVERRIDE) != "1"' in source


# ---------------------------------------------------------------------------
# What the migration does to rows the new key cannot keep apart
# ---------------------------------------------------------------------------


class TestRowsTheNewKeyCannotSeparate:
    """The old key held `(hash, analysis_version, clap_model_version)`. Two rows can
    share a hash and a pipeline while differing in either — Familiar's v7 to v8 bump
    produces exactly that, since it moved the counter while the pipeline stood
    still. The new key cannot hold both, so the migration has to choose."""

    def test_duplicates_are_collapsed_before_the_key_is_applied(self):
        source = MIGRATION.read_text()
        collapse = source.index("PARTITION BY fingerprint_hash, pipeline_version")
        key = source.index("create_primary_key")
        assert collapse < key, (
            "adding the key before collapsing duplicates would fail on real data"
        )

    def test_the_survivor_is_the_earliest(self):
        """First-write-wins, which is the rule the contribution path has always
        used. Any other choice would make the migration disagree with the running
        server about which submission counts."""
        source = MIGRATION.read_text()
        assert "ORDER BY created_at, ctid" in source

    def test_confirmations_are_summed_rather_than_discarded(self):
        """Each was a real submission of this recording under this pipeline.
        `ADR-0004` point 4 already warns the field counts submissions rather than
        independent contributors, so summing loses nothing it was claiming."""
        source = MIGRATION.read_text()
        assert "sum(contributor_count) OVER" in source

    def test_the_column_becomes_not_null_only_after_the_removal(self):
        source = MIGRATION.read_text()
        body = source[source.index("def upgrade()") :]
        assert body.index("DELETE FROM embeddings WHERE pipeline_version IS NULL") < body.index(
            'alter_column("embeddings", "pipeline_version", nullable=False)'
        )

    def test_the_downgrade_admits_what_it_cannot_undo(self):
        """A downgrade that silently restores the old key while the rows stay gone
        is worse than one that says so."""
        doc = MIGRATION.read_text()
        down = doc[doc.index("def downgrade()") :]
        assert "cannot restore" in down
        assert "pg_dump" in down


# ---------------------------------------------------------------------------
# Point 4: an undeclared contribution is rejected
# ---------------------------------------------------------------------------


class TestAnUndeclaredContributionIsRejected:
    def test_a_submission_without_a_pipeline_does_not_validate(self):
        with pytest.raises(ValidationError) as caught:
            EmbeddingRequest(
                fingerprint_hash="a" * 64,
                embedding=_vec(1.0),
                analysis_version=8,
                clap_model_version="laion/clap-htsat-unfused",
            )
        assert "pipeline_version" in str(caught.value)

    def test_client_id_stays_optional_beside_it(self):
        """Point 4 draws the contrast deliberately: an unattributed submission is
        still evidence, whereas an unidentified pipeline is a vector that cannot be
        compared with anything, including itself later."""
        assert _req().client_id is None

    def test_the_rejection_is_the_schema_rather_than_a_branch(self):
        """The field is not optional-and-then-checked. A contribution without one
        is not a contribution, so the check belongs where the shape is defined —
        and a branch could be reordered behind a database write."""
        assert EmbeddingRequest.model_fields["pipeline_version"].is_required()
        body = inspect.getsource(routes.contribute_embedding)
        assert "if not req.pipeline_version" not in body


# ---------------------------------------------------------------------------
# The write path keys on the pipeline
# ---------------------------------------------------------------------------


class TestTheWritePathKeysOnThePipeline:
    def test_an_existing_row_is_found_by_recording_and_pipeline(self):
        body = inspect.getsource(routes.contribute_embedding)
        lookup = body[body.index("select(Embedding).where") : body.index("existing = result")]
        assert "Embedding.pipeline_version == req.pipeline_version" in lookup
        assert "Embedding.analysis_version" not in lookup
        assert "Embedding.clap_model_version" not in lookup

    def test_point_7s_guard_survives_becoming_structurally_true(self):
        """Selecting by the pipeline makes a mismatch impossible, so the comparison
        is now always true. Kept because the guarantee moved into the shape of a
        query several lines away, and a change to that query would take the
        guarantee with it silently."""
        body = inspect.getsource(routes.contribute_embedding)
        assert "comparable = req.pipeline_version == existing.pipeline_version" in body
        assert "if similarity is not None and comparable:" in body


# ---------------------------------------------------------------------------
# The read path, where the same mistake can reappear
# ---------------------------------------------------------------------------


class TestTheReadPathAfterTheKeyChange:
    """Under the old key the three lookup parameters selected at most one row.
    Under the new one they select any number, and a caller that asks the old
    question can be handed a vector from a pipeline it cannot use."""

    def test_a_client_predating_the_change_still_works(self):
        """`ADR-0005` point 10 — the contract widens, it does not move. Familiar
        sends `analysis_version` and `clap_model_version` and nothing else, and it
        must keep getting an answer."""
        sig = inspect.signature(routes.lookup_embedding)
        for name in ("analysis_version", "clap_model_version"):
            assert sig.parameters[name].default is None, f"{name} must be optional"

    def test_the_pipeline_can_be_named_so_the_answer_is_exact(self):
        sig = inspect.signature(routes.lookup_embedding)
        assert "pipeline_version" in sig.parameters
        assert sig.parameters["pipeline_version"].default is None
        body = inspect.getsource(routes.lookup_embedding)
        assert "Embedding.pipeline_version == _decode_pipeline(pipeline_version)" in body

    def test_an_ambiguous_lookup_is_deterministic(self):
        """A caller who named no pipeline gets a defensible row rather than
        whatever the planner returned first. It is not a claim that this row is
        right for them — which is why the response carries the pipeline."""
        body = inspect.getsource(routes.lookup_embedding)
        assert "order_by" in body and "limit(1)" in body
        assert "contributor_count.desc()" in body
        # Total, not partial. Count and timestamp tie readily — two pipelines
        # contributed in one batch share both — and a partial order lets Postgres
        # answer the same request differently between calls, which reads as the
        # corpus having changed. The key's second column is unique here.
        assert "Embedding.pipeline_version," in body
        assert EmbeddingResponse.model_fields["pipeline_version"].is_required()

    def test_a_plus_sign_survives_the_query_string(self):
        """**The trap every caller of this endpoint will hit once.** A pipeline
        identity is `+`-joined, and `+` in a query string is the legacy encoding of
        a space — so an unescaped identity arrives with five spaces in it, matches
        nothing, and 404s. That reads as "the corpus does not have this recording",
        not as "you encoded it wrong", which is a silent failure of the exact kind
        this record exists to remove rather than relocate.

        A space cannot occur in an identity, so mapping it back is unambiguous."""
        identity = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"
        mangled = identity.replace("+", " ")
        assert routes._decode_pipeline(mangled) == identity
        # A correctly escaped %2B arrives as a real `+` and must not be touched.
        assert routes._decode_pipeline(identity) == identity

    def test_the_leniency_is_confined_to_the_query_string(self):
        """`/v1/similar` takes the same field in a JSON body, where the problem does
        not arise. Applying the same fix-up there would be guessing at data a client
        sent deliberately."""
        assert "_decode_pipeline" not in inspect.getsource(routes.similar)
        assert "_decode_pipeline" not in inspect.getsource(routes.contribute_embedding)

    def test_the_docstring_tells_a_caller_which_question_to_ask(self):
        """The failure here is silent: a lookup by metadata alone returns a
        perfectly valid vector that is not comparable with the caller's own."""
        # Collapsed, because the docstring wraps and the assertion is about what it
        # says rather than where the lines break.
        doc = " ".join((inspect.getdoc(routes.lookup_embedding) or "").split())
        assert "pipeline_version" in doc
        assert re.search(r"not comparable|cannot use|comparable with", doc)


# ---------------------------------------------------------------------------
# Agreement records follow the same identity
# ---------------------------------------------------------------------------


class TestAgreementFollowsTheSameIdentity:
    def test_agreements_can_be_found_by_recording_and_pipeline(self):
        indexed = {
            tuple(c.name for c in ix.columns)
            for ix in SubmissionAgreement.__table__.indexes
        }
        assert ("fingerprint_hash", "pipeline_version") in indexed

    def test_the_embeddings_metadata_columns_are_still_queryable(self):
        """They stopped being identity and stayed provenance. Someone will ask
        "which analysis versions are in here" and should not get a sequential
        scan for it."""
        indexed = {
            tuple(c.name for c in ix.columns) for ix in Embedding.__table__.indexes
        }
        assert ("analysis_version",) in indexed
        assert ("pipeline_version",) in indexed
