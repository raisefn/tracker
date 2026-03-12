from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class Project(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(500))
    slug: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(Text)
    twitter: Mapped[str | None] = mapped_column(String(200))
    github: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(String(100), index=True)
    chains: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    status: Mapped[str] = mapped_column(
        String(50), default="active"
    )  # active|acquired|dead|unknown

    # Exit / acquisition data
    exit_type: Mapped[str | None] = mapped_column(
        String(50)
    )  # acquisition, ipo, shutdown, token_launch
    exit_date: Mapped[date | None] = mapped_column(Date)
    acquirer: Mapped[str | None] = mapped_column(String(300))
    exit_valuation_usd: Mapped[int | None] = mapped_column(BigInteger)

    # DefiLlama protocol enrichment
    defillama_slug: Mapped[str | None] = mapped_column(String(200))
    tvl: Mapped[int | None] = mapped_column(BigInteger)
    tvl_change_7d: Mapped[float | None] = mapped_column(Float)

    # CoinGecko enrichment
    coingecko_id: Mapped[str | None] = mapped_column(String(200))
    token_symbol: Mapped[str | None] = mapped_column(String(50))
    market_cap: Mapped[int | None] = mapped_column(BigInteger)
    token_price_usd: Mapped[float | None] = mapped_column(Float)

    # GitHub enrichment
    github_org: Mapped[str | None] = mapped_column(String(200))
    github_stars: Mapped[int | None] = mapped_column(Integer)
    github_commits_30d: Mapped[int | None] = mapped_column(Integer)
    github_contributors: Mapped[int | None] = mapped_column(Integer)

    # Snapshot governance enrichment
    snapshot_space: Mapped[str | None] = mapped_column(String(200))
    snapshot_proposals_count: Mapped[int | None] = mapped_column(Integer)
    snapshot_voters_count: Mapped[int | None] = mapped_column(Integer)
    snapshot_proposal_activity_30d: Mapped[int | None] = mapped_column(Integer)

    # Reddit enrichment
    reddit_subreddit: Mapped[str | None] = mapped_column(String(200))
    reddit_subscribers: Mapped[int | None] = mapped_column(Integer)
    reddit_active_users: Mapped[int | None] = mapped_column(Integer)

    # Hacker News enrichment
    hn_mentions_90d: Mapped[int | None] = mapped_column(Integer)
    hn_total_points: Mapped[int | None] = mapped_column(Integer)

    # CoinGecko community data
    twitter_followers: Mapped[int | None] = mapped_column(Integer)
    telegram_members: Mapped[int | None] = mapped_column(Integer)

    # Etherscan on-chain enrichment
    token_contract: Mapped[str | None] = mapped_column(String(100))
    token_holder_count: Mapped[int | None] = mapped_column(Integer)

    # SEC EDGAR
    sec_cik: Mapped[str | None] = mapped_column(String(20), index=True)
    sec_accession_number: Mapped[str | None] = mapped_column(String(30))
    sec_filing_date: Mapped[str | None] = mapped_column(String(20))
    sec_state: Mapped[str | None] = mapped_column(String(10))
    sec_industry_group: Mapped[str | None] = mapped_column(String(200))
    sec_revenue_range: Mapped[str | None] = mapped_column(String(100))

    # Accelerator data
    accelerator: Mapped[str | None] = mapped_column(String(100), index=True)
    accelerator_batch: Mapped[str | None] = mapped_column(String(50))
    team_size: Mapped[int | None] = mapped_column(Integer)
    one_liner: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))

    # npm/PyPI enrichment
    npm_package: Mapped[str | None] = mapped_column(String(200))
    npm_downloads_monthly: Mapped[int | None] = mapped_column(Integer)
    pypi_package: Mapped[str | None] = mapped_column(String(200))
    pypi_downloads_monthly: Mapped[int | None] = mapped_column(Integer)

    # Product Hunt enrichment
    producthunt_slug: Mapped[str | None] = mapped_column(String(200))
    producthunt_votes: Mapped[int | None] = mapped_column(Integer)

    # Enrichment metadata
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_freshness: Mapped[dict | None] = mapped_column(JSONB)

    rounds: Mapped[list["Round"]] = relationship(back_populates="project")  # noqa: F821
    founders: Mapped[list["Founder"]] = relationship(back_populates="project")  # noqa: F821
