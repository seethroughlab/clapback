"""The store, and the two properties that would produce wrong answers silently."""

import numpy as np
import pytest

from clapback_cli.store import Store


def _unit(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


class TestRoundTrip:
    def test_what_goes_in_comes_back(self, tmp_path):
        s = Store(tmp_path)
        v = _unit(1)
        s.add("/music/a.flac", 1.0, 10, v)
        s.pipeline_version = "x+pool1+fp32"
        s.save()

        again = Store(tmp_path).load()
        assert [e.path for e in again.entries] == ["/music/a.flac"]
        assert again.pipeline_version == "x+pool1+fp32"
        assert np.allclose(again.vectors[0], v, atol=1e-6)

    def test_an_empty_store_loads(self, tmp_path):
        s = Store(tmp_path).load()
        assert len(s.entries) == 0 and s.nearest(np.zeros(512), 5) == []


class TestTheHalvesMustAgree:
    """Vectors and entries are kept in step by position, so a mismatch does not
    fail — it attributes every result to the wrong file."""

    def test_a_mismatched_store_is_discarded_rather_than_trusted(self, tmp_path):
        s = Store(tmp_path)
        s.add("/music/a.flac", 1.0, 10, _unit(1))
        s.add("/music/b.flac", 2.0, 20, _unit(2))
        s.save()
        # lose one vector, as a half-finished write would
        np.save(s.vectors_path, s.vectors[:1])

        again = Store(tmp_path).load()
        assert len(again.entries) == 0, "a store whose halves disagree must not be used"


class TestNearest:
    def test_a_vector_is_nearest_to_itself(self, tmp_path):
        s = Store(tmp_path)
        for i in range(5):
            s.add(f"/music/{i}.flac", 1.0, 1, _unit(i))
        hits = s.nearest(np.asarray(s.vectors[2]), limit=3)
        assert hits[0][0] == 2
        assert hits[0][1] == pytest.approx(1.0, abs=1e-5)

    def test_results_are_ordered_best_first(self, tmp_path):
        s = Store(tmp_path)
        for i in range(10):
            s.add(f"/music/{i}.flac", 1.0, 1, _unit(i))
        scores = [sc for _, sc in s.nearest(np.asarray(s.vectors[0]), limit=5)]
        assert scores == sorted(scores, reverse=True)

    def test_similarity_needs_no_conversion(self, tmp_path):
        """Unit vectors mean the dot product *is* the cosine. Nothing here should
        be subtracting from one or dividing by a norm."""
        import inspect

        body = inspect.getsource(Store.nearest)
        assert "1 -" not in body and "linalg.norm" not in body
