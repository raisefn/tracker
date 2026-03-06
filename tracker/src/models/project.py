from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
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
    status: Mapped[str] = mapped_column(String(50), default="active")  # active|acquired|dead|unknown

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

    # Enrichment metadata
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rounds: Mapped[list["Round"]] = relationship(back_populates="project")  # noqa: F821
