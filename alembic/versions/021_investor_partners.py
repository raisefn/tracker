"""Add investor_partners table — partner-level data for VC firms.

Revision ID: 021_investor_partners
Revises: 020_investor_focus
Create Date: 2026-05-02

Rationale: existing `investors` table is firm-level for VCs (and individual
for angels). Founders need to target specific *people* at firms — "Sarah
Tavel at Benchmark," not just "Benchmark." This table holds person-at-firm
records, populated by auto-collection (Form D, scrapes, web_search). Sits
alongside `investor_intel.key_partners` (human-contributed intel) — both
flow into match_investors output via the existing community_intel path.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision: str = "021_investor_partners"
down_revision: Union[str, None] = "020_investor_focus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Idempotent — skip if already exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name='investor_partners'"
    ))
    if result.fetchone():
        return

    op.create_table(
        "investor_partners",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("investor_id", UUID(as_uuid=True),
                  sa.ForeignKey("investors.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("focus_sectors", sa.ARRAY(sa.String), server_default="{}"),
        sa.Column("focus_stages", sa.ARRAY(sa.String), server_default="{}"),
        sa.Column("twitter", sa.String(200), nullable=True),
        sa.Column("linkedin", sa.String(500), nullable=True),
        sa.Column("email_domain", sa.String(200), nullable=True),
        sa.Column("bio", sa.Text, nullable=True),
        sa.Column("source_freshness", JSONB, server_default="{}"),
        sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("ix_partners_investor_id", "investor_partners", ["investor_id"])
    op.create_index("ix_partners_focus_sectors", "investor_partners",
                    ["focus_sectors"], postgresql_using="gin")
    op.create_index("ux_partners_investor_name", "investor_partners",
                    ["investor_id", "name"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_partners_investor_name", "investor_partners")
    op.drop_index("ix_partners_focus_sectors", "investor_partners")
    op.drop_index("ix_partners_investor_id", "investor_partners")
    op.drop_table("investor_partners")
