"""Test fixtures for the tracker test suite."""

from collections.abc import AsyncGenerator

import fakeredis.aioredis as fakeredis
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.api.app import app
from src.api.auth import require_api_key
from src.api.deps import get_db, get_redis
from src.config import settings
from src.models.base import Base

TEST_DB_URL = settings.database_url.rsplit("/tracker", 1)[0] + "/tracker_test"

_tables_ready = False


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    global _tables_ready
    engine = create_async_engine(TEST_DB_URL, echo=False, pool_size=1, max_overflow=0)

    if not _tables_ready:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        _tables_ready = True

    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        yield session
        await session.close()
        await trans.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, fake_redis) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield fake_redis

    async def override_require_api_key():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[require_api_key] = override_require_api_key

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
