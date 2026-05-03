"""InvestorPartner — partner-level data for VC firms.

Person-at-firm records (Sarah Tavel at Benchmark, etc.). Populated by
auto-collection (Form D, scrapes, web_search). Sits alongside
investor_intel.key_partners (human-contributed intel).
"""
import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class InvestorPartner(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "investor_partners"

    investor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("investors.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(300))
    role: Mapped[str | None] = mapped_column(String(100))  # Partner, GP, Principal, Scout

    # Personal focus (may differ from firm-level)
    focus_sectors: Mapped[list[str] | None] = mapped_column(ARRAY(String), server_default="{}")
    focus_stages: Mapped[list[str] | None] = mapped_column(ARRAY(String), server_default="{}")

    # Contact + identity
    twitter: Mapped[str | None] = mapped_column(String(200))
    linkedin: Mapped[str | None] = mapped_column(String(500))
    email_domain: Mapped[str | None] = mapped_column(String(200))  # firm pattern, not real email
    bio: Mapped[str | None] = mapped_column(Text)

    # Enrichment metadata
    source_freshness: Mapped[dict | None] = mapped_column(JSONB, server_default="{}")
    last_enriched_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), nullable=True)

    investor: Mapped["Investor"] = relationship(back_populates="partners")  # noqa: F821
