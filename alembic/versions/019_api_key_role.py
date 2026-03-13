"""Add role column to api_keys.

Revision ID: 019_api_key_role
Revises: 018_investor_intel
Create Date: 2026-03-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019_api_key_role"
down_revision: Union[str, None] = "018_investor_intel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("role", sa.String(20), server_default="founder", nullable=False))


def downgrade() -> None:
    op.drop_column("api_keys", "role")
