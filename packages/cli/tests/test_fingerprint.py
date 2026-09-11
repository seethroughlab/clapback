"""`ADR-0010`: the corpus key is a function of the audio.

The defect this guards against did not look like a bug. Familiar hashed the
fingerprint it had stored, which was correct code given a canonical input and was
never given one — so the same recording reached the corpus under two keys
depending on a column's migration history. These tests are the rule stated as
assertions, because the failure is silent: a wrong key is a well-formed 64-character
hex string that simply matches nothing.
"""

from __future__ import annotations

import hashlib

from clapback_cli.fingerprint import canonical, hash_fingerprint

#: A real-shaped chromaprint fingerprint: base64 alphabet, starts `AQAD`.
RAW = "AQADtJESbVkUhYL84z4CnwZ4HsdxHD6P4_hx_EAO_cjx"

#: The same value after a `bytea` → `text` column migration renders it in
#: Postgres's hex output format. Not a different fingerprint — the same ASCII.
ESCAPED = "\\x" + RAW.encode().hex()


def test_escaped_and_raw_are_the_same_fingerprint():
    """The premise. If this fails the rest of the file is measuring nothing."""
    assert bytes.fromhex(ESCAPED[2:]).decode() == RAW


def test_the_two_encodings_hash_alike():
    """The whole point of `ADR-0010`.

    Before it, these differed, and that difference is what split the corpus into
    14,284 rows keyed one way and 11,364 the other.
    """
    assert hash_fingerprint(RAW) == hash_fingerprint(ESCAPED)


def test_the_hash_is_of_the_fingerprint_itself():
    """Canonical means the bytes chromaprint returned, not a form of our choosing.

    Pinned against `hashlib` directly rather than against `hash_fingerprint`'s own
    output, so that a change to the canonical form fails here instead of quietly
    re-keying the corpus.
    """
    assert hash_fingerprint(RAW) == hashlib.sha256(RAW.encode()).hexdigest()


def test_bytes_and_str_agree():
    """`acoustid` returns bytes on some paths and str on others."""
    assert hash_fingerprint(RAW.encode()) == hash_fingerprint(RAW)


def test_a_real_fingerprint_is_never_mistaken_for_an_encoding():
    """The decode must not misfire on the values it will actually see.

    A chromaprint fingerprint is base64 and cannot begin with a backslash, which
    is what makes the narrow check safe.
    """
    assert canonical(RAW) == RAW.encode()
    assert not RAW.startswith("\\x")


def test_unrecognised_shapes_are_left_alone():
    """Anything not recognisably a re-encoding passes through unchanged.

    Guessing would be worse than not trying: a wrong guess produces a key that is
    wrong in a new way, and nothing downstream can tell.
    """
    for odd in ("\\xZZZZ", "\\x41514", "\\x", "not a fingerprint", ""):
        assert canonical(odd) == odd.encode()


def test_escaped_non_ascii_is_not_decoded():
    """Valid hex that decodes to arbitrary bytes is not a re-encoded fingerprint.

    `\\x00ff` is well-formed hex, so only the printable-ASCII test separates it
    from an escaped fingerprint.
    """
    assert canonical("\\x00ff") == b"\\x00ff"
