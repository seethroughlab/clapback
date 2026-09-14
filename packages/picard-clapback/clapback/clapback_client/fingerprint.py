"""The corpus key's other half: `ADR-0010`.

`fingerprint_hash` is SHA256 of the AcoustID fingerprint **as chromaprint returned
it** — the base64 ASCII string — and of nothing else. The rule exists because it
was broken: Familiar hashed whatever its column happened to hold, and that column
held the same fingerprint in two encodings (14,284 hex-escaped against 11,364
raw, measured 2026-09-10), both of which are live keys in the corpus today.

So the rule is "hash what you computed, not what you stored", and this module is
where this tool computes it. Nothing here reads a database, which is the point:
the value goes from chromaprint into `sha256` without passing through storage, so
there is no encoding for storage to apply.

`canonical()` exists anyway, for the case where a fingerprint *has* been through
something. It is the one place that knows what a re-encoding looks like.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys


class FingerprintUnavailable(RuntimeError):
    """chromaprint is missing or refused the file.

    `ADR-0009` point 5: the local half of this tool — index, search, duplicates —
    works without chromaprint and must never be made to depend on it. Only talking
    to the corpus needs a fingerprint, so this is raised there and nowhere else.
    """


def canonical(fingerprint: str | bytes) -> bytes:
    """The bytes to hash, whatever shape the fingerprint arrives in.

    A fingerprint from chromaprint is already canonical and passes through. The
    one transformation undone here is Postgres's hex output format — a `text`
    column that once held `bytea` renders as `\\x` followed by hex, and hashing
    that string keys the row to a fact about somebody's schema history rather
    than about the recording. `ADR-0010` point 2.

    The check is deliberately narrow. `\\x` plus an even number of hex digits
    that decode to printable ASCII is not something a chromaprint fingerprint can
    be — its alphabet is base64 and it never begins with a backslash — so this
    cannot misfire on a real fingerprint, and anything it does not recognise is
    left alone rather than guessed at.
    """
    if isinstance(fingerprint, bytes):
        raw = fingerprint
    else:
        raw = fingerprint.encode()

    if raw.startswith(b"\\x") and len(raw) % 2 == 0:
        body = raw[2:]
        try:
            decoded = bytes.fromhex(body.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return raw
        # Only accept the decode if it produced something that looks like a
        # fingerprint rather than arbitrary bytes that happened to be valid hex.
        if decoded and all(32 <= b < 127 for b in decoded):
            return decoded
    return raw


def hash_fingerprint(fingerprint: str | bytes) -> str:
    """SHA256 of the canonical fingerprint, hex-digested — the corpus key.

    One-way on purpose: contributing says "I have this recording" without saying
    which recording it is, which is what lets somebody contribute from a library
    they would rather not publish. It is also why no server-side migration could
    ever repair a bad key — the corpus never learns the fingerprint, so only a
    client holding it can compute a different hash for the same recording.
    """
    return hashlib.sha256(canonical(fingerprint)).hexdigest()


def fingerprint_file(path: str) -> str:
    """The AcoustID fingerprint of one file, exactly as chromaprint gives it.

    Run out of process for the reason `ADR-0009` point 5 gives: chromaprint is a
    C library that crashes rather than raises on some malformed inputs, and a
    segfault in a library of 20,000 files must cost one file rather than the run.
    A crashed child is a non-zero exit code here.
    """
    if shutil.which("fpcalc") is None:
        try:
            import acoustid  # noqa: F401
        except ImportError as exc:
            raise FingerprintUnavailable(
                "chromaprint is not installed, so this tool cannot talk to the corpus. "
                "Install it (`brew install chromaprint`, `apt install libchromaprint-tools`) "
                "and `pip install pyacoustid`. Indexing, search and duplicates do not need it."
            ) from exc

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import acoustid, json, sys; "
                    "d, f = acoustid.fingerprint_file(sys.argv[1]); "
                    "print(json.dumps(f.decode() if isinstance(f, bytes) else f))"
                ),
                path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FingerprintUnavailable(f"fingerprinting timed out: {path}") from exc

    if result.returncode != 0 or not result.stdout.strip():
        detail = (result.stderr or "").strip().splitlines()
        raise FingerprintUnavailable(detail[-1] if detail else f"exit {result.returncode}")

    import json

    value = json.loads(result.stdout.strip())
    if not isinstance(value, str) or not value:
        raise FingerprintUnavailable(f"chromaprint returned nothing usable for {path}")
    return value
