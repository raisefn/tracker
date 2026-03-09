import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from src.models.base import Base, TimestampMixin, UUIDMixin


class Investor(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "investors"

    name: Mapped[str] = mapped_column(String(500), unique=True)
    slug: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    type: Mapped[str | None] = mapped_column(String(50))  # vc|angel|dao|corporate|fund_of_funds|family_office|foundation|other
    website: Mapped[str | None] = mapped_column(Text)
    twitter: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    hq_location: Mapped[str | None] = mapped_column(String(200))

    # SEC identifiers
    sec_crd: Mapped[str | None] = mapped_column(String(20), index=True)
    sec_cik: Mapped[str | None] = mapped_column(String(20), index=True)

    # Form ADV fields (investment advisers / family offices)
    aum: Mapped[int | None] = mapped_column(BigInteger)
    regulatory_status: Mapped[str | None] = mapped_column(String(100))
    legal_entity_type: Mapped[str | None] = mapped_column(String(100))
    num_clients: Mapped[int | None] = mapped_column(Integer)
    client_types: Mapped[dict | None] = mapped_column(JSON)
    compensation_types: Mapped[dict | None] = mapped_column(JSON)

    # 13F holdings data
    portfolio_value: Mapped[int | None] = mapped_column(BigInteger)
    num_holdings: Mapped[int | None] = mapped_column(Integer)
    top_holdings: Mapped[dict | None] = mapped_column(JSON)
    last_13f_date: Mapped[str | None] = mapped_column(String(20))

    # Family foundation (990-PF) fields
    ein: Mapped[str | None] = mapped_column(String(20), index=True)
    foundation_assets: Mapped[int | None] = mapped_column(BigInteger)
    annual_giving: Mapped[int | None] = mapped_column(BigInteger)
    ntee_code: Mapped[str | None] = mapped_column(String(10))

    # Form D promoter tracking
    formd_appearances: Mapped[int | None] = mapped_column(Integer)
    formd_roles: Mapped[dict | None] = mapped_column(JSON)

    # General investor metadata
    investor_category: Mapped[str | None] = mapped_column(String(100))
    source_freshness: Mapped[dict | None] = mapped_column(JSON)
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    round_participations: Mapped[list["RoundInvestor"]] = relationship(  # noqa: F821
        back_populates="investor"
    )
    aliases: Mapped[list["InvestorAlias"]] = relationship(back_populates="investor")
    funds: Mapped[list["Fund"]] = relationship(back_populates="investor")  # noqa: F821


class InvestorAlias(Base, TimestampMixin):
    __tablename__ = "investor_aliases"

    alias: Mapped[str] = mapped_column(String(500), primary_key=True)
    canonical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investors.id"), index=True
    )
    source: Mapped[str] = mapped_column(String(100), default="manual")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    investor: Mapped[Investor] = relationship(back_populates="aliases")
