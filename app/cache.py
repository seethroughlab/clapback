"""In-process async TTL cache for expensive read-only computations.

Used to absorb load on the public landing page (`/`) by caching the
per-table stat counts. Single-machine, single-worker scale — no need
for Redis or shared cache.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class TTLCache:
    """Async-safe in-process cache with per-key TTL and miss-collapsing locks."""

    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_compute(self, key: str, compute: Callable[[], Awaitable[T]]) -> T:
        now = time.monotonic()
        cached = self._store.get(key)
        if cached and cached[0] > now:
            return cached[1]  # type: ignore[return-value]

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._store.get(key)
            if cached and cached[0] > now:
                return cached[1]  # type: ignore[return-value]
            value = await compute()
            self._store[key] = (now + self._ttl, value)
            return value


stats_cache = TTLCache(ttl_seconds=60)
