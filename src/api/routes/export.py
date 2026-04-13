"""Bulk CSV export endpoint."""

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.deps import get_db
from src.models import Round, RoundInvestor

router = APIRouter(prefix="/export", tags=["export"])

MAX_EXPORT_ROWS = 10_000

CSV_COLUMNS = [
    "date",
    "project_name",
    "round_type",
    "amount_usd",
    "valuation_usd",
    "sector",
    "chains",
    "lead_investors",
    "all_investors",
    "source_url",
    "source_type",
    "confidence",
]


@router.get("/rounds")
async def export_rounds(
    db: AsyncSession = Depends(get_db),
    sector: str | None = Query(default=None),
    chain: str | None = Query(default=None),
    round_type: str | None = Query(default=None),
    min_amount: int | None = Query(default=None),
    max_amount: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
):
    stmt = select(Round).options(
        selectinload(Round.project),
        selectinload(Round.investor_participations).selectinload(RoundInvestor.investor),
    )

    filters = []
    if min_confidence > 0:
        filters.append(Round.confidence >= min_confidence)
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

    if filters:
        stmt = stmt.where(*filters)

    stmt = stmt.order_by(Round.date.desc()).limit(MAX_EXPORT_ROWS)

    result = await db.execute(stmt)
    rounds = result.scalars().unique().all()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for rd in rounds:
            leads = [ri.investor.name for ri in rd.investor_participations if ri.is_lead]
            all_inv = [ri.investor.name for ri in rd.investor_participations]
            chains_str = "|".join(rd.chains) if rd.chains else ""

            writer.writerow(
                [
                    rd.date.isoformat() if rd.date else "",
                    rd.project.name if rd.project else "",
                    rd.round_type or "",
                    rd.amount_usd or "",
                    rd.valuation_usd or "",
                    rd.sector or "",
                    chains_str,
                    "|".join(leads),
                    "|".join(all_inv),
                    rd.source_url or "",
                    rd.source_type or "",
                    rd.confidence,
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rounds_export.csv"},
    )
