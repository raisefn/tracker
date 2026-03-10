"""MCP server for raisefn tracker — exposes funding data to AI agents."""

import json
from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from src.config import settings
from src.models import Investor, Project, Round, RoundInvestor

mcp = FastMCP("raisefn-tracker", instructions=(
    "Crypto & startup fundraising intelligence. "
    "Search rounds, investors, and projects. "
    "Data sourced from DefiLlama, CryptoRank, SEC EDGAR, news, and more."
))

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    return _engine


async def _session() -> AsyncSession:
    return AsyncSession(bind=_get_engine(), expire_on_commit=False)


@mcp.tool()
async def search_rounds(
    query: str | None = None,
    sector: str | None = None,
    round_type: str | None = None,
    min_amount: int | None = None,
    max_amount: int | None = None,
    days: int = 90,
    limit: int = 20,
) -> str:
    """Search recent funding rounds. Filter by project name, sector, round type, or amount range."""
    session = await _session()
    try:
        stmt = (
            select(Round)
            .join(Project, Project.id == Round.project_id)
            .options(selectinload(Round.project))
            .order_by(Round.date.desc())
        )

        if query:
            stmt = stmt.where(Project.name.ilike(f"%{query}%"))
        if sector:
            stmt = stmt.where(Round.sector == sector)
        if round_type:
            stmt = stmt.where(Round.round_type == round_type)
        if min_amount:
            stmt = stmt.where(Round.amount_usd >= min_amount)
        if max_amount:
            stmt = stmt.where(Round.amount_usd <= max_amount)
        if days:
            stmt = stmt.where(Round.date >= date.today() - timedelta(days=days))

        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        rounds = result.scalars().all()

        return json.dumps([
            {
                "project": r.project.name,
                "round_type": r.round_type,
                "amount_usd": r.amount_usd,
                "date": str(r.date),
                "sector": r.sector,
                "source_type": r.source_type,
                "confidence": r.confidence,
            }
            for r in rounds
        ], indent=2)
    finally:
        await session.close()


@mcp.tool()
async def get_project(slug: str) -> str:
    """Get detailed info about a project by its slug (e.g. 'uniswap', 'aave')."""
    session = await _session()
    try:
        result = await session.execute(
            select(Project).where(Project.slug == slug)
        )
        project = result.scalar_one_or_none()
        if not project:
            return json.dumps({"error": f"Project '{slug}' not found"})

        # Get rounds for this project
        rounds_result = await session.execute(
            select(Round)
            .where(Round.project_id == project.id)
            .order_by(Round.date.desc())
            .limit(10)
        )
        rounds = rounds_result.scalars().all()

        return json.dumps({
            "name": project.name,
            "slug": project.slug,
            "website": project.website,
            "sector": project.sector,
            "chains": project.chains,
            "description": project.description,
            "github_stars": project.github_stars,
            "twitter_followers": project.twitter_followers,
            "tvl": project.tvl,
            "rounds": [
                {
                    "round_type": r.round_type,
                    "amount_usd": r.amount_usd,
                    "date": str(r.date),
                    "source_type": r.source_type,
                }
                for r in rounds
            ],
        }, indent=2)
    finally:
        await session.close()


@mcp.tool()
async def search_investors(
    query: str | None = None,
    limit: int = 20,
) -> str:
    """Search investors by name. Returns investor profiles with round counts."""
    session = await _session()
    try:
        stmt = select(
            Investor,
            func.count(RoundInvestor.round_id).label("round_count"),
        ).outerjoin(
            RoundInvestor, RoundInvestor.investor_id == Investor.id
        ).group_by(Investor.id).order_by(
            func.count(RoundInvestor.round_id).desc()
        ).limit(limit)

        if query:
            stmt = stmt.where(Investor.name.ilike(f"%{query}%"))

        result = await session.execute(stmt)
        rows = result.all()

        return json.dumps([
            {
                "name": inv.name,
                "slug": inv.slug,
                "type": inv.type,
                "hq_location": inv.hq_location,
                "round_count": count,
            }
            for inv, count in rows
        ], indent=2)
    finally:
        await session.close()


@mcp.tool()
async def get_stats(period: str = "90d") -> str:
    """Get market stats: total rounds, capital deployed, averages. Period: 30d, 90d, 1y, all."""
    session = await _session()
    try:
        days_map = {"30d": 30, "90d": 90, "1y": 365, "all": None}
        days = days_map.get(period)

        filters = [Round.amount_usd.isnot(None)]
        if days:
            filters.append(Round.date >= date.today() - timedelta(days=days))

        result = await session.execute(
            select(
                func.count(Round.id).label("total_rounds"),
                func.sum(Round.amount_usd).label("total_capital"),
                func.avg(Round.amount_usd).label("avg_round_size"),
            ).where(*filters)
        )
        row = result.one()

        return json.dumps({
            "period": period,
            "total_rounds": row.total_rounds,
            "total_capital": row.total_capital,
            "avg_round_size": int(row.avg_round_size) if row.avg_round_size else None,
        }, indent=2)
    finally:
        await session.close()


@mcp.tool()
async def search_projects(
    query: str,
    limit: int = 10,
) -> str:
    """Fuzzy search projects by name."""
    session = await _session()
    try:
        stmt = (
            select(Project.name, Project.slug, Project.sector, Project.website)
            .where(Project.name.ilike(f"%{query}%"))
            .order_by(Project.name)
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()

        return json.dumps([
            {
                "name": r.name,
                "slug": r.slug,
                "sector": r.sector,
                "website": r.website,
            }
            for r in rows
        ], indent=2)
    finally:
        await session.close()
