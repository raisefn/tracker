"""Redis connection pool."""

import redis.asyncio as redis

from src.config import settings

redis_pool = redis.ConnectionPool.from_url(settings.redis_url, decode_responses=True)


def get_redis_client() -> redis.Redis:
    return redis.Redis(connection_pool=redis_pool)
