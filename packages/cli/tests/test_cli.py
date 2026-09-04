"""What the tool is, and — as much — what ADR-0009 point 9 says it is not."""

import inspect

from clapback_cli import cli


class TestTheTwoCapabilities:
    """ADR-0001 point 8 names them: 'search your own library by description, find
    duplicates across formats and masters'."""

    def test_search_and_duplicates_both_exist(self):
        assert callable(cli.cmd_search) and callable(cli.cmd_duplicates)

    def test_neither_touches_the_network(self):
        for fn in (cli.cmd_index, cli.cmd_search, cli.cmd_duplicates):
            body = inspect.getsource(fn)
            for forbidden in ("http", "requests", "urlopen", "socket"):
                assert forbidden not in body.lower(), f"{fn.__name__} reaches the network"

    def test_nothing_requires_a_fingerprint(self):
        """ADR-0009 point 5: chromaprint is a native binary that crashes on
        malformed channel counts, and it is needed only to talk to the corpus.
        Requiring it would put it in front of the local value."""
        for fn in (cli.cmd_index, cli.cmd_search, cli.cmd_duplicates):
            body = inspect.getsource(fn).lower()
            assert "acoustid" not in body and "chromaprint" not in body


class TestTheDuplicateThreshold:
    def test_it_sits_below_the_band_so_every_rip_is_caught(self):
        """Two rips of one recording measure 0.9972–0.9995 under this pipeline.

        The threshold has to sit *below* the floor of that band, not inside it: a
        cutoff of 0.999 would catch the tightest pairs and silently miss the ones
        at 0.9972, which are the same recording just as much. Below the floor,
        with room for a pair slightly looser than anything ADR-0104 measured.
        """
        assert cli.DEFAULT_DUPLICATE_THRESHOLD < 0.9972

    def test_it_stays_far_above_where_different_music_sits(self):
        """ADR-0104 measured a middle-10s embedding of two rips at 0.950 and
        called that 'the same distance as a genuinely different piece of music'.
        A threshold anywhere near there would report a library as one big
        duplicate."""
        assert cli.DEFAULT_DUPLICATE_THRESHOLD > 0.99

    def test_it_is_adjustable(self):
        """'Duplicate' is partly a judgement — a remaster is a different master."""
        src = inspect.getsource(cli.main)
        assert "--threshold" in src


class TestFailureIsLegible:
    def test_a_missing_model_says_what_to_do(self):
        """614 MB of encoders are not bundled. A traceback here would read as a
        bug rather than a one-time setup step."""
        body = inspect.getsource(cli.cmd_index)
        assert "ArtifactsMissing" in body
        assert "CLAPBACK_MODEL_DIR" in body

    def test_one_unreadable_file_does_not_end_the_run(self):
        body = inspect.getsource(cli.cmd_index)
        assert "continue" in body and "failed" in body


class TestItReimplementsNothing:
    def test_embedding_comes_from_the_published_package(self):
        """ADR-0009 point 7. A tool doing its own windowing would undo the whole
        argument for there being one implementation."""
        src = inspect.getsource(cli)
        assert "import clapback_embed" in src
        for own in ("def log_mel", "def windows", "n_fft", "hop_length"):
            assert own not in src

    def test_the_store_records_which_pipeline_made_it(self):
        assert "PIPELINE_VERSION" in inspect.getsource(cli.cmd_index)
