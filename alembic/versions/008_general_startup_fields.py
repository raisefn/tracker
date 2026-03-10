"""Add fields for general startup tracking: SEC EDGAR, accelerators, npm/PyPI, Product Hunt.

Revision ID: 008
Revises: 007
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"


def upgrade() -> None:
    # SEC EDGAR
    op.add_column("projects", sa.Column("sec_cik", sa.String(20), nullable=True))
    op.add_column("projects", sa.Column("sec_accession_number", sa.String(30), nullable=True))
    op.add_column("projects", sa.Column("sec_filing_date", sa.String(20), nullable=True))
    op.add_column("projects", sa.Column("sec_state", sa.String(10), nullable=True))
    op.add_column("projects", sa.Column("sec_industry_group", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("sec_revenue_range", sa.String(100), nullable=True))
    op.create_index("ix_projects_sec_cik", "projects", ["sec_cik"])

    # Accelerator data
    op.add_column("projects", sa.Column("accelerator", sa.String(100), nullable=True))
    op.add_column("projects", sa.Column("accelerator_batch", sa.String(50), nullable=True))
    op.add_column("projects", sa.Column("team_size", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("one_liner", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("location", sa.String(200), nullable=True))
    op.create_index("ix_projects_accelerator", "projects", ["accelerator"])

    # npm/PyPI enrichment
    op.add_column("projects", sa.Column("npm_package", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("npm_downloads_monthly", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("pypi_package", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("pypi_downloads_monthly", sa.Integer(), nullable=True))

    # Product Hunt enrichment
    op.add_column("projects", sa.Column("producthunt_slug", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("producthunt_votes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "producthunt_votes")
    op.drop_column("projects", "producthunt_slug")
    op.drop_column("projects", "pypi_downloads_monthly")
    op.drop_column("projects", "pypi_package")
    op.drop_column("projects", "npm_downloads_monthly")
    op.drop_column("projects", "npm_package")
    op.drop_index("ix_projects_accelerator", "projects")
    op.drop_column("projects", "location")
    op.drop_column("projects", "one_liner")
    op.drop_column("projects", "team_size")
    op.drop_column("projects", "accelerator_batch")
    op.drop_column("projects", "accelerator")
    op.drop_index("ix_projects_sec_cik", "projects")
    op.drop_column("projects", "sec_revenue_range")
    op.drop_column("projects", "sec_industry_group")
    op.drop_column("projects", "sec_state")
    op.drop_column("projects", "sec_filing_date")
    op.drop_column("projects", "sec_accession_number")
    op.drop_column("projects", "sec_cik")
