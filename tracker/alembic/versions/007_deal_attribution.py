"""Add deal attribution placeholder fields to round_investors.

Revision ID: 007
Revises: 006
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"


def upgrade() -> None:
    op.add_column("round_investors", sa.Column("deal_lead_name", sa.String(200), nullable=True))
    op.add_column("round_investors", sa.Column("deal_lead_role", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("round_investors", "deal_lead_role")
    op.drop_column("round_investors", "deal_lead_name")
