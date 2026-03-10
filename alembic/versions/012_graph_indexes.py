"""Add composite index for co-investor graph queries.

Revision ID: 012
Revises: 011
Create Date: 2025-03-09
"""

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite index for the self-join pattern in co-investor/syndicate queries
    op.execute(
        "CREATE INDEX ix_ri_investor_round ON round_investors (investor_id, round_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ri_investor_round")
