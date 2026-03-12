"""Market stats endpoints."""

from datetime import date, timedelta

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.schemas import (
    PeriodChange,
    RoundTypeBreakdown,
    SectorStatsOut,
    StatsInvestorsResponse,
    StatsOverviewResponse,
    StatsTrendsResponse,
    TopInvestorOut,
    TrendPointOut,
)
from src.models import Investor, Round, RoundInvestor

router = APIRouter(prefix="/stats", tags=["stats"])

PERIOD_DAYS = {"30d": 30, "90d": 90, "1y": 365, "all": None}
VALID_METRICS = {"round_count", "total_capital", "avg_size"}
VALID_GRANULARITIES = {"week", "month", "quarter"}


def _date_filter(period: str) -> date | None:
    """Return the start date for a period, or None for 'all'."""
    days = PERIOD_DAYS.get(period)
    if days is None:
        return None
    return date.today() - timedelta(days=days)


@router.get("/overview", response_model=StatsOverviewResponse)
async def stats_overview(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    period: str = Query(default="90d", pattern="^(30d|90d|1y|all)$"),
):
    ck = cache_key("stats_overview", {"period": period})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    start = _date_filter(period)
    filters = [Round.amount_usd.isnot(None)]
    if start:
        filters.append(Round.date >= start)

    # Main aggregates
    stmt = select(
        func.count(Round.id).label("total_rounds"),
        func.sum(Round.amount_usd).label("total_capital"),
        cast(func.avg(Round.amount_usd), Float).label("avg_round_size"),
        func.percentile_cont(0.5).within_group(Round.amount_usd).label("median_round_size"),
    ).where(*filters)

    row = (await db.execute(stmt)).one()
    total_rounds = row.total_rounds
    total_capital = int(row.total_capital) if row.total_capital else None
    avg_round_size = int(row.avg_round_size) if row.avg_round_size else None
    median_round_size = int(row.median_round_size) if row.median_round_size else None

    # By round type
    type_stmt = (
        select(
            Round.round_type,
            func.count(Round.id).label("count"),
            func.sum(Round.amount_usd).label("total_capital"),
        )
        .where(*filters, Round.round_type.isnot(None))
        .group_by(Round.round_type)
        .order_by(func.count(Round.id).desc())
    )
    type_rows = (await db.execute(type_stmt)).all()
    by_round_type = [
        RoundTypeBreakdown(
            round_type=r.round_type,
            count=r.count,
            total_capital=int(r.total_capital) if r.total_capital else None,
        )
        for r in type_rows
    ]

    # Prior period comparison
    prior_change = None
    if start:
        days = PERIOD_DAYS[period]
        prior_start = start - timedelta(days=days)
        prior_filters = [
            Round.amount_usd.isnot(None),
            Round.date >= prior_start,
            Round.date < start,
        ]
        prior_stmt = select(
            func.count(Round.id).label("total_rounds"),
            func.sum(Round.amount_usd).label("total_capital"),
        ).where(*prior_filters)
        prior_row = (await db.execute(prior_stmt)).one()

        rounds_pct = None
        capital_pct = None
        if prior_row.total_rounds and prior_row.total_rounds > 0:
            rounds_pct = round(
                (total_rounds - prior_row.total_rounds)
                / prior_row.total_rounds * 100, 1,
            )
        if prior_row.total_capital and prior_row.total_capital > 0 and total_capital:
            capital_pct = round(
                (total_capital - int(prior_row.total_capital))
                / int(prior_row.total_capital) * 100, 1,
            )
        prior_change = PeriodChange(total_rounds_pct=rounds_pct, total_capital_pct=capital_pct)

    response = StatsOverviewResponse(
        period=period,
        total_rounds=total_rounds,
        total_capital=total_capital,
        avg_round_size=avg_round_size,
        median_round_size=median_round_size,
        by_round_type=by_round_type,
        prior_period_change=prior_change,
    )
    await set_cached(r, ck, response.model_dump_json())
    return response


@router.get("/sectors", response_model=list[SectorStatsOut])
async def stats_sectors(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    period: str = Query(default="90d", pattern="^(30d|90d|1y|all)$"),
):
    ck = cache_key("stats_sectors", {"period": period})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    start = _date_filter(period)
    filters = [Round.sector.isnot(None)]
    if start:
        filters.append(Round.date >= start)

    stmt = (
        select(
            Round.sector,
            func.count(Round.id).label("round_count"),
            func.sum(Round.amount_usd).label("total_capital"),
            cast(func.avg(Round.amount_usd), Float).label("avg_round_size"),
        )
        .where(*filters)
        .group_by(Round.sector)
        .order_by(func.sum(Round.amount_usd).desc().nullslast())
    )

    rows = (await db.execute(stmt)).all()
    data = [
        SectorStatsOut(
            sector=r.sector,
            round_count=r.round_count,
            total_capital=int(r.total_capital) if r.total_capital else None,
            avg_round_size=int(r.avg_round_size) if r.avg_round_size else None,
        )
        for r in rows
    ]

    from pydantic import TypeAdapter
    json_str = TypeAdapter(list[SectorStatsOut]).dump_json(data).decode()
    await set_cached(r, ck, json_str)
    return data


