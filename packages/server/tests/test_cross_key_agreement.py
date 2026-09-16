"""Agreement is counted per recording, not per key — `ADR-0019` point 2.

The measurement in `ADR-0010`'s Implementation block: two fingerprinting paths
key one file differently more often than not on CD audio, so a second client
on another path lands on a new row, and under the old write path its vector
agreed with nothing. Point 2 says the recording id joins what the key could
not: a contribution that names its recording is compared with every row under
the same pipeline claimed under that recording, the comparison is recorded
naming the row it was measured against, and confirmation is counted by
distinct `client_id` across all of the recording's rows.

This is the test the record's point 8 demands, and it is the suite's first
against a real database — see `conftest.py`. Against Postgres, over HTTP, the
whole write path and every read that serves the figure.
"""

from __future__ import annotations

import math

from tests.conftest import needs_db

pytestmark = needs_db

PIPELINE = "laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32"
MBID = "1c6da765-da50-476b-a000-61e7cf45ded8"
H1, H2, H3, H4 = ("a1" * 32, "b2" * 32, "c3" * 32, "d4" * 32)


def _unit(seed: int) -> list[float]:
    v = [math.sin(seed * 7.0 + i * 0.37) for i in range(512)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _contribution(fingerprint_hash, client_id, vector, mbid=MBID):
    body = {
        "fingerprint_hash": fingerprint_hash,
        "embedding": vector,
        "analysis_version": 1,
        "clap_model_version": "x",
        "pipeline_version": PIPELINE,
    }
    if client_id:
        body["client_id"] = client_id
    if mbid:
        body["recording_mbid"] = mbid
    return body


async def _figures(client, path):
    r = await client.get(path)
    assert r.status_code == 200, r.text
    b = r.json()
    b = b["embeddings"][0] if "embeddings" in b else b
    return b["recording_confirmations"], b["recording_contradictions"]


class TestOneRecordingTwoKeysTwoClients:
    async def test_the_second_client_confirms_across_keys(self, db_client):
        """Client A holds the recording under H1; client B, on another
        fingerprinting path, holds the same audio under H2. B's contribution is
        a new row — and one confirmation of the recording."""
        v = _unit(1)
        r = await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", v))
        assert r.status_code == 201 and r.json()["status"] == "created"
        assert await _figures(db_client, f"/v1/embeddings/{H1}") == (0, 0)

        r = await db_client.post("/v1/embeddings", json=_contribution(H2, "client-b", v))
        assert r.status_code == 201 and r.json()["status"] == "created", r.text

        # Served everywhere the recording is: by either key, by id, in a batch, as a neighbour.
        assert await _figures(db_client, f"/v1/embeddings/{H1}") == (1, 0)
        assert await _figures(db_client, f"/v1/embeddings/{H2}") == (1, 0)
        assert await _figures(db_client, f"/v1/recordings/{MBID}") == (1, 0)
        r = await db_client.post(
            "/v1/embeddings/lookup", json={"keys": [{"recording_mbid": MBID}], "vectors": False}
        )
        assert (
            r.json()["results"][0]["row"]["recording_confirmations"],
            r.json()["results"][0]["row"]["recording_contradictions"],
        ) == (1, 0)
        r = await db_client.post(
            "/v1/similar", json={"embedding": v, "limit": 2, "pipeline_version": PIPELINE}
        )
        assert {n["recording_confirmations"] for n in r.json()["neighbours"]} == {1}

    async def test_a_third_client_with_a_different_vector_contradicts(self, db_client):
        """`ADR-0008` point 4: disagreement is served, not hidden. A different
        rip, or a different pipeline wearing the same identity, lands outside
        the `identical` band and is counted as a contradiction beside the
        confirmation, never averaged into it."""
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", _unit(1)))
        await db_client.post("/v1/embeddings", json=_contribution(H2, "client-b", _unit(1)))
        r = await db_client.post("/v1/embeddings", json=_contribution(H3, "client-c", _unit(2)))
        assert r.status_code == 201
        assert await _figures(db_client, f"/v1/recordings/{MBID}") == (1, 1)

    async def test_a_client_never_confirms_its_own_row(self, db_client):
        """`ADR-0004` point 4: independence is distinct clients. A re-sent
        library agrees with itself and must not read as a confirmation."""
        v = _unit(1)
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", v))
        r = await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", v))
        assert r.json()["status"] == "confirmed"  # the key-level count moves...
        assert await _figures(db_client, f"/v1/embeddings/{H1}") == (
            0,
            0,
        )  # ...the recording's does not
        # Nor by re-fingerprinting on another path: A under H2 is still A.
        await db_client.post("/v1/embeddings", json=_contribution(H2, "client-a", v))
        assert await _figures(db_client, f"/v1/embeddings/{H1}") == (0, 0)

    async def test_an_unattributed_contribution_is_evidence_but_not_independence(self, db_client):
        """`ADR-0004` point 3. It is compared and recorded; it confirms nothing."""
        v = _unit(1)
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", v))
        r = await db_client.post("/v1/embeddings", json=_contribution(H4, None, v, mbid=None))
        assert r.status_code == 201
        assert await _figures(db_client, f"/v1/embeddings/{H1}") == (0, 0)

    async def test_the_agreement_names_the_row_it_was_measured_against(self, db_client):
        """The new column: `fingerprint_hash` is the submitted key, `other_hash`
        the stored row. A same-key agreement names its own row; a cross-key one
        names the other."""
        from sqlalchemy import select

        from app.db.models import SubmissionAgreement
        from app.db.session import async_session_maker

        v = _unit(1)
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", v))
        await db_client.post("/v1/embeddings", json=_contribution(H2, "client-b", v))
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-b", v))
        async with async_session_maker() as db:
            rows = (await db.execute(select(SubmissionAgreement))).scalars().all()
        pairs = sorted((r.fingerprint_hash[:2], r.other_hash[:2], r.client_id) for r in rows)
        assert pairs == [
            ("a1", "a1", "client-b"),
            ("a1", "b2", "client-b"),
            ("b2", "a1", "client-b"),
        ]
        assert all(r.similarity >= 0.999999 for r in rows)

    async def test_a_batch_row_gets_the_same_comparison(self, db_client):
        """`ADR-0016` point 2 made the batch call `_contribute_one`; this is
        what that bought — cross-key agreement without the batch changing."""
        v = _unit(1)
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", v))
        r = await db_client.post(
            "/v1/embeddings/batch", json={"contributions": [_contribution(H2, "client-b", v)]}
        )
        assert r.status_code == 200 and r.json()["created"] == 1
        assert await _figures(db_client, f"/v1/recordings/{MBID}") == (1, 0)


class TestSimilarityCollapsesRowsSharingARecording:
    """`ADR-0019` point 4: one neighbour per claimed recording under the
    requested pipeline — the nearest of its rows — so a recording two installs
    keyed differently appears once, not as two adjacent near-identical results.
    Rows nobody has named stay as they are."""

    async def test_two_keys_one_recording_is_one_neighbour(self, db_client):
        v = _unit(1)
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", v))
        await db_client.post("/v1/embeddings", json=_contribution(H2, "client-b", v))
        await db_client.post(
            "/v1/embeddings", json=_contribution(H3, "client-c", _unit(3), mbid=None)
        )
        r = await db_client.post(
            "/v1/similar", json={"embedding": v, "limit": 10, "pipeline_version": PIPELINE}
        )
        body = r.json()
        hashes = [n["fingerprint_hash"] for n in body["neighbours"]]
        assert len(hashes) == 2 and hashes[0] in (H1, H2) and hashes[1] == H3
        assert body["collapsed"] == 1
        assert body["searched"] == 3  # what was ranked, not what survived
        assert body["neighbours"][0]["recording_confirmations"] == 1

    async def test_the_nearest_row_of_the_recording_is_the_one_kept(self, db_client):
        """Two rips of one recording, a few e-04 apart: the one nearer the
        query is the neighbour, and the other is the row folded away."""
        near, far = _unit(1), [x * 0.999 + y * 0.001 for x, y in zip(_unit(1), _unit(9))]
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", far))
        await db_client.post("/v1/embeddings", json=_contribution(H2, "client-b", near))
        r = await db_client.post(
            "/v1/similar", json={"embedding": near, "limit": 5, "pipeline_version": PIPELINE}
        )
        assert [n["fingerprint_hash"] for n in r.json()["neighbours"]] == [H2]

    async def test_unnamed_rows_are_never_folded(self, db_client):
        v = _unit(1)
        await db_client.post("/v1/embeddings", json=_contribution(H1, "client-a", v, mbid=None))
        await db_client.post("/v1/embeddings", json=_contribution(H2, "client-b", v, mbid=None))
        r = await db_client.post(
            "/v1/similar", json={"embedding": v, "limit": 5, "pipeline_version": PIPELINE}
        )
        assert len(r.json()["neighbours"]) == 2 and r.json()["collapsed"] == 0

    async def test_the_window_widens_until_limit_distinct_recordings(self, db_client):
        """Eight rows under one recording and one under another, limit 2: the
        first window of four is all one recording, and the search keeps going."""
        v = _unit(1)
        for i, h in enumerate(f"{i:02d}" * 32 for i in range(8)):
            await db_client.post("/v1/embeddings", json=_contribution(h, f"client-{i}", v))
        other = "2c6da765-da50-476b-a000-61e7cf45ded8"
        await db_client.post(
            "/v1/embeddings", json=_contribution(H4, "client-z", _unit(2), mbid=other)
        )
        r = await db_client.post(
            "/v1/similar", json={"embedding": v, "limit": 2, "pipeline_version": PIPELINE}
        )
        assert [n["recording_mbid"] for n in r.json()["neighbours"]] == [MBID, other]
        assert r.json()["collapsed"] == 7


class TestTheIndexAnswersTheWholeWindow:
    async def test_a_limit_above_forty_is_not_truncated(self, db_client):
        """pgvector's HNSW returns at most `hnsw.ef_search` rows — 40 by default —
        whatever the LIMIT; measured on the instance 2026-09-16. `similar` sets it
        to its window per query. Sixty rows would be sequentially scanned, which
        answers the whole LIMIT and proves nothing, so the planner is told to use
        the index for the duration — the way the instance, at 25,886 rows, does."""
        from sqlalchemy import text

        from app.db.session import engine

        for i in range(60):
            await db_client.post(
                "/v1/embeddings",
                json=_contribution(f"{i:02x}" * 32, f"c{i}", _unit(i + 100), mbid=None),
            )
        async with engine.begin() as conn:
            await conn.execute(text("ALTER DATABASE cache SET enable_seqscan = off"))
        await engine.dispose()  # new connections pick the setting up
        try:
            # Unfiltered: with a `pipeline_version` filter on sixty rows the planner
            # takes the btree and sorts exactly, and HNSW is never asked.
            r = await db_client.post("/v1/similar", json={"embedding": _unit(100), "limit": 50})
            assert len(r.json()["neighbours"]) == 50
        finally:
            async with engine.begin() as conn:
                await conn.execute(text("ALTER DATABASE cache RESET enable_seqscan"))
            await engine.dispose()


ACOUSTID = "9ff43b6a-4f16-427c-93c2-92307ca505e0"


def _with_acoustid(body, acoustid=ACOUSTID):
    return {**body, "acoustid_track_id": acoustid}


class TestTheAcoustidTrackIdIsASecondClaimType:
    """`ADR-0019` point 6: admitted, not required. A client that has one sends it
    beside the MBID, and point 2's join runs on either id."""

    async def test_the_join_runs_on_the_acoustid_id_alone(self, db_client):
        """A holds MBID + AcoustID id under H1. B holds only the AcoustID id —
        a tool with AcoustID but no MusicBrainz match — under H2. B's vector
        still meets A's, through the id they share."""
        v = _unit(1)
        await db_client.post(
            "/v1/embeddings", json=_with_acoustid(_contribution(H1, "client-a", v))
        )
        r = await db_client.post(
            "/v1/embeddings", json=_with_acoustid(_contribution(H2, "client-b", v, mbid=None))
        )
        assert r.status_code == 201 and r.json()["status"] == "created"
        assert await _figures(db_client, f"/v1/embeddings/{H1}") == (1, 0)
        # H2 has no MBID; its figures are grouped under its AcoustID id, and the
        # same population is reached from either row.
        assert await _figures(db_client, f"/v1/embeddings/{H2}") == (1, 0)

    async def test_the_reads_carry_both_names(self, db_client):
        v = _unit(1)
        await db_client.post(
            "/v1/embeddings", json=_with_acoustid(_contribution(H1, "client-a", v))
        )
        r = (await db_client.get(f"/v1/embeddings/{H1}")).json()
        assert (
            r["recording_mbid"],
            r["recording_claims"],
            r["acoustid_track_id"],
            r["acoustid_claims"],
        ) == (MBID, 1, ACOUSTID, 1)
        claims = (await db_client.get(f"/v1/recordings/by-hash/{H1}")).json()["claims"]
        assert {(c["type"], c["id"]) for c in claims} == {
            ("musicbrainz_recording", MBID),
            ("acoustid_track", ACOUSTID),
        }

    async def test_the_recording_route_and_batch_take_the_type(self, db_client):
        v = _unit(1)
        await db_client.post(
            "/v1/embeddings", json=_with_acoustid(_contribution(H1, "client-a", v, mbid=None))
        )
        r = await db_client.get(f"/v1/recordings/{ACOUSTID}?type=acoustid_track")
        assert r.status_code == 200 and r.json()["type"] == "acoustid_track"
        assert r.json()["embeddings"][0]["fingerprint_hash"] == H1
        # As an MBID it is unknown — the two are never mixed.
        assert (await db_client.get(f"/v1/recordings/{ACOUSTID}")).status_code == 404
        assert (await db_client.get(f"/v1/recordings/{ACOUSTID}?type=other")).status_code == 422
        r = await db_client.post(
            "/v1/embeddings/lookup",
            json={
                "keys": [{"acoustid_track_id": ACOUSTID}, {"recording_mbid": ACOUSTID}],
                "vectors": False,
            },
        )
        rows = [x["row"] for x in r.json()["results"]]
        assert rows[0]["fingerprint_hash"] == H1 and rows[1] is None

    async def test_a_claim_may_name_either_or_both(self, db_client):
        await db_client.post(
            "/v1/embeddings", json=_contribution(H1, "client-a", _unit(1), mbid=None)
        )
        r = await db_client.post(
            "/v1/recordings/claims",
            json={"fingerprint_hash": H1, "acoustid_track_id": ACOUSTID, "client_id": "client-a"},
        )
        assert (
            r.status_code == 201
            and r.json()["acoustid_track_id"] == ACOUSTID
            and r.json()["recording_mbid"] is None
        )
        r = await db_client.post(
            "/v1/recordings/claims", json={"fingerprint_hash": H1, "client_id": "client-a"}
        )
        assert r.status_code == 422

    async def test_similarity_collapses_on_the_acoustid_id_when_there_is_no_mbid(self, db_client):
        v = _unit(1)
        await db_client.post(
            "/v1/embeddings", json=_with_acoustid(_contribution(H1, "client-a", v, mbid=None))
        )
        await db_client.post(
            "/v1/embeddings", json=_with_acoustid(_contribution(H2, "client-b", v, mbid=None))
        )
        r = await db_client.post(
            "/v1/similar", json={"embedding": v, "limit": 5, "pipeline_version": PIPELINE}
        )
        assert len(r.json()["neighbours"]) == 1 and r.json()["collapsed"] == 1
        assert r.json()["neighbours"][0]["acoustid_track_id"] == ACOUSTID
