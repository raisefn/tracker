"""Investor endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.api.schemas import InvestorDetail, InvestorListResponse, PaginationMeta
from src.config import settings
from src.models import Investor, RoundInvestor

router = APIRouter(prefix="/investors", tags=["investors"])


@router.get("", response_model=InvestorListResponse)
async def list_investors(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=settings.default_page_limit, le=settings.max_page_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    type: str | None = Query(default=None),
    search: str | None = Query(default=None, min_length=2),
):
    stmt = select(Investor)
    count_stmt = select(func.count(Investor.id))

    filters = []
    if type:
        filters.append(Investor.type == type)
    if search:
        filters.append(Investor.name.ilike(f"%{search}%"))

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    stmt = stmt.order_by(Investor.name).offset(offset).limit(limit)

    result = await db.execute(stmt)
    investors = result.scalars().all()

    total = (await db.execute(count_stmt)).scalar_one()

    return InvestorListResponse(
        data=[InvestorDetail.model_validate(inv) for inv in investors],
        meta=PaginationMeta(total=total, limit=limit, offset=offset, has_more=offset + limit < total),
    )


@router.get("/{slug}", response_model=InvestorDetail)
async def get_investor(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Investor).where(Investor.slug == slug))
    investor = result.scalar_one_or_none()
    if investor is None:
        raise HTTPException(status_code=404, detail="Investor not found")
    return InvestorDetail.model_validate(investor)
