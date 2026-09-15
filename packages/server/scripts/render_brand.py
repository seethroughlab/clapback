#!/usr/bin/env python3
"""Render the brand PNGs from their SVGs. Offline; the results are committed.

Safari does not use SVG favicons and link scrapers do not rasterise SVG, so the
two drawings in `app/static/img/` — `favicon.svg` and `og.svg`, both carrying the
same mark as `templates/_wordmark.html` — are exported once here:

    python scripts/render_brand.py            # uses rsvg-convert (brew install librsvg)
    uv run --with cairosvg python scripts/render_brand.py   # or cairosvg, where cairo exists

Neither reaches the image (`ADR-0001` point 3: the server keeps its own
dependencies). Re-run after touching either SVG; a test asserts the mark's paths
agree across the three files, and this keeps the PNGs agreeing with them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

IMG = Path(__file__).resolve().parents[1] / "app" / "static" / "img"

TARGETS = [
    ("favicon.svg", "favicon-32.png", 32, 32),
    ("favicon.svg", "apple-touch-icon.png", 180, 180),
    ("og.svg", "og.png", 1200, 630),
]


def _render(src: Path, dst: Path, w: int, h: int) -> None:
    if shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "-w", str(w), "-h", str(h), "-o", str(dst), str(src)], check=True
        )
        return
    import cairosvg  # optional, and only if rsvg-convert is absent

    cairosvg.svg2png(url=str(src), write_to=str(dst), output_width=w, output_height=h)


def main() -> None:
    for src, dst, w, h in TARGETS:
        _render(IMG / src, IMG / dst, w, h)
        print(f"{dst}: {w}×{h} from {src}")


if __name__ == "__main__":
    main()
