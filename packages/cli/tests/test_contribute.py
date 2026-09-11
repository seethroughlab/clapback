"""`ADR-0009` point 6 — contribution, and the three things it must not get wrong.

The interesting assertions here are all about restraint: not re-sending, not
mislabelling, and not minting an identifier for somebody who only ever searched
their own files.
"""

from __future__ import annotations

import argparse

import numpy as np
import pytest

from clapback_cli import cli
from clapback_cli import corpus as corpus_mod
from clapback_cli.store import Store

PIPELINE = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"


class FakeEmbed:
    PIPELINE_VERSION = PIPELINE


class FakeCorpus:
    """Records what was asked of it, in order."""

    base_url = "https://example.invalid"

    def __init__(self, *_a, **_k) -> None:
        self.calls: list[tuple[str, str]] = []
        self.holds: set[str] = set()
        self.contributed: list[dict] = []

    def has(self, fingerprint_hash: str, pipeline_version: str) -> bool:
        self.calls.append(("has", fingerprint_hash))
        return fingerprint_hash in self.holds

    def contribute(self, **kw):
        self.calls.append(("contribute", kw["fingerprint_hash"]))
        self.contributed.append(kw)
        return "contributed"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    for i, name in enumerate(("a.flac", "b.flac")):
        p = tmp_path / name
        p.write_bytes(b"not really audio")
        v = np.zeros(512, dtype=np.float32)
        v[i] = 1.0
        s.add(str(p), p.stat().st_mtime, p.stat().st_size, v.tolist())
    s.pipeline_version = PIPELINE
    s.save()
    return s


def _args(tmp_path, **kw):
    base = {"home": tmp_path, "url": "https://example.invalid", "limit": 0, "dry_run": False}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def wired(monkeypatch, store):
    fake = FakeCorpus()
    monkeypatch.setattr(cli, "_embedder", lambda: FakeEmbed)
    monkeypatch.setattr(corpus_mod, "Corpus", lambda *a, **k: fake)
    # One fingerprint per file, distinct and canonical-shaped.
    monkeypatch.setattr(
        "clapback_cli.fingerprint.fingerprint_file",
        lambda path: "AQAD" + ("a" if path.endswith("a.flac") else "b") * 20,
    )
    return fake


class TestItDoesNotManufactureAgreement:
    def test_every_track_is_looked_up_before_it_is_offered(self, wired, store, tmp_path):
        """A repeat POST increments `contributor_count` and writes a
        `submission_agreement` row, so re-sending a library would be one install
        independently agreeing with itself — the measurement `ADR-0008` rests on."""
        cli.cmd_contribute(_args(tmp_path))
        assert [k for k, _ in wired.calls] == ["has", "contribute", "has", "contribute"]

    def test_what_the_corpus_already_holds_is_not_resent(self, wired, store, tmp_path):
        cli.cmd_contribute(_args(tmp_path))
        already = {c["fingerprint_hash"] for c in wired.contributed}

        second = FakeCorpus()
        second.holds = already
        wired.calls.clear()
        import clapback_cli.corpus as cm

        original = cm.Corpus
        cm.Corpus = lambda *a, **k: second
        try:
            cli.cmd_contribute(_args(tmp_path))
        finally:
            cm.Corpus = original
        assert second.contributed == []


class TestItSendsWhatTheRecordsRequire:
    def test_it_declares_a_pipeline_and_a_client(self, wired, store, tmp_path):
        """`ADR-0006` point 1 and `ADR-0004` point 1. A new client with no legacy
        has no excuse for contributing unattributably."""
        cli.cmd_contribute(_args(tmp_path))
        for sent in wired.contributed:
            assert sent["pipeline_version"] == PIPELINE
            assert sent["client_id"]

    def test_the_checkpoint_agrees_with_the_pipeline_identity(self, wired, store, tmp_path):
        """Taken from the identity rather than written down twice, so the two
        cannot disagree about one fact."""
        cli.cmd_contribute(_args(tmp_path))
        assert all(s["clap_model_version"] == PIPELINE.split("+")[0] for s in wired.contributed)

    def test_each_track_is_sent_with_its_own_vector(self, wired, store, tmp_path):
        """The entries are addressed by position; looking them up by value would
        pick the wrong vector for two that compare equal."""
        cli.cmd_contribute(_args(tmp_path))
        sent = sorted(np.argmax(s["embedding"]) for s in wired.contributed)
        assert sent == [0, 1]


class TestItRefusesToMislabel:
    def test_a_store_from_another_pipeline_is_refused(self, wired, store, tmp_path):
        """Since `ADR-0006` phase 4 the pipeline identity is half the key, so
        sending these under the installed embedder's identity would assert a
        pipeline produced vectors it did not."""
        store.pipeline_version = "something+else+entirely"
        store.save()
        with pytest.raises(SystemExit) as exc:
            cli.cmd_contribute(_args(tmp_path))
        assert "re-index" in str(exc.value).lower()


class TestNothingLeavesTheMachineByDefault:
    def test_a_dry_run_sends_nothing(self, wired, store, tmp_path):
        cli.cmd_contribute(_args(tmp_path, dry_run=True))
        assert wired.calls == []

    def test_a_dry_run_mints_no_identifier(self, wired, store, tmp_path):
        """`ADR-0009` point 4 covers the fact that this install exists, too."""
        cli.cmd_contribute(_args(tmp_path, dry_run=True))
        assert Store(tmp_path).load().client_id is None

    def test_indexing_alone_mints_no_identifier(self, store, tmp_path):
        assert Store(tmp_path).load().client_id is None

    def test_the_identifier_survives_the_run(self, wired, store, tmp_path):
        cli.cmd_contribute(_args(tmp_path))
        first = Store(tmp_path).load().client_id
        assert first
        cli.cmd_contribute(_args(tmp_path))
        assert Store(tmp_path).load().client_id == first


class TestTheFingerprintCacheSurvives:
    def test_hashes_are_written_back_to_the_store(self, wired, store, tmp_path):
        """Fingerprinting spawns a process per file; an interrupted run must not
        throw that away."""
        cli.cmd_contribute(_args(tmp_path))
        assert all(e.fingerprint_hash for e in Store(tmp_path).load().entries)
