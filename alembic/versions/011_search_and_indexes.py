"""Add pg_trgm extension and performance indexes.

Revision ID: 011
Revises: 010
Create Date: 2025-03-09
"""

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable trigram extension for fuzzy search
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Trigram indexes for fuzzy name search
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_name_trgm ON projects USING gin (name gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_investors_name_trgm ON investors USING gin (name gin_trgm_ops)")

    # Composite index for stats queries (date + sector grouping)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rounds_date_sector ON rounds (date, sector)")

    # Index for round_type grouping in stats
    op.execute("CREATE INDEX IF NOT EXISTS ix_rounds_round_type ON rounds (round_type)")


def downgrade() -> None:
    op.drop_index("ix_rounds_round_type", table_name="rounds")
    op.execute("DROP INDEX IF EXISTS ix_rounds_date_sector")
    op.execute("DROP INDEX IF EXISTS ix_investors_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_projects_name_trgm")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
