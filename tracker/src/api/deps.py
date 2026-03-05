from collections.abc import AsyncGenerator

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.redis import get_redis_client
from src.db.session import async_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def get_redis() -> AsyncGenerator[redis.Redis, None]:
    client = get_redis_client()
    try:
        yield client
    finally:
        await client.aclose()
