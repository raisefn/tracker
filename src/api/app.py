"""FastAPI application."""

import redis.asyncio as redis
from fastapi import Depends, FastAPI
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.routes import investors, projects, rounds
from src.api.schemas import HealthResponse
from src.config import settings
from src.models import CollectorRun, Investor, Project, Round

app = FastAPI(
    title="raisefn tracker",
    description="Crypto fundraising intelligence — open data layer",
    version="0.1.0",
)

app.include_router(rounds.router, prefix=settings.api_prefix, dependencies=[Depends(require_api_key)])
app.include_router(investors.router, prefix=settings.api_prefix, dependencies=[Depends(require_api_key)])
app.include_router(projects.router, prefix=settings.api_prefix, dependencies=[Depends(require_api_key)])


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
):
    ck = cache_key("health", {})

    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    round_count = (await db.execute(select(func.count(Round.id)))).scalar_one()
    investor_count = (await db.execute(select(func.count(Investor.id)))).scalar_one()
    project_count = (await db.execute(select(func.count(Project.id)))).scalar_one()

    last_run = (
        await db.execute(
            select(CollectorRun.completed_at)
            .where(CollectorRun.completed_at.is_not(None))
            .order_by(CollectorRun.completed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    response = HealthResponse(
        status="ok",
        round_count=round_count,
        investor_count=investor_count,
        project_count=project_count,
        last_collection=last_run,
    )
    await set_cached(r, ck, response.model_dump_json(), ttl=60)
    return response
