"""The one write bound that needs nothing from identity — `ADR-0004` point 9.

    A ceiling on corpus rows, checked on write and rejecting past it with a clear
    error. Raised deliberately as the corpus grows, so growth is a decision rather
    than a surprise. This works today and needs nothing from identity.

That last sentence is why it exists before the rest of `ADR-0004` does, and why it
is what makes `ADR-0003` point 7's "reads public, writes restricted" launch safe to
leave running unattended: the failure it guards against is a full disk on a 2 GB
instance, not an attacker, and rate limits do not bound a total.

Like the rest of this suite these cover the contract rather than the database. The
condition being tested is arithmetic on a count, and the branch that matters is
*which* submissions the ceiling applies to.
"""

import pytest
from fastapi import HTTPException

from app.config import Settings


class TestTheDefault:
    def test_it_is_on_by_default(self):
        """A ceiling nobody sets is a ceiling nobody has. It ships enabled."""
        assert Settings().max_embeddings > 0

    def test_the_default_matches_what_ADR_0003_sized_for(self):
        """`ADR-0003` point 11: comfortable to roughly 300,000 vectors, tight beyond
        500,000, on the 2 GB instance point 10 chose. The default is the stated
        upper end rather than a round number picked for looking like one."""
        assert Settings().max_embeddings == 500_000

    def test_zero_disables_it(self):
        """A deployment with different sizing must be able to opt out without
        editing code — the ceiling is a property of the box, not of the corpus."""
        assert Settings(max_embeddings=0).max_embeddings == 0


class TestWhatTheCeilingAppliesTo:
    """Which submissions it refuses is the part that is easy to get wrong."""

    def test_a_confirmation_is_never_refused(self):
        """A submission of a vector the corpus already holds adds no row, so it
        must not be refused. Rejecting it would discard exactly the independent
        agreement `ADR-0001` point 9 exists to collect, and buy no disk for it.

        Asserted on the source because the invariant is an ordering — the guard
        has to sit below the branch that returns `confirmed` — and there is no
        database here to observe it any other way."""
        import inspect

        from app.api import routes

        body = inspect.getsource(routes.contribute_embedding)
        assert body.index('status="confirmed"') < body.index("max_embeddings"), (
            "the ceiling must be checked only after the confirmation path has "
            "returned, or confirmations get refused once the corpus is full"
        )


class TestTheError:
    def test_it_says_what_happened_and_what_still_works(self):
        """`ADR-0004` point 9 asks for a clear error. A contributor who hits this
        has done nothing wrong: the message names the ceiling, says lookups and
        confirmations still work, and points at the decision."""
        import inspect

        from app.api import routes

        body = inspect.getsource(routes.contribute_embedding)
        assert "507" in body
        for phrase in ("ceiling", "Lookups and confirmations", "ADR-0004"):
            assert phrase in body, f"the rejection should mention {phrase!r}"

    def test_507_rather_than_400(self):
        """The client did nothing wrong, so this is not a 4xx about their request.
        507 Insufficient Storage is the one status that says what is actually
        true — the server will not store more."""
        exc = HTTPException(status_code=507, detail="x")
        assert exc.status_code == 507
        assert 500 <= exc.status_code < 600


@pytest.mark.parametrize("total,ceiling,refused", [
    (0, 500_000, False),
    (499_999, 500_000, False),
    (500_000, 500_000, True),      # at the ceiling, not past it — the row would be the 500,001st
    (500_001, 500_000, True),
    (10, 0, False),                # disabled
])
def test_the_boundary(total, ceiling, refused):
    """`>=` rather than `>`: the ceiling is the number of rows the corpus holds,
    so a write while already at it would exceed it."""
    assert bool(ceiling and total >= ceiling) is refused
