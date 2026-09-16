"""The contract, as the Picard plugin follows it — with the corpus scripted.

What must hold is the same thing the beets plugin's tests hold: look up before
anything is sent; a repeat is never a contribution; a claim names a row the
corpus already has; nothing is computed when nobody asked; lookup-only mode says
so rather than pretending; and a hash is shown as a hash.
"""

from __future__ import annotations

import pytest

MBID = "1c6da765-da50-476b-a000-61e7cf45ded8"
FP = "AQADfingerprint"
PIPELINE = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"


class ScriptedCorpus:
    """Answers lookups from a dict; records every write."""

    def __init__(self, rows=None, neighbours=None, down=False):
        self.rows = rows or {}
        self.neighbours_out = neighbours or []
        self.down = down
        self.claims: list[dict] = []
        self.contributions: list[dict] = []
        self.lookups: list[tuple[str, str | None]] = []
        self.similar_calls: list[dict] = []

    def lookup(self, key=None, pipeline=None, *, recording_mbid=None, acoustid_track_id=None, pipeline_version=None):
        """Rows are keyed by hash, by MBID, or by ("acoustid", id), as the real
        corpus answers each kind of key (`ADR-0019` points 3 and 6)."""
        from picard.plugins.clapback.clapback_client import CorpusError

        if self.down:
            raise CorpusError("https://corpus.invalid is unreachable")
        pipeline = pipeline if pipeline is not None else pipeline_version
        k = recording_mbid or (("acoustid", acoustid_track_id) if acoustid_track_id else key)
        self.lookups.append((k, pipeline))
        row = self.rows.get(k)
        if row is None:
            return None
        if pipeline is not None and row.get("pipeline_version") != pipeline:
            return None
        return row

    def lookup_many(self, keys, pipeline=None, *, vectors=True):
        for k in keys:
            if isinstance(k, tuple):
                yield k, self.lookup(acoustid_track_id=k[1], pipeline=pipeline)
            elif len(k) == 36:
                yield k, self.lookup(recording_mbid=k, pipeline=pipeline)
            else:
                yield k, self.lookup(k, pipeline)

    def claim(self, **kw):
        self.claims.append(kw)
        return {"status": "claimed"}

    def contribute(self, **kw):
        self.contributions.append(kw)
        return "created"

    def similar(self, embedding, *, limit, pipeline_version=None):
        self.similar_calls.append({"embedding": embedding, "limit": limit, "pipeline_version": pipeline_version})
        return self.neighbours_out


class FakeEmbedder:
    PIPELINE_VERSION = PIPELINE

    def __init__(self):
        self.embedded: list[str] = []

    def embed_file(self, path):
        self.embedded.append(path)
        return [0.0] * 511 + [1.0]


def run(core, corpus, **over):
    kw = dict(
        fingerprint=FP,
        path="/music/a.flac",
        recording_mbid=MBID,
        contribute=False,
        client_id=lambda: "install-1",
        embedder=None,
    )
    kw.update(over)
    return core.process(corpus, **kw)


@pytest.fixture
def key(core):
    return core.hash_fingerprint(FP)


class TestLookupFirst:
    def test_a_held_recording_is_found_and_nothing_is_sent(self, core, key):
        c = ScriptedCorpus({key: {"embedding": [0.1] * 512, "contributor_count": 3, "pipeline_version": PIPELINE}})
        out = run(core, c)
        assert out.status == "found" and "3 contributions" in out.detail
        assert c.contributions == [] and c.claims == []

    def test_lookup_only_mode_asks_by_any_pipeline(self, core, key):
        c = ScriptedCorpus({key: {"embedding": [0.1] * 512, "contributor_count": 1, "pipeline_version": "other"}})
        assert run(core, c).status == "found"
        assert c.lookups == [(MBID, None), (key, None)]  # by id first, then by hash

    def test_with_an_embedder_the_lookup_is_by_its_pipeline(self, core, key):
        c = ScriptedCorpus()
        run(core, c, embedder=FakeEmbedder(), contribute=True)
        assert c.lookups == [(MBID, PIPELINE), (key, PIPELINE)]

    def test_no_fingerprint_means_nothing_is_asked(self, core):
        c = ScriptedCorpus()
        out = run(core, c, fingerprint=None)
        assert out.status == "unfingerprinted" and c.lookups == []

    def test_a_corpus_that_is_down_is_an_error_not_a_miss(self, core):
        out = run(core, ScriptedCorpus(down=True), contribute=True, embedder=FakeEmbedder())
        assert out.status == "error" and "unreachable" in out.detail


