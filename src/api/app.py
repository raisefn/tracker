"""FastAPI application."""

import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.auth import require_api_key
from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.routes import comps, export, investors, projects, rounds, search, stats, webhooks
from src.api.schemas import HealthResponse
from src.config import settings
from src.models import CollectorRun, Investor, Project, Round
from src.pipeline.log_sanitizer import sanitize
from src.scheduler import scheduler_loop


class _SanitizingFilter(logging.Filter):
    """Strip sensitive data (tokens, passwords) from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize(record.msg)
        return True


# Apply to root logger so all modules benefit
logging.getLogger().addFilter(_SanitizingFilter())

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background scheduler on startup, cancel on shutdown."""
    scheduler_task = asyncio.create_task(scheduler_loop())
    logger.info("Background scheduler started")
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    logger.info("Background scheduler stopped")


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect HTTP to HTTPS in production (behind reverse proxy)."""

    async def dispatch(self, request: Request, call_next):
        proto = request.headers.get("x-forwarded-proto", "https")
        if proto == "http" and not settings.debug:
            url = request.url.replace(scheme="https")
            return RedirectResponse(url=str(url), status_code=301)
        return await call_next(request)


app = FastAPI(
    title="raisefn tracker",
    description="Startup fundraising intelligence — open data layer",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(HTTPSRedirectMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET"],
    allow_headers=["X-API-Key"],
)

_api_deps = [Depends(require_api_key)]
app.include_router(rounds.router, prefix=settings.api_prefix, dependencies=_api_deps)
app.include_router(investors.router, prefix=settings.api_prefix, dependencies=_api_deps)
app.include_router(projects.router, prefix=settings.api_prefix, dependencies=_api_deps)
app.include_router(stats.router, prefix=settings.api_prefix, dependencies=_api_deps)
app.include_router(search.router, prefix=settings.api_prefix, dependencies=_api_deps)
app.include_router(comps.router, prefix=settings.api_prefix, dependencies=_api_deps)
app.include_router(export.router, prefix=settings.api_prefix, dependencies=_api_deps)
app.include_router(webhooks.router, prefix=settings.api_prefix, dependencies=_api_deps)


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
