import uuid

from sqlalchemy import ForeignKey, String, Text, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class Investor(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "investors"

    name: Mapped[str] = mapped_column(String(500), unique=True)
    slug: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    type: Mapped[str | None] = mapped_column(String(50))  # vc|angel|dao|corporate|fund_of_funds|other
    website: Mapped[str | None] = mapped_column(Text)
    twitter: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    hq_location: Mapped[str | None] = mapped_column(String(200))

    round_participations: Mapped[list["RoundInvestor"]] = relationship(  # noqa: F821
        back_populates="investor"
    )
    aliases: Mapped[list["InvestorAlias"]] = relationship(back_populates="investor")


class InvestorAlias(Base, TimestampMixin):
    __tablename__ = "investor_aliases"

    alias: Mapped[str] = mapped_column(String(500), primary_key=True)
    canonical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investors.id"), index=True
    )
    source: Mapped[str] = mapped_column(String(100), default="manual")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    investor: Mapped[Investor] = relationship(back_populates="aliases")
