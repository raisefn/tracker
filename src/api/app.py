"""FastAPI application."""

from fastapi import FastAPI
from sqlalchemy import func, select

from src.api.deps import get_db
from src.api.routes import investors, projects, rounds
from src.api.schemas import HealthResponse
from src.config import settings
from src.models import CollectorRun, Investor, Project, Round

app = FastAPI(
    title="raisefn tracker",
    description="Crypto fundraising intelligence — open data layer",
    version="0.1.0",
)

app.include_router(rounds.router, prefix=settings.api_prefix)
app.include_router(investors.router, prefix=settings.api_prefix)
app.include_router(projects.router, prefix=settings.api_prefix)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    async for db in get_db():
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

        return HealthResponse(
            status="ok",
            round_count=round_count,
            investor_count=investor_count,
            project_count=project_count,
            last_collection=last_run,
        )
