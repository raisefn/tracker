"""Market stats endpoints."""

from datetime import date, timedelta

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.schemas import (
    InvestorVelocityOut,
    PeriodChange,
    ProjectSignalOut,
    RoundTypeBreakdown,
    SectorMomentumOut,
    SectorStatsOut,
    StatsInvestorsResponse,
    StatsOverviewResponse,
    StatsTrendsResponse,
    TopInvestorOut,
    TrendPointOut,
)
from src.models import Investor, Project, Round, RoundInvestor

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


@router.get("/momentum", response_model=list[SectorMomentumOut])
async def stats_momentum(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    days: int = Query(default=30, ge=7, le=365),
):
    """Sector momentum: compare current vs prior period round counts and capital."""
    ck = cache_key("stats_momentum", {"days": days})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    today = date.today()
    current_start = today - timedelta(days=days)
    prior_start = current_start - timedelta(days=days)

    # Current period
    current_stmt = (
        select(
            Round.sector,
            func.count(Round.id).label("count"),
            func.sum(Round.amount_usd).label("capital"),
        )
        .where(Round.date >= current_start, Round.sector.isnot(None))
        .group_by(Round.sector)
    )
    current_rows = {r.sector: r for r in (await db.execute(current_stmt)).all()}

    # Prior period
    prior_stmt = (
        select(
            Round.sector,
            func.count(Round.id).label("count"),
            func.sum(Round.amount_usd).label("capital"),
        )
        .where(Round.date >= prior_start, Round.date < current_start, Round.sector.isnot(None))
        .group_by(Round.sector)
    )
    prior_rows = {r.sector: r for r in (await db.execute(prior_stmt)).all()}

    all_sectors = set(current_rows.keys()) | set(prior_rows.keys())
    data = []
    for sector in all_sectors:
        cur = current_rows.get(sector)
        pri = prior_rows.get(sector)
        cur_count = cur.count if cur else 0
        pri_count = pri.count if pri else 0
        cur_cap = int(cur.capital) if cur and cur.capital else None
        pri_cap = int(pri.capital) if pri and pri.capital else None

        change_pct = None
        if pri_count > 0:
            change_pct = round((cur_count - pri_count) / pri_count * 100, 1)

        cap_change_pct = None
        if pri_cap and pri_cap > 0 and cur_cap:
            cap_change_pct = round((cur_cap - pri_cap) / pri_cap * 100, 1)

        data.append(SectorMomentumOut(
            sector=sector,
            current_count=cur_count,
            prior_count=pri_count,
            change_pct=change_pct,
            current_capital=cur_cap,
            prior_capital=pri_cap,
            capital_change_pct=cap_change_pct,
        ))

    # Sort by change_pct descending (hottest sectors first)
    data.sort(key=lambda x: x.change_pct or -999, reverse=True)

    from pydantic import TypeAdapter
    json_str = TypeAdapter(list[SectorMomentumOut]).dump_json(data).decode()
    await set_cached(r, ck, json_str)
    return data


