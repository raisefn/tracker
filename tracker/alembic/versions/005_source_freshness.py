"""Add source_freshness JSONB to projects.

Revision ID: 005
Revises: 004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "005"
down_revision = "004"


def upgrade() -> None:
    op.add_column("projects", sa.Column("source_freshness", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "source_freshness")
