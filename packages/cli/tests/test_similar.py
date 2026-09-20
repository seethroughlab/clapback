"""`ADR-0022` point 7 — the reference client asks the commons what sounds like a track.

What matters here is what a neighbour is *shown as*: your own file only when this
store can prove it, a recording a person can open when somebody has named it,
and a bare hash otherwise — and that the seed's own row never comes back as its
own best match.
"""

from __future__ import annotations

import argparse
import inspect

import clapback_client
import numpy as np
import pytest

from clapback_cli import cli
from clapback_cli.store import Store

PIPELINE = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"
MBID = "b1a9c0de-1234-4abc-8def-0123456789ab"
HASH_MINE = "a" * 64  # alpha-one.flac, contributed by this store
HASH_SEED = "c" * 64  # seed.flac, contributed by this store
HASH_NAMED = "d" * 64  # somebody else's, named
HASH_BARE = "e" * 64  # somebody else's, unnamed


class FakeCorpus:
    base_url = "https://example.invalid"
    unreachable = False

    def __init__(self, *_a, **_k) -> None:
        self.asked: list[dict] = []

    def similar(self, embedding, *, limit=10, pipeline_version=None):
        self.asked.append({"limit": limit, "pipeline_version": pipeline_version})
        if self.unreachable:
            raise clapback_client.CorpusError("connection refused")
        rows = [
            {"fingerprint_hash": HASH_SEED, "similarity": 1.0, "recording_mbid": None},
            {"fingerprint_hash": HASH_MINE, "similarity": 0.93, "recording_mbid": None},
            {
                "fingerprint_hash": HASH_NAMED,
                "similarity": 0.91,
                "recording_mbid": MBID,
                "recording_claims": 2,
            },
            {"fingerprint_hash": HASH_BARE, "similarity": 0.90, "recording_mbid": None},
        ]
        return rows[:limit]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    for i, (name, h) in enumerate(
        (("seed.flac", HASH_SEED), ("alpha-one.flac", HASH_MINE), ("alpha-two.flac", None))
    ):
        p = tmp_path / name
        p.write_bytes(b"not really audio")
        v = np.zeros(512, dtype=np.float32)
        v[i] = 1.0
        s.add(str(p), p.stat().st_mtime, p.stat().st_size, v.tolist())
        s.entries[-1].fingerprint_hash = h
    s.pipeline_version = PIPELINE
    s.save()
    return s


@pytest.fixture
def corpus(monkeypatch):
    fake = FakeCorpus()
    monkeypatch.setattr(clapback_client, "Corpus", lambda *a, **k: fake)
    return fake


def _args(tmp_path, track, **kw):
    base = {"home": tmp_path, "url": "https://example.invalid", "limit": 10, "track": track}
    base.update(kw)
    return argparse.Namespace(**base)


class TestWhatANeighbourIsShownAs:
    def test_the_seed_never_matches_itself(self, corpus, store, tmp_path, capsys):
        cli.cmd_similar(_args(tmp_path, str(tmp_path / "seed.flac")))
        out = capsys.readouterr().out
        assert "1.0000" not in out
        assert out.startswith(f"sounds like: {tmp_path / 'seed.flac'}")

    def test_a_hash_this_store_contributed_is_your_file(self, corpus, store, tmp_path, capsys):
        cli.cmd_similar(_args(tmp_path, "seed.flac"))
        out = capsys.readouterr().out
        assert f"0.9300  {tmp_path / 'alpha-one.flac'}  (in your library)" in out

    def test_a_named_row_is_a_recording_you_can_open(self, corpus, store, tmp_path, capsys):
        cli.cmd_similar(_args(tmp_path, "seed.flac"))
        assert (
            f"0.9100  https://musicbrainz.org/recording/{MBID}  (2 claims)"
            in capsys.readouterr().out
        )

    def test_an_unnamed_row_is_the_hash_it_still_is(self, corpus, store, tmp_path, capsys):
        cli.cmd_similar(_args(tmp_path, "seed.flac"))
        assert f"0.9000  {HASH_BARE[:16]}…  (not yet named by anyone)" in capsys.readouterr().out

    def test_the_count_says_how_much_is_past_the_library(self, corpus, store, tmp_path, capsys):
        """The number ADR-0022 turns on, and the one the follow-up measures."""
        cli.cmd_similar(_args(tmp_path, "seed.flac"))
        assert (
            "3 shown · in your library 1 · not in your library 2 (1 not yet named)"
            in capsys.readouterr().out
        )

    def test_a_store_that_never_contributed_says_it_cannot_recognise_its_own(
        self, corpus, store, tmp_path, capsys
    ):
        for e in store.entries:
            e.fingerprint_hash = None
        store.save()
        cli.cmd_similar(_args(tmp_path, "seed.flac"))
        out = capsys.readouterr().out
        assert "(in your library)" not in out
        assert "contributed nothing" in out


class TestTheAsk:
    def test_it_asks_for_one_more_than_shown_and_only_comparable_vectors(
        self, corpus, store, tmp_path
    ):
        cli.cmd_similar(_args(tmp_path, "seed.flac", limit=2))
        assert corpus.asked == [{"limit": 3, "pipeline_version": PIPELINE}]

    def test_limit_is_honoured_after_the_seed_is_dropped(self, corpus, store, tmp_path, capsys):
        cli.cmd_similar(_args(tmp_path, "seed.flac", limit=2))
        out = capsys.readouterr().out
        assert "2 shown" in out and HASH_BARE[:16] not in out

    def test_it_needs_neither_the_embedder_nor_chromaprint(self):
        """The vector is already in the store. ADR-0009 point 5 for the fingerprint."""
        body = inspect.getsource(cli.cmd_similar).lower()
        assert "_embedder" not in body
        assert "acoustid" not in body and "chromaprint" not in body

    def test_an_unreachable_corpus_is_a_sentence(self, corpus, store, tmp_path):
        corpus.unreachable = True
        with pytest.raises(SystemExit, match="corpus unreachable"):
            cli.cmd_similar(_args(tmp_path, "seed.flac"))


class TestFindingTheSeed:
    def test_an_unindexed_path_says_so(self, corpus, store, tmp_path):
        with pytest.raises(SystemExit, match="not indexed"):
            cli.cmd_similar(_args(tmp_path, str(tmp_path / "nope.flac")))

    def test_an_ambiguous_fragment_lists_the_candidates(self, corpus, store, tmp_path):
        with pytest.raises(SystemExit, match="2 indexed tracks match"):
            cli.cmd_similar(_args(tmp_path, "alpha-"))

    def test_the_subcommand_is_wired(self):
        assert "similar" in inspect.getsource(cli.main)
