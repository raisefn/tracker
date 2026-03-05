from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, UUIDMixin


class CollectorRun(Base, UUIDMixin):
    __tablename__ = "collector_runs"

    collector: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rounds_fetched: Mapped[int] = mapped_column(Integer, default=0)
    rounds_new: Mapped[int] = mapped_column(Integer, default=0)
    rounds_updated: Mapped[int] = mapped_column(Integer, default=0)
    rounds_flagged: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[dict | None] = mapped_column(JSONB)
