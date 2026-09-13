"""What goes over the wire, and what the client does when the corpus pushes back.

No network. `urllib.request.urlopen` is replaced with a fake that records every
request and answers from a script, so each test states exactly which HTTP
exchange it is about. The properties here are the ones a plug-in author would
otherwise have to rediscover: the query-string escaping that turns a `+` into a
miss, the lookup that must precede every write, the defaults derived from the
pipeline identity, and the backoff that keeps a tool from being the client an
operator has to block.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from clapback_client import corpus as corpus_mod
from clapback_client.corpus import Corpus, CorpusError

PIPELINE = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"
HASH = "d1" * 32
VECTOR = [0.0] * 511 + [1.0]


class FakeResponse(io.BytesIO):
    def __init__(self, status: int, body: dict | None):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture
def wire(monkeypatch):
    """A scripted server. Append (status, body) answers; read `.requests` back."""
    log = []
    answers = []

    def fake_urlopen(req, timeout=None):
        log.append(req)
        status, body = answers.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(
                req.full_url, status, "err", {}, io.BytesIO(json.dumps(body or {}).encode())
            )
        return FakeResponse(status, body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(corpus_mod.time, "sleep", lambda s: log.append(("slept", s)))

    class Wire:
        requests = log

        @staticmethod
        def answer(status, body=None):
            answers.append((status, body))

    return Wire


class TestTheLookup:
    def test_the_pipeline_identity_is_escaped(self, wire):
        """`+` means a space in a query string, so an unescaped identity 404s as
        though the recording were absent. `ADR-0006` records what that cost."""
        wire.answer(404)
        Corpus("https://x.invalid").lookup(HASH, PIPELINE)
        url = wire.requests[0].full_url
        assert "+" not in urlsplit(url).query
        assert parse_qs(urlsplit(url).query)["pipeline_version"] == [PIPELINE]

    def test_a_row_comes_back_whole(self, wire):
        row = {"fingerprint_hash": HASH, "embedding": VECTOR, "contributor_count": 2}
        wire.answer(200, row)
        assert Corpus("https://x.invalid").lookup(HASH, PIPELINE) == row

    def test_absent_is_none_and_has_is_false(self, wire):
        wire.answer(404)
        wire.answer(404)
        c = Corpus("https://x.invalid")
        assert c.lookup(HASH, PIPELINE) is None
        assert c.has(HASH, PIPELINE) is False

    def test_anything_else_is_an_error_not_a_miss(self, wire):
        """A 500 that read as 'absent' would contribute a duplicate on the retry.
        Familiar's backfill made exactly this distinction for the same reason."""
        wire.answer(500)
        with pytest.raises(CorpusError):
            Corpus("https://x.invalid").lookup(HASH, PIPELINE)


class TestTheContribution:
    def _sent(self, wire):
        return json.loads(wire.requests[-1].data)

    def test_it_sends_what_the_records_require(self, wire):
        """`ADR-0006` point 4 and `ADR-0004` point 1: no defaults for these two."""
        wire.answer(201, {"status": "created"})
        Corpus("https://x.invalid").contribute(
            fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c-1"
        )
        body = self._sent(wire)
        assert body["pipeline_version"] == PIPELINE
        assert body["client_id"] == "c-1"
        assert body["fingerprint_hash"] == HASH
        assert len(body["embedding"]) == 512

    def test_the_checkpoint_is_derived_from_the_identity(self, wire):
        """Taken from the identity rather than written twice, so the two cannot
        disagree about one fact."""
        wire.answer(201, {})
        Corpus("https://x.invalid").contribute(
            fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c"
        )
        assert self._sent(wire)["clap_model_version"] == "laion/clap-htsat-unfused"
        assert self._sent(wire)["analysis_version"] == 1

    def test_a_caller_may_override_the_recorded_columns(self, wire):
        wire.answer(201, {})
        Corpus("https://x.invalid").contribute(
            fingerprint_hash=HASH,
            embedding=VECTOR,
            pipeline_version=PIPELINE,
            client_id="c",
            clap_model_version="laion/clap-htsat-unfused:v1",
            analysis_version=8,
        )
        assert self._sent(wire)["clap_model_version"] == "laion/clap-htsat-unfused:v1"
        assert self._sent(wire)["analysis_version"] == 8

    def test_it_names_itself(self, wire):
        """Not identity — that is `client_id` — but an operator reading logs
        should be able to tell this from a browser."""
        wire.answer(201, {})
        Corpus("https://x.invalid").contribute(
            fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c"
        )
        assert wire.requests[-1].get_header("User-agent") == "clapback-client"


class TestWhenTheCorpusPushesBack:
    def test_a_rate_limit_is_waited_out_then_retried(self, wire):
        wire.answer(429)
        wire.answer(429)
        wire.answer(201, {})
        result = Corpus("https://x.invalid").contribute(
            fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c"
        )
        assert result == "contributed"
        posts = [r for r in wire.requests if not isinstance(r, tuple)]
        sleeps = [r for r in wire.requests if isinstance(r, tuple)]
        assert len(posts) == 3
        assert [s for _, s in sleeps] == list(corpus_mod._RETRY_DELAYS[:2])

    def test_a_persistent_rate_limit_gives_up_cleanly(self, wire):
        for _ in range(len(corpus_mod._RETRY_DELAYS) + 1):
            wire.answer(429)
        with pytest.raises(CorpusError, match="rate limited"):
            Corpus("https://x.invalid").contribute(
                fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c"
            )

    def test_a_malformed_submission_is_reported_not_retried(self, wire):
        """A 422 is the server saying what was wrong. Retrying it is noise."""
        wire.answer(422, {"detail": "pipeline_version: field required"})
        with pytest.raises(CorpusError, match="malformed"):
            Corpus("https://x.invalid").contribute(
                fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c"
            )
        assert len([r for r in wire.requests if not isinstance(r, tuple)]) == 1

    def test_a_full_corpus_is_the_corpus_working(self, wire):
        """`ADR-0004` point 9's ceiling. Stop rather than hammer it."""
        wire.answer(507, {"detail": "ceiling"})
        with pytest.raises(CorpusError, match="full"):
            Corpus("https://x.invalid").contribute(
                fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c"
            )


class TestTheContractIsOnlyHTTP:
    def test_nothing_here_opens_a_database(self):
        """`ADR-0005` point 12: a direct connection is a second write path with
        none of the guarantees. A client must not even be able to."""
        import inspect

        src = inspect.getsource(corpus_mod)
        for forbidden in ("psycopg", "asyncpg", "sqlalchemy", "postgresql://"):
            assert forbidden not in src
