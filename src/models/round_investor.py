import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class RoundInvestor(Base, TimestampMixin):
    __tablename__ = "round_investors"

    round_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rounds.id"), primary_key=True, index=True
    )
    investor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investors.id"), primary_key=True, index=True
    )
    is_lead: Mapped[bool] = mapped_column(Boolean, default=False)
    deal_lead_name: Mapped[str | None] = mapped_column(String(200))
    deal_lead_role: Mapped[str | None] = mapped_column(String(100))
    check_size_usd: Mapped[int | None] = mapped_column(BigInteger)
    participation_type: Mapped[str | None] = mapped_column(
        String(50)
    )  # equity, safe, convertible_note, debt, token_warrant

    round: Mapped["Round"] = relationship(back_populates="investor_participations")  # noqa: F821
    investor: Mapped["Investor"] = relationship(back_populates="round_participations")  # noqa: F821
