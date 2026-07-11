"""Async Redis client factory.

Used for: session state, per-session locks, dedup, quarantine, bypass flags,
Celery broker/backend (SPEC §11, §7, §8). Tests inject fakeredis (no real Redis).
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings

_client: Any = None


def get_redis() -> Any:
    """Return a cached redis.asyncio.Redis client. Tests inject a fakeredis client."""
    global _client
    if _client is None:
        import redis.asyncio as redis  # local import

        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def new_redis() -> Any:
    """A FRESH, uncached async Redis client. For code that runs its own event loop per call —
    Celery tasks via `asyncio.run`. The cached `get_redis()` binds its connection pool to the
    first loop; a later `asyncio.run` (new loop) then fails with 'Event loop is closed'. Callers
    own the lifecycle: `await client.aclose()` when done.
    """
    import redis.asyncio as redis

    return redis.from_url(settings.redis_url, decode_responses=True)


def set_redis_for_test(client: Any) -> None:
    """Inject a fakeredis (or mock) client — tests only."""
    global _client
    _client = client
