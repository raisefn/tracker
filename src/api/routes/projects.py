"""Project endpoints."""

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import and_, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.schemas import (
    MetricSnapshotOut,
    PaginationMeta,
    ProjectDetail,
    ProjectListResponse,
    ProjectMetricsHistoryResponse,
)
from src.config import settings
from src.models import Project, ProjectMetricSnapshot

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    limit: int = Query(default=settings.default_page_limit, le=settings.max_page_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    sector: str | None = Query(default=None),
    chain: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None, min_length=2),
    sort: str | None = Query(
        default=None,
        description="Sort by: tvl, market_cap, github_stars, last_enriched_at, name",
    ),
):
    params = {
        "limit": limit, "offset": offset, "sector": sector,
        "chain": chain, "status": status, "search": search, "sort": sort,
    }
    ck = cache_key("projects", params)

    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    stmt = select(Project).options(selectinload(Project.founders))
    count_stmt = select(func.count(Project.id))

    # Exclude junk project names (SEC EDGAR artifacts) unless explicitly searching
    junk_filter = and_(
        not_(Project.name.regexp_match(r"^\$")),
        not_(Project.name.regexp_match(r"^\d{5,}\)?$")),
        not_(Project.name.regexp_match(r"^[0-9\s\-\(\)]+$")),
        not_(Project.name.regexp_match(r"^N/?A\b")),
    )

    filters = [junk_filter]
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

    sort_col = {
        "tvl": Project.tvl.desc().nulls_last(),
        "market_cap": Project.market_cap.desc().nulls_last(),
        "github_stars": Project.github_stars.desc().nulls_last(),
        "last_enriched_at": Project.last_enriched_at.desc().nulls_last(),
        "name": Project.name,
    }.get(sort, Project.name)
    stmt = stmt.order_by(sort_col).offset(offset).limit(limit)

    result = await db.execute(stmt)
    projects = result.scalars().all()

    total = (await db.execute(count_stmt)).scalar_one()

    response = ProjectListResponse(
        data=[ProjectDetail.model_validate(proj) for proj in projects],
        meta=PaginationMeta(
            total=total, limit=limit, offset=offset,
            has_more=offset + limit < total,
        ),
    )
    await set_cached(r, ck, response.model_dump_json())
    return response


@router.get("/{slug}", response_model=ProjectDetail)
async def get_project(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.founders))
        .where(Project.slug == slug)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectDetail.model_validate(project)


@router.get("/{slug}/metrics/history", response_model=ProjectMetricsHistoryResponse)
async def get_project_metrics_history(
    slug: str,
    db: AsyncSession = Depends(get_db),
    source: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get historical metric snapshots for a project."""
    project = (
        await db.execute(select(Project).where(Project.slug == slug))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(ProjectMetricSnapshot)
        .where(
            ProjectMetricSnapshot.project_id == project.id,
            ProjectMetricSnapshot.snapshotted_at >= cutoff,
        )
        .order_by(ProjectMetricSnapshot.snapshotted_at.desc())
    )
    if source:
        stmt = stmt.where(ProjectMetricSnapshot.source == source)

    result = await db.execute(stmt)
    snapshots = result.scalars().all()

    return ProjectMetricsHistoryResponse(
        project_slug=slug,
        snapshots=[MetricSnapshotOut.model_validate(s) for s in snapshots],
    )
