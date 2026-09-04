"""The capability ADR-0002 says the commons exists to provide.

    Given a vector, it returns the nearest recordings in the corpus. This is the
    capability the commons exists to provide; exact-key lookup is a cache, and a
    cache is not worth a public endpoint.

Decided in ADR-0002 point 1 and unbuilt through three later records. These cover
the contract — what it takes, what it returns, and the two things about it that
are deliberate rather than accidental.
"""

import inspect

import pytest
from pydantic import ValidationError

from app.api import routes


class TestTheRequest:
    def test_it_takes_a_full_vector(self):
        r = routes.SimilarRequest(embedding=[0.0] * 512)
        assert len(r.embedding) == 512

    @pytest.mark.parametrize("n", [511, 513, 0])
    def test_a_wrong_length_vector_is_refused(self, n):
        """512 is the shape of the space. A 511-float query is not a near miss,
        it is a different question."""
        with pytest.raises(ValidationError):
            routes.SimilarRequest(embedding=[0.0] * n)

    def test_limit_is_bounded(self):
        """Unbounded, one request returns the corpus."""
        with pytest.raises(ValidationError):
            routes.SimilarRequest(embedding=[0.0] * 512, limit=101)

    def test_analysis_version_defaults_to_every_pipeline(self):
        """Filtering to a version the caller guesses wrong returns nothing at
        all, silently — which is worse than returning across pipelines and
        labelling each result with the one it came from."""
        assert routes.SimilarRequest(embedding=[0.0] * 512).analysis_version is None


class TestTheResponse:
    def test_each_neighbour_says_which_pipeline_produced_it(self):
        """Vectors from two pipelines are not comparable (ADR-0006), so a result
        that did not name its pipeline would invite exactly that comparison."""
        fields = set(routes.Neighbour.model_fields)
        assert {"analysis_version", "clap_model_version"} <= fields

    def test_it_reports_similarity_not_distance(self):
        """`<=>` is cosine distance. Every other number in this project is quoted
        as similarity, and leaving the sign for a caller to notice is a trap."""
        assert "similarity" in routes.Neighbour.model_fields
        assert "distance" not in routes.Neighbour.model_fields
        body = inspect.getsource(routes.similar)
        assert "1 - " in body

    def test_it_says_how_much_was_searched(self):
        """20 neighbours out of 22,000 and 20 out of 20 are different answers."""
        assert "searched" in routes.SimilarResponse.model_fields


class TestWhatItIsNot:
    def test_it_is_a_read_and_carries_the_lookup_limit(self):
        """POST only because 512 floats do not fit in a URL. Charging it the
        contribution rate limit would throttle reading at the rate meant to bound
        writing."""
        body = inspect.getsource(routes.similar)
        assert "settings.lookup_rate_limit" in body
        assert "contribute_rate_limit" not in body

    def test_the_docstring_admits_it_returns_hashes(self):
        """ADR-0002 point 4: 'Do not ship the endpoint and call the capability
        delivered.' A caller who does not hold the audio cannot resolve a hash,
        and that belongs in the docstring rather than in a support thread."""
        doc = routes.similar.__doc__ or ""
        assert "hash" in doc.lower()
        assert "ADR-0002" in doc
