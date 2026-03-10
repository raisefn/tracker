"""Intelligence feedback — outcome data from raises for calibration."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, UUIDMixin


class IntelFeedback(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "intel_feedback"

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intel_requests.id"), index=True
    )
    outcome: Mapped[str] = mapped_column(String(50), index=True)
    outcome_details: Mapped[dict | None] = mapped_column(JSONB)
    agent_notes: Mapped[str | None] = mapped_column(Text)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
