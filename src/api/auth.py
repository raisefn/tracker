"""API key authentication and rate limiting."""

import hashlib
import time
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader, APIKeyQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db, get_redis
from src.models.api_key import ApiKey

RATE_LIMITS = {
    "free": 100,
    "basic": 1000,
    "pro": 10000,
}

WINDOW_SECONDS = 3600  # 1 hour

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def require_api_key(
    header_key: str | None = Security(api_key_header),
    query_key: str | None = Security(api_key_query),
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> ApiKey:
    raw_key = header_key or query_key
    if not raw_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_hash = hash_key(raw_key)
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="API key has expired")

    # Sliding window rate limit via Redis sorted set
    rate_key = f"raisefn:ratelimit:{api_key.id}"
    now = time.time()
    window_start = now - WINDOW_SECONDS

    pipe = r.pipeline()
    pipe.zremrangebyscore(rate_key, 0, window_start)
    pipe.zcard(rate_key)
    pipe.zadd(rate_key, {str(now): now})
    pipe.expire(rate_key, WINDOW_SECONDS)
    results = await pipe.execute()

    request_count = results[1]
    limit = RATE_LIMITS.get(api_key.tier, 100)

    if request_count >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({limit} requests/hour for {api_key.tier} tier)",
            headers={"Retry-After": "60"},
        )

    return api_key
