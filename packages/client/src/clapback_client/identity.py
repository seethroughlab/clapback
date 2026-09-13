"""The identifier a contribution carries — `ADR-0004` point 1.

An opaque per-install UUID, minted once and never derived from anything about the
machine or its owner. It exists so the corpus can tell two contributions apart
from one client retrying, which is the distinction `contributor_count` cannot make
on its own. It is not an identity: there is no registration, no lookup, and the
server never needs to know more.

Two rules the caller has to keep, because this module cannot keep them for it:

**Mint it on first contribution, not on install.** `ADR-0009` point 4 — nothing
leaves the machine by default — covers the fact that an install exists. A tool
whose user only ever searched their own files has no reason to carry one, and a
dry run should not create one.

**Store it somewhere the user can find and delete.** Deleting it makes the user a
new contributor and changes nothing else. A tool that hides it has broken the one
promise the identifier makes.
"""

from __future__ import annotations

import uuid
from pathlib import Path


def mint_client_id() -> str:
    """A fresh identifier. Call it once per install, and only when contributing."""
    return str(uuid.uuid4())


def ensure_client_id(path: str | Path) -> str:
    """The identifier stored at `path`, minting and writing one if there is none.

    For tools with no better place to keep it. A tool that already has a settings
    store — Familiar's `settings.json`, beets' config, the CLI's `index.json` —
    should keep it there and call `mint_client_id` itself, so the user has one
    place to look rather than two.
    """
    p = Path(path)
    if p.exists():
        existing = p.read_text().strip()
        if existing:
            return existing
    fresh = mint_client_id()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fresh + "\n")
    return fresh
