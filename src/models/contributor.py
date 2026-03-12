"""Trusted contributors who can submit investor intel."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class Contributor(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "contributors"

    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(300), unique=True)
    trust_tier: Mapped[str] = mapped_column(
        String(20), default="contributor"
    )  # admin | trusted | contributor
    invited_by: Mapped[str | None] = mapped_column(String(200))
    api_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_prefix: Mapped[str] = mapped_column(String(12))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    intel_submissions: Mapped[list["InvestorIntel"]] = relationship(
        back_populates="contributor"
    )
