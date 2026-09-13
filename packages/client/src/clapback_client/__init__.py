"""`clapback-client` — the contract a tool follows to take part in the commons.

`ADR-0011` point 2. A tool that contributes has four obligations, each decided in
an earlier record, and this package is those obligations as code:

1. Fingerprint the audio and hash it canonically — `hash_fingerprint`,
   `fingerprint_file`. `ADR-0010`.
2. Produce the vector through a declared pipeline — the caller's job; this
   package never embeds. `clapback-embed` is the reference pipeline, and a tool
   with its own declares its own identity.
3. Look up before contributing — `Corpus.has`, `Corpus.lookup`. `ADR-0008`.
4. Send `client_id` and `pipeline_version` — `Corpus.contribute`, `identity`.
   `ADR-0004`, `ADR-0006`.

Nothing beyond the standard library, on purpose: a tool that has an embedder and
only wants to contribute must not have to install ONNX Runtime to do so.

    from clapback_client import Corpus, fingerprint_file, hash_fingerprint

    key = hash_fingerprint(fingerprint_file(path))
    corpus = Corpus()
    row = corpus.lookup(key, pipeline_version)
    if row is None:
        corpus.contribute(
            fingerprint_hash=key,
            embedding=vector,
            pipeline_version=pipeline_version,
            client_id=client_id,
        )
"""

from .corpus import DEFAULT_BASE_URL, Corpus, CorpusError
from .fingerprint import FingerprintUnavailable, canonical, fingerprint_file, hash_fingerprint
from .identity import ensure_client_id, mint_client_id

__all__ = [
    "DEFAULT_BASE_URL",
    "Corpus",
    "CorpusError",
    "FingerprintUnavailable",
    "canonical",
    "ensure_client_id",
    "fingerprint_file",
    "hash_fingerprint",
    "mint_client_id",
]
