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


class FounderOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str | None = None
    linkedin: str | None = None
    twitter: str | None = None
    github: str | None = None
    bio: str | None = None
    previous_companies: list[dict] | None = None
    source: str | None = None

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
    exit_type: str | None = None
    exit_date: date | None = None
    acquirer: str | None = None
    exit_valuation_usd: int | None = None
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
    founders: list[FounderOut] = []
    last_enriched_at: datetime | None = None
    source_freshness: dict[str, str] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Metric Snapshots ---


class MetricSnapshotOut(BaseModel):
    source: str
    snapshotted_at: datetime
    metrics: dict

    model_config = {"from_attributes": True}


class ProjectMetricsHistoryResponse(BaseModel):
    project_slug: str
    snapshots: list[MetricSnapshotOut]


# --- Co-investor ---


class CoInvestorOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    type: str | None = None
    shared_rounds: int
    shared_sectors: list[str] = []
    first_coinvest: date | None = None
    latest_coinvest: date | None = None
    both_led: int = 0


class InvestorSectorOut(BaseModel):
    sector: str
    round_count: int
    total_invested: int | None


# --- Investor ---


class InvestorBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str

    model_config = {"from_attributes": True}


# --- Syndicates ---


class SyndicateMemberOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class SyndicateOut(BaseModel):
    members: list[SyndicateMemberOut]
    shared_rounds: int
    sectors: list[str]
    example_deals: list[str]


class SyndicateResponse(BaseModel):
    investor: InvestorBrief
    syndicates: list[SyndicateOut]


# --- Network stats ---


class InvestorNetworkOut(BaseModel):
    total_co_investors: int
    avg_syndicate_size: float
    lead_rate: float
    rounds_as_lead: int
    rounds_as_participant: int
    avg_round_size: int | None
    total_deployed: int | None
    most_active_year: int | None


class FundOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    vintage_year: int | None = None
    fund_size_usd: int | None = None
    focus_sectors: list[str] | None = None
    focus_stages: list[str] | None = None
    status: str | None = None
    source: str | None = None

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
    rounds_count: int = 0
    last_active: date | None = None
    funds: list[FundOut] = []
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Round ---


class RoundInvestorOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_lead: bool
    deal_lead_name: str | None = None
    deal_lead_role: str | None = None
    check_size_usd: int | None = None
    participation_type: str | None = None

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


# --- Stats ---


class RoundTypeBreakdown(BaseModel):
    round_type: str
    count: int
    total_capital: int | None


class PeriodChange(BaseModel):
    total_rounds_pct: float | None
    total_capital_pct: float | None


class StatsOverviewResponse(BaseModel):
    period: str
    total_rounds: int
    total_capital: int | None
    avg_round_size: int | None
    median_round_size: int | None
    by_round_type: list[RoundTypeBreakdown]
    prior_period_change: PeriodChange | None


class SectorStatsOut(BaseModel):
    sector: str
    round_count: int
    total_capital: int | None
    avg_round_size: int | None


class TopInvestorOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    round_count: int
    total_deployed: int | None


class StatsInvestorsResponse(BaseModel):
    period: str
    most_active: list[TopInvestorOut]
    biggest_deployers: list[TopInvestorOut]


class TrendPointOut(BaseModel):
    period: str
    value: float | None


class StatsTrendsResponse(BaseModel):
    metric: str
    granularity: str
    sector: str | None
    data: list[TrendPointOut]


# --- Signals / Derived Metrics ---


class SectorMomentumOut(BaseModel):
    sector: str
    current_count: int
    prior_count: int
    change_pct: float | None
    current_capital: int | None
    prior_capital: int | None
    capital_change_pct: float | None


class ProjectSignalOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    sector: str | None
    days_since_last_raise: int
    last_round_type: str | None
    last_round_amount: int | None
    total_raised: int | None
    round_count: int
    github_stars: int | None = None
    github_commits_30d: int | None = None


class InvestorVelocityOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    deals_30d: int
    deals_90d: int
    deals_365d: int
    total_deals: int
    avg_days_between_deals: float | None


# --- Community ---


class CommunityStatsResponse(BaseModel):
    founders: int
    investors: int
    builders: int


# --- Search ---


class SearchResultOut(BaseModel):
    entity_type: str
    id: uuid.UUID
    name: str
    slug: str
    score: float
    extra: dict = {}


class SearchResponse(BaseModel):
    results: list[SearchResultOut]
    total: int


# --- Comps ---


class CompRoundBrief(BaseModel):
    round_type: str | None
    amount_usd: int | None
    date: date

    model_config = {"from_attributes": True}


class CompOut(BaseModel):
    project: ProjectBrief
    score: int
    match_reasons: list[str]
    latest_round: CompRoundBrief | None


class CompsResponse(BaseModel):
    target: ProjectBrief
    comps: list[CompOut]


# --- Health ---


class HealthResponse(BaseModel):
    status: str
    round_count: int
    investor_count: int
    project_count: int
    last_collection: datetime | None
