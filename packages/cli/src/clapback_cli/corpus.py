"""Talking to the commons over HTTP, and only over HTTP.

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
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://clapback.seethroughlab.com"

#: The server rate-limits contributions. Backing off politely is the difference
#: between a slow client and a client the operator has to block, and a long run
#: will meet this: Familiar's backfill of 26,431 tracks took roughly 80 minutes
#: of paced lookups.
_RETRY_DELAYS = (2.0, 5.0, 15.0)


class CorpusError(RuntimeError):
    """The corpus could not be reached, or refused something it should not have."""


class Corpus:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
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
                "User-Agent": "clapback-cli",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = None
            return exc.code, payload
        except urllib.error.URLError as exc:
            raise CorpusError(f"{self.base_url} is unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise CorpusError(f"{self.base_url} timed out after {self.timeout}s") from exc

    def health(self) -> bool:
        status, _ = self._request("GET", "/health")
        return status == 200

    def has(self, fingerprint_hash: str, pipeline_version: str) -> bool:
        """Whether the corpus already holds this recording from this pipeline.

        **Asked before every contribution, and that is not an optimisation.** A
        repeat POST of a vector that is already there increments
        `contributor_count` and records a `submission_agreement` row, so a client
        that re-sent its library would manufacture evidence of one installation
        independently agreeing with itself — which is precisely the measurement
        `ADR-0008` is built on. Familiar's backfill learned this the same way.
        """
        # The pipeline identity is `+`-joined, and `+` means a space in a query
        # string. `ADR-0006`'s Implementation block records what an unescaped one
        # costs: a 404 that looks exactly like the recording being absent.
        from urllib.parse import quote

        status, _ = self._request(
            "GET",
            f"/v1/embeddings/{fingerprint_hash}?pipeline_version={quote(pipeline_version, safe='')}",
        )
        if status == 200:
            return True
        if status == 404:
            return False
        raise CorpusError(f"lookup returned {status}")

    def contribute(
        self,
        *,
        fingerprint_hash: str,
        embedding: list[float],
        pipeline_version: str,
        clap_model_version: str,
        analysis_version: int,
        client_id: str,
    ) -> str:
        """POST one embedding. Returns a short word describing what happened."""
        body = {
            "fingerprint_hash": fingerprint_hash,
            "embedding": embedding,
            "pipeline_version": pipeline_version,
            "clap_model_version": clap_model_version,
            "analysis_version": analysis_version,
            "client_id": client_id,
        }
        for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
            status, payload = self._request("POST", "/v1/embeddings", body)
            if status in (200, 201):
                return "contributed"
            if status == 429 and delay is not None:
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
