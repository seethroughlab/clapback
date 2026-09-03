"""The measurement Phase 0 exists for: how closely do independent submissions agree?

**The first tests in this repository.** `pyproject.toml` has declared pytest,
pytest-asyncio and httpx as dev dependencies since the beginning and nothing was
ever written against them; `fly-deploy.yml` pushes to production on every commit to
`main` with no gate at all.

These cover the arithmetic and the contract, not the database. `_cosine_similarity`
is a pure function and is where the subtle failures live — a mismatched length or a
zero vector recorded as similarity 0.0 would poison the very distribution this is
built to measure, and it would look exactly like a contributor disagreeing.
"""

import math

import pytest

from app.api.routes import EmbeddingRequest, _cosine_similarity


def _vec(*head: float) -> list[float]:
    """A 512-dim vector with `head` at the front and zeros behind it."""
    return list(head) + [0.0] * (512 - len(head))


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def test_identical_vectors_are_exactly_one():
    """The expected case, and the one the whole design hopes is common.

    Two machines running the pinned checkpoint over the same audio should land
    here or within a rounding error of it.
    """
    v = _vec(0.3, -0.7, 0.1)
    assert _cosine_similarity(v, v) == pytest.approx(1.0)


def test_a_hair_of_float_error_does_not_exceed_one():
    """Float error can push an identical pair a fraction past 1.0.

    Left unclamped it would show up in the distribution as an impossible
    similarity, which is worse than useless — it is a number that makes the
    measurement look broken.
    """
    a = _vec(1.0, 2.0, 3.0)
    b = [x * (1 + 1e-16) for x in a]
    assert _cosine_similarity(a, b) <= 1.0


def test_orthogonal_and_opposed_vectors():
    assert _cosine_similarity(_vec(1.0, 0.0), _vec(0.0, 1.0)) == pytest.approx(0.0)
    assert _cosine_similarity(_vec(1.0, 0.0), _vec(-1.0, 0.0)) == pytest.approx(-1.0)


def test_a_small_perturbation_stays_close_to_one():
    """What honest disagreement is expected to look like.

    If BLAS or hardware differences move a vector slightly, similarity should sit
    just below 1.0 rather than anywhere near the middle of the range — which is
    what makes a threshold possible at all.
    """
    a = _vec(*[0.1] * 16)
    b = _vec(*[0.1 + 1e-6] * 16)
    assert _cosine_similarity(a, b) > 0.9999


# ---------------------------------------------------------------------------
# Broken input is not disagreement
# ---------------------------------------------------------------------------


def test_a_zero_vector_returns_none_rather_than_zero():
    """A zero-magnitude vector has no direction, so it has no similarity.

    Recording it as 0.0 would put a broken submission in the middle of the
    distribution and make honest divergence impossible to read.
    """
    assert _cosine_similarity(_vec(), _vec(1.0)) is None
    assert _cosine_similarity(_vec(1.0), _vec()) is None


def test_mismatched_lengths_return_none():
    """Not a disagreement — a comparison that cannot be made."""
    assert _cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) is None


def test_no_result_is_ever_outside_the_valid_range():
    """Whatever comes back is a similarity or nothing. Never a number that isn't one."""
    cases = [
        (_vec(1.0), _vec(1.0)),
        (_vec(1.0, 1.0), _vec(-1.0, -1.0)),
        (_vec(*[1e-8] * 32), _vec(*[1e8] * 32)),
        (_vec(*[-3.0] * 512), _vec(*[7.0] * 512)),
    ]
    for a, b in cases:
        result = _cosine_similarity(a, b)
        assert result is not None
        assert -1.0 <= result <= 1.0
        assert not math.isnan(result)


# ---------------------------------------------------------------------------
# The request contract
# ---------------------------------------------------------------------------


def test_client_id_is_optional_so_existing_clients_keep_working():
    """Every client in the field predates this field.

    Making it required would reject all 44 contributing installations at once, and
    the whole point of Phase 0 is that it changes nothing observable.
    """
    req = EmbeddingRequest(
        fingerprint_hash="a" * 64,
        embedding=_vec(1.0),
        analysis_version=1,
        clap_model_version="laion/clap-htsat-unfused:v1",
    )
    assert req.client_id is None


def test_client_id_is_accepted_when_sent():
    req = EmbeddingRequest(
        fingerprint_hash="a" * 64,
        embedding=_vec(1.0),
        analysis_version=1,
        clap_model_version="laion/clap-htsat-unfused:v1",
        client_id="0d1b2c3d-4e5f-6789-abcd-ef0123456789",
    )
    assert req.client_id == "0d1b2c3d-4e5f-6789-abcd-ef0123456789"


def test_an_overlong_client_id_is_rejected():
    """It is an opaque token, not a place to put arbitrary text."""
    with pytest.raises(ValueError):
        EmbeddingRequest(
            fingerprint_hash="a" * 64,
            embedding=_vec(1.0),
            analysis_version=1,
            clap_model_version="v1",
            client_id="x" * 65,
        )


def test_the_storage_precision_floor_is_documented_not_assumed():
    """A byte-identical resubmission scores 0.99999994, not 1.0.

    `pgvector`'s column is float4, so the stored vector comes back truncated to
    single precision while the submitted one is float64. Measured end-to-end
    against a real database. The `identical` bucket on the dashboard starts at
    0.999999 precisely so this lands in it rather than looking like disagreement.
    """
    v = _vec(*[0.123456789012345] * 64)
    truncated = [float(f"{x:.7g}") for x in v]  # what float4 storage does to it
    similarity = _cosine_similarity(v, truncated)
    assert similarity is not None
    assert similarity > 0.999999, "must land in the identical bucket"
    assert similarity < 1.0 or similarity == pytest.approx(1.0)
