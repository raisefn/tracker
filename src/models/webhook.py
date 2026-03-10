"""Webhook model for event notifications."""

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, UUIDMixin


class Webhook(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "webhooks"

    url: Mapped[str] = mapped_column(String(2048))
    events: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    secret: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(200), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
