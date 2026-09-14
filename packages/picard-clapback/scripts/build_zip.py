#!/usr/bin/env python3
"""Build `dist/clapback.zip`, the file Picard's Options → Plugins → Install accepts.

Picard loads a zip whose top-level directory is a package with `__init__.py`.
The version in the archive name is read from the plugin header so a tag and the
header cannot disagree silently.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PKG = HERE / "clapback"


def plugin_version() -> str:
    text = (PKG / "__init__.py").read_text()
    m = re.search(r'^PLUGIN_VERSION = "([^"]+)"', text, re.M)
    if not m:
        sys.exit("PLUGIN_VERSION not found in clapback/__init__.py")
    return m.group(1)


def main() -> None:
    version = plugin_version()
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"clapback-{version}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(PKG.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            zf.write(p, p.relative_to(HERE))
        zf.write(HERE / "LICENSE", "clapback/LICENSE")
    print(out)


if __name__ == "__main__":
    main()
