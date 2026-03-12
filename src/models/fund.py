import uuid

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class Fund(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "funds"

    investor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investors.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(500))
    slug: Mapped[str] = mapped_column(String(500), index=True)
    vintage_year: Mapped[int | None] = mapped_column(Integer)
    fund_size_usd: Mapped[int | None] = mapped_column(BigInteger)
    focus_sectors: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    focus_stages: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    status: Mapped[str | None] = mapped_column(
        String(50)
    )  # raising, active, fully_deployed, harvesting
    source: Mapped[str | None] = mapped_column(String(100))  # sec_form_adv, sec_13f, manual

    investor: Mapped["Investor"] = relationship(back_populates="funds")  # noqa: F821
