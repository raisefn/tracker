import uuid
from datetime import date, datetime

from pydantic import BaseModel


# --- Shared ---

class PaginationMeta(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


# --- Project ---

class ProjectBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str

    model_config = {"from_attributes": True}


class ProjectDetail(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    website: str | None
    twitter: str | None
    github: str | None
    description: str | None
    sector: str | None
    chains: list[str] | None
    status: str
    tvl: int | None = None
    tvl_change_7d: float | None = None
    token_symbol: str | None = None
    market_cap: int | None = None
    token_price_usd: float | None = None
    github_stars: int | None = None
    github_commits_30d: int | None = None
    github_contributors: int | None = None
    snapshot_proposals_count: int | None = None
    snapshot_voters_count: int | None = None
    snapshot_proposal_activity_30d: int | None = None
    reddit_subscribers: int | None = None
    reddit_active_users: int | None = None
    hn_mentions_90d: int | None = None
    hn_total_points: int | None = None
    twitter_followers: int | None = None
    telegram_members: int | None = None
    token_holder_count: int | None = None
    last_enriched_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Investor ---

class InvestorBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str

    model_config = {"from_attributes": True}


class InvestorDetail(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    type: str | None
    website: str | None
    twitter: str | None
    description: str | None
    hq_location: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Round ---

class RoundInvestorOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_lead: bool

    model_config = {"from_attributes": True}


class RoundOut(BaseModel):
    id: uuid.UUID
    project: ProjectBrief
    round_type: str | None
    amount_usd: int | None
    valuation_usd: int | None
    date: date
    chains: list[str] | None
    sector: str | None
    category: str | None
    source_url: str | None
    source_type: str
    confidence: float
    investors: list[RoundInvestorOut] = []
    created_at: datetime

    model_config = {"from_attributes": True}


# --- List responses ---

class RoundListResponse(BaseModel):
    data: list[RoundOut]
    meta: PaginationMeta


class InvestorListResponse(BaseModel):
    data: list[InvestorDetail]
    meta: PaginationMeta


class ProjectListResponse(BaseModel):
    data: list[ProjectDetail]
    meta: PaginationMeta


# --- Health ---

class HealthResponse(BaseModel):
    status: str
    round_count: int
    investor_count: int
    project_count: int
    last_collection: datetime | None
