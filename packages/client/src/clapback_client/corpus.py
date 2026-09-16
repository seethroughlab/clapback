"""Talking to the commons over HTTP, and only over HTTP.

This is the client half of the contract `ADR-0011` publishes: look up before you
contribute, send what the records require, and back off when told to. A tool that
imports this and `fingerprint.py` has everything it needs to be a contributor,
and nothing it does not.

`ADR-0005` point 12: the API is the only way in. Every guarantee the corpus makes
— revocation, quotas, the row ceiling, agreement recording — is code on the write
path, so a client that reached the database directly would be a second write path
with none of them.

`urllib` rather than `httpx` or `requests` on purpose. This package's argument is
that it is small enough to install next to anything; two calls against a JSON API
do not justify a dependency, and the one place that matters — retrying a 429 — is
a loop either way.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator

DEFAULT_BASE_URL = "https://clapback.seethroughlab.com"

#: The server rate-limits contributions. Backing off politely is the difference
#: between a slow client and a client the operator has to block, and a long run
#: will meet this: Familiar's backfill of 26,431 tracks took roughly 80 minutes
#: of paced lookups.
_RETRY_DELAYS = (2.0, 5.0, 15.0)

#: `ADR-0015` point 1: the most keys one batch lookup may carry. Over it is a
#: 422, not a partial answer, so the client never sends more.
LOOKUP_BATCH = 100

#: `ADR-0016` point 1: the most rows one batch contribution may carry.
CONTRIBUTE_BATCH = 100

#: The longest a `Retry-After` is honoured for. A window is a minute; anything
#: far past that is a server telling us something other than "wait".
_MAX_RETRY_AFTER = 120.0

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _key_of(key: str) -> dict[str, str]:
    """A batch key as the corpus wants it: by shape, a hash or a recording id."""
    k = key.strip().lower()
    if _HEX64.match(k):
        return {"fingerprint_hash": k}
    if _UUID.match(k):
        return {"recording_mbid": k}
    raise ValueError(f"not a fingerprint hash or a recording MBID: {key!r}")


def _retry_after(headers: dict[str, str], fallback: float | None) -> float | None:
    """Seconds to wait after a 429: the server's `Retry-After` if it sent one and
    it is sane, else the next fixed delay, else None to give up."""
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is not None:
        try:
            return min(max(float(value), 0.0), _MAX_RETRY_AFTER)
        except ValueError:
            pass
    return fallback


class CorpusError(RuntimeError):
    """The corpus could not be reached, or refused something it should not have."""


class Corpus:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
        status, payload, _ = self._request_with_headers(method, path, body)
        return status, payload

    def _request_with_headers(
        self, method: str, path: str, body: dict | None = None
    ) -> tuple[int, dict | None, dict[str, str]]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                # Say who is calling. Not identity — `ADR-0004` point 1 keeps that
                # to `client_id` in the body — but an operator reading logs should
                # be able to tell this tool from a browser.
                "User-Agent": "clapback-client",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return (
                    resp.status,
                    (json.loads(raw) if raw else None),
                    dict(getattr(resp, "headers", None) or {}),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = None
            return exc.code, payload, dict(exc.headers or {})
        except urllib.error.URLError as exc:
            raise CorpusError(f"{self.base_url} is unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise CorpusError(f"{self.base_url} timed out after {self.timeout}s") from exc

    def health(self) -> bool:
        status, _ = self._request("GET", "/health")
        return status == 200

    def lookup(
        self,
        fingerprint_hash: str | None = None,
        pipeline_version: str | None = None,
        *,
        recording_mbid: str | None = None,
    ) -> dict | None:
        """The corpus's row for this recording from this pipeline, or None.

        The row carries `embedding` (512 floats), `contributor_count`, and the
        pipeline it was produced by. A tool that gets a row back here does not
        need to run the model: that is the whole exchange a plug-in makes, and
        on a Raspberry Pi it is minutes per track.

        **Look up by `recording_mbid=` if you hold one, by hash otherwise, and
        contribute under your hash either way** (`ADR-0019` point 3). The hash is
        exact within one fingerprinting path and may differ across two — the
        `fpcalc` binary and pyacoustid's library agree on 24 of 56 FLACs,
        measured 2026-09-16 — so a miss by hash does not mean the corpus lacks
        the recording. A MusicBrainz recording id is the same on every path.
        Asked by id, this returns the row most clients have claimed under it
        (then the most-confirmed), in the same shape a hash lookup returns, with
        `recording_mbid` set to the id you asked by. Exactly one of the two keys
        must be given.

        With `pipeline_version`, only a row from the *same* pipeline is returned.
        Two vectors are comparable exactly when their pipeline identities match
        (`ADR-0006`), so a vector from another pipeline would be a wrong answer
        wearing the right shape. **Pass it whenever you hold a vector of your
        own** — to compare, or to decide whether to contribute.

        Without it, the most-confirmed row from any pipeline is returned, and the
        row says which. That is the shape for a tool with no embedder at all —
        Picard's plugin in lookup-only mode — which wants to know whether the
        corpus holds a recording, name it, or ask what it sounds like using the
        corpus's own vector under the corpus's own pipeline identity. Such a tool
        never contributes, so the comparability question does not arise for it.
        """
        # The pipeline identity is `+`-joined, and `+` means a space in a query
        # string. `ADR-0006`'s Implementation block records what an unescaped one
        # costs: a 404 that looks exactly like the recording being absent.
        from urllib.parse import quote

        if (fingerprint_hash is None) == (recording_mbid is None):
            raise ValueError(
                "lookup takes a fingerprint_hash or a recording_mbid, not both or neither"
            )
        if recording_mbid is not None:
            rows = self.recording(recording_mbid, pipeline_version=pipeline_version)
            if not rows:
                return None
            return {**rows[0], "recording_mbid": recording_mbid}

        path = f"/v1/embeddings/{fingerprint_hash}"
        if pipeline_version is not None:
            path += f"?pipeline_version={quote(pipeline_version, safe='')}"
        status, payload = self._request("GET", path)
        if status == 200:
            return payload
        if status == 404:
            return None
        raise CorpusError(f"lookup returned {status}")

    def has(self, fingerprint_hash: str, pipeline_version: str) -> bool:
        """Whether the corpus already holds this recording from this pipeline.

        **Asked before every contribution, and that is not an optimisation.** A
        repeat POST of a vector that is already there increments
        `contributor_count` and records a `submission_agreement` row, so a client
        that re-sent its library would manufacture evidence of one installation
        independently agreeing with itself — which is precisely the measurement
        `ADR-0008` is built on. Two clients have learned this now; it is why the
        contract publishes the check rather than trusting each tool to write it.
        """
        return self.lookup(fingerprint_hash, pipeline_version) is not None

    def lookup_many(
        self,
        keys: Iterable[str],
        pipeline_version: str | None = None,
        *,
        vectors: bool = True,
    ) -> Iterator[tuple[str, dict | None]]:
        """Look up a library: `(key, row_or_None)` for every key, in order.

        `ADR-0015`. A key is a fingerprint hash (64 hex characters) or a
        MusicBrainz recording id (a UUID) — told apart by shape, so a tool can
        hand over whichever it holds per track, and should hand over the id
        when it has one (`ADR-0019` point 3: the id is the same on every
        fingerprinting path; the hash may not be). Sent in batches of 100 to
        `POST /v1/embeddings/lookup`; each answer is what `lookup` would have
        returned for that key, or `None`.

        `vectors=False` asks for everything but the embedding — "which of these
        do you hold, and what are they called?" — which is the whole-library
        form of "which recording is this?" (`ADR-0018` point 3) and a fraction
        of the bytes.

        The corpus counts the limit per key, not per request, so a library
        larger than the per-minute limit will be told to wait; this honours
        `Retry-After` and then continues, so a full pass over any library is one
        call that takes as long as it takes.
        """
        batch: list[str] = []
        for key in keys:
            batch.append(key)
            if len(batch) == LOOKUP_BATCH:
                yield from self._lookup_batch(batch, pipeline_version, vectors)
                batch = []
        if batch:
            yield from self._lookup_batch(batch, pipeline_version, vectors)

    def _lookup_batch(
        self, keys: list[str], pipeline_version: str | None, vectors: bool
    ) -> Iterator[tuple[str, dict | None]]:
        body: dict = {"keys": [_key_of(k) for k in keys], "vectors": vectors}
        if pipeline_version is not None:
            body["pipeline_version"] = pipeline_version
        for delay in (*_RETRY_DELAYS, None):
            status, payload, headers = self._request_with_headers(
                "POST", "/v1/embeddings/lookup", body
            )
            if status == 200:
                results = (payload or {}).get("results", [])
                if len(results) != len(keys):
                    raise CorpusError(f"batch lookup answered {len(results)} of {len(keys)} keys")
                for key, result in zip(keys, results):
                    yield key, result.get("row")
                return
            if status == 429:
                wait = _retry_after(headers, delay)
                if wait is None:
                    break
                time.sleep(wait)
                continue
            if status == 422:
                raise CorpusError(
                    f"the corpus refused the batch as malformed: {(payload or {}).get('detail')}"
                )
            raise CorpusError(f"batch lookup returned {status}: {payload}")
        raise CorpusError("rate limited repeatedly; try again later")

    def contribute(
        self,
        *,
        fingerprint_hash: str,
        embedding: list[float],
        pipeline_version: str,
        client_id: str,
        clap_model_version: str | None = None,
        analysis_version: int = 1,
        recording_mbid: str | None = None,
    ) -> str:
        """POST one embedding. Returns a short word describing what happened.

        `pipeline_version` and `client_id` are what the records require of a
        contribution (`ADR-0006` point 4, `ADR-0004` point 1) and have no
        defaults. A tool with its own pipeline declares its own identity — five
        `+`-joined tokens: checkpoint, front-end, windowing, pooling, and the
        precision of what is *sent*; "Naming your pipeline" in the README has
        the convention and `pipelines()` lists what the corpus already holds
        (`ADR-0014`). The other two are recorded columns the key no longer includes:
        `clap_model_version` defaults to the first component of the pipeline
        identity, which is the checkpoint, so the two cannot disagree about one
        fact; `analysis_version` is the caller's own counter and starts at 1.

        `recording_mbid` — `ADR-0012` — is the MusicBrainz *recording* id, if the
        caller holds one, and is recorded as this client's claim alongside the
        vector. It is what turns this row from a hash into a title in somebody
        else's similarity results. Sending it tells the corpus operator which
        recording this client holds, so a tool should send it under the same
        setting that sends the vector, and say so where the user will read it.

        Everything sent is dedicated to the public domain under CC0 1.0, like every other row in the corpus, and may be republished in its public exports (`ADR-0013` point 2). A tool says this beside its
        contribution switch, because a self-issued client has nowhere else to
        consent.
        """
        body = {
            "fingerprint_hash": fingerprint_hash,
            "embedding": embedding,
            "pipeline_version": pipeline_version,
            "clap_model_version": clap_model_version or pipeline_version.split("+")[0],
            "analysis_version": analysis_version,
            "client_id": client_id,
        }
        if recording_mbid:
            body["recording_mbid"] = recording_mbid
        for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
            status, payload = self._request("POST", "/v1/embeddings", body)
            if status in (200, 201):
                return "contributed"
            if status == 429:
                if delay is None:
                    break
                time.sleep(delay)
                continue
            if status == 422:
                detail = (payload or {}).get("detail")
                raise CorpusError(f"the corpus refused the submission as malformed: {detail}")
            if status == 507 or (status == 403 and "ceiling" in str(payload).lower()):
                # `ADR-0004` point 9's row ceiling. A refusal here is the corpus
                # working, not failing — stop rather than hammering it.
                raise CorpusError("the corpus is full and is refusing writes (ADR-0004 point 9)")
            raise CorpusError(f"contribute returned {status}: {payload}")
        raise CorpusError("rate limited repeatedly; try again later")

    def contribute_many(self, rows: Iterable[dict]) -> Iterator[dict]:
        """Contribute a library: one result per row, in order.

        `ADR-0016`. Each row is the keyword arguments `contribute` takes —
        `fingerprint_hash`, `embedding`, `pipeline_version`, `client_id`, and
        optionally `recording_mbid`, `clap_model_version`, `analysis_version` —
        and `client_id` is required on every one: a contribution nobody can
        confirm is admissible one at a time and not by the hundred (point 5).
        Sent in batches of 100 to `POST /v1/embeddings/batch`, which runs every
        per-row guarantee per row in the same code the single endpoint uses.

        **Look up first, as with `contribute`.** A batch does not change the
        rule: a row the corpus already holds from this pipeline is recorded as
        agreement, and a library re-sent by the hundred manufactures a hundred
        agreements of one install with itself. `lookup_many` is the check.

        Each result is `{"fingerprint_hash", "status", "code", ...}`: status
        `created` or `confirmed` with `contributor_count`, or `refused` with the
        `code` and `detail` the row would have got alone — 507 at the corpus
        ceiling, 429 at this client's daily quota with `retry_after` in seconds.
        A batch is not atomic (point 4): 97 created, 2 confirmed and 1 refused
        is 99 rows contributed, and the client retries a refused row on its own
        result, not the batch. A 429 for the *batch* — the per-minute row limit
        — is waited out here, honouring `Retry-After`.
        """
        batch: list[dict] = []
        for row in rows:
            batch.append(row)
            if len(batch) == CONTRIBUTE_BATCH:
                yield from self._contribute_batch(batch)
                batch = []
        if batch:
            yield from self._contribute_batch(batch)

    def _contribute_batch(self, rows: list[dict]) -> Iterator[dict]:
        contributions = []
        for row in rows:
            if not row.get("client_id"):
                raise ValueError(
                    "client_id is required on every row of contribute_many (ADR-0016 point 5)"
                )
            pipeline = row["pipeline_version"]
            contributions.append(
                {
                    "fingerprint_hash": row["fingerprint_hash"],
                    "embedding": row["embedding"],
                    "pipeline_version": pipeline,
                    "clap_model_version": row.get("clap_model_version") or pipeline.split("+")[0],
                    "analysis_version": row.get("analysis_version", 1),
                    "client_id": row["client_id"],
                    **(
                        {"recording_mbid": row["recording_mbid"]}
                        if row.get("recording_mbid")
                        else {}
                    ),
                }
            )
        body = {"contributions": contributions}
        for delay in (*_RETRY_DELAYS, None):
            status, payload, headers = self._request_with_headers(
                "POST", "/v1/embeddings/batch", body
            )
            if status == 200:
                results = (payload or {}).get("results", [])
                if len(results) != len(rows):
                    raise CorpusError(
                        f"batch contribute answered {len(results)} of {len(rows)} rows"
                    )
                yield from results
                return
            if status == 429:
                wait = _retry_after(headers, delay)
                if wait is None:
                    break
                time.sleep(wait)
                continue
            if status == 422:
                raise CorpusError(
                    f"the corpus refused the batch as malformed: {(payload or {}).get('detail')}"
                )
            if status == 413:
                raise CorpusError(
                    "the corpus refused the batch as too large; send fewer rows per batch"
                )
            raise CorpusError(f"batch contribute returned {status}: {payload}")
        raise CorpusError("rate limited repeatedly; try again later")

    def claim(self, *, fingerprint_hash: str, recording_mbid: str, client_id: str) -> dict:
        """Name a recording the corpus already holds — without re-sending its vector.

        `ADR-0012` point 4's second write path, and the one a tool uses to add ids
        to tracks it contributed earlier. **Never re-send the vector to do this**:
        a repeat `contribute` is recorded as agreement, and a tool tagging its
        library must not read as that library agreeing with itself.

        **Claim only what you established yourself** — from your tags, from
        AcoustID, from Picard — never an id a lookup or `claims()` told you. A
        tool that sends back what it copied counts itself as independent
        confirmation of it, which is the manufactured agreement `ADR-0004` point
        4 and `ADR-0008` exist to exclude. The server cannot tell; this contract
        is the only defence (`ADR-0018` point 4).

        Returns the corpus's answer — what the hash now resolves to, and how many
        distinct clients say so. Raises `CorpusError` on a 404, which means the
        corpus does not hold the row yet: contribute the vector first.
        """
        status, payload = self._request(
            "POST",
            "/v1/recordings/claims",
            {
                "fingerprint_hash": fingerprint_hash,
                "recording_mbid": recording_mbid,
                "client_id": client_id,
            },
        )
        if status == 201:
            return payload or {}
        if status == 404:
            raise CorpusError("the corpus holds no row for that hash; contribute it first")
        if status == 422:
            raise CorpusError(f"the corpus refused the claim: {(payload or {}).get('detail')}")
        raise CorpusError(f"claim returned {status}: {payload}")

    def similar(
        self, embedding: list[float], *, limit: int = 10, pipeline_version: str | None = None
    ) -> list[dict]:
        """What sounds like this — across every library the corpus holds.

        Each neighbour carries `fingerprint_hash`, `similarity`, `pipeline_version`,
        and — since `ADR-0012` — `recording_mbid` and `recording_claims`. A
        neighbour with an id is a recording a person can look up; one without is
        still a hash, and the fields say which. Pass `pipeline_version` to get
        only vectors comparable with the one you sent.
        """
        body: dict = {"embedding": embedding, "limit": limit}
        if pipeline_version:
            body["pipeline_version"] = pipeline_version
        status, payload = self._request("POST", "/v1/similar", body)
        if status != 200:
            raise CorpusError(f"similar returned {status}: {payload}")
        return list((payload or {}).get("neighbours", []))

    def claims(self, fingerprint_hash: str) -> list[dict] | None:
        """Every recording id claimed for a row, most-supported first, no vector.

        `ADR-0018` point 2 — "which recording is this?", with the dissent that
        `lookup`'s single `recording_mbid` summarises away. Each entry is
        `{"type": "musicbrainz_recording", "id": ..., "clients": n}`, where
        `clients` is how many distinct installs asserted it. `None` when the
        corpus does not hold the row; `[]` when it does and nobody has named it.

        What this is not: verified, or AcoustID. It answers only for rows the
        corpus holds, from what contributors asserted, and a count of one means
        one install said so. And **never send an id you learned here back as
        your own claim** — see `claim`.
        """
        status, payload = self._request("GET", f"/v1/recordings/by-hash/{fingerprint_hash}")
        if status == 200:
            return list((payload or {}).get("claims", []))
        if status == 404:
            return None
        raise CorpusError(f"claims returned {status}: {payload}")

    def pipelines(self) -> list[dict]:
        """Every pipeline identity the corpus holds rows under, most populated first.

        `ADR-0014` point 3. Each entry carries `pipeline_version`, `rows`,
        `named` (rows anyone has claimed a recording for) and the first and
        latest contribution. Ask before choosing an identity: if yours is here,
        contributing under it joins that population; if not, yours starts one.
        The corpus never interprets the strings — see "Naming your pipeline" in
        the README for the convention it expects you to follow.
        """
        status, payload = self._request("GET", "/v1/pipelines")
        if status == 200:
            return list((payload or {}).get("pipelines", []))
        raise CorpusError(f"pipelines returned {status}: {payload}")

    def recording(self, recording_mbid: str, *, pipeline_version: str | None = None) -> list[dict]:
        """What does recording X sound like — without holding X.

        `ADR-0012` point 5's third read. Every row any client has claimed under
        this id, each with its vector, most-claimed first. Empty when nobody has
        claimed it. Two rows under one pipeline are one file keyed twice by two
        fingerprinting paths (`ADR-0019`); `lookup(recording_mbid=)` picks the
        first for a tool that wants one.
        """
        from urllib.parse import quote

        path = f"/v1/recordings/{recording_mbid}"
        if pipeline_version:
            path += f"?pipeline_version={quote(pipeline_version, safe='')}"
        status, payload = self._request("GET", path)
        if status == 200:
            return list((payload or {}).get("embeddings", []))
        if status == 404:
            return []
        raise CorpusError(f"recording lookup returned {status}: {payload}")