class TestNaming:
    def test_a_held_row_is_named_under_contribute(self, core, key):
        c = ScriptedCorpus({key: {"embedding": [], "contributor_count": 1, "pipeline_version": PIPELINE}})
        out = run(core, c, contribute=True)
        assert c.claims == [
            {"fingerprint_hash": key, "recording_mbid": MBID, "acoustid_track_id": None, "client_id": "install-1"}
        ]
        assert out.named == MBID and "named by you" in out.detail
        assert c.contributions == []

    def test_naming_is_not_repeated(self, core, key):
        c = ScriptedCorpus({key: {"embedding": [], "contributor_count": 1, "pipeline_version": PIPELINE}})
        run(core, c, contribute=True, already_named=MBID)
        assert c.claims == []

    def test_no_claim_without_contribute(self, core, key):
        c = ScriptedCorpus({key: {"embedding": [], "contributor_count": 1, "pipeline_version": PIPELINE}})
        run(core, c, contribute=False)
        assert c.claims == []

    def test_no_claim_without_an_id(self, core, key):
        c = ScriptedCorpus({key: {"embedding": [], "contributor_count": 1, "pipeline_version": PIPELINE}})
        run(core, c, contribute=True, recording_mbid=None)
        assert c.claims == []

    def test_the_id_is_lower_cased(self, core, key):
        c = ScriptedCorpus({key: {"embedding": [], "contributor_count": 1, "pipeline_version": PIPELINE}})
        run(core, c, contribute=True, recording_mbid=MBID.upper())
        assert c.claims[0]["recording_mbid"] == MBID

    def test_the_client_id_is_only_asked_for_when_something_is_sent(self, core, key):
        asked = []

        def cid():
            asked.append(1)
            return "x"

        held = ScriptedCorpus({key: {"embedding": [], "contributor_count": 1, "pipeline_version": PIPELINE}})
        run(core, ScriptedCorpus(), contribute=False, client_id=cid)
        run(core, held, contribute=False, client_id=cid)
        assert asked == []


class TestContributing:
    def test_absent_and_off_computes_nothing(self, core):
        e = FakeEmbedder()
        out = run(core, ScriptedCorpus(), contribute=False, embedder=e)
        assert out.status == "absent" and e.embedded == []

    def test_absent_on_and_no_embedder_says_lookup_only(self, core):
        c = ScriptedCorpus()
        out = run(core, c, contribute=True, embedder=None)
        assert out.status == "lookup-only" and "clapback-embed is not installed" in out.detail
        assert c.contributions == []

    def test_absent_on_and_embedder_contributes_with_the_id(self, core, key):
        c, e = ScriptedCorpus(), FakeEmbedder()
        out = run(core, c, contribute=True, embedder=e)
        assert e.embedded == ["/music/a.flac"]
        (sent,) = c.contributions
        assert sent["fingerprint_hash"] == key and sent["pipeline_version"] == PIPELINE
        assert sent["recording_mbid"] == MBID and sent["client_id"] == "install-1"
        assert len(sent["embedding"]) == 512
        assert out.status == "contributed" and out.named == MBID

    def test_a_file_that_will_not_embed_costs_one_file(self, core):
        class Bad(FakeEmbedder):
            def embed_file(self, path):
                raise RuntimeError("decode failed")

        out = run(core, ScriptedCorpus(), contribute=True, embedder=Bad())
        assert out.status == "error" and "decode failed" in out.detail


