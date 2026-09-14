#!/usr/bin/env python3
"""Refresh the vendored `clapback_client` from `packages/client`, or check it.

The Picard plugin carries a copy of the client rather than depending on it,
because Picard's bundled application cannot `pip install` anything. A copy
drifts unless something stops it: `tests/test_core.py` fails when the copy and
the package differ, and this is how the copy is brought back into line.

    python scripts/sync_client.py          # copy
    python scripts/sync_client.py --check  # exit 1 if they differ
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SOURCE = HERE.parent / "client" / "src" / "clapback_client"
TARGET = HERE / "clapback" / "clapback_client"


def differs() -> list[str]:
    out = []
    names = sorted(p.name for p in SOURCE.glob("*.py"))
    for name in names:
        if not (TARGET / name).exists() or not filecmp.cmp(SOURCE / name, TARGET / name, shallow=False):
            out.append(name)
    out += sorted(p.name for p in TARGET.glob("*.py") if p.name not in names)
    return out


def main() -> None:
    if "--check" in sys.argv:
        bad = differs()
        if bad:
            print("vendored clapback_client differs from packages/client:", ", ".join(bad))
            sys.exit(1)
        print("vendored clapback_client is current")
        return
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    for p in SOURCE.glob("*.py"):
        shutil.copy2(p, TARGET / p.name)
    print(f"copied {len(list(TARGET.glob('*.py')))} files into {TARGET.relative_to(HERE)}")


if __name__ == "__main__":
    main()