@router.get("/signals", response_model=list[ProjectSignalOut])
async def stats_signals(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    min_days: int = Query(default=270, ge=90, le=730),
    max_days: int = Query(default=730, ge=180, le=1825),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Projects likely to raise soon: raised 9-24 months ago, sorted by activity signals."""
    ck = cache_key("stats_signals", {"min_days": min_days, "max_days": max_days, "limit": limit})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    today = date.today()

    # Subquery: latest round per project with aggregate stats
    latest_round = (
        select(
            Round.project_id,
            func.max(Round.date).label("last_raise_date"),
            func.count(Round.id).label("round_count"),
            func.sum(Round.amount_usd).label("total_raised"),
        )
        .group_by(Round.project_id)
        .subquery()
    )

    # Subquery: details of the most recent round
    last_round_detail = (
        select(
            Round.project_id,
            Round.round_type,
            Round.amount_usd,
        )
        .distinct(Round.project_id)
        .order_by(Round.project_id, Round.date.desc())
        .subquery()
    )

    stmt = (
        select(
            Project.id,
            Project.name,
            Project.slug,
            Project.sector,
            Project.github_stars,
            Project.github_commits_30d,
            latest_round.c.last_raise_date,
            latest_round.c.round_count,
            latest_round.c.total_raised,
            last_round_detail.c.round_type.label("last_round_type"),
            last_round_detail.c.amount_usd.label("last_round_amount"),
        )
        .join(latest_round, Project.id == latest_round.c.project_id)
        .join(last_round_detail, Project.id == last_round_detail.c.project_id)
        .where(
            Project.status == "active",
            latest_round.c.last_raise_date <= today - timedelta(days=min_days),
            latest_round.c.last_raise_date >= today - timedelta(days=max_days),
        )
        # Prioritize: projects with GitHub activity, multiple rounds, and recent enrichment
        .order_by(
            func.coalesce(Project.github_commits_30d, 0).desc(),
            latest_round.c.round_count.desc(),
        )
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    data = [
        ProjectSignalOut(
            id=r.id,
            name=r.name,
            slug=r.slug,
            sector=r.sector,
            days_since_last_raise=(today - r.last_raise_date).days,
            last_round_type=r.last_round_type,
            last_round_amount=int(r.last_round_amount) if r.last_round_amount else None,
            total_raised=int(r.total_raised) if r.total_raised else None,
            round_count=r.round_count,
            github_stars=r.github_stars,
            github_commits_30d=r.github_commits_30d,
        )
        for r in rows
    ]

    from pydantic import TypeAdapter
    json_str = TypeAdapter(list[ProjectSignalOut]).dump_json(data).decode()
    await set_cached(r, ck, json_str)
    return data


@router.get("/velocity", response_model=list[InvestorVelocityOut])
async def stats_velocity(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    limit: int = Query(default=30, ge=1, le=100),
):
    """Investor deal velocity: deals per period and average time between deals."""
    ck = cache_key("stats_velocity", {"limit": limit})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    today = date.today()

    stmt = (
        select(
            Investor.id,
            Investor.name,
            Investor.slug,
            func.count(RoundInvestor.round_id).filter(
                Round.date >= today - timedelta(days=30)
            ).label("deals_30d"),
            func.count(RoundInvestor.round_id).filter(
                Round.date >= today - timedelta(days=90)
            ).label("deals_90d"),
            func.count(RoundInvestor.round_id).filter(
                Round.date >= today - timedelta(days=365)
            ).label("deals_365d"),
            func.count(RoundInvestor.round_id).label("total_deals"),
            # Average days between deals: (last deal - first deal) / (count - 1)
            case(
                (
                    func.count(RoundInvestor.round_id) > 1,
                    cast(
                        func.extract("epoch", func.max(Round.date) - func.min(Round.date)) / 86400
                        / (func.count(RoundInvestor.round_id) - 1),
                        Float,
                    ),
                ),
                else_=None,
            ).label("avg_days_between"),
        )
        .join(RoundInvestor, RoundInvestor.investor_id == Investor.id)
        .join(Round, Round.id == RoundInvestor.round_id)
        .group_by(Investor.id, Investor.name, Investor.slug)
        .having(func.count(RoundInvestor.round_id) >= 3)  # Only investors with 3+ deals
        .order_by(
            func.count(RoundInvestor.round_id).filter(
                Round.date >= today - timedelta(days=90)
            ).desc()
        )
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    data = [
        InvestorVelocityOut(
            id=r.id,
            name=r.name,
            slug=r.slug,
            deals_30d=r.deals_30d,
            deals_90d=r.deals_90d,
            deals_365d=r.deals_365d,
            total_deals=r.total_deals,
            avg_days_between_deals=round(r.avg_days_between, 1) if r.avg_days_between else None,
        )
        for r in rows
    ]

    from pydantic import TypeAdapter
    json_str = TypeAdapter(list[InvestorVelocityOut]).dump_json(data).decode()
    await set_cached(r, ck, json_str)
    return data
