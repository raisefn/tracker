"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Projects
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(500), nullable=False, unique=True, index=True),
        sa.Column("website", sa.Text),
        sa.Column("twitter", sa.String(200)),
        sa.Column("github", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("sector", sa.String(100), index=True),
        sa.Column("chains", ARRAY(sa.String)),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Investors
    op.create_table(
        "investors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False, unique=True),
        sa.Column("slug", sa.String(500), nullable=False, unique=True, index=True),
        sa.Column("type", sa.String(50)),
        sa.Column("website", sa.Text),
        sa.Column("twitter", sa.String(200)),
        sa.Column("description", sa.Text),
        sa.Column("hq_location", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Investor aliases
    op.create_table(
        "investor_aliases",
        sa.Column("alias", sa.String(500), primary_key=True),
        sa.Column(
            "canonical_id",
            UUID(as_uuid=True),
            sa.ForeignKey("investors.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("source", sa.String(100), nullable=False, server_default="manual"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Rounds
    op.create_table(
        "rounds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("round_type", sa.String(50), index=True),
        sa.Column("amount_usd", sa.BigInteger, index=True),
        sa.Column("valuation_usd", sa.BigInteger),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("chains", ARRAY(sa.String)),
        sa.Column("sector", sa.String(100), index=True),
        sa.Column("category", sa.String(200)),
        sa.Column("source_url", sa.Text),
        sa.Column("source_type", sa.String(50), nullable=False, index=True),
        sa.Column("raw_data", JSONB),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("validation_failures", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Round-Investor join table
    op.create_table(
        "round_investors",
        sa.Column(
            "round_id",
            UUID(as_uuid=True),
            sa.ForeignKey("rounds.id"),
            primary_key=True,
            index=True,
        ),
        sa.Column(
            "investor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("investors.id"),
            primary_key=True,
            index=True,
        ),
        sa.Column("is_lead", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Collector runs
    op.create_table(
        "collector_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("collector", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("rounds_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rounds_new", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rounds_updated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rounds_flagged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors", JSONB),
    )


def downgrade() -> None:
    op.drop_table("round_investors")
    op.drop_table("rounds")
    op.drop_table("investor_aliases")
    op.drop_table("investors")
    op.drop_table("collector_runs")
    op.drop_table("projects")
