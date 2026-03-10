"""Add expires_at to api_keys."""

revision = "013"
down_revision = "012"

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "expires_at")