class TestTheIdsAFileHolds:
    """`ADR-0019` points 3 and 6: by recording id first, then AcoustID id, then
    hash; both ids sent; a batch's prefetched answer used when given."""

    ACOUSTID = "9ff43b6a-4f16-427c-93c2-92307ca505e0"

    def test_a_recording_id_finds_a_row_under_another_paths_key(self, core, key):
        theirs = "ab" * 32
        row = {"fingerprint_hash": theirs, "embedding": [], "contributor_count": 1, "pipeline_version": PIPELINE}
        c = ScriptedCorpus({MBID: row})
        out = run(core, c, contribute=True)
        assert out.status == "found" and out.fingerprint_hash == key  # our key in the outcome...
        assert c.claims[0]["fingerprint_hash"] == theirs  # ...their row gets the claim
        assert c.lookups == [(MBID, None)]

    def test_the_acoustid_id_is_asked_after_the_mbid_and_before_the_hash(self, core, key):
        c = ScriptedCorpus()
        run(core, c, acoustid_track_id=self.ACOUSTID)
        assert c.lookups == [(MBID, None), (("acoustid", self.ACOUSTID), None), (key, None)]

    def test_both_ids_go_out_with_the_vector(self, core, key):
        c, e = ScriptedCorpus(), FakeEmbedder()
        out = run(core, c, contribute=True, embedder=e, acoustid_track_id=self.ACOUSTID)
        (sent,) = c.contributions
        assert (sent["recording_mbid"], sent["acoustid_track_id"]) == (MBID, self.ACOUSTID)
        assert (out.named, out.named_acoustid) == (MBID, self.ACOUSTID)
        assert "recording id and AcoustID id" in out.detail

    def test_a_prefetched_hit_is_used_without_asking_again(self, core, key):
        c = ScriptedCorpus()
        row = {"fingerprint_hash": key, "embedding": [], "contributor_count": 2, "pipeline_version": PIPELINE}
        out = run(core, c, prefetched=row)
        assert out.status == "found" and c.lookups == []

    def test_a_prefetched_miss_on_an_id_still_asks_by_hash(self, core, key):
        c = ScriptedCorpus({key: {"embedding": [], "contributor_count": 1, "pipeline_version": PIPELINE}})
        out = run(core, c, prefetched=None)
        assert out.status == "found" and c.lookups == [(key, None)]

    def test_a_prefetched_miss_on_a_hash_key_is_final(self, core, key):
        c = ScriptedCorpus()
        out = run(core, c, recording_mbid=None, prefetched=None)
        assert out.status == "absent" and c.lookups == []

    def test_best_key_prefers_the_ids(self, core, key):
        assert core.best_key(fingerprint=FP, recording_mbid=MBID, acoustid_track_id=self.ACOUSTID) == MBID
        by_acoustid = core.best_key(fingerprint=FP, recording_mbid=None, acoustid_track_id=self.ACOUSTID)
        assert by_acoustid == ("acoustid", self.ACOUSTID)
        assert core.best_key(fingerprint=FP, recording_mbid=None, acoustid_track_id=None) == key
        assert core.best_key(fingerprint=None, recording_mbid=None, acoustid_track_id=None) is None


class TestNeighbours:
    def test_the_corpus_own_vector_and_pipeline_are_used_when_it_holds_the_file(self, core, key):
        c = ScriptedCorpus(
            {key: {"embedding": [0.5] * 512, "pipeline_version": "corpus-pipe", "contributor_count": 1}},
            neighbours=[
                {"fingerprint_hash": key, "similarity": 1.0},
                {"fingerprint_hash": "b" * 64, "similarity": 0.95, "recording_mbid": MBID, "recording_claims": 2},
                {"fingerprint_hash": "c" * 64, "similarity": 0.9, "recording_mbid": None, "recording_claims": 0},
            ],
        )
        out = core.neighbours(c, fingerprint=FP, path="/x", embedder=None, limit=10)
        assert c.similar_calls[0]["pipeline_version"] == "corpus-pipe"
        assert [n.is_query for n in out] == [True, False, False]
        assert out[1].url == f"https://musicbrainz.org/recording/{MBID}"
        assert out[2].url is None

    def test_without_the_corpus_or_an_embedder_it_says_why(self, core):
        from picard.plugins.clapback.clapback_client import CorpusError

        with pytest.raises(CorpusError, match="clapback-embed is not installed"):
            core.neighbours(ScriptedCorpus(), fingerprint=FP, path="/x", embedder=None)

    def test_the_embedder_is_used_only_when_the_corpus_has_nothing(self, core):
        e = FakeEmbedder()
        c = ScriptedCorpus(neighbours=[])
        core.neighbours(c, fingerprint=FP, path="/x", embedder=e)
        assert e.embedded == ["/x"] and c.similar_calls[0]["pipeline_version"] == PIPELINE

    def test_a_hash_is_shown_as_a_hash(self, core):
        n = core.Neighbour(0.9, "c" * 64, None, 0, False)
        html = core.neighbour_html(n)
        assert "not yet named by anyone" in html and "cccccccccccccccc…" in html
        named = core.Neighbour(0.95, "b" * 64, MBID, 2, False)
        assert "musicbrainz.org/recording/" in core.neighbour_html(named) and "2 claims" in core.neighbour_html(named)
        assert "this file" in core.neighbour_html(core.Neighbour(1.0, "a" * 64, None, 0, True))


class TestTheVendoredClient:
    def test_it_is_byte_identical_to_the_published_package(self):
        """`clapback_client` is copied in rather than depended on, because Picard's
        bundled application cannot pip install. The copy must be the package, not
        a fork of it — `scripts/sync_client.py` refreshes it, and this is the check."""
        from pathlib import Path

        here = Path(__file__).resolve().parents[1]
        vendored = here / "clapback" / "clapback_client"
        source = here.parent / "client" / "src" / "clapback_client"
        assert source.is_dir(), "run from the workspace checkout"
        names = sorted(p.name for p in source.glob("*.py"))
        assert sorted(p.name for p in vendored.glob("*.py")) == names
        for name in names:
            assert (vendored / name).read_bytes() == (source / name).read_bytes(), name
