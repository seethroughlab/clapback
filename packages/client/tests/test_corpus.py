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
MBID = "1c6da765-da50-476b-a000-61e7cf45ded8"


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


class TestNamingARecording:
    """`ADR-0012`: a recording id is a claim, sent with the vector or afterwards."""

    def _sent(self, wire):
        return json.loads(wire.requests[-1].data)

    def test_contribute_sends_the_id_only_when_given(self, wire):
        wire.answer(201, {})
        Corpus("https://x.invalid").contribute(
            fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c"
        )
        assert "recording_mbid" not in self._sent(wire)
        wire.answer(201, {})
        Corpus("https://x.invalid").contribute(
            fingerprint_hash=HASH, embedding=VECTOR, pipeline_version=PIPELINE, client_id="c",
            recording_mbid=MBID,
        )
        assert self._sent(wire)["recording_mbid"] == MBID

    def test_claim_goes_to_its_own_path_with_no_vector(self, wire):
        """The endpoint exists so nobody re-sends a vector to name it."""
        wire.answer(201, {"status": "claimed", "recording_mbid": MBID, "recording_claims": 1})
        out = Corpus("https://x.invalid").claim(
            fingerprint_hash=HASH, recording_mbid=MBID, client_id="c"
        )
        req = wire.requests[-1]
        assert req.full_url.endswith("/v1/recordings/claims")
        assert "embedding" not in json.loads(req.data)
        assert out["recording_claims"] == 1

    def test_claiming_an_absent_row_says_to_contribute_first(self, wire):
        wire.answer(404, {"detail": "no row"})
        with pytest.raises(CorpusError, match="contribute it first"):
            Corpus("https://x.invalid").claim(
                fingerprint_hash=HASH, recording_mbid=MBID, client_id="c"
            )

    def test_the_method_says_never_to_resend_the_vector(self):
        import inspect

        doc = " ".join((inspect.getdoc(Corpus.claim) or "").split()).lower()
        assert "never re-send the vector" in doc


class TestSimilarAndRecording:
    def test_similar_returns_neighbours_with_their_recordings(self, wire):
        wire.answer(200, {"neighbours": [
            {"fingerprint_hash": HASH, "similarity": 1.0, "pipeline_version": PIPELINE,
             "recording_mbid": MBID, "recording_claims": 1},
            {"fingerprint_hash": "e2" * 32, "similarity": 0.9, "pipeline_version": PIPELINE,
             "recording_mbid": None, "recording_claims": 0},
        ], "searched": 2})
        n = Corpus("https://x.invalid").similar(VECTOR, limit=2, pipeline_version=PIPELINE)
        assert [x["recording_mbid"] for x in n] == [MBID, None]
        assert json.loads(wire.requests[-1].data)["pipeline_version"] == PIPELINE

    def test_recording_returns_rows_and_empty_when_unclaimed(self, wire):
        wire.answer(200, {"recording_mbid": MBID, "embeddings": [
            {"fingerprint_hash": HASH, "pipeline_version": PIPELINE, "embedding": VECTOR,
             "contributor_count": 1, "recording_claims": 1}]})
        rows = Corpus("https://x.invalid").recording(MBID, pipeline_version=PIPELINE)
        assert rows[0]["fingerprint_hash"] == HASH
        assert "+" not in urlsplit(wire.requests[-1].full_url).query
        wire.answer(404)
        assert Corpus("https://x.invalid").recording(MBID) == []


class TestLookupByRecording:
    """`ADR-0019` point 3: the hash is exact within a fingerprinting path and may
    differ across two, so a tool that holds a MusicBrainz recording id asks by it
    first. The id is the same on every path."""

    def test_by_recording_goes_to_the_recording_route_and_returns_one_row(self, wire):
        wire.answer(200, {"recording_mbid": MBID, "embeddings": [
            {"fingerprint_hash": HASH, "pipeline_version": PIPELINE, "embedding": VECTOR,
             "contributor_count": 2, "recording_claims": 3},
            {"fingerprint_hash": "e2" * 32, "pipeline_version": PIPELINE, "embedding": VECTOR,
             "contributor_count": 1, "recording_claims": 1},
        ]})
        row = Corpus("https://x.invalid").lookup(recording_mbid=MBID, pipeline_version=PIPELINE)
        url = urlsplit(wire.requests[-1].full_url)
        assert url.path == f"/v1/recordings/{MBID}"
        assert parse_qs(url.query)["pipeline_version"] == [PIPELINE]
        # The most-claimed row, in the shape a hash lookup returns.
        assert row["fingerprint_hash"] == HASH
        assert row["embedding"] == VECTOR
        assert row["recording_mbid"] == MBID
        assert row["recording_claims"] == 3

    def test_an_unclaimed_recording_is_none_like_a_missing_hash(self, wire):
        wire.answer(404)
        assert Corpus("https://x.invalid").lookup(recording_mbid=MBID) is None

    def test_exactly_one_key(self):
        c = Corpus("https://x.invalid")
        with pytest.raises(ValueError):
            c.lookup()
        with pytest.raises(ValueError):
            c.lookup(HASH, recording_mbid=MBID)

    def test_the_docstring_states_the_rule(self):
        import inspect
        doc = " ".join((inspect.getdoc(Corpus.lookup) or "").split()).lower()
        assert "by `recording_mbid=` if you hold one, by hash otherwise" in doc
        assert "contribute under your hash either way" in doc


class TestLookupWithoutAPipeline:
    """0.2.1: the filter became optional for a tool with no embedder at all —
    Picard's plugin in lookup-only mode — which wants the corpus's own row and
    the pipeline it says it came from."""

    def test_no_pipeline_means_no_query_string(self):
        seen = {}

        def fake(method, path, body=None):
            seen["path"] = path
            return 200, {"embedding": [0.0] * 512, "pipeline_version": "corpus-pipe", "contributor_count": 1}

        c = Corpus("https://x.invalid")
        c._request = fake  # type: ignore[method-assign]
        row = c.lookup(HASH)
        assert seen["path"] == f"/v1/embeddings/{HASH}"
        assert row["pipeline_version"] == "corpus-pipe"

    def test_a_pipeline_is_still_escaped_when_given(self):
        seen = {}

        def fake(method, path, body=None):
            seen["path"] = path
            return 404, None

        c = Corpus("https://x.invalid")
        c._request = fake  # type: ignore[method-assign]
        assert c.lookup(HASH, PIPELINE) is None
        assert "%2B" in seen["path"] and "+" not in seen["path"]
