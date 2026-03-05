"""Response caching helpers using Redis."""

import hashlib
import json
from typing import Any

import redis.asyncio as redis

DEFAULT_TTL = 300  # 5 minutes


def cache_key(prefix: str, params: dict[str, Any]) -> str:
    """Deterministic cache key from endpoint prefix and sorted query params."""
    filtered = {k: v for k, v in sorted(params.items()) if v is not None}
    param_hash = hashlib.md5(json.dumps(filtered, default=str).encode()).hexdigest()
    return f"raisefn:{prefix}:{param_hash}"


async def get_cached(r: redis.Redis, key: str) -> str | None:
    return await r.get(key)


async def set_cached(r: redis.Redis, key: str, value: str, ttl: int = DEFAULT_TTL) -> None:
    await r.set(key, value, ex=ttl)


async def invalidate_all(r: redis.Redis) -> None:
    """Invalidate all raisefn cache keys using SCAN (non-blocking)."""
    async for key in r.scan_iter(match="raisefn:*", count=100):
        await r.delete(key)
