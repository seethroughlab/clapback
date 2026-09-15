"""The plugin, driven through beets itself.

`beets.test.helper.PluginTestHelper` loads `beetsplug.clapback` the way `beet`
does and runs commands through `beets.ui`, so what is under test is the plugin
as a beets user meets it — config keys, flexible attributes, the sidecar store,
the summary line. Only two things are stubbed: the corpus (a scripted server
that records what it was asked) and the embedder (which would otherwise need
614 MB of ONNX encoders). Neither is the property under test.

The properties are the ones `ADR-0011` point 5 and point 6 ask of any plug-in:
look up before you contribute, contribute only when told to, never re-embed what
is already embedded, hash canonically, and mint nothing for a user who has not
contributed.
"""

from __future__ import annotations

import json

import pytest
from beets.exceptions import UserError
from beets.test.helper import PluginTestHelper

import beetsplug.clapback as plug

PIPELINE = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"
RAW_FP = "AQADtJESbVkUhYL84z4CnwZ4HsdxHD6P4_hx_EAO_cjx"
MBID = "1c6da765-da50-476b-a000-61e7cf45ded8"


def _unit(i: int) -> list[float]:
    v = [0.0] * 512
    v[i % 512] = 1.0
    return v


class FakeEmbed:
    PIPELINE_VERSION = PIPELINE

    class ArtifactsMissing(Exception):
        pass

    def __init__(self):
        self.calls: list[str] = []

    def embed_file(self, path):
        self.calls.append(path)
        return _unit(len(self.calls))

    def embed_text(self, text):
        # Point the query at the first embedded track.
        return _unit(1)


class FakeCorpus:
    base_url = "https://example.invalid"

    def __init__(self, *_a, **_k):
        pass

    # Class-level so the plugin's fresh instances share one script; the
    # fixture resets them before every test.
    holds: dict[tuple[str, str], list[float]] = {}  # noqa: RUF012
    log: list[tuple] = []  # noqa: RUF012
    neighbours: list[dict] = []  # noqa: RUF012
    unreachable = False

    def lookup(self, h, pv):
        FakeCorpus.log.append(("lookup", h, pv))
        if FakeCorpus.unreachable:
            raise plug.CorpusError("down")
        return {"embedding": FakeCorpus.holds[(h, pv)]} if (h, pv) in FakeCorpus.holds else None

    def contribute(self, **kw):
        FakeCorpus.log.append(("contribute", kw["fingerprint_hash"], kw["pipeline_version"]))
        FakeCorpus.last_kw = kw
        return "contributed"

    def claim(self, **kw):
        FakeCorpus.log.append(("claim", kw["fingerprint_hash"], kw["recording_mbid"]))
        return {"status": "claimed", "recording_mbid": kw["recording_mbid"], "recording_claims": 1}

    def similar(self, vector, *, limit=10, pipeline_version=None):
        FakeCorpus.log.append(("similar", limit, pipeline_version))
        return list(FakeCorpus.neighbours)[:limit]


class ClapbackHarness(PluginTestHelper):
    """beets up, plugin loadable, corpus and embedder stubbed. Not collected."""

    plugin = "clapback"
    preload_plugin = False

    @pytest.fixture(autouse=True)
    def _stubs(self, monkeypatch, setup):
        # `setup` is the helper's own autouse fixture; naming it orders this
        # one after beets is up and before it is torn down.
        self.embed = FakeEmbed()
        FakeCorpus.holds, FakeCorpus.log, FakeCorpus.unreachable = {}, [], False
        FakeCorpus.neighbours = []
        monkeypatch.setattr(plug, "_embedder", lambda: self.embed)
        monkeypatch.setattr(plug, "Corpus", FakeCorpus)

    # --- helpers --------------------------------------------------------

    def _track(self, title="t", fp=RAW_FP, **kw):
        return self.add_item(title=title, acoustid_fingerprint=fp, **kw)

    def _run(self, *args, **cfg):
        with self.configure_plugin({"contribute": False, **cfg}):
            self.run_command("clapback", *args)

    def _kinds(self):
        return [k for k, *_ in FakeCorpus.log]

    def _store(self):
        return json.loads((plug.Path(self.config.config_dir()) / "clapback" / "index.json").read_text())



