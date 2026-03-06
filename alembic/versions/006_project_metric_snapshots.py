"""Create project_metric_snapshots table for historical tracking.

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "006"
down_revision = "005"


def upgrade() -> None:
    op.create_table(
        "project_metric_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("snapshotted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_pms_project_time", "project_metric_snapshots", ["project_id", "snapshotted_at"])
    op.create_index("ix_pms_project_source_time", "project_metric_snapshots", ["project_id", "source", "snapshotted_at"])


def downgrade() -> None:
    op.drop_index("ix_pms_project_source_time")
    op.drop_index("ix_pms_project_time")
    op.drop_table("project_metric_snapshots")
