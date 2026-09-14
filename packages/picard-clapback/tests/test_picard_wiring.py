"""What the plugin registers with Picard, checked against real Picard.

A headless QApplication and a throwaway config, so the option page can be
constructed, loaded and saved the way the dialog does it.
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def config(app, plugin):
    from picard.config import ListOption, get_config, setup_config

    setup_config(app, os.path.join(tempfile.mkdtemp(), "picard.ini"))
    ListOption("setting", "enabled_plugins", [])
    get_config().setting["enabled_plugins"] = ["clapback"]
    return get_config()


def test_the_header_is_what_picard_reads(plugin):
    assert plugin.PLUGIN_NAME == "Clapback"
    assert "2.13" in plugin.PLUGIN_API_VERSIONS
    assert plugin.PLUGIN_LICENSE == "MIT"


def test_actions_are_registered_for_tracks_and_files(config, plugin):
    from picard.ui import itemviews

    for point in (itemviews._track_actions, itemviews._file_actions):
        names = [a.NAME for a in point]
        assert "Clapback: look up in the commons…" in names
        assert "Clapback: what sounds like this…" in names


def test_the_post_save_hook_and_options_page_are_registered(config, plugin):
    from picard.file import _file_post_save_processors
    from picard.ui.options import _pages

    registered = [f for prio in _file_post_save_processors.functions.values() for f in prio]
    assert plugin._after_save in registered
    assert plugin.ClapbackOptionsPage in list(_pages)


def test_the_options_page_round_trips_and_contribute_defaults_off(config, plugin):
    page = plugin.ClapbackOptionsPage()
    page.load()
    assert page.contribute.isChecked() is False
    assert page.on_save.isChecked() is False
    assert page.url.text() == plugin.DEFAULT_BASE_URL
    page.contribute.setChecked(True)
    page.url.setText("https://corpus.invalid/")
    page.save()
    assert config.setting[plugin.OPT_CONTRIBUTE] is True
    assert config.setting[plugin.OPT_URL] == "https://corpus.invalid"


def test_the_client_id_is_minted_once_and_kept(config, plugin):
    assert config.setting[plugin.OPT_CLIENT_ID] == ""
    first = plugin._client_id()
    assert len(first) == 36
    assert plugin._client_id() == first
    assert config.setting[plugin.OPT_CLIENT_ID] == first


def test_a_selection_yields_each_file_once(plugin):
    from picard.file import File

    class Fake(File):
        def __init__(self, name):
            self.filename = name

    a, b = Fake("a"), Fake("b")

    class Track:
        def iterfiles(self):
            return [a, b]

    assert list(plugin._files_of([a, Track(), b])) == [a, b]


def test_the_built_zip_loads_through_picards_own_installer(app, plugin, tmp_path):
    """The zip's basename is the module name Picard imports. `clapback-0.1.0.zip`
    was released for nine minutes on 2026-09-14 before this was discovered."""
    import subprocess
    import sys
    from pathlib import Path

    from picard.i18n import setup_gettext
    from picard.pluginmanager import PluginManager, register_plugin_dir

    here = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(here / "scripts" / "build_zip.py")], check=True, capture_output=True)
    built = here / "dist" / "clapback.zip"
    assert built.name == "clapback.zip"
    setup_gettext(None, "en")
    register_plugin_dir(str(tmp_path))
    manager = PluginManager(plugins_directory=str(tmp_path))
    manager.install_plugin(str(built))
    assert [p.module_name for p in manager.plugins] == ["clapback"]
    assert (tmp_path / "clapback.zip").exists()

    # And the negative, so this test means something: the same bytes under the
    # old name do not load, because "clapback-0.1.0" is not a module name.
    import shutil

    other = tmp_path / "other"
    other.mkdir()
    register_plugin_dir(str(other))
    shutil.copy(built, other / "clapback-0.1.0.zip")
    manager2 = PluginManager(plugins_directory=str(other))
    manager2.load_plugins_from_directory(str(other))
    assert manager2.plugins == []
