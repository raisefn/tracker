"""Add enrichment fields to projects.

Revision ID: 003
Revises: 002
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"


def upgrade() -> None:
    op.add_column("projects", sa.Column("defillama_slug", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("tvl", sa.BigInteger(), nullable=True))
    op.add_column("projects", sa.Column("tvl_change_7d", sa.Float(), nullable=True))
    op.add_column("projects", sa.Column("coingecko_id", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("token_symbol", sa.String(50), nullable=True))
    op.add_column("projects", sa.Column("market_cap", sa.BigInteger(), nullable=True))
    op.add_column("projects", sa.Column("token_price_usd", sa.Float(), nullable=True))
    op.add_column("projects", sa.Column("github_org", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("github_stars", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("github_commits_30d", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("github_contributors", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "last_enriched_at")
    op.drop_column("projects", "github_contributors")
    op.drop_column("projects", "github_commits_30d")
    op.drop_column("projects", "github_stars")
    op.drop_column("projects", "github_org")
    op.drop_column("projects", "token_price_usd")
    op.drop_column("projects", "market_cap")
    op.drop_column("projects", "token_symbol")
    op.drop_column("projects", "coingecko_id")
    op.drop_column("projects", "tvl_change_7d")
    op.drop_column("projects", "tvl")
    op.drop_column("projects", "defillama_slug")
