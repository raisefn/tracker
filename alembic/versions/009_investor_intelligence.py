"""Add fields for investor intelligence: SEC Form ADV, 13F, family foundations.

Revision ID: 009
Revises: 008
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"


def upgrade() -> None:
    # SEC identifiers
    op.add_column("investors", sa.Column("sec_crd", sa.String(20), nullable=True))
    op.add_column("investors", sa.Column("sec_cik", sa.String(20), nullable=True))
    op.create_index("ix_investors_sec_crd", "investors", ["sec_crd"])
    op.create_index("ix_investors_sec_cik", "investors", ["sec_cik"])

    # Form ADV fields (investment advisers / family offices)
    op.add_column("investors", sa.Column("aum", sa.BigInteger(), nullable=True))
    op.add_column("investors", sa.Column("regulatory_status", sa.String(100), nullable=True))
    op.add_column("investors", sa.Column("legal_entity_type", sa.String(100), nullable=True))
    op.add_column("investors", sa.Column("num_clients", sa.Integer(), nullable=True))
    op.add_column("investors", sa.Column("client_types", sa.JSON(), nullable=True))
    op.add_column("investors", sa.Column("compensation_types", sa.JSON(), nullable=True))

    # 13F holdings data
    op.add_column("investors", sa.Column("portfolio_value", sa.BigInteger(), nullable=True))
    op.add_column("investors", sa.Column("num_holdings", sa.Integer(), nullable=True))
    op.add_column("investors", sa.Column("top_holdings", sa.JSON(), nullable=True))
    op.add_column("investors", sa.Column("last_13f_date", sa.String(20), nullable=True))

    # Family foundation (990-PF) fields
    op.add_column("investors", sa.Column("ein", sa.String(20), nullable=True))
    op.add_column("investors", sa.Column("foundation_assets", sa.BigInteger(), nullable=True))
    op.add_column("investors", sa.Column("annual_giving", sa.BigInteger(), nullable=True))
    op.add_column("investors", sa.Column("ntee_code", sa.String(10), nullable=True))
    op.create_index("ix_investors_ein", "investors", ["ein"])

    # Form D promoter tracking
    op.add_column("investors", sa.Column("formd_appearances", sa.Integer(), nullable=True))
    op.add_column("investors", sa.Column("formd_roles", sa.JSON(), nullable=True))

    # General investor metadata
    op.add_column("investors", sa.Column("investor_category", sa.String(100), nullable=True))
    op.add_column("investors", sa.Column("source_freshness", sa.JSON(), nullable=True))
    op.add_column("investors", sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("investors", "last_enriched_at")
    op.drop_column("investors", "source_freshness")
    op.drop_column("investors", "investor_category")
    op.drop_column("investors", "formd_roles")
    op.drop_column("investors", "formd_appearances")
    op.drop_index("ix_investors_ein", "investors")
    op.drop_column("investors", "ntee_code")
    op.drop_column("investors", "annual_giving")
    op.drop_column("investors", "foundation_assets")
    op.drop_column("investors", "ein")
    op.drop_column("investors", "last_13f_date")
    op.drop_column("investors", "top_holdings")
    op.drop_column("investors", "num_holdings")
    op.drop_column("investors", "portfolio_value")
    op.drop_column("investors", "compensation_types")
    op.drop_column("investors", "client_types")
    op.drop_column("investors", "num_clients")
    op.drop_column("investors", "legal_entity_type")
    op.drop_column("investors", "regulatory_status")
    op.drop_column("investors", "aum")
    op.drop_index("ix_investors_sec_cik", "investors")
    op.drop_index("ix_investors_sec_crd", "investors")
    op.drop_column("investors", "sec_cik")
    op.drop_column("investors", "sec_crd")
