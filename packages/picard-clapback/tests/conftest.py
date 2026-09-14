"""Import the plugin the way Picard does: as `picard.plugins.clapback`.

Real Picard, not a stub — it is on PyPI and imports headless — so the surfaces
the plugin registers against are the real ones and a renamed hook fails here
rather than on somebody's desktop.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def plugin():
    import picard.plugins

    if str(ROOT) not in picard.plugins.__path__:
        picard.plugins.__path__.append(str(ROOT))
    return importlib.import_module("picard.plugins.clapback")


@pytest.fixture(scope="session")
def core(plugin):
    return importlib.import_module("picard.plugins.clapback._core")
