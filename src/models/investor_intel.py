"""Investor intel from trusted human contributors."""

import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class InvestorIntel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "investor_intel"

    contributor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contributors.id"), nullable=False, index=True
    )
    investor_slug: Mapped[str] = mapped_column(String(500), index=True)
    investor_name: Mapped[str | None] = mapped_column(String(500))

    # What kind of intel
    intel_type: Mapped[str] = mapped_column(
        String(50)
    )  # meeting | hearsay | public_signal | portfolio_move

    # Raw input
    raw_text: Mapped[str] = mapped_column(Text)

    # Structured fields extracted from raw_text
    deployment_focus: Mapped[str | None] = mapped_column(Text)
    check_size_min: Mapped[int | None] = mapped_column(BigInteger)
    check_size_max: Mapped[int | None] = mapped_column(BigInteger)
    fund_stage: Mapped[str | None] = mapped_column(String(100))
    key_partners: Mapped[dict | None] = mapped_column(JSONB)
    pass_patterns: Mapped[str | None] = mapped_column(Text)
    excitement_signals: Mapped[str | None] = mapped_column(Text)
    portfolio_intel: Mapped[str | None] = mapped_column(Text)
    meeting_context: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))

    # Confidence in the intel
    confidence: Mapped[str] = mapped_column(
        String(20), default="firsthand"
    )  # firsthand | secondhand | rumor

    # Review status
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending | approved | rejected

    # When the intel was observed (not when it was submitted)
    observed_at: Mapped[date | None] = mapped_column(Date)

    contributor: Mapped["Contributor"] = relationship(
        back_populates="intel_submissions"
    )
