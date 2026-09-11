"""Where the vectors live: a directory the user owns, holding two files.

`ADR-0009` point 4 — "the store is a local file the user owns, and nothing leaves
the machine by default". Point 3 rules out an index, so this is deliberately not
a database: brute force needs every vector in memory anyway, and a `.npy` loads
into exactly that with no query layer in between.

Two files rather than one because they change at different rates and for
different reasons. `vectors.npy` is 2 KB per track and rewritten whole;
`index.json` is small, human-readable, and the thing you would look at to answer
"did it index the file I think it did".
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEFAULT_HOME = Path.home() / ".clapback"


@dataclass
class Entry:
    path: str
    mtime: float
    size: int
    #: The corpus key for this recording, cached after the first time chromaprint
    #: is asked. `None` means "not fingerprinted", which is the normal state: the
    #: local half of this tool never needs one (`ADR-0009` point 5), so the cost
    #: is only paid by somebody who contributes.
    #:
    #: Cached rather than recomputed because fingerprinting spawns a process per
    #: file, and a contribute run that is interrupted should not start over.
    fingerprint_hash: str | None = None


class Store:
    """Vectors and the files they came from, kept in step by position."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = Path(home) if home else DEFAULT_HOME
        self.vectors_path = self.home / "vectors.npy"
        self.index_path = self.home / "index.json"
        self.vectors: np.ndarray = np.zeros((0, 512), dtype=np.float32)
        self.entries: list[Entry] = []
        self.pipeline_version: str | None = None
        #: `ADR-0004` point 1: an opaque per-install identifier, generated once and
        #: never derived from anything about the machine or its owner. It exists so
        #: the corpus can tell two submissions from one client retrying, which is
        #: the distinction `contributor_count` cannot make.
        self.client_id: str | None = None

    def load(self) -> Store:
        if self.index_path.exists():
            data = json.loads(self.index_path.read_text())
            # Tolerate keys this version does not know: a store written by a
            # newer clapback must not make an older one delete the library.
            fields = {f.name for f in dataclasses.fields(Entry)}
            self.entries = [
                Entry(**{k: v for k, v in e.items() if k in fields})
                for e in data.get("entries", [])
            ]
            self.pipeline_version = data.get("pipeline_version")
            self.client_id = data.get("client_id")
        if self.vectors_path.exists():
            self.vectors = np.load(self.vectors_path)
        # A store whose two halves disagree is worse than an empty one: every
        # result would be attributed to the wrong file. Rebuilding is cheap
        # relative to explaining a wrong answer.
        if len(self.entries) != len(self.vectors):
            self.entries, self.vectors = [], np.zeros((0, 512), dtype=np.float32)
        return self

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        np.save(self.vectors_path, self.vectors)
        self.index_path.write_text(
            json.dumps(
                {
                    "pipeline_version": self.pipeline_version,
                    "client_id": self.client_id,
                    "entries": [e.__dict__ for e in self.entries],
                },
                indent=1,
            )
        )

    def ensure_client_id(self) -> str:
        """The identifier this install contributes under, minted on first use.

        Deliberately not minted at `index` time. A store that has only ever been
        searched locally has no reason to carry an identifier, and `ADR-0009`
        point 4 is that nothing leaves the machine by default — including the fact
        that this install exists.
        """
        if not self.client_id:
            self.client_id = str(uuid.uuid4())
        return self.client_id

    def known(self) -> dict[str, Entry]:
        return {e.path: e for e in self.entries}

    def add(self, path: str, mtime: float, size: int, vector: list[float]) -> None:
        self.entries.append(Entry(path=path, mtime=mtime, size=size))
        v = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        self.vectors = np.vstack([self.vectors, v]) if len(self.vectors) else v

    def nearest(self, query: np.ndarray, limit: int) -> list[tuple[int, float]]:
        """Cosine similarity against everything, sorted.

        The vectors are unit length, so a dot product *is* the cosine — no
        normalisation, no distance-to-similarity conversion, and nothing to get
        the sign of wrong.
        """
        if not len(self.vectors):
            return []
        sims = self.vectors @ np.asarray(query, dtype=np.float32)
        top = np.argsort(-sims)[:limit]
        return [(int(i), float(sims[i])) for i in top]
