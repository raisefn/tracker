"""Unified search endpoint using PostgreSQL trigram similarity."""

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.schemas import SearchResponse, SearchResultOut
from src.models import Investor, Project

router = APIRouter(prefix="/search", tags=["search"])

SIMILARITY_THRESHOLD = 0.15


@router.get("", response_model=SearchResponse)
async def search(
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    q: str = Query(min_length=2, max_length=200),
    type: str = Query(default="all", pattern="^(all|projects|investors)$"),
    limit: int = Query(default=20, ge=1, le=100),
):
    ck = cache_key("search", {"q": q, "type": type, "limit": limit})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    results: list[SearchResultOut] = []

    # Check if pg_trgm is available using a savepoint so failure doesn't kill the transaction
    use_trgm = False
    try:
        async with db.begin_nested():
            await db.execute(text("SELECT similarity('test', 'test')"))
        use_trgm = True
    except Exception:
        pass

    if type in ("all", "projects"):
        if use_trgm:
            score = func.similarity(Project.name, q).label("score")
            stmt = (
                select(Project.id, Project.name, Project.slug, Project.sector, score)
                .where(func.similarity(Project.name, q) > SIMILARITY_THRESHOLD)
                .order_by(score.desc())
                .limit(limit)
            )
        else:
            stmt = (
                select(
                    Project.id, Project.name, Project.slug,
                    Project.sector, literal(1.0).label("score"),
                )
                .where(Project.name.ilike(f"%{q}%"))
                .order_by(Project.name)
                .limit(limit)
            )

        rows = (await db.execute(stmt)).all()
        for row in rows:
            results.append(SearchResultOut(
                entity_type="project",
                id=row.id,
                name=row.name,
                slug=row.slug,
                score=round(float(row.score), 3),
                extra={"sector": row.sector} if row.sector else {},
            ))

    if type in ("all", "investors"):
        if use_trgm:
            score = func.similarity(Investor.name, q).label("score")
            stmt = (
                select(Investor.id, Investor.name, Investor.slug, Investor.type, score)
                .where(func.similarity(Investor.name, q) > SIMILARITY_THRESHOLD)
                .order_by(score.desc())
                .limit(limit)
            )
        else:
            stmt = (
                select(
                    Investor.id, Investor.name, Investor.slug,
                    Investor.type, literal(1.0).label("score"),
                )
                .where(Investor.name.ilike(f"%{q}%"))
                .order_by(Investor.name)
                .limit(limit)
            )

        rows = (await db.execute(stmt)).all()
        for row in rows:
            results.append(SearchResultOut(
                entity_type="investor",
                id=row.id,
                name=row.name,
                slug=row.slug,
                score=round(float(row.score), 3),
                extra={"type": row.type} if row.type else {},
            ))

    # Sort combined results by score descending
    results.sort(key=lambda x: x.score, reverse=True)
    results = results[:limit]

    response = SearchResponse(results=results, total=len(results))
    await set_cached(r, ck, response.model_dump_json())
    return response
