"""Investor endpoints."""

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.schemas import (
    CoInvestorOut, InvestorDetail, InvestorListResponse,
    InvestorSectorOut, PaginationMeta,
)
from src.config import settings
from src.models import Investor, Round, RoundInvestor

router = APIRouter(prefix="/investors", tags=["investors"])

SORT_FIELDS = {"name", "rounds_count"}


@router.get("", response_model=InvestorListResponse)
async def list_investors(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    limit: int = Query(default=settings.default_page_limit, le=settings.max_page_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    type: str | None = Query(default=None),
    search: str | None = Query(default=None, min_length=2),
    sort: str = Query(default="rounds_count"),
):
    params = {"limit": limit, "offset": offset, "type": type, "search": search, "sort": sort}
    ck = cache_key("investors", params)

    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    # Subquery: count rounds per investor
    rounds_sub = (
        select(
            RoundInvestor.investor_id,
            func.count().label("rounds_count"),
        )
        .group_by(RoundInvestor.investor_id)
        .subquery()
    )

    stmt = (
        select(Investor, func.coalesce(rounds_sub.c.rounds_count, 0).label("rounds_count"))
        .outerjoin(rounds_sub, Investor.id == rounds_sub.c.investor_id)
        .options(selectinload(Investor.funds))
    )
    count_stmt = select(func.count(Investor.id))

    filters = []
    if type:
        filters.append(Investor.type == type)
    if search:
        filters.append(Investor.name.ilike(f"%{search}%"))

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    # Sorting
    if sort == "rounds_count":
        stmt = stmt.order_by(func.coalesce(rounds_sub.c.rounds_count, 0).desc(), Investor.name)
    else:
        stmt = stmt.order_by(Investor.name)

    stmt = stmt.offset(offset).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()

    total = (await db.execute(count_stmt)).scalar_one()

    data = []
    for row in rows:
        investor = row[0]
        rounds_count = row[1]
        inv_dict = InvestorDetail.model_validate(investor).model_dump()
        inv_dict["rounds_count"] = rounds_count
        data.append(InvestorDetail(**inv_dict))

    response = InvestorListResponse(
        data=data,
        meta=PaginationMeta(total=total, limit=limit, offset=offset, has_more=offset + limit < total),
    )
    await set_cached(r, ck, response.model_dump_json())
    return response


@router.get("/{slug}", response_model=InvestorDetail)
async def get_investor(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    rounds_sub = (
        select(
            RoundInvestor.investor_id,
            func.count().label("rounds_count"),
        )
        .group_by(RoundInvestor.investor_id)
        .subquery()
    )

    result = await db.execute(
        select(Investor, func.coalesce(rounds_sub.c.rounds_count, 0).label("rounds_count"))
        .outerjoin(rounds_sub, Investor.id == rounds_sub.c.investor_id)
        .options(selectinload(Investor.funds))
        .where(Investor.slug == slug)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Investor not found")

    inv_dict = InvestorDetail.model_validate(row[0]).model_dump()
    inv_dict["rounds_count"] = row[1]
    return InvestorDetail(**inv_dict)


@router.get("/{slug}/co-investors", response_model=list[CoInvestorOut])
async def get_co_investors(
    slug: str,
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    min_rounds: int = Query(default=2, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Find investors who frequently co-invest with this investor."""
    ck = cache_key("co_investors", {"slug": slug, "min_rounds": min_rounds, "limit": limit})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    investor = (
        await db.execute(select(Investor).where(Investor.slug == slug))
    ).scalar_one_or_none()
    if investor is None:
        raise HTTPException(status_code=404, detail="Investor not found")

    ri1 = aliased(RoundInvestor)
    ri2 = aliased(RoundInvestor)

    stmt = (
        select(
            Investor.id,
            Investor.name,
            Investor.slug,
            func.count(distinct(ri1.round_id)).label("shared_rounds"),
        )
        .select_from(ri1)
        .join(ri2, (ri2.round_id == ri1.round_id) & (ri2.investor_id != investor.id))
        .join(Investor, Investor.id == ri2.investor_id)
        .where(ri1.investor_id == investor.id)
        .group_by(Investor.id, Investor.name, Investor.slug)
        .having(func.count(distinct(ri1.round_id)) >= min_rounds)
        .order_by(func.count(distinct(ri1.round_id)).desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    data = [
        CoInvestorOut(id=row[0], name=row[1], slug=row[2], shared_rounds=row[3])
        for row in rows
    ]

    from pydantic import TypeAdapter
    json_str = TypeAdapter(list[CoInvestorOut]).dump_json(data).decode()
    await set_cached(r, ck, json_str)
    return data


@router.get("/{slug}/sectors", response_model=list[InvestorSectorOut])
async def get_investor_sectors(
    slug: str,
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
):
    """Get sector breakdown for an investor's portfolio."""
    ck = cache_key("investor_sectors", {"slug": slug})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    investor = (
        await db.execute(select(Investor).where(Investor.slug == slug))
    ).scalar_one_or_none()
    if investor is None:
        raise HTTPException(status_code=404, detail="Investor not found")

    stmt = (
        select(
            Round.sector,
            func.count(Round.id).label("round_count"),
            func.sum(Round.amount_usd).label("total_invested"),
        )
        .join(RoundInvestor, RoundInvestor.round_id == Round.id)
        .where(RoundInvestor.investor_id == investor.id)
        .where(Round.sector.isnot(None))
        .group_by(Round.sector)
        .order_by(func.count(Round.id).desc())
    )

    rows = (await db.execute(stmt)).all()
    data = [
        InvestorSectorOut(sector=row[0], round_count=row[1], total_invested=row[2])
        for row in rows
    ]

    from pydantic import TypeAdapter
    json_str = TypeAdapter(list[InvestorSectorOut]).dump_json(data).decode()
    await set_cached(r, ck, json_str)
    return data
