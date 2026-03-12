import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class Round(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rounds"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), index=True
    )
    round_type: Mapped[str | None] = mapped_column(String(50), index=True)
    amount_usd: Mapped[int | None] = mapped_column(BigInteger, index=True)
    valuation_usd: Mapped[int | None] = mapped_column(BigInteger)
    date: Mapped[date] = mapped_column(Date, index=True)
    chains: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    sector: Mapped[str | None] = mapped_column(String(100), index=True)
    category: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(
        String(50), index=True
    )  # defillama|sec_edgar|news|community|manual
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    validation_failures: Mapped[dict | None] = mapped_column(JSONB)

    project: Mapped["Project"] = relationship(back_populates="rounds")  # noqa: F821
    investor_participations: Mapped[list["RoundInvestor"]] = relationship(  # noqa: F821
        back_populates="round"
    )
