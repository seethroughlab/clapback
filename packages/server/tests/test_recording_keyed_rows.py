"""A contribution without a fingerprint is keyed on its recording — `ADR-0020`.

The second contributor holds vectors and MusicBrainz recording ids for tracks
it never fingerprinted. Point 1 keys such a contribution on
`SHA256("musicbrainz_recording:" + mbid)` in the existing column; point 2 says
every row declares its `key_type`; point 3 says the row claims its own
recording and thereby joins `ADR-0019`'s agreement and collapse with no new
read code; point 7 says a request with neither key is a 422 that says so.

The pure tests pin the shape. The database tests are point 10's: one recording
under a fingerprint key from one client and a recording key from another, and
the confirmation is counted — on both reads, by id, in a batch, as a neighbour.
"""

from __future__ import annotations

import hashlib
import math

import pytest
from pydantic import ValidationError

from app.api.routes import (
    KEY_FINGERPRINT,
    KEY_RECORDING,
    EmbeddingRequest,
    recording_key,
)
from tests.conftest import needs_db

PIPELINE = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"
MBID = "1c6da765-da50-476b-a000-61e7cf45ded8"
H1 = "a1" * 32


def _unit(seed: int) -> list[float]:
    v = [math.sin(seed * 7.0 + i * 0.37) for i in range(512)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _body(vector, client_id, *, fingerprint_hash=None, mbid=MBID):
    body = {
        "embedding": vector,
        "analysis_version": 1,
        "clap_model_version": "x",
        "pipeline_version": PIPELINE,
    }
    if fingerprint_hash:
        body["fingerprint_hash"] = fingerprint_hash
    if client_id:
        body["client_id"] = client_id
    if mbid:
        body["recording_mbid"] = mbid
    return body


class TestTheDerivedKey:
    def test_it_is_the_digest_the_record_states(self):
        """Point 1, literally — so a client, or a takedown, can compute it."""
        assert (
            recording_key(MBID)
            == hashlib.sha256(f"musicbrainz_recording:{MBID}".encode()).hexdigest()
        )
        assert len(recording_key(MBID)) == 64

    def test_it_is_canonical_in_the_id(self):
        """Case and whitespace in the id do not make a second key."""
        assert recording_key(MBID.upper()) == recording_key(f" {MBID} ")

    def test_a_request_without_a_fingerprint_is_keyed_on_its_recording(self):
        req = EmbeddingRequest(**_body(_unit(1), "c"))
        assert req.fingerprint_hash is None
        assert req.key == recording_key(MBID)
        assert req.key_type == KEY_RECORDING

    def test_a_request_with_both_is_a_fingerprint_row_with_a_claim(self):
        """Point 7: nothing changes for a client that has a fingerprint."""
        req = EmbeddingRequest(**_body(_unit(1), "c", fingerprint_hash=H1))
        assert req.key == H1
        assert req.key_type == KEY_FINGERPRINT

    def test_neither_is_refused_and_says_why(self):
        with pytest.raises(ValidationError) as exc:
            EmbeddingRequest(**_body(_unit(1), "c", mbid=None))
        assert "ADR-0020" in str(exc.value)

    def test_only_the_musicbrainz_id_keys_a_row(self):
        """Point 4: an AcoustID track id alone is not a key — a client holding
        one fingerprinted to get it, and keys on the fingerprint."""
        body = _body(_unit(1), "c", mbid=None)
        body["acoustid_track_id"] = MBID
        with pytest.raises(ValidationError):
            EmbeddingRequest(**body)


@needs_db
class TestOneRecordingTwoKindsOfKey:
    async def test_the_recording_keyed_row_confirms_the_fingerprint_row(self, db_client):
        """Client A fingerprinted the file; client B never did and sends the
        vector under the MBID alone. B's row is created under the derived key,
        says so, claims its recording, and is one confirmation of it."""
        v = _unit(1)
        r = await db_client.post("/v1/embeddings", json=_body(v, "client-a", fingerprint_hash=H1))
        assert r.status_code == 201 and r.json()["key_type"] == KEY_FINGERPRINT

        r = await db_client.post("/v1/embeddings", json=_body(v, "client-b"))
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "created"
        assert r.json()["fingerprint_hash"] == recording_key(MBID)
        assert r.json()["key_type"] == KEY_RECORDING

        # Point 3: the join is the self-claim, and every read carries the figure.
        for path in (
            f"/v1/embeddings/{H1}",
            f"/v1/embeddings/{recording_key(MBID)}",
        ):
            r = await db_client.get(path)
            assert r.status_code == 200, r.text
            assert (r.json()["recording_confirmations"], r.json()["recording_contradictions"]) == (
                1,
                0,
            )
        r = await db_client.get(f"/v1/embeddings/{recording_key(MBID)}")
        assert r.json()["key_type"] == KEY_RECORDING
        assert r.json()["recording_mbid"] == MBID

        # By id: both rows, each saying what it is.
        r = await db_client.get(f"/v1/recordings/{MBID}")
        rows = {e["fingerprint_hash"]: e for e in r.json()["embeddings"]}
        assert set(rows) == {H1, recording_key(MBID)}
        assert rows[H1]["key_type"] == KEY_FINGERPRINT
        assert rows[recording_key(MBID)]["key_type"] == KEY_RECORDING
        assert {e["recording_confirmations"] for e in rows.values()} == {1}

        # Point 2: the batch lookup and the neighbour carry it too.
        r = await db_client.post(
            "/v1/embeddings/lookup", json={"keys": [{"recording_mbid": MBID}], "vectors": False}
        )
        assert r.json()["results"][0]["row"]["key_type"] in (KEY_FINGERPRINT, KEY_RECORDING)
        r = await db_client.post(
            "/v1/similar", json={"embedding": v, "limit": 5, "pipeline_version": PIPELINE}
        )
        kinds = {n["key_type"] for n in r.json()["neighbours"]}
        # `ADR-0019` point 4 folds the two rows into one neighbour, so one kind
        # is served; either is a correct answer, and the field is present.
        assert kinds and kinds <= {KEY_FINGERPRINT, KEY_RECORDING}
        assert r.json()["collapsed"] == 1

    async def test_a_second_recording_keyed_client_confirms_the_same_row(self, db_client):
        """Two installs that never fingerprinted land on one row: a confirmation
        at the key, with the agreement recorded, exactly as for a fingerprint."""
        v = _unit(1)
        await db_client.post("/v1/embeddings", json=_body(v, "client-b"))
        r = await db_client.post("/v1/embeddings", json=_body(v, "client-c"))
        assert r.json()["status"] == "confirmed"
        assert r.json()["contributor_count"] == 2
        assert r.json()["key_type"] == KEY_RECORDING

    async def test_a_recording_keyed_row_needs_a_client(self, db_client):
        """Point 6: the row is a claim, and a claim has somebody behind it."""
        r = await db_client.post("/v1/embeddings", json=_body(_unit(1), None))
        assert r.status_code == 422
        assert "client_id" in r.text

    async def test_the_batch_takes_either_shape_and_says_which_it_made(self, db_client):
        v = _unit(1)
        r = await db_client.post(
            "/v1/embeddings/batch",
            json={
                "contributions": [
                    _body(v, "client-a", fingerprint_hash=H1),
                    _body(v, "client-b"),
                ]
            },
        )
        assert r.status_code == 200, r.text
        results = r.json()["results"]
        assert [x["key_type"] for x in results] == [KEY_FINGERPRINT, KEY_RECORDING]
        assert results[1]["fingerprint_hash"] == recording_key(MBID)
        assert {x["status"] for x in results} == {"created"}

    async def test_pipelines_counts_recording_keyed_rows(self, db_client):
        await db_client.post(
            "/v1/embeddings", json=_body(_unit(1), "client-a", fingerprint_hash=H1)
        )
        await db_client.post("/v1/embeddings", json=_body(_unit(2), "client-b"))
        r = await db_client.get("/v1/pipelines")
        (entry,) = r.json()["pipelines"]
        assert (entry["rows"], entry["named"], entry["recording_keyed"]) == (2, 2, 1)

    async def test_a_takedown_by_the_derived_key_removes_it(self, db_client):
        """`ADR-0004` point 7's path addresses rows by hash; the derived key is
        one, so nothing new is needed to take a recording-keyed row down."""
        from sqlalchemy import select

        from app.db.models import Embedding, RecordingClaim
        from app.db.session import async_session_maker

        await db_client.post("/v1/embeddings", json=_body(_unit(1), "client-b"))
        async with async_session_maker() as db:
            assert (
                await db.scalar(
                    select(Embedding.key_type).where(
                        Embedding.fingerprint_hash == recording_key(MBID)
                    )
                )
            ) == KEY_RECORDING
            claims = (
                (
                    await db.execute(
                        select(RecordingClaim).where(
                            RecordingClaim.fingerprint_hash == recording_key(MBID)
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [(c.claim_type, c.recording_id, c.client_id) for c in claims] == [
            (KEY_RECORDING, MBID, "client-b")
        ]
