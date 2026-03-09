"""Phase 1 data model: founders table, funds table, exit fields, round investor enhancements.

Revision ID: 010
Revises: 009
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "010"
down_revision = "009"


def upgrade() -> None:
    # --- Founders table ---
    op.create_table(
        "founders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("slug", sa.String(300), nullable=False),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("linkedin", sa.Text(), nullable=True),
        sa.Column("twitter", sa.String(200), nullable=True),
        sa.Column("github", sa.String(200), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("previous_companies", JSONB(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_founders_project_id", "founders", ["project_id"])
    op.create_index("ix_founders_slug", "founders", ["slug"])

    # --- Funds table ---
    op.create_table(
        "funds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("investor_id", UUID(as_uuid=True), sa.ForeignKey("investors.id"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(500), nullable=False),
        sa.Column("vintage_year", sa.Integer(), nullable=True),
        sa.Column("fund_size_usd", sa.BigInteger(), nullable=True),
        sa.Column("focus_sectors", ARRAY(sa.String()), nullable=True),
        sa.Column("focus_stages", ARRAY(sa.String()), nullable=True),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_funds_investor_id", "funds", ["investor_id"])
    op.create_index("ix_funds_slug", "funds", ["slug"])

    # --- RoundInvestor enhancements ---
    op.add_column("round_investors", sa.Column("check_size_usd", sa.BigInteger(), nullable=True))
    op.add_column("round_investors", sa.Column("participation_type", sa.String(50), nullable=True))

    # --- Project exit fields ---
    op.add_column("projects", sa.Column("exit_type", sa.String(50), nullable=True))
    op.add_column("projects", sa.Column("exit_date", sa.Date(), nullable=True))
    op.add_column("projects", sa.Column("acquirer", sa.String(300), nullable=True))
    op.add_column("projects", sa.Column("exit_valuation_usd", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    # --- Project exit fields ---
    op.drop_column("projects", "exit_valuation_usd")
    op.drop_column("projects", "acquirer")
    op.drop_column("projects", "exit_date")
    op.drop_column("projects", "exit_type")

    # --- RoundInvestor enhancements ---
    op.drop_column("round_investors", "participation_type")
    op.drop_column("round_investors", "check_size_usd")

    # --- Funds table ---
    op.drop_index("ix_funds_slug", "funds")
    op.drop_index("ix_funds_investor_id", "funds")
    op.drop_table("funds")

    # --- Founders table ---
    op.drop_index("ix_founders_slug", "founders")
    op.drop_index("ix_founders_project_id", "founders")
    op.drop_table("founders")
