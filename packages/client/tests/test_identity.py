"""`ADR-0004` point 1 — an identifier, not an identity."""

from __future__ import annotations

import uuid

from clapback_client.identity import ensure_client_id, mint_client_id


class TestMinting:
    def test_it_is_a_uuid_and_nothing_else(self):
        """Derived from nothing about the machine or its owner: two mints on the
        same machine differ, and each parses as a random UUID."""
        a, b = mint_client_id(), mint_client_id()
        assert a != b
        assert uuid.UUID(a).version == 4


class TestTheFileHelper:
    def test_it_mints_once_and_then_returns_the_same_one(self, tmp_path):
        p = tmp_path / "deep" / "client_id"
        first = ensure_client_id(p)
        assert p.read_text().strip() == first
        assert ensure_client_id(p) == first

    def test_deleting_it_makes_a_new_contributor(self, tmp_path):
        """The one promise the identifier makes to the user."""
        p = tmp_path / "client_id"
        first = ensure_client_id(p)
        p.unlink()
        assert ensure_client_id(p) != first

    def test_an_empty_file_is_treated_as_absent(self, tmp_path):
        p = tmp_path / "client_id"
        p.write_text("\n")
        assert uuid.UUID(ensure_client_id(p))
