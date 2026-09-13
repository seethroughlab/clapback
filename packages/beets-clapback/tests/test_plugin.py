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


class TestClapbackPlugin(PluginTestHelper):
    plugin = "clapback"
    preload_plugin = False

    @pytest.fixture(autouse=True)
    def _stubs(self, monkeypatch, setup):
        # `setup` is the helper's own autouse fixture; naming it orders this
        # one after beets is up and before it is torn down.
        self.embed = FakeEmbed()
        FakeCorpus.holds, FakeCorpus.log, FakeCorpus.unreachable = {}, [], False
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
