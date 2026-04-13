"""Investor endpoints."""

import collections
from datetime import date

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import TypeAdapter
from sqlalchemy import case, distinct, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from src.api.cache import cache_key, get_cached, set_cached
from src.api.deps import get_db, get_redis
from src.api.schemas import (
    CoInvestorOut,
    InvestorBrief,
    InvestorDetail,
    InvestorListResponse,
    InvestorNetworkOut,
    InvestorSectorOut,
    PaginationMeta,
    ProjectBrief,
    RoundInvestorOut,
    RoundListResponse,
    RoundOut,
    SyndicateMemberOut,
    SyndicateOut,
    SyndicateResponse,
)
from src.config import settings
from src.models import Investor, Project, Round, RoundInvestor

router = APIRouter(prefix="/investors", tags=["investors"])

SORT_FIELDS = {"name", "rounds_count", "last_active"}


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

    # Subquery: count rounds and most recent round date per investor
    rounds_sub = (
        select(
            RoundInvestor.investor_id,
            func.count().label("rounds_count"),
            func.max(Round.date).label("last_active"),
        )
        .join(Round, Round.id == RoundInvestor.round_id)
        .group_by(RoundInvestor.investor_id)
        .subquery()
    )

    stmt = (
        select(
            Investor,
            func.coalesce(rounds_sub.c.rounds_count, 0).label("rounds_count"),
            rounds_sub.c.last_active,
        )
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
    if sort == "last_active":
        stmt = stmt.order_by(rounds_sub.c.last_active.desc().nulls_last(), Investor.name)
    elif sort == "rounds_count":
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
        last_active = row[2]
        inv_dict = InvestorDetail.model_validate(investor).model_dump()
        inv_dict["rounds_count"] = rounds_count
        inv_dict["last_active"] = last_active
        data.append(InvestorDetail(**inv_dict))

    response = InvestorListResponse(
        data=data,
        meta=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + limit < total,
        ),
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
    """Find investors who frequently co-invest with this investor, with context."""
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

    # Count rounds where both investors were lead
    both_led_expr = func.count(
        distinct(
            case(
                (ri1.is_lead.is_(True) & ri2.is_lead.is_(True), ri1.round_id),
                else_=None,
            )
        )
    )

    stmt = (
        select(
            Investor.id,
            Investor.name,
            Investor.slug,
            Investor.type,
            func.count(distinct(ri1.round_id)).label("shared_rounds"),
            func.min(Round.date).label("first_coinvest"),
            func.max(Round.date).label("latest_coinvest"),
            both_led_expr.label("both_led"),
        )
        .select_from(ri1)
        .join(Round, Round.id == ri1.round_id)
        .join(ri2, (ri2.round_id == ri1.round_id) & (ri2.investor_id != investor.id))
        .join(Investor, Investor.id == ri2.investor_id)
        .where(ri1.investor_id == investor.id)
        .group_by(Investor.id, Investor.name, Investor.slug, Investor.type)
        .having(func.count(distinct(ri1.round_id)) >= min_rounds)
        .order_by(func.count(distinct(ri1.round_id)).desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()

    # Get shared sectors for each co-investor
    data = []
    for row in rows:
        co_inv_id = row[0]

        # Find shared round IDs, then get distinct sectors
        ri_target = aliased(RoundInvestor)
        ri_co = aliased(RoundInvestor)
        sector_stmt = (
            select(distinct(Round.sector))
            .select_from(ri_target)
            .join(ri_co, (ri_co.round_id == ri_target.round_id) & (ri_co.investor_id == co_inv_id))
            .join(Round, Round.id == ri_target.round_id)
            .where(ri_target.investor_id == investor.id)
            .where(Round.sector.isnot(None))
        )
        sector_rows = (await db.execute(sector_stmt)).scalars().all()

        data.append(
            CoInvestorOut(
                id=row[0],
                name=row[1],
                slug=row[2],
                type=row[3],
                shared_rounds=row[4],
                shared_sectors=sorted(sector_rows),
                first_coinvest=row[5],
                latest_coinvest=row[6],
                both_led=row[7],
            )
        )

    json_str = TypeAdapter(list[CoInvestorOut]).dump_json(data).decode()
    await set_cached(r, ck, json_str)
    return data


@router.get("/{slug}/syndicates", response_model=SyndicateResponse)
async def get_syndicates(
    slug: str,
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    min_appearances: int = Query(default=2, ge=2),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Detect groups of 3+ investors that repeatedly invest together."""
    ck = cache_key("syndicates", {"slug": slug, "min": min_appearances, "limit": limit})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    investor = (
        await db.execute(select(Investor).where(Investor.slug == slug))
    ).scalar_one_or_none()
    if investor is None:
        raise HTTPException(status_code=404, detail="Investor not found")

    # Get all rounds for this investor
    target_rounds_stmt = select(RoundInvestor.round_id).where(
        RoundInvestor.investor_id == investor.id
    )
    target_round_ids = (await db.execute(target_rounds_stmt)).scalars().all()

    if not target_round_ids:
        response = SyndicateResponse(
            investor=InvestorBrief(id=investor.id, name=investor.name, slug=investor.slug),
            syndicates=[],
        )
        json_str = response.model_dump_json()
        await set_cached(r, ck, json_str, ttl=900)
        return response

    # For each round, get the set of co-investor IDs (excluding target)
    co_inv_stmt = (
        select(RoundInvestor.round_id, RoundInvestor.investor_id)
        .where(RoundInvestor.round_id.in_(target_round_ids))
        .where(RoundInvestor.investor_id != investor.id)
    )
    co_inv_rows = (await db.execute(co_inv_stmt)).all()

    # Build round_id → set of co-investor IDs
    round_investors: dict[str, set[str]] = {}
    for round_id, inv_id in co_inv_rows:
        round_investors.setdefault(str(round_id), set()).add(str(inv_id))

    # Find all pairs of co-investors that appear together (with target) in multiple rounds
    # Then extend to groups of 3+ by finding cliques
    pair_counter: collections.Counter = collections.Counter()
    pair_rounds: dict[frozenset, list[str]] = {}

    for round_id, inv_ids in round_investors.items():
        if len(inv_ids) < 2:
            continue
        inv_list = sorted(inv_ids)
        for i in range(len(inv_list)):
            for j in range(i + 1, len(inv_list)):
                pair = frozenset([inv_list[i], inv_list[j]])
                pair_counter[pair] += 1
                pair_rounds.setdefault(pair, []).append(round_id)

    # Find groups: start from frequent pairs, extend to larger groups
    # For each round, find the maximal subset of co-investors that appear together frequently
    group_counter: collections.Counter = collections.Counter()
    group_rounds: dict[frozenset, list[str]] = {}

    for round_id, inv_ids in round_investors.items():
        if len(inv_ids) < 2:
            continue
        # For this round, find all subsets of size >=2
        # where every pair has co-invested >=min_appearances times
        inv_list = sorted(inv_ids)
        # Check all subsets of size 2+ (cap at size 6 to avoid combinatorial explosion)
        max_group_size = min(len(inv_list), 6)
        for size in range(max_group_size, 1, -1):
            from itertools import combinations

            for combo in combinations(inv_list, size):
                group = frozenset(combo)
                group_counter[group] += 1
                group_rounds.setdefault(group, []).append(round_id)

    # Filter to groups appearing ≥min_appearances times, deduplicate subsets
    qualifying = [
        (group, count)
        for group, count in group_counter.items()
        if count >= min_appearances and len(group) >= 2
    ]
    qualifying.sort(key=lambda x: len(x[0]) * x[1], reverse=True)

    # Remove subsets: if {A,B,C} qualifies, remove {A,B}, {A,C}, {B,C}
    final_groups: list[tuple[frozenset, int]] = []
    seen_supersets: set[frozenset] = set()
    for group, count in qualifying:
        # Skip if this group is a subset of an already-selected group
        is_subset = any(group < sg for sg in seen_supersets)
        if is_subset:
            continue
        final_groups.append((group, count))
        seen_supersets.add(group)
        if len(final_groups) >= limit:
            break

    # Resolve investor IDs to names and get round details
    all_inv_ids = set()
    all_round_ids = set()
    for group, _ in final_groups:
        all_inv_ids.update(group)
        all_round_ids.update(group_rounds.get(group, []))

    inv_map = {}
    if all_inv_ids:
        from uuid import UUID

        inv_rows = (
            await db.execute(
                select(Investor.id, Investor.name, Investor.slug).where(
                    Investor.id.in_([UUID(x) for x in all_inv_ids])
                )
            )
        ).all()
        inv_map = {str(row[0]): (row[1], row[2]) for row in inv_rows}

    round_info = {}
    if all_round_ids:
        from uuid import UUID

        round_rows = (
            await db.execute(
                select(Round.id, Round.sector, Project.name)
                .join(Project, Project.id == Round.project_id)
                .where(Round.id.in_([UUID(x) for x in all_round_ids]))
            )
        ).all()
        round_info = {str(row[0]): (row[1], row[2]) for row in round_rows}

    syndicates = []
    for group, count in final_groups:
        members = []
        for inv_id in sorted(group):
            if inv_id in inv_map:
                from uuid import UUID

                members.append(
                    SyndicateMemberOut(
                        id=UUID(inv_id),
                        name=inv_map[inv_id][0],
                        slug=inv_map[inv_id][1],
                    )
                )

        sectors = set()
        deals = []
        for rid in group_rounds.get(group, [])[:5]:
            if rid in round_info:
                sector, project_name = round_info[rid]
                if sector:
                    sectors.add(sector)
                if project_name and project_name not in deals:
                    deals.append(project_name)

        syndicates.append(
            SyndicateOut(
                members=members,
                shared_rounds=count,
                sectors=sorted(sectors),
                example_deals=deals[:5],
            )
        )

    response = SyndicateResponse(
        investor=InvestorBrief(id=investor.id, name=investor.name, slug=investor.slug),
        syndicates=syndicates,
    )
    json_str = response.model_dump_json()
    await set_cached(r, ck, json_str, ttl=1800)
    return response


@router.get("/{slug}/network", response_model=InvestorNetworkOut)
async def get_investor_network(
    slug: str,
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
):
    """Get network-level stats for an investor."""
    ck = cache_key("investor_network", {"slug": slug})
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    investor = (
        await db.execute(select(Investor).where(Investor.slug == slug))
    ).scalar_one_or_none()
    if investor is None:
        raise HTTPException(status_code=404, detail="Investor not found")

    # Lead/participant counts
    lead_count = (
        await db.execute(
            select(func.count())
            .select_from(RoundInvestor)
            .where(RoundInvestor.investor_id == investor.id, RoundInvestor.is_lead.is_(True))
        )
    ).scalar_one()

    total_rounds = (
        await db.execute(
            select(func.count())
            .select_from(RoundInvestor)
            .where(RoundInvestor.investor_id == investor.id)
        )
    ).scalar_one()

    participant_count = total_rounds - lead_count
    lead_rate = lead_count / total_rounds if total_rounds > 0 else 0.0

    # Unique co-investors
    ri1 = aliased(RoundInvestor)
    ri2 = aliased(RoundInvestor)
    co_inv_count = (
        await db.execute(
            select(func.count(distinct(ri2.investor_id)))
            .select_from(ri1)
            .join(ri2, (ri2.round_id == ri1.round_id) & (ri2.investor_id != investor.id))
            .where(ri1.investor_id == investor.id)
        )
    ).scalar_one()

    # Avg syndicate size (avg number of investors per round this investor is in)
    (
        select(func.avg(func.count(RoundInvestor.investor_id)))
        .select_from(RoundInvestor)
        .where(
            RoundInvestor.round_id.in_(
                select(RoundInvestor.round_id).where(RoundInvestor.investor_id == investor.id)
            )
        )
        .group_by(RoundInvestor.round_id)
    )
    # Need to wrap in subquery for avg of counts
    size_sub = (
        select(func.count(RoundInvestor.investor_id).label("cnt"))
        .where(
            RoundInvestor.round_id.in_(
                select(RoundInvestor.round_id).where(RoundInvestor.investor_id == investor.id)
            )
        )
        .group_by(RoundInvestor.round_id)
        .subquery()
    )
    avg_size = (await db.execute(select(func.avg(size_sub.c.cnt)))).scalar_one()
    avg_syndicate_size = float(avg_size) if avg_size else 0.0

    # Avg round size and total deployed
    round_stats = (
        await db.execute(
            select(
                func.avg(Round.amount_usd).label("avg_amount"),
                func.sum(Round.amount_usd).label("total_deployed"),
            )
            .join(RoundInvestor, RoundInvestor.round_id == Round.id)
            .where(RoundInvestor.investor_id == investor.id)
            .where(Round.amount_usd.isnot(None))
        )
    ).one()
    avg_round_size = int(round_stats[0]) if round_stats[0] else None
    total_deployed = int(round_stats[1]) if round_stats[1] else None

    # Most active year
    year_stmt = (
        select(
            extract("year", Round.date).label("yr"),
            func.count().label("cnt"),
        )
        .join(RoundInvestor, RoundInvestor.round_id == Round.id)
        .where(RoundInvestor.investor_id == investor.id)
        .group_by(extract("year", Round.date))
        .order_by(func.count().desc())
        .limit(1)
    )
    year_row = (await db.execute(year_stmt)).one_or_none()
    most_active_year = int(year_row[0]) if year_row else None

    response = InvestorNetworkOut(
        total_co_investors=co_inv_count,
        avg_syndicate_size=round(avg_syndicate_size, 1),
        lead_rate=round(lead_rate, 3),
        rounds_as_lead=lead_count,
        rounds_as_participant=participant_count,
        avg_round_size=avg_round_size,
        total_deployed=total_deployed,
        most_active_year=most_active_year,
    )
    json_str = response.model_dump_json()
    await set_cached(r, ck, json_str)
    return response


@router.get("/{slug}/rounds", response_model=RoundListResponse)
async def get_investor_rounds(
    slug: str,
    db: AsyncSession = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
    limit: int = Query(default=settings.default_page_limit, le=settings.max_page_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    sector: str | None = Query(default=None),
    round_type: str | None = Query(default=None),
    after: date | None = Query(default=None),
    before: date | None = Query(default=None),
    is_lead: bool | None = Query(default=None),
):
    """Get paginated rounds for an investor."""
    params = {
        "slug": slug,
        "limit": limit,
        "offset": offset,
        "sector": sector,
        "round_type": round_type,
        "after": str(after) if after else None,
        "before": str(before) if before else None,
        "is_lead": is_lead,
    }
    ck = cache_key("investor_rounds", params)
    cached = await get_cached(r, ck)
    if cached:
        return Response(content=cached, media_type="application/json")

    investor = (
        await db.execute(select(Investor).where(Investor.slug == slug))
    ).scalar_one_or_none()
    if investor is None:
        raise HTTPException(status_code=404, detail="Investor not found")

    # Build filters
    filters = [RoundInvestor.investor_id == investor.id]
    if sector:
        filters.append(Round.sector == sector)
    if round_type:
        filters.append(Round.round_type == round_type)
    if after:
        filters.append(Round.date >= after)
    if before:
        filters.append(Round.date <= before)
    if is_lead is not None:
        filters.append(RoundInvestor.is_lead.is_(is_lead))

    stmt = (
        select(Round)
        .join(RoundInvestor, RoundInvestor.round_id == Round.id)
        .where(*filters)
        .options(
            selectinload(Round.project),
            selectinload(Round.investor_participations).selectinload(RoundInvestor.investor),
        )
        .order_by(Round.date.desc())
        .offset(offset)
        .limit(limit)
    )

    count_stmt = (
        select(func.count(Round.id))
        .join(RoundInvestor, RoundInvestor.round_id == Round.id)
        .where(*filters)
    )

    rounds = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()

    data = []
    for rd in rounds:
        investors_out = [
            RoundInvestorOut(
                id=ri.investor.id,
                name=ri.investor.name,
                slug=ri.investor.slug,
                is_lead=ri.is_lead,
                deal_lead_name=ri.deal_lead_name,
                deal_lead_role=ri.deal_lead_role,
                check_size_usd=ri.check_size_usd,
                participation_type=ri.participation_type,
            )
            for ri in rd.investor_participations
        ]
        data.append(
            RoundOut(
                id=rd.id,
                project=ProjectBrief.model_validate(rd.project),
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
                investors=investors_out,
                created_at=rd.created_at,
            )
        )

    response = RoundListResponse(
        data=data,
        meta=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + limit < total,
        ),
    )
    await set_cached(r, ck, response.model_dump_json())
    return response


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
        InvestorSectorOut(sector=row[0], round_count=row[1], total_invested=row[2]) for row in rows
    ]

    json_str = TypeAdapter(list[InvestorSectorOut]).dump_json(data).decode()
    await set_cached(r, ck, json_str)
    return data
