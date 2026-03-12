"""Add enrichment fields to founders table.

Revision ID: 017_founder_enrichment
Revises: 016_api_key_email
Create Date: 2026-03-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "017_founder_enrichment"
down_revision: Union[str, None] = "016_api_key_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("founders", sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("founders", sa.Column("source_freshness", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("founders", "source_freshness")
    op.drop_column("founders", "last_enriched_at")
