"""Intelligence request logging — tracks every Brain API call for outcome calibration."""

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, UUIDMixin


class IntelRequest(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "intel_requests"

    endpoint: Mapped[str] = mapped_column(String(50), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    input_data: Mapped[dict] = mapped_column(JSONB)
    response_data: Mapped[dict | None] = mapped_column(JSONB)
    scores: Mapped[dict | None] = mapped_column(JSONB)
    model_id: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id"), index=True, nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text)