class TestClapbackPlugin(ClapbackHarness):
    # --- the four obligations ------------------------------------------

    def test_a_hit_in_the_corpus_means_the_model_does_not_run(self):
        """The whole exchange a plug-in makes."""
        t = self._track()
        FakeCorpus.holds[(plug.hash_fingerprint(RAW_FP), PIPELINE)] = _unit(7)
        self._run()
        t.load()
        assert t.clapback_status == "found"
        assert self.embed.calls == []
        assert self._kinds() == ["lookup"]

    def test_a_miss_is_embedded_and_kept_local_unless_asked(self, capsys):
        """`ADR-0011` point 6: contribution is a second, separate setting."""
        t = self._track()
        self._run()
        t.load()
        assert t.clapback_status == "local"
        assert len(self.embed.calls) == 1
        assert "contribute" not in self._kinds()
        assert "contribution is off" in capsys.readouterr().out

    def test_contributing_sends_what_the_records_require(self):
        t = self._track()
        self._run(contribute=True)
        t.load()
        assert t.clapback_status == "contributed"
        assert self._kinds() == ["lookup", "contribute"], "look up first, always"
        kw = FakeCorpus.last_kw
        assert kw["pipeline_version"] == PIPELINE
        assert kw["client_id"]
        assert len(kw["embedding"]) == 512

    def test_the_hash_is_canonical_whatever_the_column_holds(self):
        """`ADR-0010` point 2. beets' column measured clean on 99 tracks, but a
        column is where the last defect hid, so the rule applies regardless."""
        raw = self._track("raw", fp=RAW_FP)
        esc = self._track("esc", fp="\\x" + RAW_FP.encode().hex())
        self._run()
        raw.load()
        esc.load()
        assert raw.clapback_hash == esc.clapback_hash

    # --- restraint -------------------------------------------------------

    def test_turning_contribution_on_later_does_not_re_embed(self):
        """The vector is in the store; the model does not run again. This is
        the path a user takes after indexing with contribution off."""
        t = self._track()
        self._run()
        assert len(self.embed.calls) == 1
        FakeCorpus.log.clear()
        self._run(contribute=True)
        t.load()
        assert len(self.embed.calls) == 1, "re-embedded a track it already had"
        assert t.clapback_status == "contributed"

    def test_done_tracks_are_skipped_without_force(self):
        self._track()
        self._run(contribute=True)
        FakeCorpus.log.clear()
        self._run(contribute=True)
        assert FakeCorpus.log == [], "a finished track went back to the corpus"
        self._run("-f", contribute=True)
        assert "lookup" in self._kinds()

    def test_pretend_touches_nothing(self, capsys):
        t = self._track()
        self._run("-p", contribute=True)
        t.load()
        assert t.get("clapback_status") is None
        assert FakeCorpus.log == [] and self.embed.calls == []
        assert "would process" in capsys.readouterr().out

    def test_no_identifier_is_minted_until_a_contribution_happens(self):
        """`ADR-0009` point 4 covers the fact that an install exists."""
        self._track()
        self._run()
        assert not (plug.Path(self.config.config_dir()) / "clapback" / "client_id").exists()
        self._run(contribute=True)
        assert (plug.Path(self.config.config_dir()) / "clapback" / "client_id").exists()

    # --- when things are missing ----------------------------------------

    def test_no_fingerprint_and_no_chromaprint_is_a_status_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(
            plug, "fingerprint_file", lambda p: (_ for _ in ()).throw(
                plug.FingerprintUnavailable("chromaprint is not installed")
            )
        )
        t = self.add_item(title="nofp")
        self._run()
        t.load()
        assert t.clapback_status == "unfingerprinted"
        assert self.embed.calls == []

    def test_an_unreachable_corpus_still_embeds_locally(self):
        """Offline is a normal state. The store fills; nothing is sent."""
        FakeCorpus.unreachable = True
        t = self._track()
        self._run(contribute=True)
        t.load()
        assert t.clapback_status == "local"
        assert len(self.embed.calls) == 1
        assert "contribute" not in self._kinds()

    def test_a_store_from_another_pipeline_is_refused(self):
        """`ADR-0006` point 5: recomputed, not relabelled."""
        self._track()
        self._run()
        idx = plug.Path(self.config.config_dir()) / "clapback" / "index.json"
        data = json.loads(idx.read_text())
        data["pipeline_version"] = "something+else"
        idx.write_text(json.dumps(data))
        with pytest.raises(UserError, match="Delete the store"):
            self._run("-f")

    # --- the local value ------------------------------------------------

    def test_search_ranks_the_matching_track_first(self, capsys):
        """`ADR-0001` point 8: worth running with the corpus empty."""
        a = self._track("alpha", fp=RAW_FP)
        b = self._track("beta", fp=RAW_FP[::-1])
        self._run()
        capsys.readouterr()  # drop the run's summary line
        with self.configure_plugin({}):
            self.run_command("clapback-search", "anything at all")
        out = capsys.readouterr().out.strip().splitlines()
        assert "alpha" in out[0]
        assert self._store()["pipeline_version"] == PIPELINE
        assert sorted(self._store()["ids"]) == sorted([a.id, b.id])


