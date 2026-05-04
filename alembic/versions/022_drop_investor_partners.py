"""Drop investor_partners table — scaffolding never populated.

Revision ID: 022_drop_investor_partners
Revises: 021_investor_partners
Create Date: 2026-05-04

The table was added in 021 to hold partner-at-firm data for the
"name the human, not the firm" feature. We're moving to a different
model: individuals (angels, family office principals, partners-at-firms)
are surfaced via user_profiles where role='investor', populated by the
manual investor add admin form and self-signup. This table was never
populated — dropping it cleanly to reduce surface area.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022_drop_investor_partners"
down_revision: Union[str, None] = "021_investor_partners"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name='investor_partners'"
    ))
    if not result.fetchone():
        return
    op.drop_table("investor_partners")


def downgrade() -> None:
    # Re-running migration 021 recreates the table.
    pass
