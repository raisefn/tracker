"""Add email field to api_keys for raise notifications.

Revision ID: 016_api_key_email
Revises: 015_brain_tables
Create Date: 2026-03-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016_api_key_email"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("email", sa.String(320), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "email")