class TestNamingTheRecording(ClapbackHarness):
    """`ADR-0012`: the id goes out under the contribute switch, never silently,
    and never by re-sending a vector."""

    def test_a_contribution_carries_the_recording_id_when_the_track_has_one(self):
        self._track("named", mb_trackid=MBID)
        self._run(contribute=True)
        assert FakeCorpus.last_kw["recording_mbid"] == MBID

    def test_a_track_without_an_id_sends_none(self):
        self._track("unnamed")
        self._run(contribute=True)
        assert FakeCorpus.last_kw.get("recording_mbid") is None

    def test_a_corpus_hit_is_named_by_a_claim_not_a_resend(self):
        """Somebody else's row, our id. The vector stays theirs."""
        t = self._track("theirs", mb_trackid=MBID)
        FakeCorpus.holds[(plug.hash_fingerprint(RAW_FP), PIPELINE)] = _unit(3)
        self._run(contribute=True)
        t.load()
        assert self._kinds() == ["lookup", "claim"]
        assert "contribute" not in self._kinds()
        assert t.clapback_named == MBID
        assert self.embed.calls == []

    def test_nothing_is_named_while_contribution_is_off(self):
        """The id discloses which recording you hold; that is the contribute
        switch's consent, not lookup's."""
        t = self._track("quiet", mb_trackid=MBID)
        FakeCorpus.holds[(plug.hash_fingerprint(RAW_FP), PIPELINE)] = _unit(3)
        self._run()
        t.load()
        assert "claim" not in self._kinds()
        assert t.get("clapback_named") is None

    def test_turning_contribution_on_later_names_what_was_found_before(self):
        t = self._track("later", mb_trackid=MBID)
        FakeCorpus.holds[(plug.hash_fingerprint(RAW_FP), PIPELINE)] = _unit(3)
        self._run()
        FakeCorpus.log.clear()
        self._run(contribute=True)
        t.load()
        assert "claim" in self._kinds()
        assert t.clapback_named == MBID
        FakeCorpus.log.clear()
        self._run(contribute=True)
        assert FakeCorpus.log == [], "named once; a second run has nothing to say"



class TestWhatSoundsLikeThis(ClapbackHarness):
    """The reason to install this that is not altruism."""

    def _seed(self):
        t = self._track("seed", fp=RAW_FP)
        self._run()
        return t

    def test_it_shows_a_named_neighbour_as_a_recording_a_person_can_open(self, capsys):
        self._seed()
        FakeCorpus.neighbours = [
            {"fingerprint_hash": "ee" * 32, "similarity": 0.97, "recording_mbid": MBID,
             "recording_claims": 2},
        ]
        capsys.readouterr()
        with self.configure_plugin({}):
            self.run_command("clapback-similar", "title:seed")
        out = capsys.readouterr().out
        assert f"https://musicbrainz.org/recording/{MBID}" in out
        assert "2 claims" in out

    def test_an_unnamed_neighbour_is_honestly_a_hash(self, capsys):
        self._seed()
        FakeCorpus.neighbours = [
            {"fingerprint_hash": "ee" * 32, "similarity": 0.9, "recording_mbid": None,
             "recording_claims": 0},
        ]
        capsys.readouterr()
        with self.configure_plugin({}):
            self.run_command("clapback-similar", "title:seed")
        out = capsys.readouterr().out
        assert "not yet named by anyone" in out
        assert "musicbrainz.org" not in out

    def test_a_neighbour_you_own_is_shown_as_your_track(self, capsys):
        self._seed()
        other = self._track("mine", fp=RAW_FP[::-1])
        self._run()
        other.load()
        FakeCorpus.neighbours = [
            {"fingerprint_hash": other.clapback_hash, "similarity": 0.95,
             "recording_mbid": None, "recording_claims": 0},
        ]
        capsys.readouterr()
        with self.configure_plugin({}):
            self.run_command("clapback-similar", "title:seed")
        out = capsys.readouterr().out
        assert "mine" in out and "in your library" in out

    def test_the_seed_itself_is_not_listed_and_the_pipeline_is_sent(self, capsys):
        seed = self._seed()
        seed.load()
        FakeCorpus.neighbours = [
            {"fingerprint_hash": seed.clapback_hash, "similarity": 1.0, "recording_mbid": None,
             "recording_claims": 0},
        ]
        capsys.readouterr()
        with self.configure_plugin({}):
            self.run_command("clapback-similar", "title:seed")
        out = capsys.readouterr().out
        assert out.count("1.0000") == 0
        assert ("similar", 11, PIPELINE) in FakeCorpus.log


def test_the_plugin_says_what_is_sent_is_cc0():
    """`ADR-0013` point 2, in the docstring beets shows and the README beside the option."""
    from beetsplug import clapback as plugin

    assert "CC0 1.0" in plugin.__doc__

