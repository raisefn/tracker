"""Funding rounds endpoints."""

import uuid
from datetime import date

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.schemas import PaginationMeta, RoundListResponse, RoundOut
from src.config import settings
from src.models import Investor, Round, RoundInvestor

router = APIRouter(prefix="/rounds", tags=["rounds"])


@router.get("", response_model=RoundListResponse)
async def list_rounds(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    limit: int = Query(default=settings.default_page_limit, le=settings.max_page_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    sector: str | None = Query(default=None),
    chain: str | None = Query(default=None),
    round_type: str | None = Query(default=None),
    min_amount: int | None = Query(default=None),
    max_amount: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    min_confidence: float = Query(default=settings.min_confidence, ge=0.0, le=1.0),
    investor_slug: str | None = Query(default=None),
):
    params = {
        "limit": limit, "offset": offset, "sector": sector, "chain": chain,
        "round_type": round_type, "min_amount": min_amount, "max_amount": max_amount,
        "date_from": date_from, "date_to": date_to, "min_confidence": min_confidence,
        "investor_slug": investor_slug,
    }
    ck = cache_key("rounds", params)

    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    stmt = select(Round).options(
        selectinload(Round.project),
        selectinload(Round.investor_participations).selectinload(RoundInvestor.investor),
    )
    count_stmt = select(func.count(Round.id))

    # Filters
    filters = [Round.confidence >= min_confidence]
    if sector:
        filters.append(Round.sector == sector)
    if chain:
        filters.append(Round.chains.any(chain))
    if round_type:
        filters.append(Round.round_type == round_type)
    if min_amount is not None:
        filters.append(Round.amount_usd >= min_amount)
    if max_amount is not None:
        filters.append(Round.amount_usd <= max_amount)
    if date_from:
        filters.append(Round.date >= date_from)
    if date_to:
        filters.append(Round.date <= date_to)
    if investor_slug:
        stmt = stmt.join(Round.investor_participations).join(RoundInvestor.investor)
        count_stmt = count_stmt.join(Round.investor_participations).join(RoundInvestor.investor)
        filters.append(Investor.slug == investor_slug)

    stmt = stmt.where(*filters).order_by(Round.date.desc()).offset(offset).limit(limit)
    count_stmt = count_stmt.where(*filters)

    result = await db.execute(stmt)
    rounds = result.scalars().unique().all()

    total = (await db.execute(count_stmt)).scalar_one()

    data = []
    for rd in rounds:
        investors = [
            {
                "id": ri.investor.id,
                "name": ri.investor.name,
                "slug": ri.investor.slug,
                "is_lead": ri.is_lead,
            }
            for ri in rd.investor_participations
        ]
        data.append(
            RoundOut(
                id=rd.id,
                project=rd.project,
                round_type=rd.round_type,
                amount_usd=rd.amount_usd,
                valuation_usd=rd.valuation_usd,
                date=rd.date,
                chains=rd.chains,
                sector=rd.sector,
                category=rd.category,
                source_url=rd.source_url,
                source_type=rd.source_type,
                confidence=rd.confidence,
                investors=investors,
                created_at=rd.created_at,
            )
        )

    response = RoundListResponse(
        data=data,
        meta=PaginationMeta(
            total=total, limit=limit, offset=offset,
            has_more=offset + limit < total,
        ),
    )
    await set_cached(r, ck, response.model_dump_json())
    return response


@router.get("/{round_id}", response_model=RoundOut)
async def get_round(
    round_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Round)
        .options(
            selectinload(Round.project),
            selectinload(Round.investor_participations).selectinload(RoundInvestor.investor),
        )
        .where(Round.id == round_id)
    )
    result = await db.execute(stmt)
    rd = result.scalar_one_or_none()
    if rd is None:
        raise HTTPException(status_code=404, detail="Round not found")

    investors = [
        {
            "id": ri.investor.id,
            "name": ri.investor.name,
            "slug": ri.investor.slug,
            "is_lead": ri.is_lead,
        }
        for ri in rd.investor_participations
    ]
    return RoundOut(
        id=rd.id,
        project=rd.project,
        round_type=rd.round_type,
        amount_usd=rd.amount_usd,
        valuation_usd=rd.valuation_usd,
        date=rd.date,
        chains=rd.chains,
        sector=rd.sector,
        category=rd.category,
        source_url=rd.source_url,
        source_type=rd.source_type,
        confidence=rd.confidence,
        investors=investors,
        created_at=rd.created_at,
    )
