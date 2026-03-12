"""Comparable companies engine."""

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.schemas import CompOut, CompRoundBrief, CompsResponse, ProjectBrief
from src.models import Project, Round

router = APIRouter(prefix="/projects", tags=["comps"])


def _score_comp(
    target: Project,
    target_round: Round | None,
    candidate: Project,
    candidate_round: Round | None,
) -> tuple[int, list[str]]:
    """Score a candidate project against the target. Returns (score, reasons)."""
    score = 0
    reasons: list[str] = []

    # Same sector
    if target.sector and candidate.sector and target.sector == candidate.sector:
        score += 3
        reasons.append(f"Same sector: {target.sector}")

    # Chain overlap
    if target.chains and candidate.chains:
        shared = set(target.chains) & set(candidate.chains)
        if shared:
            score += 2 * len(shared)
            reasons.append(f"Shared chains: {', '.join(sorted(shared))}")

    # Similar round stage
    if target_round and candidate_round and target_round.round_type and candidate_round.round_type:
        if target_round.round_type == candidate_round.round_type:
            score += 3
            reasons.append(f"Same stage: {target_round.round_type}")

    # Similar funding amount (within 3x)
    if target_round and candidate_round and target_round.amount_usd and candidate_round.amount_usd:
        ratio = target_round.amount_usd / candidate_round.amount_usd
        if 0.33 <= ratio <= 3.0:
            score += 2
            reasons.append("Similar funding amount")

    # Similar team size
    if target.team_size and candidate.team_size:
        ratio = target.team_size / candidate.team_size
        if 0.5 <= ratio <= 2.0:
            score += 1
            reasons.append("Similar team size")

    return score, reasons


@router.get("/{slug}/comps", response_model=CompsResponse)
async def get_comps(
    slug: str,
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    limit: int = Query(default=10, ge=1, le=50),
):
    ck = cache_key("comps", {"slug": slug, "limit": limit})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    # Load target project
    target = (
        await db.execute(select(Project).where(Project.slug == slug))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get target's latest round
    target_round = (
        await db.execute(
            select(Round)
            .where(Round.project_id == target.id)
            .order_by(Round.date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # Find candidates: same sector or overlapping chains, excluding self
    candidate_filters = [Project.id != target.id]
    if target.sector:
        candidate_filters.append(Project.sector == target.sector)
    else:
        # Without sector, just get all projects (will be scored low)
        pass

    candidates_stmt = select(Project).where(*candidate_filters).limit(200)
    candidates = (await db.execute(candidates_stmt)).scalars().all()

    # Get latest round for each candidate (batch)
    candidate_ids = [c.id for c in candidates]
    if candidate_ids:
        # Subquery for latest round per project
        latest_round_sub = (
            select(
                Round.project_id,
                func.max(Round.date).label("max_date"),
            )
            .where(Round.project_id.in_(candidate_ids))
            .group_by(Round.project_id)
            .subquery()
        )
        rounds_stmt = (
            select(Round)
            .join(
                latest_round_sub,
                (Round.project_id == latest_round_sub.c.project_id)
                & (Round.date == latest_round_sub.c.max_date),
            )
        )
        rounds_result = (await db.execute(rounds_stmt)).scalars().all()
        candidate_rounds = {r.project_id: r for r in rounds_result}
    else:
        candidate_rounds = {}

    # Score each candidate
    scored: list[tuple[Project, int, list[str], Round | None]] = []
    for cand in candidates:
        cand_round = candidate_rounds.get(cand.id)
        score, reasons = _score_comp(target, target_round, cand, cand_round)
        if score > 0:
            scored.append((cand, score, reasons, cand_round))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[1], reverse=True)
    scored = scored[:limit]

    comps = [
        CompOut(
            project=ProjectBrief.model_validate(cand),
            score=score,
            match_reasons=reasons,
            latest_round=CompRoundBrief(
                round_type=cand_round.round_type,
                amount_usd=cand_round.amount_usd,
                date=cand_round.date,
            ) if cand_round else None,
        )
        for cand, score, reasons, cand_round in scored
    ]

    response = CompsResponse(
        target=ProjectBrief.model_validate(target),
        comps=comps,
    )
    await set_cached(r, ck, response.model_dump_json(), ttl=900)
    return response
