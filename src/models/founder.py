import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class Founder(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "founders"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(300), index=True)
    role: Mapped[str | None] = mapped_column(String(100))  # CEO, CTO, Co-Founder, etc.
    linkedin: Mapped[str | None] = mapped_column(Text)
    twitter: Mapped[str | None] = mapped_column(String(200))
    github: Mapped[str | None] = mapped_column(String(200))
    bio: Mapped[str | None] = mapped_column(Text)
    previous_companies: Mapped[dict | None] = mapped_column(
        JSONB
    )  # [{"name": "...", "role": "...", "years": "..."}]
    source: Mapped[str | None] = mapped_column(
        String(100)
    )  # yc_directory, techstars, sec_edgar, manual

    # Enrichment metadata
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_freshness: Mapped[dict | None] = mapped_column(JSONB)

    project: Mapped["Project"] = relationship(back_populates="founders")  # noqa: F821
