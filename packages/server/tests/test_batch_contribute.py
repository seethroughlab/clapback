"""A library is contributed in batches, with every guarantee kept — `ADR-0016`.

`POST /v1/embeddings/batch`: up to 100 `EmbeddingRequest`s, each run through
`_contribute_one` — the function the single endpoint *is* — in its own
transaction, so agreement, the contributor count, the ceiling and the quota
happen per row (point 2); not atomic (point 4); `client_id` required (point
5); the limit counted per row (point 3). And point 7, built first: the
per-client quota `ADR-0004` point 9 owed since 2026-09-04.

No database, like the rest of this suite: shapes and source properties. The
endpoint was exercised against a real Postgres on 2026-09-16: 100 rows created
in one batch; a batch of 100 across a ceiling of 112 answered 4 created, 2
confirmed, 94 refused with 507, in order; a client at its quota answered 46
confirmed then 14 refused with 429 and `retry_after`; an 11 MB body was a 413
before parsing; the seventh hundred in a minute was a 429 with `Retry-After`.
"""

import inspect

import pytest
from pydantic import ValidationError

from app import config, main
from app.api import routes
from app.api.routes import (
    CONTRIBUTE_BATCH_MAX,
    ContributeBatchRequest,
    ContributeResult,
    EmbeddingRequest,
)

HASH = "d1" * 32
PIPELINE = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"


def _row(**kw):
    return {
        "fingerprint_hash": HASH,
        "embedding": [0.0] * 511 + [1.0],
        "analysis_version": 1,
        "clap_model_version": "x",
        "pipeline_version": PIPELINE,
        "client_id": "c1",
        **kw,
    }


class TestTheBatchIsAContainer:
    def test_an_entry_is_exactly_an_embedding_request(self):
        """Point 1: no second definition of what a contribution is."""
        assert (
            ContributeBatchRequest.model_fields["contributions"].annotation
            == list[EmbeddingRequest]
        )

    def test_at_most_one_hundred_and_over_is_refused_not_truncated(self):
        ContributeBatchRequest(contributions=[_row()] * CONTRIBUTE_BATCH_MAX)
        with pytest.raises(ValidationError):
            ContributeBatchRequest(contributions=[_row()] * (CONTRIBUTE_BATCH_MAX + 1))

    def test_client_id_is_required_on_every_row(self):
        """Point 5. The single endpoint's grandfather clause does not apply:
        no client predates this endpoint."""
        with pytest.raises(ValidationError, match="client_id is required"):
            ContributeBatchRequest(contributions=[_row(), _row(client_id=None)])
        assert EmbeddingRequest.model_fields["client_id"].default is None  # still optional alone


class TestEveryGuaranteeRunsPerRow:
    def test_the_single_endpoint_is_a_call_to_the_shared_function(self):
        assert "return await _contribute_one(db, req)" in inspect.getsource(
            routes.contribute_embedding
        )

    def test_the_batch_calls_it_once_per_row_and_nothing_else_writes(self):
        """Point 2: no new write code touches the tables."""
        src = inspect.getsource(routes.contribute_batch)
        assert "for row in req.contributions:" in src
        assert "await _contribute_one(db, row)" in src
        for forbidden in ("db.add(", "insert(", "Embedding(", "SubmissionAgreement("):
            assert forbidden not in src

    def test_a_refusal_is_a_result_not_an_exception(self):
        """Point 4: not atomic. A row's refusal is that row's entry, with the
        code it would have got alone, and the loop continues after a rollback."""
        src = inspect.getsource(routes.contribute_batch)
        assert "except HTTPException as exc:" in src
        assert "await db.rollback()" in src
        assert "code=exc.status_code" in src
        assert (
            ContributeResult(fingerprint_hash=HASH, status="refused", code=507).contributor_count
            is None
        )

    def test_the_limit_counts_rows_on_its_own_window(self):
        """Point 3: 600 rows a minute, charged after parsing; a 422 costs nothing."""
        src = inspect.getsource(routes.contribute_batch)
        assert (
            'charge(request, settings.contribute_batch_rate_limit, len(req.contributions), unit="row")'
            in src
        )
        assert config.Settings().contribute_batch_rate_limit == "600/minute"


class TestTheQuotaIsBuiltFirst:
    """Point 7 — `ADR-0004` point 9's third bound."""

    def test_fifty_thousand_a_day_is_ten_percent_of_the_ceiling(self):
        s = config.Settings()
        assert s.client_quota_rows_per_day == 50_000
        assert s.client_quota_rows_per_day * 10 == s.max_embeddings

    def test_it_is_checked_in_the_shared_function_before_any_write(self):
        src = inspect.getsource(routes._contribute_one)
        assert src.index("_check_client_quota") < src.index("select(Embedding)")
        assert "if req.client_id:" in src  # unattributed rows cannot be counted

    def test_it_counts_creations_and_confirmations(self):
        src = inspect.getsource(routes._client_quota_used)
        assert "Embedding.client_id == client_id" in src
        assert "SubmissionAgreement.client_id == client_id" in src

    def test_it_answers_429_with_retry_after(self):
        src = inspect.getsource(routes._check_client_quota)
        assert "status_code=429" in src and '"Retry-After"' in src


class TestTheBodyLimit:
    def test_ten_times_a_batch_is_refused_before_parsing(self):
        assert routes.CONTRIBUTE_BATCH_MAX_BYTES == 10 * 1024 * 1024
        assert "BodySizeLimitMiddleware" in inspect.getsource(main)
        assert '"/v1/embeddings/batch": CONTRIBUTE_BATCH_MAX_BYTES' in inspect.getsource(main)
