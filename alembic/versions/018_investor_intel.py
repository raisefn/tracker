"""Add contributors and investor_intel tables for human intelligence capture.

Revision ID: 018_investor_intel
Revises: 017_founder_enrichment
Create Date: 2026-03-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "018_investor_intel"
down_revision: str | None = "017_founder_enrichment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contributors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(300), nullable=False, unique=True),
        sa.Column("trust_tier", sa.String(20), nullable=False, server_default="contributor"),
        sa.Column("invited_by", sa.String(200), nullable=True),
        sa.Column("api_token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(12), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "investor_intel",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("contributor_id", UUID(as_uuid=True), sa.ForeignKey("contributors.id"), nullable=False, index=True),
        sa.Column("investor_slug", sa.String(500), nullable=False, index=True),
        sa.Column("investor_name", sa.String(500), nullable=True),
        sa.Column("intel_type", sa.String(50), nullable=False),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("deployment_focus", sa.Text, nullable=True),
        sa.Column("check_size_min", sa.BigInteger, nullable=True),
        sa.Column("check_size_max", sa.BigInteger, nullable=True),
        sa.Column("fund_stage", sa.String(100), nullable=True),
        sa.Column("key_partners", JSONB, nullable=True),
        sa.Column("pass_patterns", sa.Text, nullable=True),
        sa.Column("excitement_signals", sa.Text, nullable=True),
        sa.Column("portfolio_intel", sa.Text, nullable=True),
        sa.Column("meeting_context", sa.Text, nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=False, server_default="firsthand"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("observed_at", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("investor_intel")
    op.drop_table("contributors")
