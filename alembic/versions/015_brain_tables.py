"""Add intel_requests and intel_feedback tables for Brain API.

Revision ID: 015
Revises: 014
Create Date: 2026-03-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intel_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("endpoint", sa.String(50), nullable=False, index=True),
        sa.Column("input_hash", sa.String(64), nullable=False, index=True),
        sa.Column("input_data", JSONB, nullable=False),
        sa.Column("response_data", JSONB, nullable=True),
        sa.Column("scores", JSONB, nullable=True),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "api_key_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "intel_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("intel_requests.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("outcome", sa.String(50), nullable=False, index=True),
        sa.Column("outcome_details", JSONB, nullable=True),
        sa.Column("agent_notes", sa.Text, nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index("idx_intel_requests_created", "intel_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_intel_requests_created", table_name="intel_requests")
    op.drop_table("intel_feedback")
    op.drop_table("intel_requests")
