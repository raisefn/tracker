"""Project endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.api.schemas import PaginationMeta, ProjectDetail, ProjectListResponse
from src.config import settings
from src.models import Project

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=settings.default_page_limit, le=settings.max_page_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    sector: str | None = Query(default=None),
    chain: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None, min_length=2),
):
    stmt = select(Project)
    count_stmt = select(func.count(Project.id))

    filters = []
    if sector:
        filters.append(Project.sector == sector)
    if chain:
        filters.append(Project.chains.any(chain))
    if status:
        filters.append(Project.status == status)
    if search:
        filters.append(Project.name.ilike(f"%{search}%"))

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    stmt = stmt.order_by(Project.name).offset(offset).limit(limit)

    result = await db.execute(stmt)
    projects = result.scalars().all()

    total = (await db.execute(count_stmt)).scalar_one()

    return ProjectListResponse(
        data=[ProjectDetail.model_validate(proj) for proj in projects],
        meta=PaginationMeta(total=total, limit=limit, offset=offset, has_more=offset + limit < total),
    )


@router.get("/{slug}", response_model=ProjectDetail)
async def get_project(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Project).where(Project.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectDetail.model_validate(project)
