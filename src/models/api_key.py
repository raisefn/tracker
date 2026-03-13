"""API key model for authentication and rate limiting."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, UUIDMixin


class ApiKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_keys"

    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(8))
    owner: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20), default="founder")  # founder | investor | builder
    tier: Mapped[str] = mapped_column(String(20), default="free")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
