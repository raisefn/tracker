"""Add batch 2 enrichment fields: Snapshot, Reddit, HN, community, Etherscan.

Revision ID: 004
Revises: 003
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"


def upgrade() -> None:
    # Snapshot governance
    op.add_column("projects", sa.Column("snapshot_space", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("snapshot_proposals_count", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("snapshot_voters_count", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("snapshot_proposal_activity_30d", sa.Integer(), nullable=True))

    # Reddit
    op.add_column("projects", sa.Column("reddit_subreddit", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("reddit_subscribers", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("reddit_active_users", sa.Integer(), nullable=True))

    # Hacker News
    op.add_column("projects", sa.Column("hn_mentions_90d", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("hn_total_points", sa.Integer(), nullable=True))

    # CoinGecko community
    op.add_column("projects", sa.Column("twitter_followers", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("telegram_members", sa.Integer(), nullable=True))

    # Etherscan
    op.add_column("projects", sa.Column("token_contract", sa.String(100), nullable=True))
    op.add_column("projects", sa.Column("token_holder_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "token_holder_count")
    op.drop_column("projects", "token_contract")
    op.drop_column("projects", "telegram_members")
    op.drop_column("projects", "twitter_followers")
    op.drop_column("projects", "hn_total_points")
    op.drop_column("projects", "hn_mentions_90d")
    op.drop_column("projects", "reddit_active_users")
    op.drop_column("projects", "reddit_subscribers")
    op.drop_column("projects", "reddit_subreddit")
    op.drop_column("projects", "snapshot_proposal_activity_30d")
    op.drop_column("projects", "snapshot_voters_count")
    op.drop_column("projects", "snapshot_proposals_count")
    op.drop_column("projects", "snapshot_space")
