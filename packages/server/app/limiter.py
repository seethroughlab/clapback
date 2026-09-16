"""Rate limiter configuration."""

import time

from fastapi import HTTPException, Request
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address

# Rate limiter instance (shared across app)
limiter = Limiter(key_func=get_remote_address)


def charge(request: Request, limit_value: str, cost: int, *, unit: str = "key") -> None:
    """Spend `cost` of a per-address limit in one go — `ADR-0015` point 3.

    A batch of a hundred keys spends a hundred of the lookup limit, so the limit
    keeps meaning "how fast one address may read the corpus through this
    endpoint" and a batch changes only the number of round trips it takes to
    reach it. `slowapi`'s decorator charges one per request and takes its cost
    before the body is parsed, so a batch handler charges here, after it knows
    how many keys it was sent.

    The window is keyed the way `slowapi` keys every other route's — by client
    address and request *path* (its default `key_style="url"`) — so this is
    the batch route's own window. Measured 2026-09-16: that key style means a
    path-parameter route such as `GET /v1/embeddings/{hash}` has one window per
    distinct hash, and a scan of a library never reaches its limit; the single
    route's figure bounds repeats of one key, not reads of many.

    Raises the same 429 the decorator would, with `Retry-After` in seconds.
    """
    if not limiter.enabled:
        return
    item = parse(limit_value)
    scope = (
        request["path"]
        if limiter._key_style == "url"
        else f"{request.scope['endpoint'].__module__}.{request.scope['endpoint'].__name__}"
    )
    args = [get_remote_address(request), scope]
    if limiter._key_prefix:
        args = [limiter._key_prefix, *args]
    if limiter.limiter.hit(item, *args, cost=cost):
        return
    stats = limiter.limiter.get_window_stats(item, *args)
    retry_after = max(1, int(stats.reset_time - time.time()) + 1)
    raise HTTPException(
        status_code=429,
        detail=f"Rate limit exceeded: {limit_value}, counted per {unit}",
        headers={"Retry-After": str(retry_after)},
    )