@router.get("/investors", response_model=StatsInvestorsResponse)
async def stats_investors(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    period: str = Query(default="90d", pattern="^(30d|90d|1y|all)$"),
    limit: int = Query(default=20, ge=1, le=100),
):
    ck = cache_key("stats_investors", {"period": period, "limit": limit})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    start = _date_filter(period)
    date_filters = []
    if start:
        date_filters.append(Round.date >= start)

    # Most active by round count
    active_stmt = (
        select(
            Investor.id,
            Investor.name,
            Investor.slug,
            func.count(RoundInvestor.round_id).label("round_count"),
            func.sum(Round.amount_usd).label("total_deployed"),
        )
        .join(RoundInvestor, RoundInvestor.investor_id == Investor.id)
        .join(Round, Round.id == RoundInvestor.round_id)
        .where(*date_filters)
        .group_by(Investor.id, Investor.name, Investor.slug)
        .order_by(func.count(RoundInvestor.round_id).desc())
        .limit(limit)
    )

    active_rows = (await db.execute(active_stmt)).all()
    most_active = [
        TopInvestorOut(
            id=r.id, name=r.name, slug=r.slug,
            round_count=r.round_count,
            total_deployed=int(r.total_deployed) if r.total_deployed else None,
        )
        for r in active_rows
    ]

    # Biggest deployers by total capital
    deployer_stmt = (
        select(
            Investor.id,
            Investor.name,
            Investor.slug,
            func.count(RoundInvestor.round_id).label("round_count"),
            func.sum(Round.amount_usd).label("total_deployed"),
        )
        .join(RoundInvestor, RoundInvestor.investor_id == Investor.id)
        .join(Round, Round.id == RoundInvestor.round_id)
        .where(*date_filters, Round.amount_usd.isnot(None))
        .group_by(Investor.id, Investor.name, Investor.slug)
        .order_by(func.sum(Round.amount_usd).desc())
        .limit(limit)
    )

    deployer_rows = (await db.execute(deployer_stmt)).all()
    biggest_deployers = [
        TopInvestorOut(
            id=r.id, name=r.name, slug=r.slug,
            round_count=r.round_count,
            total_deployed=int(r.total_deployed) if r.total_deployed else None,
        )
        for r in deployer_rows
    ]

    response = StatsInvestorsResponse(
        period=period,
        most_active=most_active,
        biggest_deployers=biggest_deployers,
    )
    await set_cached(r, ck, response.model_dump_json())
    return response


@router.get("/trends", response_model=StatsTrendsResponse)
async def stats_trends(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    metric: str = Query(default="round_count", pattern="^(round_count|total_capital|avg_size)$"),
    granularity: str = Query(default="month", pattern="^(week|month|quarter)$"),
    sector: str | None = Query(default=None),
    period: str = Query(default="1y", pattern="^(30d|90d|1y|all)$"),
):
    ck = cache_key("stats_trends", {
        "metric": metric, "granularity": granularity,
        "sector": sector, "period": period,
    })
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    start = _date_filter(period)
    filters = []
    if start:
        filters.append(Round.date >= start)
    if sector:
        filters.append(Round.sector == sector)

    period_col = func.date_trunc(granularity, Round.date).label("period")

    if metric == "round_count":
        value_col = func.count(Round.id).label("value")
    elif metric == "total_capital":
        value_col = func.sum(Round.amount_usd).label("value")
    else:  # avg_size
        value_col = cast(func.avg(Round.amount_usd), Float).label("value")

    stmt = (
        select(period_col, value_col)
        .where(*filters)
        .group_by(period_col)
        .order_by(period_col)
    )

    rows = (await db.execute(stmt)).all()
    data = [
        TrendPointOut(
            period=r.period.strftime("%Y-%m-%d") if r.period else "",
            value=float(r.value) if r.value is not None else None,
        )
        for r in rows
    ]

    response = StatsTrendsResponse(
        metric=metric,
        granularity=granularity,
        sector=sector,
        data=data,
    )
    await set_cached(r, ck, response.model_dump_json())
    return response
