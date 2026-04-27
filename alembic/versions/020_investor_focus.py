"""Add focus_sectors and focus_stages to investors.

Revision ID: 020_investor_focus
Revises: 019_api_key_role
Create Date: 2026-04-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020_investor_focus"
down_revision: Union[str, None] = "019_api_key_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns WHERE table_name='investors' AND column_name='focus_sectors'"
    ))
    if not result.fetchone():
        op.add_column("investors", sa.Column("focus_sectors", sa.ARRAY(sa.String), nullable=True))

    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns WHERE table_name='investors' AND column_name='focus_stages'"
    ))
    if not result.fetchone():
        op.add_column("investors", sa.Column("focus_stages", sa.ARRAY(sa.String), nullable=True))


def downgrade() -> None:
    op.drop_column("investors", "focus_stages")
    op.drop_column("investors", "focus_sectors")
